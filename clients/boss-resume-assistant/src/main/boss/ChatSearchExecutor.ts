/**
 * 沟通页搜索找人执行器（设计文档 §10.6，CLI send-to 子命令的找人段）。
 *
 * 沟通页顶部有搜索入口（CSS 背景图标，DOMSnapshot 抓不到节点），点击后弹出搜索框；
 * 输入姓名后结果按「联系人/职位/...」分类，点「联系人」下第一张结果卡片进入对话。
 *
 * 真机校准（2026-08-13，窗口 1249x1277）：
 * - 搜索图标：SEARCH_ICON_POINT。GetCursorPos 校准值——CSS 背景图标在 DOMSnapshot 里抓不到节点，
 *   无法几何定位，只能用真机标定的固定坐标（窗口尺寸/布局变化时需重新校准）。
 * - 搜索框：点入口后弹出的 INPUT（点击前后 diff 新增定位，无绝对坐标依赖）。
 * - 搜索结果项：输入姓名后等异步渲染（SEARCH_RESULT_DELAY=2500ms），结果项有 layout bounds，
 *   直接按目标姓名在视口内（cx<850）唯一定位点击。真机修正（2026-08-13）：曾因 sleep 太早（1100ms）
 *   误判"结果卡片人名 DOMSnapshot 抓不到"，实为延时不够；且不再点固定第一项（目标未必在第一项，会点错人）。
 * - 进入对话校验：发送按钮出现（locateSendButton 命中 1 个）。
 *
 * 关键结论（会话列表滚动 CDP mouseWheel + Win32 mouse_event 双失效，BOSS 非标准滚动）→ 改用搜索找人。
 *
 * 安全设计（fail-loud）：搜索框 0/多个、进入对话后无发送按钮 → 抛 ChatSearchError，绝不盲点。
 * 写动作（点图标/搜索框/结果卡片）走 Win32（deps.click）；逐字输入姓名走 CDP（typeChar）。
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
  /** CDP 浏览类点击（可选）。搜索入口/搜索框聚焦用 CDP：真机 2026-08-24 实证 CDP 点击
   *  能唤起搜索弹层，而 Win32 物理点击存在 DPI/页面缩放换算偏差可能点偏——入口点击是
   *  浏览类动作（无业务写效应）。缺省回退 click */
  clickBrowse?(point: ClickPoint): Promise<void>
  /** 按 Escape（可选）：openContact 开头清场——搜索弹层是开关型（toggle），残留弹层会让
   *  「点击入口」变成关闭，diff 永远为空。缺省跳过 */
  pressEscape?(): Promise<void>
  /** CDP char 事件逐字输入（调用方保证焦点已在目标输入框） */
  typeChar(ch: string): Promise<void>
  /** 协作式取消信号：入口检查一次，触发即抛 CancelledError */
  signal?: AbortSignal
  /** 可注入 sleep（测试） */
  sleep?(ms: number): Promise<void>
}

/** 搜索框尺寸下限（视口宽比例，替代原绝对 w>200）：搜索框是左栏主体宽度的输入条 */
const SEARCH_BOX_MIN_W_RATIO = 0.08
/** 搜索框 y 上限（视口比例）：点开的搜索浮层必在页面上部 */
const SEARCH_BOX_MAX_CY_RATIO = 0.4
/** INPUT 近似相等阈值（device px）：diff 判定「同一输入框」用（位置微动/宽度展开视为变化而非新增） */
const INPUT_SAME_POS_TOL = 12
const INPUT_SAME_SIZE_TOL = 48
/** 点搜索图标后等弹层（ms） */
const SEARCH_OPEN_DELAY = 1400
/** 输入姓名后等搜索结果异步渲染（ms）。真机修正（2026-08-13）：搜索结果项（公司名）bounds 异步渲染较慢，
 *  太早（<3000ms）项无 bounds 会误判"抓不到"；真机实测需 ~4000ms 稳定出现 */
