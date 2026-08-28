/**
 * 沟通页搜索找人执行器（设计文档 §10.6，CLI send-to 子命令的找人段）。
 *
 * 沟通页顶部有搜索入口（CSS 背景图标，DOMSnapshot 抓不到节点），点击后弹出搜索框；
 * 输入姓名后结果按「联系人/职位/...」分类，点「联系人」下第一张结果卡片进入对话。
 *
 * 真机校准（2026-08-13，窗口 1249x1277）：
 * - 搜索图标：批量锚点动态定位（locateSearchEntry，2026-08-24 重写，分辨率/DPI 自适应）。
 * - 搜索框（2026-08-27 诊断后范式变更，见 findSearchField 注释）：点入口后左栏顶部收缩条
 *   **原位变形**为原生 <input>——不 diff 新增节点，直接按 tag=INPUT + 形态识别。
 * - 搜索结果项：输入姓名后等异步渲染（SEARCH_RESULT_DELAY=4000ms），结果项有 layout bounds，
 *   直接按目标姓名在视口内（cx<850）唯一定位点击。真机修正（2026-08-13）：曾因 sleep 太早（1100ms）
 *   误判"结果卡片人名 DOMSnapshot 抓不到"，实为延时不够；且不再点固定第一项（目标未必在第一项，会点错人）。
 * - 进入对话校验：发送按钮出现（locateSendButton 命中 1 个）。
 *
 * 关键结论（会话列表滚动 CDP mouseWheel + Win32 mouse_event 双失效，BOSS 非标准滚动）→ 改用搜索找人。
 *
 * 安全设计（fail-loud）：搜索框 0/多个、进入对话后无发送按钮 → 抛 ChatSearchError，绝不盲点。
 * 通道（2026-08-26 决策）：点击/输入类第一优先 Win32 真实事件（防爬）。点图标/结果卡片走
 * deps.click；搜索框聚焦+姓名输入走 deps.clickAndType（原子：点击聚焦后紧接真实键盘逐字）。
 * 2026-08-24 曾因误判「Win32 DPI 换算偏差」全改 CDP——实为用户移动窗口致输入框不可见，已回退。
 * 输入落地重试（2026-08-27 聚焦竞态）：clickAndType 后校验姓名字符出现在 strings，未落地时
 * clearInput（CDP ctrl+a + Delete）清空后重新定位+重输一次，仍失败才报错（详见 typeNameWithRetry）。
 */
import {
  type DomSnapshot,
  type ClickPoint,
  findNodesByString,
  accumulateOwnerOffset,
  boundsCenter,
  indexedValues,
} from './domSnapshot.js'
import { viewportOf } from './FilterSetter.js'
import { locateSendButton } from './ChatSendExecutor.js'
import { CancelledError } from '../operations/types.js'

export class ChatSearchError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'ChatSearchError'
  }
}

export interface ChatSearchDeps {
  /** 采集 fresh DOMSnapshot（每次定位前重新采集，禁止复用旧坐标） */
  snapshot(): Promise<DomSnapshot>
  /** Win32 真实鼠标点击（viewport 为页面截图尺寸，device px） */
  click(point: ClickPoint, viewport: { width: number; height: number }): Promise<void>
  /** Win32 原子「真实鼠标点击聚焦 + 真实键盘逐字输入」（一次调用完成，防焦点被抢） */
  clickAndType(point: ClickPoint, viewport: { width: number; height: number }, text: string): Promise<void>
  /** 清空当前聚焦输入框（可选）：CDP ctrl+a 全选 + Delete。输入未落地的清空重试用——
   *  未提供时输入校验失败直接报错（不清空重打会与残留文本拼接，不能盲重试） */
  clearInput?(): Promise<void>
  /** 按 Escape（可选）：openContact 开头清场——搜索弹层是开关型（toggle），残留弹层会让
   *  「点击入口」变成关闭。缺省跳过 */
  pressEscape?(): Promise<void>
  /** 协作式取消信号：入口检查一次，触发即抛 CancelledError */
  signal?: AbortSignal
  /** 可注入 sleep（测试） */
  sleep?(ms: number): Promise<void>
}

