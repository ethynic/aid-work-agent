/**
 * 沟通页发消息执行器（设计文档 §10.6，CLI send-to / send-current 子命令的发消息段）。
 *
 * 沟通页当前会话底部有「发送」按钮（右侧 cx>850 视口内唯一），其左上是聊天输入框。
 * 流程：定位发送按钮 → 由发送按钮 center + 固定偏移定位输入框激活点 → Win32 点击聚焦 →
 * CDP char 逐字输入消息 →（dry-run 只输入不发送 / 真发送点「发送」按钮）。
 *
 * 真机校准（2026-08-13，窗口 1249x1277）：
 * - 发送按钮：DOMSnapshot 文本「发送」，cx>850（LIST_MAX_X）视口内唯一命中；真机 center (1146,1233)
 * - 输入框激活点 = 发送按钮 center + ACTIVATE_OFFSET；Win32 点击聚焦；真机 (1016,1195)
 * - 逐字输入：CDP dispatchKey(type=char)，每字 ~200ms；风控拦鼠标合成事件不拦键盘
 * - 聊天输入框 value 进了 DOMSnapshot strings（dry-run 可校验输入内容）
 * - 发送校验（TODO 真机验证）：点发送后输入框应清空（strings 不再含消息）；未清空=UNKNOWN 不重试
 *
 * 安全设计（fail-loud）：发送按钮 0/多个不盲发；发送后校验不过不重试，请人工查看。
 * 所有写动作（点击激活点、点发送）走 Win32（deps.click）；逐字输入走 CDP（typeChar）。
 */
import {
  type DomSnapshot,
  type ClickPoint,
  findNodesByString,
  accumulateOwnerOffset,
  boundsCenter,
} from './domSnapshot.js'
import { viewportOf } from './FilterSetter.js'
import { LIST_MAX_X } from './ResumeConsentExecutor.js'
import { CancelledError } from '../operations/types.js'

export class ChatSendError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'ChatSendError'
  }
}

export interface ChatSendDeps {
  /** 采集 fresh DOMSnapshot（每次定位前重新采集，禁止复用旧坐标） */
  snapshot(): Promise<DomSnapshot>
  /** Win32 真实鼠标点击（viewport 为页面截图尺寸，device px） */
  click(point: ClickPoint, viewport: { width: number; height: number }): Promise<void>
  /** CDP char 事件逐字输入（调用方保证焦点已在目标输入框） */
  typeChar(ch: string): Promise<void>
  /** 协作式取消信号：入口检查一次，触发即抛 CancelledError */
  signal?: AbortSignal
  /** 可注入 sleep（测试） */
  sleep?(ms: number): Promise<void>
}

/** 发送按钮文案（沟通页底部） */
const SEND_TEXT = '发送'
/** 发送按钮 center → 输入框激活点的偏移（device px，真机校准 2026-08-13：发送按钮 (1146,1233) → 激活点 (1016,1195)） */
const ACTIVATE_OFFSET = { dx: -130, dy: -38 }
/** 点击激活点后等待输入框聚焦（ms） */
const FOCUS_DELAY = 500
/** 逐字输入后等待输入落地（ms） */
const TYPE_SETTLE_DELAY = 600
/** 点发送后等待发送完成（ms） */
const SEND_DELAY = 1500
/** 逐字输入间隔（ms，拟人节奏；风控拦鼠标合成事件不拦键盘） */
const TYPE_INTERVAL_MS = 200

/**
 * 沟通页底部发送按钮（cx > LIST_MAX_X 视口内唯一「发送」文本）。
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
        if (x <= LIST_MAX_X || x > viewport.width || y < 0 || y > viewport.height) continue
        hits.push({ x, y })
      }
    })
  })
  return { point: hits.length === 1 ? hits[0]! : null, count: hits.length }
}

export class ChatSendExecutor {
  private readonly sleep: (ms: number) => Promise<void>

  constructor(private readonly deps: ChatSendDeps) {
    this.sleep = deps.sleep ?? ((ms) => new Promise((r) => setTimeout(r, ms)))
  }

  /**
   * 在当前会话输入框逐字输入消息；dryRun=true 只输入不点发送，dryRun=false 点发送。
   * 返回 { sent }：dry-run 恒 false，真发送成功 true。
   * 任何歧义（发送按钮 0/多个、发送后未清空）抛 ChatSendError（fail-loud，绝不盲发）。
   */
  async sendMessage(opts: { message: string; dryRun?: boolean }): Promise<{ sent: boolean }> {
    if (this.deps.signal?.aborted) throw new CancelledError()
    const message = opts.message
    const dryRun = opts.dryRun ?? false
    if (!message) throw new ChatSendError('消息内容不能为空')

    // 1. 定位发送按钮（cx>850 视口内唯一）；由它推导输入框激活点
    const snap = await this.deps.snapshot()
    const { point: sendBtn, count } = locateSendButton(snap)
    if (!sendBtn) {
      throw new ChatSendError(
        count === 0
          ? '未找到发送按钮（右侧面板 cx>850 视口内 0 个「发送」）：请先在沟通页打开一个会话'
          : `右侧面板找到 ${count} 个「发送」按钮，无法确定目标，已停止，请人工查看`,
      )
    }

    // 2. 点击输入框激活点（发送按钮 center + 偏移，真机校准），聚焦聊天输入框
    const activate: ClickPoint = { x: sendBtn.x + ACTIVATE_OFFSET.dx, y: sendBtn.y + ACTIVATE_OFFSET.dy }
    await this.deps.click(activate, viewportOf(snap))
    await this.sleep(FOCUS_DELAY)

    // 3. CDP 逐字输入消息（风控拦鼠标合成事件，不拦键盘 char 事件）
    for (const ch of message) {
      if (this.deps.signal?.aborted) throw new CancelledError('已取消：输入消息时中止')
      await this.deps.typeChar(ch)
      await this.sleep(TYPE_INTERVAL_MS)
    }
    await this.sleep(TYPE_SETTLE_DELAY)

    // 4. dry-run：校验输入落地即结束，不点发送
    if (dryRun) {
      const dry = await this.deps.snapshot()
      if (!dry.strings.some((s) => s.includes(message))) {
        throw new ChatSendError('dry-run 输入校验：聊天输入框 value 未包含输入内容，输入未落地，请人工查看')
      }
      return { sent: false }
    }

    // 5. 真发送：重新定位发送按钮（fresh snapshot，禁止复用旧坐标）后点发送（Win32 写动作）
    const beforeSend = await this.deps.snapshot()
    const { point: sendBtn2 } = locateSendButton(beforeSend)
    if (!sendBtn2) {
      throw new ChatSendError('输入消息后发送按钮消失（页面可能切换/弹层遮挡），已停止，请人工查看（消息未发送）')
    }
    await this.deps.click(sendBtn2, viewportOf(beforeSend))
    await this.sleep(SEND_DELAY)

    // TODO 真机验证：发送后输入框应清空（strings 不再含消息）。当前以「未清空」判定发送结果未知。
    // 注意：发送成功后消息会作为聊天气泡出现在历史里（也在 strings），本 naive 校验可能误判；
    // 真机需确认发送后输入框 value 条目消失的时机/可区分性，必要时改为查消息气泡出现等更稳的信号。
    const after = await this.deps.snapshot()
    if (after.strings.some((s) => s.includes(message))) {
      throw new ChatSendError(
        '点击「发送」后输入框未清空（消息内容仍在 strings），无法确认消息是否成功发出，请人工查看页面（系统不会自动重试）',
      )
    }
    return { sent: true }
  }
}