const SEARCH_RESULT_DELAY = 4000
/** 点结果卡片后等进入对话（ms） */
const ENTER_CHAT_DELAY = 2000
/** 点击搜索框后等待聚焦（ms） */
const SEARCH_FOCUS_DELAY = 500
/** 逐字输入间隔（ms，拟人节奏；风控拦鼠标合成事件不拦键盘） */
const TYPE_INTERVAL_MS = 200

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

/** 两 INPUT bounds 是否「同一输入框」（位置/尺寸在容差内：微动或收缩态展开） */
function isSameInput(a: [number, number, number, number], b: [number, number, number, number]): boolean {
  return Math.abs(a[0] - b[0]) <= INPUT_SAME_POS_TOL && Math.abs(a[1] - b[1]) <= INPUT_SAME_POS_TOL
    && Math.abs(a[2] - b[2]) <= INPUT_SAME_SIZE_TOL && Math.abs(a[3] - b[3]) <= INPUT_SAME_SIZE_TOL
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
    //    「关闭」，diff 永远为空——先 Escape 关掉保证「点击=打开」语义
    if (this.deps.pressEscape) {
      await this.deps.pressEscape()
      await this.sleep(400)
    }

    // 1. 点搜索入口（锚点动态定位）。CDP 点击优先：真机 2026-08-24 实证 Win32 物理点击
    //    存在 DPI/页面缩放换算偏差（点了但页面无响应），CDP 点击稳定唤起弹层
    const snap0 = await this.deps.snapshot()
    const fieldsBefore = this.collectFieldLike(snap0)
    const entry = this.locateSearchEntry(snap0)
    if (entry.count !== 1) {
      throw new ChatSearchError(
        entry.count === 0
          ? '未找到顶栏搜索入口（无「批量」锚点且无左栏 svg）：页面布局可能已改版，请人工查看'
          : `顶栏搜索入口候选 ${entry.count} 个无法唯一定位（${this.lastEntryDiag}），请人工查看`,
      )
    }
    await this.clickBrowseFirst(entry.point!, viewportOf(snap0))
    await this.sleep(SEARCH_OPEN_DELAY)

    // 2. 定位搜索框：diff「点击后新增的输入条形态元素」（不挑标签——BOSS 已改版为
    //    contenteditable DIV，不再是 <input>；形态=左栏内、视口上部、宽而矮的条）；
    //    无新增时重试一次点击再 diff（弹层偶发不响应首击，浏览类动作重试安全）
    let box = this.findSearchField(await this.deps.snapshot(), fieldsBefore)
    if (!box.point && box.count === 0) {
      await this.clickBrowseFirst(entry.point!, viewportOf(snap0))
      await this.sleep(SEARCH_OPEN_DELAY)
      box = this.findSearchField(await this.deps.snapshot(), fieldsBefore)
    }
    if (!box.point) {
      throw new ChatSearchError(
        box.count === 0
          ? `点击搜索入口(入口@${Math.round(entry.point!.x)},${Math.round(entry.point!.y)})后未发现新增的搜索输入条：搜索弹层未打开或页面结构已变，请人工查看`
          : `点击搜索入口后新增 ${box.count} 个候选输入条，无法唯一定位，请人工查看`,
      )
    }
    await this.clickBrowseFirst(box.point, viewportOf(snap0))
    await this.sleep(SEARCH_FOCUS_DELAY)

    // 3. CDP 逐字输入姓名（风控拦鼠标合成事件，不拦键盘 char 事件）。
    //    输入后立即校验文字落地（真机 2026-08-24：搜索框聚焦失败时 typeChar 落空，
    //    带病继续会把聊天消息输进搜索框——姓名每个字符都必须出现在页面 strings）
    for (const ch of name) {
      if (this.deps.signal?.aborted) throw new CancelledError('已取消：输入搜索姓名时中止')
      await this.deps.typeChar(ch)
      await this.sleep(TYPE_INTERVAL_MS)
    }
    await this.sleep(SEARCH_RESULT_DELAY)
    const snapTyped = await this.deps.snapshot()
    const charsPresent = new Set(snapTyped.strings.filter((s) => typeof s === 'string' && s.trim().length === 1))
    const missingChars = [...new Set(name.split(''))].filter((ch) => !charsPresent.has(ch))
    if (missingChars.length > 0) {
      throw new ChatSearchError(
        `搜索姓名「${name}」未落地（页面未见输入字符 ${JSON.stringify(missingChars)}）：搜索框未聚焦或输入被拦，已停止，请人工查看`,
      )
    }

    // 4. 定位目标姓名的搜索结果项（「联系人」标签下方带的公司名唯一命中），CDP 点击优先
    //    （真机 2026-08-24：结果卡片点击与入口一样存在 Win32 DPI 偏差，点偏后误进错误会话）
    const { point: target, count: targetCount } = this.locateTargetResult(snapTyped)
    if (targetCount !== 1) {
      throw new ChatSearchError(
        targetCount === 0
          ? `搜索「${name}」后未在结果列表找到该姓名的可见项：搜索无结果或目标在列表深处，请人工查看`
          : `搜索「${name}」在结果列表有 ${targetCount} 个可见命中，无法唯一定位，请人工查看`,
      )
    }
    await this.clickBrowseFirst(target!, viewportOf(snapTyped))
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

  /** CDP 浏览类点击优先（入口/搜索框聚焦——真机实证 Win32 换算有 DPI 偏差），缺省回退 Win32 */
  private async clickBrowseFirst(point: ClickPoint, viewport: { width: number; height: number }): Promise<void> {
    if (this.deps.clickBrowse) return this.deps.clickBrowse(point)
    return this.deps.click(point, viewport)
  }

  /**
   * 搜索输入条定位（2026-08-24 重写）：diff「点击入口后新增的输入条形态元素」。
   * BOSS 已把搜索框从 <input> 改为 contenteditable DIV——不能按标签找，按形态：
   * 左栏内（cx<视口 68%）、视口上部（cy<45%）、宽条（w>视口 8%）且矮（h≤60）。
   * 新增多个时优先唯一 INPUT；无新增回退：现存 INPUT 中满足形态者唯一命中
   * （兼容旧版 <input> 页面）。
   */
  private findSearchField(
    snap: DomSnapshot,
    fieldsBefore: Array<[number, number, number, number]>,
  ): { point: ClickPoint | null; count: number } {
    const viewport = viewportOf(snap)
    const current = this.collectFieldLike(snap)
    const added = current.filter((b) => !fieldsBefore.some((o) => isSameInput(o, b)))
    if (added.length === 1) {
      const b = added[0]!
      return { point: { x: b[0]! + b[2]! / 2, y: b[1]! + b[3]! / 2 }, count: 1 }
    }
    if (added.length > 1) {
      // 弹层内多个新增（如输入条+按钮条）：真 INPUT 优先；无 INPUT 则不唯一 fail-loud
      const inputs = this.collectVisibleInputs(snap).filter((b) => isFieldShape(b, viewport) && added.some((a) => isSameInput(a, b)))
      if (inputs.length === 1) {
        const b = inputs[0]!
        return { point: { x: b[0]! + b[2]! / 2, y: b[1]! + b[3]! / 2 }, count: 1 }
      }
      return { point: null, count: added.length }
    }
    // 回退：现存唯一 INPUT 形态命中（旧版页面 <input> 常驻）
    const hits: ClickPoint[] = []
    for (const b of this.collectVisibleInputs(snap)) {
      if (!isFieldShape(b, viewport)) continue
      hits.push({ x: b[0]! + b[2]! / 2, y: b[1]! + b[3]! / 2 })
    }
    return { point: hits.length === 1 ? hits[0]! : null, count: hits.length }
  }

  /** 输入条形态（视口相对）：左栏内、上部、宽而矮——INPUT 与 contenteditable DIV 通吃 */
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