/** 搜索框尺寸下限（视口宽比例，替代原绝对 w>200）：搜索框是左栏主体宽度的输入条 */
const SEARCH_BOX_MIN_W_RATIO = 0.08
/** 搜索框 y 上限（视口比例）：点开的搜索浮层必在页面上部 */
const SEARCH_BOX_MAX_CY_RATIO = 0.4
/** 多 INPUT 候选时与搜索入口的 y 邻近带（device px）：搜索框在入口正下方原位变形，真机 Δy≈0 */
const SEARCH_BOX_ENTRY_Y_BAND = 80
/** DIV 兜底的 cx 上限（视口比例）：会话列表列右界≈聊天面板起点（真机 548/1249≈0.44）。
 *  contenteditable DIV 兜底必须限定在列表列——聊天面板的消息气泡容器（真机 .text DIV
 *  192x36 @x=626）同样满足「宽而矮」形态且 cy 可落入上部带，只靠 68% 左栏带会误命中 */
const SEARCH_FALLBACK_MAX_CX_RATIO = 0.44
/** 点搜索图标后等弹层（ms） */
const SEARCH_OPEN_DELAY = 1400
/** 输入姓名后等搜索结果异步渲染（ms）。真机修正（2026-08-13）：搜索结果项（公司名）bounds 异步渲染较慢，
 *  太早（<3000ms）项无 bounds 会误判"抓不到"；真机实测需 ~4000ms 稳定出现 */
const SEARCH_RESULT_DELAY = 4000
/** 点结果卡片后等进入对话（ms） */
const ENTER_CHAT_DELAY = 2000
/** 输入未落地清空后等待输入框稳定（ms） */
const CLEAR_INPUT_SETTLE = 300

/** 候选聚簇：中心距离 <12px 视为同一目标的嵌套元素（如 34x34 容器与内部 13x13 svg），
 *  每簇取面积最大者代表——点击坐标等价 */
function clusterByCenter(hits: Array<{ cx: number; cy: number; x: number; y: number; w: number; h: number; kind: string }>) {
  const reps: typeof hits = []
  for (const c of hits) {
    const rep = reps.find((r) => Math.abs(r.cx - c.cx) < 12 && Math.abs(r.cy - c.cy) < 12)
    if (!rep) reps.push(c)
    else if (c.w * c.h > rep.w * rep.h) Object.assign(rep, c)
  }
  return reps
}

/** outer 几何完全包含 inner（「DIV 容器包 svg」的图标按钮形态判定） */
function containsBounds(
  outer: { x: number; y: number; w: number; h: number },
  inner: { x: number; y: number; w: number; h: number },
): boolean {
  return inner.x >= outer.x && inner.y >= outer.y && inner.x + inner.w <= outer.x + outer.w && inner.y + inner.h <= outer.y + outer.h
}

/** 输入条形态判定（视口相对）：左栏内、视口上部、宽而矮（搜索输入条特征） */
function isFieldShape(b: [number, number, number, number], viewport: { width: number; height: number }): boolean {
  const cx = b[0]! + b[2]! / 2
  const cy = b[1]! + b[3]! / 2
  return cx < viewport.width * 0.68 && cy < viewport.height * 0.45
    && b[2]! > Math.max(150, viewport.width * SEARCH_BOX_MIN_W_RATIO) && b[3]! <= 60
}

