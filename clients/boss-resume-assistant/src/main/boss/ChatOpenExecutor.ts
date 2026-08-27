/**
 * 沟通页「打开指定联系人的会话」执行器（2026-08-27，CLI open-chat / MCP boss_open_chat）。
 *
 * read-chat 只能读当前已打开的会话；本执行器负责**切换到指定联系人**，三条路径：
 * 1) already：fresh snapshot 读头部联系人（headerContactOf 共享函数）已 === 目标 → 零点击返回；
 * 2) search（用户定调优先）：组装 ChatSearchExecutor 跑搜索找人——列表滚动在 BOSS 非标准滚动下
 *    历史上不可靠（见 ChatSearchExecutor 头注释），搜索是验证过的主链路。openContact 成功后
 *    再读一次头部做**身份校验**（2026-08-27 CR 补）：openContact 只证明「进入了某个对话」
 *    （发送按钮唯一+浮层关闭），不证明就是目标联系人——头部 ≠ 目标（错开卡片/结构漂移）时
 *    视同搜索失败，进列表兜底，绝不静默报成功；
 * 3) list 兜底：搜索失败（ChatSearchError / 头部身份不符）不立即报错，先 pressEscape 清场
 *    （搜索失败时浮层常残留，会话列表被搜索浮层替换或遮挡——直接按列表坐标点击会点在浮层上，
 *    可能误开别人的会话），再重新 snapshot 在 doc[0] 会话列表列（x∈[188,548)）找目标姓名文本
 *    （trim 全等）：唯一命中 → Win32 点击（姓名中心 x+60，2026-08-27 真机探针验证点击行可切换
 *    头部）；视口外 → CDP mouseWheel 滚动到可见再点；点击后轮询头部切换校验。
 *
 * 失败语义（fail-loud）：列表 0 命中（附可用联系人名单 + 搜索失败原因）、同名多命中（列坐标）、
 * 滚动不生效、头部未切换——全部抛 ChatOpenError，绝不盲点。两路都失败时汇总两路错误。
 */
import {
  type DomSnapshot,
  type ClickPoint,
  classOf,
  indexedValues,
} from './domSnapshot.js'
import { viewportOf } from './FilterSetter.js'
import { ChatSearchExecutor, ChatSearchError } from './ChatSearchExecutor.js'
import { headerContactOf, conversationListNamesOf } from './ChatReadExecutor.js'
import { CancelledError } from '../operations/types.js'

export class ChatOpenError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'ChatOpenError'
  }
}

export interface ChatOpenDeps {
  /** 采集 fresh DOMSnapshot（每次定位前重新采集，禁止复用旧坐标） */
  snapshot(): Promise<DomSnapshot>
  /** Win32 真实鼠标点击（viewport 为页面截图尺寸，device px）——搜索结果/列表项点击走此通道 */
  click(point: ClickPoint, viewport: { width: number; height: number }): Promise<void>
  /** Win32 原子「真实鼠标点击聚焦 + 真实键盘逐字输入」（搜索路径输入姓名用） */
  clickAndType(point: ClickPoint, viewport: { width: number; height: number }, text: string): Promise<void>
  /** CDP mouseWheel 滚动（可选，列表兜底路径滚动用；浏览类滚动不占用真实鼠标） */
  mouseWheel?(x: number, y: number, deltaY: number): Promise<void>
  /** 按 Escape（可选，双用途：透传给 ChatSearchExecutor 清场残留搜索弹层；列表兜底前清场恢复会话列表） */
  pressEscape?(): Promise<void>
  /** 清空当前聚焦输入框（可选，透传给 ChatSearchExecutor 输入未落地重试用） */
  clearInput?(): Promise<void>
  /** 协作式取消信号 */
  signal?: AbortSignal
  /** 可注入 sleep（测试） */
  sleep?(ms: number): Promise<void>
}

export interface ChatOpenResult {
  /** 实际打开的联系人姓名（= 入参 contact，trim 后） */
  contact: string
  /** 打开路径：already=已在目标会话（零点击）/ search=搜索找人 / list=点击列表项兜底 */
  via: 'search' | 'list' | 'already'
}

/** 会话列表列的 x 带（真机列表 x∈[188,547] 姓名 x≈264，右界 548=聊天面板起点；
 *  x 带同时排除右侧消息区/头部的同名文本） */
const LIST_NAME_X_RANGE: readonly [number, number] = [188, 548]
/** 列表项点击点：姓名中心 x 右移 60px 落到行中部（2026-08-27 真机探针验证有效） */
const LIST_CLICK_DX = 60
/** 视口外目标的最大滚动轮数 */
const SCROLL_ROUNDS_MAX = 15
/** 每轮滚动后等列表重排（ms） */
const SCROLL_SETTLE = 600
/** 每轮滚动步长（device px，向下为正——CDP mouseWheel deltaY 语义） */
const SCROLL_STEP = 600
/** 列表兜底前 Escape 清场后等搜索浮层关闭/列表恢复（ms，与 ChatSearchExecutor 清场延时一致） */
const LAYER_CLOSE_SETTLE = 400
/** 点击列表项后头部切换轮询：间隔/轮数（真机头部切换 <1s，8 轮余量充足） */
const HEADER_POLL_INTERVAL = 500
const HEADER_POLL_ROUNDS = 8

