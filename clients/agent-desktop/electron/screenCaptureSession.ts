/**
 * 采集窗口的会话登记（设计 6.1）：同一时间最多一个隐藏采集窗口。
 * open 先关旧窗再建新窗（同一会话重复 open 也重建）；close 只销毁属于该会话的窗口；
 * 渲染进程崩溃与退出清理共用同一销毁路径。窗口操作由注入的句柄完成：
 * 主进程传 Electron 实现，测试传假句柄。Host→Main 帧见 runtimeSupervisor 的 RuntimeScreenHostFrame。
 */

export interface ScreenCaptureOps<W> {
  /** 同步创建并开始加载采集页；加载失败由实现内部经 windowCrashed 通道结算 */
  create(sessionId: string, stunUrls: string[]): W
  destroy(window: W): void
  /** 向采集页转发 Host 的消息 */
  forward(window: W, message: unknown): void
  /** 窗口存在状态变化（托盘提示与菜单标题） */
  onActiveChange(active: boolean): void
}

export class ScreenCaptureSession<W> {
  private window: W | null = null
  private sessionId: string | null = null

  constructor(private readonly ops: ScreenCaptureOps<W>) {}

  /** 当前采集窗口；调用方用引用相等核对发送方 */
  current(): W | null {
    return this.window
  }

  /** Host→Main action=open：先关旧窗再建新窗 */
  open(sessionId: string, stunUrls: string[]): void {
    this.closeCurrent()
    this.window = this.ops.create(sessionId, stunUrls)
    this.sessionId = sessionId
    this.ops.onActiveChange(true)
  }

  /** Host→Main action=send：会话不匹配时丢弃，不让旧会话的迟到消息进新窗口 */
  send(sessionId: string, message: unknown): void {
    if (this.sessionId !== sessionId) return
    const window = this.window
    if (window) this.ops.forward(window, message)
  }

  /** Host→Main action=close：对不存在或不匹配的会话编号没有效果 */
  close(sessionId: string): void {
    if (this.sessionId !== sessionId) return
    this.closeCurrent()
  }

  /**
   * 采集页消息（发送方已由调用方核对是采集窗口本身）→ Main→Host 帧（契约 2.25 平铺形状）。
   * event/version/session_id 由登记处填写，页面字段不能覆盖；没有会话时为 null
   */
  pageFrame(message: Record<string, unknown>): Record<string, unknown> | null {
    if (!this.sessionId) return null
    const { event: _event, version: _version, session_id: _sessionId, ...fields } = message
    return { event: 'runtime_screen', version: 1, session_id: this.sessionId, ...fields }
  }

  /** 渲染进程异常退出：window-crashed 帧 + 销毁窗口（Host 按阶段结算会话）。没有会话时为 null */
  windowCrashed(): Record<string, unknown> | null {
    const sessionId = this.sessionId
    this.closeCurrent()
    return sessionId ? { event: 'runtime_screen', version: 1, session_id: sessionId, kind: 'window-crashed' } : null
  }

  /** 退出清理（will-quit、Host 断开）：直接销毁，不等网络 */
  destroy(): void {
    this.closeCurrent()
  }

  private closeCurrent(): void {
    const window = this.window
    if (!window) return
    this.window = null
    this.sessionId = null
    this.ops.destroy(window)
    this.ops.onActiveChange(false)
  }
}