export class ChatSearchExecutor {
  /**
   * 定位顶栏搜索入口（2026-08-24 重写，分辨率/DPI/布局自适应）：
   * 1) 主锚：#text「批量」（沟通列表批量操作按钮，紧贴搜索按钮正下方）→ 其正上方
   *    （y-60 ~ y-5、横向重叠）找小尺寸（≤44px）DIV/svg/I 图标容器 → 唯一命中点中心。
   *    真机嗅探（鼠标悬停 GetCursorPos 换算页面 523,136）：图标容器 DIV 34x34 @(503,124)、
   *    svg 13x13 @(513,135)、「批量」#text @(510,171)——图标在批量正上方 ~38px、x 对齐。
   * 2) 兜底：左栏中上部（x∈[100,940]、cy<视口 25%）唯一的 svg 节点（排除左侧菜单 svg cx<100
   *    与右上全局搜索 svg cx>940）。
   * fail-loud：无锚点/候选歧义 → 报 count，绝不盲点。
   */
  private locateSearchEntry(snap: DomSnapshot): { point: ClickPoint | null; count: number } {
    const viewport = viewportOf(snap)
    interface NodeHit { cx: number; cy: number; x: number; y: number; w: number; h: number; kind: string }
    const smallIcons: NodeHit[] = []
    const svgs: NodeHit[] = []
    const batchTexts: Array<{ cx: number; top: number; left: number; right: number }> = []

    snap.documents.forEach((document) => {
      const nameByNode = new Map(indexedValues(document.nodes.nodeName, 'nodeName'))
      const valueByNode = new Map(indexedValues(document.nodes.nodeValue, 'nodeValue'))
      document.layout.nodeIndex.forEach((nodeIndex, layoutIndex) => {
        const b = document.layout.bounds[layoutIndex]
        if (!b || b[2]! <= 0 || b[3]! <= 0) return
        const nn = String(snap.strings[nameByNode.get(nodeIndex) ?? -1] ?? '')
        const txt = String(snap.strings[valueByNode.get(nodeIndex) ?? -1] ?? '').trim()
        const hit: NodeHit = {
          cx: b[0]! + b[2]! / 2, cy: b[1]! + b[3]! / 2,
          x: b[0]!, y: b[1]!, w: b[2]!, h: b[3]!, kind: nn,
        }
        if (hit.cy > viewport.height * 0.3) return // 顶栏带以下不参与（会话列表图标排除）
        // 参与区间（视口相对，替代原绝对值 [100,940]）：下限排除最左导航菜单列（约 6% 宽），
        // 上限排除右上全局搜索区（约 25% 起）——列表页 1249 视口 ≈ [75,937]，与原值一致
        const cxMin = viewport.width * 0.06
        const cxMax = viewport.width * 0.75
        if (nn === 'svg' && hit.cx >= cxMin && hit.cx < cxMax) svgs.push(hit)
        if (hit.w <= 44 && hit.h <= 44 && hit.cx >= cxMin && hit.cx < cxMax && (nn === 'DIV' || nn === 'svg' || nn === 'I' || nn === 'A')) {
          smallIcons.push(hit)
        }
        if (txt === '批量' && hit.cx < cxMax) batchTexts.push({ cx: hit.cx, top: hit.y, left: hit.x, right: hit.x + b[2]! })
      })
    })

    // 主锚：批量正上方的小图标。真机（2026-08-24 诊断）：±40px 横向带内有 4 簇小图标
    // （通知点/徽标等），收紧到 ±20px（图标与批量中心基本对齐，实测 dx=3）；仍歧义时
    // 优先「DIV 容器完全包含 svg」的按钮形态（搜索按钮真机形态：34x34 DIV 包 13x13 svg）
    const diag: string[] = []
    for (const batch of batchTexts) {
      // 横向对齐容差与上方带高均为视口相对（替代原 ±20/64px 绝对值）：图标与批量中心基本对齐
      const xTol = Math.max(16, viewport.width * 0.02)
      const bandH = Math.max(48, viewport.height * 0.05)
      const inBand = (ic: { cx: number; y: number; h: number }) =>
        Math.abs(ic.cx - batch.cx) <= xTol && ic.y + ic.h <= batch.top + 4 && ic.y >= batch.top - bandH
      const cands = smallIcons.filter(inBand)
      const reps = clusterByCenter(cands)
      diag.push(`批量@(${Math.round(batch.cx)},${batch.top}) 候选${cands.length} 簇${reps.length}`)
      if (reps.length === 1) return { point: { x: reps[0]!.cx, y: reps[0]!.cy }, count: 1 }
      if (reps.length > 1) {
        const btnLike = reps.filter(
          (r) => r.kind === 'DIV' && svgs.some((s) => containsBounds(r, s)),
        )
        if (btnLike.length === 1) return { point: { x: btnLike[0]!.cx, y: btnLike[0]!.cy }, count: 1 }
      }
      // 兜底（缩到批量邻近窗口的 svg，比全局 svg 唯一性强）：批量正上方 svg 聚簇唯一
      const nearSvgs = svgs.filter(inBand)
      const svgReps = clusterByCenter(nearSvgs)
      diag.push(`svg邻近 ${nearSvgs.length}个 聚簇${svgReps.length}`)
      if (svgReps.length === 1) return { point: { x: svgReps[0]!.cx, y: svgReps[0]!.cy }, count: 1 }
    }
    this.lastEntryDiag = `锚点${batchTexts.length}个[${diag.join(' | ')}] 小图标${smallIcons.length} svg${svgs.length}`
    return { point: null, count: batchTexts.length === 0 ? 0 : smallIcons.length }
  }