export class ChatOpenExecutor {
  private readonly sleep: (ms: number) => Promise<void>

  constructor(private readonly deps: ChatOpenDeps) {
    this.sleep = deps.sleep ?? ((ms) => new Promise((r) => setTimeout(r, ms)))
  }

  async open(opts: { contact: string }): Promise<ChatOpenResult> {
    if (this.deps.signal?.aborted) throw new CancelledError()
    const contact = opts.contact.trim()
    if (!contact) throw new ChatOpenError('联系人姓名不能为空')

    // 1. already 快速路径：头部已是目标联系人 → 零点击返回（幂等，重复 open 无副作用）
    const snap0 = await this.deps.snapshot()
    if (headerContactOf(snap0) === contact) {
      return { contact, via: 'already' }
    }

    // 2. 搜索路径（优先，用户定调）：失败（ChatSearchError / 头部身份不符）不报错，进列表兜底；
    //    CancelledError / CDP 断连 / Win32 失败等非搜索错误直接上抛（兜底也依赖同一通道，重试无意义）
    let searchError: string | null = null
    try {
      const searcher = new ChatSearchExecutor({
        snapshot: this.deps.snapshot,
        click: this.deps.click,
        clickAndType: this.deps.clickAndType,
        clearInput: this.deps.clearInput,
        pressEscape: this.deps.pressEscape,
        signal: this.deps.signal,
        sleep: this.sleep,
      })
      await searcher.openContact({ name: contact })
      // 身份校验（2026-08-27 CR 补）：openContact 只证明「进入了某个对话」（发送按钮唯一+浮层关闭），
      // 不证明进入的就是目标联系人——结果卡片按公司名点击，错卡会进错会话。fresh snapshot 读头部：
      // === 目标 → via=search；≠ 目标（含头部未读到 ''）→ 视同搜索失败进列表兜底，绝不静默报成功
      const header = headerContactOf(await this.deps.snapshot())
      if (header === contact) {
        return { contact, via: 'search' }
      }
      searchError = `搜索进入的会话头部为「${header || '(未读到)'}」≠ 目标「${contact}」`
    } catch (err) {
      if (!(err instanceof ChatSearchError)) throw err // CancelledError / CDP 断连 / Win32 失败等原样上抛
      searchError = err.message
    }
    const note = `（搜索路径失败：${searchError}）`

    // 3. 列表兜底：先清场——搜索失败/错开时搜索浮层常残留，会话列表被浮层替换或遮挡，
    //    直接按列表坐标点击会点在浮层上（可能误开别人的会话）。pressEscape 关闭浮层恢复列表
    //    （弹层是开关型，已关时再按无害），再重新 fresh snapshot（禁止复用 snap0）
    if (this.deps.pressEscape) {
      await this.deps.pressEscape()
      await this.sleep(LAYER_CLOSE_SETTLE)
    }
    let snap = await this.deps.snapshot()
    let hit = this.locateListName(snap, contact)
    if (hit.count === 0) {
      const available = conversationListNamesOf(snap)
      const list = available.length > 0 ? available.join('、') : '（列表为空或不可读）'
      throw new ChatOpenError(`会话列表中不存在「${contact}」，可用联系人：${list}${note}。请确认姓名后重试`)
    }
    if (hit.count > 1) {
      const coords = hit.points.map((p) => `(${Math.round(p.x)},${Math.round(p.y)})`).join('、')
      throw new ChatOpenError(`会话列表中有 ${hit.count} 个「${contact}」姓名命中 [${coords}]，无法唯一定位，请人工查看${note}`)
    }

    // 视口外 → 滚动到可见（CDP mouseWheel 于列表容器中心；无滚动原语时 fail-loud）
    let target = hit.points[0]!
    let viewport = viewportOf(snap)
    let rounds = 0
    while (!this.inViewport(target, viewport)) {
      if (!this.deps.mouseWheel) {
        throw new ChatOpenError(`会话「${contact}」在列表视口外且本环境无滚动原语，无法自动定位，请人工滚动列表后重试${note}`)
      }
      if (rounds++ >= SCROLL_ROUNDS_MAX) {
        throw new ChatOpenError(`滚动 ${SCROLL_ROUNDS_MAX} 轮后「${contact}」仍未进入视口（列表可能未按预期滚动），请人工查看${note}`)
      }
      // 方向按目标 y：目标在视口下方 → 向下滚（deltaY 正）；上方 → 向上滚
      const deltaY = target.y > viewport.height ? SCROLL_STEP : -SCROLL_STEP
      const anchor = this.listScrollAnchor(snap)
      await this.deps.mouseWheel(anchor.x, anchor.y, deltaY)
      await this.sleep(SCROLL_SETTLE)
      if (this.deps.signal?.aborted) throw new CancelledError('已取消：滚动会话列表时中止')
      snap = await this.deps.snapshot()
      hit = this.locateListName(snap, contact)
      if (hit.count !== 1) {
        throw new ChatOpenError(`滚动后重定位「${contact}」失败（命中 ${hit.count} 个，列表可能已虚拟化或滚动异常），请人工查看${note}`)
      }
      target = hit.points[0]!
      viewport = viewportOf(snap)
    }

    // Win32 点击列表项（姓名中心 x+60 = 行中部；真实鼠标，2026-08-27 真机探针验证）
    if (this.deps.signal?.aborted) throw new CancelledError('已取消：点击会话列表项时中止')
    await this.deps.click({ x: target.x + LIST_CLICK_DX, y: target.y }, viewport)

    // 校验：轮询头部切换为 contact（点击已发出的「写后校验」，超时 fail-loud 请人工查看）
    for (let i = 0; i < HEADER_POLL_ROUNDS; i++) {
      await this.sleep(HEADER_POLL_INTERVAL)
      if (this.deps.signal?.aborted) throw new CancelledError('已取消：等待会话切换时中止')
      const snapAfter = await this.deps.snapshot()
      if (headerContactOf(snapAfter) === contact) {
        return { contact, via: 'list' }
      }
    }
    throw new ChatOpenError(
      `点击会话项后头部未切换为「${contact}」（轮询 ${HEADER_POLL_ROUNDS} 次）：点击可能落空或页面结构已变化，请人工查看${note}`,
    )
  }

