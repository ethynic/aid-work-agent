/** Prepare a message in the uniquely scoped editor, then submit to the same conversation.
 * Success requires the editor to clear and a new exact outgoing message from ChatReadExecutor.
 * A final submission with unknown outcome is never automatically repeated.
 */
import {
  type DomSnapshot,
  type ClickPoint,
  findNodesByString,
  accumulateOwnerOffset,
  boundsCenter,
  indexedValues, isDescendantOf,
} from './domSnapshot.js'
import { viewportOf } from './FilterSetter.js'
import { CancelledError } from '../operations/types.js'
import { ChatReadExecutor, headerContactOf } from './ChatReadExecutor.js'
import { BusinessOperationState } from './BusinessOperationState.js'

export class ChatSendError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'ChatSendError'
  }
}

export interface ChatSendDeps {
  state?: BusinessOperationState
  /** 采集 fresh DOMSnapshot（每次定位前重新采集，禁止复用旧坐标） */
  snapshot(): Promise<DomSnapshot>
  /** Win32 真实鼠标点击（viewport 为页面截图尺寸，device px） */
  click(point: ClickPoint, viewport: { width: number; height: number }): Promise<void>
  /** Win32 原子「真实鼠标点击聚焦 + 真实键盘逐字输入」（一次调用完成，防焦点被抢） */
  clickAndType(point: ClickPoint, viewport: { width: number; height: number }, text: string): Promise<void>
  /** 协作式取消信号：入口检查一次，触发即抛 CancelledError */
  signal?: AbortSignal
  /** 可注入 sleep（测试） */
  sleep?(ms: number): Promise<void>
}

/** 发送按钮文案（沟通页底部） */
const SEND_TEXT = '发送'
/** 发送按钮 center → 输入框激活点的偏移（device px，真机校准 2026-08-13：发送按钮 (1146,1233) → 激活点 (1016,1195)） */
const ACTIVATE_OFFSET = { dx: -130, dy: -38 }
/** 逐字输入后等待输入落地（ms） */
const TYPE_SETTLE_DELAY = 600
/** 点发送后等待发送完成（ms） */
const SEND_DELAY = 1500

/**
 * 沟通页底部发送按钮（cx > 视口宽一半的右半区内唯一「发送」文本）。
 * 返回命中数与（唯一时的）屏幕坐标；0 或多个时 point=null（由调用方区分报错文案）。
 * 同一文案在 strings 表可能有多个下标（见 §17 坑：同文案多下标），故遍历全部下标。
 */
export function locateSendButton(snap: DomSnapshot): { point: ClickPoint | null; count: number } {
  const viewport = viewportOf(snap)
  const hits: ClickPoint[] = []
  snap.strings.forEach((s, stringIndex) => {
    if (s.trim() !== SEND_TEXT) return
    snap.documents.forEach((document, documentIndex) => {
      let offset: { x: number; y: number }
      try {
        offset = accumulateOwnerOffset(snap, documentIndex)
      } catch {
        return // 隐藏 iframe owner 无可见 bounds（后台标签页），跳过
      }
      for (const { bounds } of findNodesByString(document, stringIndex)) {
        if (bounds[2] <= 0 || bounds[3] <= 0) continue
        const c = boundsCenter(bounds)
        const x = offset.x + c.x - (document.scrollOffsetX ?? 0)
        const y = offset.y + c.y - (document.scrollOffsetY ?? 0)
        if (x <= viewport.width / 2 || x > viewport.width || y < 0 || y > viewport.height) continue
        hits.push({ x, y })
      }
    })
  })
  return { point: hits.length === 1 ? hits[0]! : null, count: hits.length }
}

export class ChatSendExecutor {
  private readonly sleep: (ms: number) => Promise<void>
  readonly state = new BusinessOperationState()
  targetContact = ''

  constructor(private readonly deps: ChatSendDeps) {
    this.state = deps.state ?? this.state
    this.sleep = deps.sleep ?? ((ms) => new Promise((r) => setTimeout(r, ms)))
  }