  /** locateSearchEntry 失败诊断（错误消息用，排查布局变化） */
  private lastEntryDiag = ''


  private readonly sleep: (ms: number) => Promise<void>

  constructor(private readonly deps: ChatSearchDeps) {
    this.sleep = deps.sleep ?? ((ms) => new Promise((r) => setTimeout(r, ms)))
  }

  /**
   * 搜索姓名并进入其会话：点搜索入口 → 输入姓名 → 点「联系人」下第一张结果卡片。
   * 成功进入对话（发送按钮出现）后返回；任何歧义抛 ChatSearchError（fail-loud，绝不盲点）。
   */
  async openContact(opts: { name: string }): Promise<void> {
    if (this.deps.signal?.aborted) throw new CancelledError()
    const name = opts.name
    if (!name) throw new ChatSearchError('搜索姓名不能为空')

    // 0. 清场：搜索弹层是开关型（toggle），若此前有残留弹层（中断/重试），点击入口会变成
    //    「关闭」，后续就找不到 INPUT 搜索框——先 Escape 关掉保证「点击=打开」语义
    if (this.deps.pressEscape) {
      await this.deps.pressEscape()
      await this.sleep(400)
    }

    // 1. 点搜索入口（锚点动态定位，Win32 真实鼠标——2026-08-26 决策：点击类第一优先 Win32 防爬）
    const snap0 = await this.deps.snapshot()
    const entry = this.locateSearchEntry(snap0)
    if (entry.count !== 1) {
      throw new ChatSearchError(
        entry.count === 0
          ? '未找到顶栏搜索入口（无「批量」锚点且无左栏 svg）：页面布局可能已改版，请人工查看'
          : `顶栏搜索入口候选 ${entry.count} 个无法唯一定位（${this.lastEntryDiag}），请人工查看`,
      )
    }
    await this.deps.click(entry.point!, viewportOf(snap0))
    await this.sleep(SEARCH_OPEN_DELAY)

    // 2. 定位搜索框：直接识别 INPUT 形态（2026-08-27 范式变更，见 findSearchField 注释）；
    //    未命中时重试一次点击再找（弹层偶发不响应首击，重试安全——首击只开弹层无写效应）
    let box = this.findSearchField(await this.deps.snapshot(), entry.point!)
    if (!box.point && box.count === 0) {
      await this.deps.click(entry.point!, viewportOf(snap0))
      await this.sleep(SEARCH_OPEN_DELAY)
      box = this.findSearchField(await this.deps.snapshot(), entry.point!)
    }
    if (!box.point) {
      throw new ChatSearchError(
        box.count === 0
          ? `点击搜索入口(入口@${Math.round(entry.point!.x)},${Math.round(entry.point!.y)})后未发现搜索输入框（无唯一 INPUT，亦无唯一输入条形态元素）：搜索弹层未打开或页面结构已变，请人工查看`
          : `点击搜索入口后发现 ${box.count} 个候选搜索输入框，无法唯一定位，请人工查看`,
      )
    }

    // 3. Win32 原子「点击搜索框聚焦 + 真实键盘逐字输入姓名」（同一次 ps1 调用，防焦点被抢）。
    //    输入后校验文字落地（聚焦失败时输入会落空，带病继续会把聊天消息输进搜索框）；
    //    未落地时清空重试一次（2026-08-27 真机诊断：框已在 DOM 但点击聚焦偶发落空/浮层未完全可交互）
    if (this.deps.signal?.aborted) throw new CancelledError('已取消：输入搜索姓名时中止')
    const snapTyped = await this.typeNameWithRetry(name, box.point, viewportOf(snap0), entry.point!)

    // 4. 定位目标姓名的搜索结果项（「联系人」标签下方带的公司名唯一命中），Win32 点击
    //    （结果卡片是切换会话的写动作，第一优先 Win32 真实鼠标）
    const { point: target, count: targetCount } = this.locateTargetResult(snapTyped)
    if (targetCount !== 1) {
      throw new ChatSearchError(
        targetCount === 0
          ? `搜索「${name}」后未在结果列表找到该姓名的可见项：搜索无结果或目标在列表深处，请人工查看`
          : `搜索「${name}」在结果列表有 ${targetCount} 个可见命中，无法唯一定位，请人工查看`,
      )
    }
    await this.deps.click(target!, viewportOf(snapTyped))
    await this.sleep(ENTER_CHAT_DELAY)

    // 5. 强校验进入对话：发送按钮唯一 + 搜索浮层已关闭（「联系人」标签消失——点击结果
    //    才会关浮层开对话；仅凭发送按钮不够：对话区右侧常驻发送按钮，会话未切换时也存在）
    const snap3 = await this.deps.snapshot()
    const { count } = locateSendButton(snap3)
    const searchLayerOpen = this.contactsLabelCenter(snap3) !== null
    if (count !== 1 || searchLayerOpen) {
      throw new ChatSearchError(
        count !== 1
          ? `点击结果卡片后未进入对话（发送按钮 ${count} 个）：搜索结果「${name}」可能不存在或点击未生效，请人工查看`
          : `点击结果卡片后搜索浮层未关闭、对话未切换到「${name}」（结果点击未生效），请人工查看`,
      )
    }
  }