  /** doc[0] 会话列表列内目标姓名的可见文本命中（trim 全等 + x∈[188,548) + 有 bounds），返回各命中中心 */
  private locateListName(snap: DomSnapshot, name: string): { points: ClickPoint[]; count: number } {
    const document = snap.documents[0]
    if (!document?.nodes?.nodeValue || !document.layout) return { points: [], count: 0 }
    const valueByNode = new Map(indexedValues(document.nodes.nodeValue, 'nodeValue'))
    const points: ClickPoint[] = []
    document.layout.nodeIndex.forEach((nodeIndex, layoutIndex) => {
      const stringIndex = valueByNode.get(nodeIndex)
      if (stringIndex === undefined) return
      const text = snap.strings[stringIndex]
      if (typeof text !== 'string' || text.trim() !== name) return
      const b = document.layout.bounds[layoutIndex]
      if (!b || b[2]! <= 0 || b[3]! <= 0) return
      const x = b[0]!
      if (x < LIST_NAME_X_RANGE[0] || x >= LIST_NAME_X_RANGE[1]) return
      points.push({ x: x + b[2]! / 2, y: b[1]! + b[3]! / 2 })
    })
    return { points, count: points.length }
  }

  /** 点是否在视口内（中心 y 落在 (0, height) 开区间；点击越界会被 Win32 层拒绝） */
  private inViewport(point: ClickPoint, viewport: { width: number; height: number }): boolean {
    return point.y > 0 && point.y < viewport.height
  }

  /**
   * 滚动锚点：.user-list 容器 bounds 中心（class 为实测稳定语义名，§10.9 真机验证），
   * 多个同 class 片段容器取面积最大者；容器定位失败回退列表列水平中心 x=368、视口半高
   * （与容器真机中心 (368,740) 同量级——列表列几何恒定，回退锚仍落在可滚区域）。
   */
  private listScrollAnchor(snap: DomSnapshot): ClickPoint {
    const document = snap.documents[0]
    if (document?.nodes?.attributes && document.layout) {
      let best: [number, number, number, number] | null = null
      document.layout.nodeIndex.forEach((nodeIndex, layoutIndex) => {
        const cls = classOf(snap, 0, nodeIndex)
        if (!cls || !cls.includes('user-list')) return
        const b = document.layout.bounds[layoutIndex]
        if (!b || b[2]! <= 0 || b[3]! <= 0) return
        if (best === null || b[2]! * b[3]! > best[2]! * best[3]!) best = [b[0]!, b[1]!, b[2]!, b[3]!]
      })
      if (best) {
        return { x: Math.round(best[0]! + best[2]! / 2), y: Math.round(best[1]! + best[3]! / 2) }
      }
    }
    const viewport = viewportOf(snap)
    return { x: Math.round((LIST_NAME_X_RANGE[0]! + LIST_NAME_X_RANGE[1]!) / 2), y: Math.round(viewport.height / 2) }
  }
}