  /**
   * 在当前会话输入框逐字输入消息；dryRun=true 只输入不点发送，dryRun=false 点发送。
   * 返回 { sent }：dry-run 恒 false，真发送成功 true。
   * 任何歧义（发送按钮 0/多个、发送后未清空）抛 ChatSendError（fail-loud，绝不盲发）。
   */
  async sendMessage(opts: { message: string; dryRun?: boolean; contact?: string; expectedContactRef?: string }): Promise<{ sent: boolean }> {
    if (this.deps.signal?.aborted) throw new CancelledError()
    const message = opts.message
    const dryRun = opts.dryRun ?? false
    if (!message) throw new ChatSendError('消息内容不能为空')

    // 1. 定位发送按钮（视口右半唯一）；由它推导输入框激活点
    const snap = await this.deps.snapshot()
    this.targetContact = headerContactOf(snap)
    this.state.bindContact(snap, opts.expectedContactRef)
    if (!this.targetContact || opts.contact && this.targetContact !== opts.contact.trim()) {
      throw new ChatSendError('当前会话身份不可确认或与目标不符，尚未输入或发送消息')
    }
    const { point: sendBtn, count } = locateSendButton(snap)
    if (!sendBtn) {
      throw new ChatSendError(
        count === 0
          ? '未找到发送按钮（视口右半区 0 个「发送」文本）：请先在沟通页打开一个会话'
          : `右侧面板找到 ${count} 个「发送」按钮，无法确定目标，已停止，请人工查看`,
      )
    }

    // Only the editor subtree proves a draft, never unrelated historical strings or character coverage.
    let beforeSend = snap
    for (;;) {
      this.verifyContact(beforeSend)
      const editor = editorText(beforeSend)
      if (editor === null) throw new ChatSendError('无法可靠定位唯一聊天编辑区，尚未发送消息')
      if (editor === message) break // An earlier preparation already entered this exact draft.
      if (editor !== '') throw new ChatSendError('聊天编辑区已有不同内容，未追加或发送消息')
      const button = locateSendButton(beforeSend).point
      if (!button) throw new ChatSendError('准备输入时发送按钮不唯一，尚未发送消息')
      if (this.deps.signal?.aborted) throw new CancelledError('已取消：输入消息时中止')
      const activate = { x: button.x + ACTIVATE_OFFSET.dx, y: button.y + ACTIVATE_OFFSET.dy }
      await this.state.input(() => this.deps.clickAndType(activate, viewportOf(beforeSend), message))
      await this.sleep(TYPE_SETTLE_DELAY)
      beforeSend = await this.deps.snapshot()
      this.verifyContact(beforeSend)
      const actual = editorText(beforeSend)
      if (actual === message) break
      if (actual === '' && this.state.retry()) {
        continue // readable empty editor: first typing did not land; fresh coordinates only
      }
      this.state.unconfirmed('编辑区内容无法确认完整落地，未点击发送')
      throw new ChatSendError('聊天编辑区输入内容未完整落地，尚未发送消息')
    }
    if (dryRun) {
      this.state.execution.stage = 'postcheck'
      this.state.execution.verification = { status: 'met', evidence: ['同一会话唯一编辑区文本与消息完整相等；未点击发送'] }
      return { sent: false }
    }

    const beforeMessages = await new ChatReadExecutor({ snapshot: async () => beforeSend }).read({ contact: this.targetContact })
    const matchesBefore = beforeMessages.messages.filter(m => m.sender === 'me' && m.text === message).length
    const { point: sendBtn2 } = locateSendButton(beforeSend)
    if (!sendBtn2) {
      throw new ChatSendError('输入消息后发送按钮消失（页面可能切换/弹层遮挡），已停止，请人工查看（消息未发送）')
    }
    if (this.deps.signal?.aborted) throw new CancelledError('已取消：发送前中止')
    await this.state.input(() => this.deps.click(sendBtn2, viewportOf(beforeSend)), true)
    await this.sleep(SEND_DELAY)

    const after = await this.deps.snapshot()
    this.verifyContact(after)
    const afterMessages = await new ChatReadExecutor({ snapshot: async () => after }).read({ contact: this.targetContact })
    const matchesAfter = afterMessages.messages.filter(m => m.sender === 'me' && m.text === message).length
    if (editorText(after) !== '' || matchesAfter <= matchesBefore) {
      this.state.unconfirmed('同一会话未同时观察到编辑区清空与新增同文我方消息，结果未知，不重复发送')
      throw new ChatSendError('无法确认消息发送结果：缺少同会话编辑区清空及新增出站消息的联合证据')
    }
    this.state.confirmed({ contact: this.targetContact, sent: true }, '同一会话编辑区清空且我方同文消息数增加')
    return { sent: true }
  }

  private verifyContact(snap: DomSnapshot): void {
    this.state.bindContact(snap)
    if (headerContactOf(snap) !== this.targetContact) {
      this.state.unconfirmed('会话身份变化，不继续输入或发送')
      throw new ChatSendError('会话身份已变化，无法确认原目标，停止后续发送')
    }
  }
}

/** Read fragmented contenteditable text in DOM order, scoped to one visible chat editor. */
export function editorText(snap: DomSnapshot): string | null {
  const viewport = viewportOf(snap)
  const editors: string[] = []
  snap.documents.forEach((document, di) => {
    if (!document.nodes.attributes || !document.nodes.parentIndex) return
    let offset: ClickPoint
    try { offset = accumulateOwnerOffset(snap, di) } catch { return }
    for (const [ni, attrs] of indexedValues(document.nodes.attributes, 'attributes')) {
      let editable = false
      for (let i = 0; i + 1 < attrs.length; i += 2) {
        if (snap.strings[attrs[i]!] === 'contenteditable' && ['true', ''].includes(snap.strings[attrs[i + 1]!] ?? 'false')) editable = true
      }
      if (!editable) continue
      const bounds = document.layout.bounds[document.layout.nodeIndex.indexOf(ni)]
      if (!bounds || bounds[2]! <= 0 || bounds[3]! <= 0) continue
      const x = offset.x + bounds[0]! - (document.scrollOffsetX ?? 0)
      const y = offset.y + bounds[1]! - (document.scrollOffsetY ?? 0)
      if (x + bounds[2]! / 2 < viewport.width / 2 || x >= viewport.width || y < viewport.height / 2 || y >= viewport.height) continue
      const text = indexedValues(document.nodes.nodeValue, 'nodeValue').filter(([child, si]) => si >= 0 && isDescendantOf(document, child, ni) === true)
        .sort(([a], [b]) => a - b).map(([, si]) => snap.strings[si] ?? '').join('')
      editors.push(text)
    }
  })
  return editors.length === 1 ? editors[0]! : null
}