  /**
   * 搜索框定位（2026-08-27 真机诊断后范式变更：放弃 diff，直接识别）。
   *
   * 旧「diff 新增输入条」范式从根上失效：沟通页真机（2026-08-27 晚）实测，点击搜索入口后
   * 左栏顶部收缩条**原位变形**为原生 <input>（INPUT@(198,124,339,34)，位置/尺寸与点击前的
   * DIV 几乎相同）——没有「新增」节点可 diff，旧范式两种真机失败：①把变形前后的容器都算
   * 「新增」误报 2 个候选；②0 新增靠「现存唯一 INPUT」回退分支侥幸命中。
   *
   * 新范式（与 diff 无关，单快照即可判定）：
   * 1) doc 内 tag=INPUT 且满足输入条形态（isFieldShape：左栏 cx<68%、上部 cy<45%、
   *    宽 w>max(150,视口8%)、矮 h≤60）的元素唯一 → 命中；
   * 2) 多个 INPUT：取与搜索入口 y 邻近（|Δy|≤80，搜索框在入口正下方原位变形，真机 Δy≈0）
   *    的唯一者；仍不唯一 → fail-loud 报候选数，绝不盲点；
   * 3) 0 个 INPUT：回退「宽而矮 DIV」形态唯一命中（**按形态识别，不校验 contenteditable 属性**
   *    ——attributes 字段非各版本快照恒有，形态已足够消歧；兼容 2026-08-13~08-24 期间的
   *    contenteditable 版页面；限定列表列 cx<44% 排除聊天面板气泡容器误命中），不唯一 → 0 处理。
   */
  private findSearchField(
    snap: DomSnapshot,
    entry: ClickPoint,
  ): { point: ClickPoint | null; count: number } {
    const viewport = viewportOf(snap)
    const inputs = this.collectVisibleInputs(snap).filter((b) => isFieldShape(b, viewport))
    if (inputs.length > 0) {
      if (inputs.length === 1) {
        const b = inputs[0]!
        return { point: { x: b[0]! + b[2]! / 2, y: b[1]! + b[3]! / 2 }, count: 1 }
      }
      const near = inputs.filter((b) => Math.abs(b[1]! + b[3]! / 2 - entry.y) <= SEARCH_BOX_ENTRY_Y_BAND)
      if (near.length === 1) {
        const b = near[0]!
        return { point: { x: b[0]! + b[2]! / 2, y: b[1]! + b[3]! / 2 }, count: 1 }
      }
      return { point: null, count: inputs.length }
    }
    // 0 INPUT：回退 DIV 形态（旧版 contenteditable 页面；按形态识别不校验属性）——列表列内
    // （cx<44%，排除聊天面板气泡容器误命中，见 SEARCH_FALLBACK_MAX_CX_RATIO 注释）唯一命中才用
    const fields = this.collectFieldLike(snap).filter(
      (b) => b[0]! + b[2]! / 2 < viewport.width * SEARCH_FALLBACK_MAX_CX_RATIO,
    )
    if (fields.length === 1) {
      const b = fields[0]!
      return { point: { x: b[0]! + b[2]! / 2, y: b[1]! + b[3]! / 2 }, count: 1 }
    }
    return { point: null, count: 0 }
  }

  /** 姓名字符在页面 strings 中的缺失清单（逐字节点反爬形态：每个字符是独立单字字符串） */
  private missingCharsOf(snap: DomSnapshot, name: string): string[] {
    const charsPresent = new Set(snap.strings.filter((s) => typeof s === 'string' && s.trim().length === 1))
    return [...new Set(name.split(''))].filter((ch) => !charsPresent.has(ch))
  }

  /**
   * 输入姓名并校验落地；未落地时清空重试一次。
   * 2026-08-27 真机诊断「聚焦竞态」：搜索框已在 DOM 出现，但 Win32 点击聚焦偶发落空（或浮层
   * 未完全可交互），输入落到了别处/被吞。此时直接报错太脆——清空（clearInput=CDP ctrl+a+Delete，
   * 作用于当前聚焦元素，可清掉部分落地的残字）→ 等稳定 → 重新 fresh snapshot 定位搜索框
   * （浮层位置可能微动，不复用旧坐标）→ 再 clickAndType 一次 → 再校验；仍失败才 fail-loud
   * （报错文案注明已重试，提示人工查看）。未注入 clearInput 时无清空手段，直接报错——
   * 不清空重打会把姓名拼进残留文本，更危险。
   */
  private async typeNameWithRetry(
    name: string,
    firstBox: ClickPoint,
    viewport: { width: number; height: number },
    entry: ClickPoint,
  ): Promise<DomSnapshot> {
    await this.deps.clickAndType(firstBox, viewport, name)
    await this.sleep(SEARCH_RESULT_DELAY)
    let snapTyped = await this.deps.snapshot()
    let missing = this.missingCharsOf(snapTyped, name)
    if (missing.length === 0) return snapTyped

    if (!this.deps.clearInput) {
      throw new ChatSearchError(
        `搜索姓名「${name}」未落地（页面未见输入字符 ${JSON.stringify(missing)}）：搜索框未聚焦或输入被拦，已停止，请人工查看`,
      )
    }
    await this.deps.clearInput()
    await this.sleep(CLEAR_INPUT_SETTLE)
    const snapRetry = await this.deps.snapshot()
    const box = this.findSearchField(snapRetry, entry)
    if (!box.point) {
      throw new ChatSearchError(
        `搜索姓名「${name}」首次输入未落地（页面未见输入字符 ${JSON.stringify(missing)}），清空重试时未再找到搜索输入框：搜索浮层可能已关闭，请人工查看`,
      )
    }
    await this.deps.clickAndType(box.point, viewportOf(snapRetry), name)
    await this.sleep(SEARCH_RESULT_DELAY)
    snapTyped = await this.deps.snapshot()
    missing = this.missingCharsOf(snapTyped, name)
    if (missing.length > 0) {
      throw new ChatSearchError(
        `搜索姓名「${name}」未落地（页面未见输入字符 ${JSON.stringify(missing)}）：已清空重试一次仍失败，搜索框未聚焦或输入被拦，请人工查看`,
      )
    }
    return snapTyped
  }

  /** 输入条形态（视口相对）：左栏内、上部、宽而矮——0 INPUT 时的 contenteditable DIV 回退用
   *  （2026-08-27 范式：主路径已改为直接识别 INPUT，本函数只服务旧版页面兜底） */
  private collectFieldLike(snap: DomSnapshot): Array<[number, number, number, number]> {
    const viewport = viewportOf(snap)
    const out: Array<[number, number, number, number]> = []
    for (const document of snap.documents) {
      if (!document?.nodes?.nodeName || !document.layout) continue
      const nameByNode = new Map(indexedValues(document.nodes.nodeName, 'nodeName'))
      document.layout.nodeIndex.forEach((nodeIndex, layoutIndex) => {
        const b = document.layout.bounds[layoutIndex]
        if (!b || b[2]! <= 0 || b[3]! <= 0) return
        const nn = String(snap.strings[nameByNode.get(nodeIndex) ?? -1] ?? '')
        // 只看可承载输入的标签（DIV/INPUT/TEXTAREA），排除文本/图标等碎片造成海量噪声
        if (nn !== 'DIV' && nn !== 'INPUT' && nn !== 'TEXTAREA') return
        if (isFieldShape([b[0]!, b[1]!, b[2]!, b[3]!], viewport)) out.push([b[0]!, b[1]!, b[2]!, b[3]!])
      })
    }
    return out
  }

  /** 全部文档可见 INPUT 的 bounds 列表 */
  private collectVisibleInputs(snap: DomSnapshot): Array<[number, number, number, number]> {
    const out: Array<[number, number, number, number]> = []
    for (const document of snap.documents) {
      if (!document?.nodes?.nodeName || !document.layout) continue
      const nameByNode = new Map(indexedValues(document.nodes.nodeName, 'nodeName'))
      document.layout.nodeIndex.forEach((nodeIndex, layoutIndex) => {
        if (snap.strings[nameByNode.get(nodeIndex) ?? -1] !== 'INPUT') return
        const b = document.layout.bounds[layoutIndex]
        if (!b || b[2]! <= 0 || b[3]! <= 0) return
        out.push([b[0]!, b[1]!, b[2]!, b[3]!])
      })
    }
    return out
  }

  /**
   * 定位搜索结果项：在「联系人」分类标签紧贴下方的结果区，找公司名文本（"_" 开头连续字符串）点击。
   *
   * 真机关键（2026-08-13）：搜索结果浮层姓名是逐字单独节点（反爬，"施""文""斌"），但**公司名是连续字符串
   * 且以 "_" 开头**（如 "_上海功存智能科技有限公司"）。点公司名所在结果项即可进入对话——不靠姓名逐字匹配，
   * 简单可靠（搜索精确匹配，结果第一项即目标）。
   * 用「联系人下方紧贴」y 范围（视口相对）排除上方搜索框区 + 下方会话列表。
   */
  private locateTargetResult(snap: DomSnapshot): { point: ClickPoint | null; count: number } {
    const viewport = viewportOf(snap)
    const contacts = this.contactsLabelCenter(snap)
    if (contacts === null) return { point: null, count: 0 } // 无联系人标签 = 搜索无结果/浮层未开
    // 结果带（视口相对，替代原绝对 80px）：联系人标签紧下方一小带，排除上方搜索框与下方会话列表；
    // x 带：结果浮层在联系人标签横向邻近区（替代原绝对 cx<850 左栏界）
    const yMin = contacts.y + 4
    const yMax = contacts.y + Math.max(60, viewport.height * 0.06)
    const xBand = viewport.width * 0.25
    const hits: ClickPoint[] = []
    snap.documents.forEach((document, documentIndex) => {
      let offset: { x: number; y: number }
      try {
        offset = accumulateOwnerOffset(snap, documentIndex)
      } catch {
        return
      }
      const valueByNode = new Map(indexedValues(document.nodes.nodeValue, 'nodeValue'))
      document.layout.nodeIndex.forEach((ni, li) => {
        const si = valueByNode.get(ni)
        const t = si !== undefined ? snap.strings[si] : ''
        if (typeof t !== 'string') return
        const trimmed = t.trim()
        if (trimmed.length === 0 || !trimmed.startsWith('_')) return // 只要公司名（"_" 开头）
        const b = document.layout.bounds[li]
        if (!b || b[2]! <= 0 || b[3]! <= 0) return
        const x = offset.x + b[0]! + b[2]! / 2 - (document.scrollOffsetX ?? 0)
        const y = offset.y + b[1]! + b[3]! / 2 - (document.scrollOffsetY ?? 0)
        if (x <= 0 || Math.abs(x - contacts.x) >= xBand || y <= 0 || y > viewport.height) return
        if (y < yMin || y > yMax) return
        hits.push({ x: Math.round(x), y: Math.round(y) })
      })
    })
    return { point: hits.length === 1 ? hits[0]! : null, count: hits.length }
  }

  /** 「联系人」分类标签中心（结果项定位锚点）：全文档可见精确命中取最上方；无命中返回 null */
  private contactsLabelCenter(snap: DomSnapshot): { x: number; y: number } | null {
    let best: { x: number; y: number } | null = null
    snap.strings.forEach((s, stringIndex) => {
      if (s.trim() !== '联系人') return
      snap.documents.forEach((document, documentIndex) => {
        let offset: { x: number; y: number }
        try {
          offset = accumulateOwnerOffset(snap, documentIndex)
        } catch {
          return
        }
        for (const { bounds } of findNodesByString(document, stringIndex)) {
          if (bounds[2] <= 0 || bounds[3] <= 0) continue
          const c = boundsCenter(bounds)
          const p = { x: offset.x + c.x - (document.scrollOffsetX ?? 0), y: offset.y + c.y - (document.scrollOffsetY ?? 0) }
          if (p.y <= 0 || p.y > viewportOf(snap).height) continue
          if (best === null || p.y < best.y) best = p
        }
      })
    })
    return best
  }
}
