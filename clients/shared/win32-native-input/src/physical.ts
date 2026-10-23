/**
 * BossPhysicalInput.cs 的 1:1 移植（一条守卫不删）。
 *
 * - 页面坐标对物理 HWND 矩形线性映射；不推断、不缓存 DPI 乘数。
 * - 每次操作用 per-thread PMv2 DPI 上下文包裹；进入失败、上下文丢失、恢复失败均
 *   直接拒绝输入，绝不静默回退到虚拟化坐标。
 * - MarkInputStarted 之后的失败信息带 [BOSS_INPUT_UNKNOWN] 前缀（效果未知会计）。
 */
import type { GuardDiagnostics, NativeApi, Point, Rect } from './types.js'

/** PER_MONITOR_AWARE_V2（DPI_AWARENESS_CONTEXT 的 -4 句柄值） */
const PER_MONITOR_AWARE_V2 = -4

function messagePrefix(inputStarted: boolean, message: string): string {
  return inputStarted ? `[BOSS_INPUT_UNKNOWN] ${message}` : message
}

/**
 * PMv2 线程上下文作用域（Scope 的移植）。
 * Node 侧所有 FFI 调用都在主线程执行，线程一致性检查照搬（防未来 worker 化误用）。
 */
export class DpiPhysicalScope {
  private readonly previous: number
  private readonly thread: number
  private disposed = false
  inputStarted = false

  private constructor(
    private readonly api: NativeApi,
    previous: number,
  ) {
    this.previous = previous
    this.thread = api.getCurrentThreadId()
  }

  /** Enter()：进入失败（旧上下文为 0）或进入后校验失败都直接抛错拒绝输入 */
  static enter(api: NativeApi): DpiPhysicalScope {
    const previous = api.setThreadDpiAwarenessContext(PER_MONITOR_AWARE_V2)
    if (previous === 0) {
      throw new Error('Cannot enter per-monitor-v2 DPI context; input refused')
    }
    const scope = new DpiPhysicalScope(api, previous)
    try {
      requirePhysicalContext(api)
      return scope
    } catch (error) {
      // 进入后校验失败：尽力恢复上下文，保留原始失败
      try { api.setThreadDpiAwarenessContext(previous) } catch { /* 恢复失败不覆盖原始错误 */ }
      throw error
    }
  }

  /** Dispose()：线程变化 / 恢复失败都抛错；inputStarted 后带 BOSS_INPUT_UNKNOWN 前缀 */
  dispose(): void {
    if (this.disposed) return
    const fail = (message: string): never => {
      throw new Error(messagePrefix(this.inputStarted, message))
    }
    if (this.thread !== this.api.getCurrentThreadId()) {
      fail('DPI context thread changed; input must stop')
    }
    if (
      this.api.setThreadDpiAwarenessContext(this.previous) === 0 ||
      !this.api.areDpiAwarenessContextsEqual(this.api.getThreadDpiAwarenessContext(), this.previous)
    ) {
      fail('Cannot restore DPI context')
    }
    this.disposed = true
  }

  /** MarkInputStarted()：物理事件发出前一刻调用，划定“效果未知”会计边界 */
  markInputStarted(): void {
    requirePhysicalContext(this.api)
    if (this.thread !== this.api.getCurrentThreadId()) {
      throw new Error('DPI context thread changed; input refused')
    }
    this.inputStarted = true
  }
}

/** RequirePhysicalContext()：线程当前上下文不是 PMv2 即拒绝 */
export function requirePhysicalContext(api: NativeApi): void {
  if (!api.areDpiAwarenessContextsEqual(api.getThreadDpiAwarenessContext(), PER_MONITOR_AWARE_V2)) {
    throw new Error('Physical DPI context lost; input refused')
  }
}

/** ReadRect()：读物理 widget 矩形，失败或退化（宽/高<=0）即拒绝 */
export function readRect(api: NativeApi, hwnd: number): Rect {
  requirePhysicalContext(api)
  const rect = api.getWindowRect(hwnd)
  if (!rect || rect.right <= rect.left || rect.bottom <= rect.top) {
    throw new Error('Cannot read physical render-widget bounds; input refused')
  }
  return rect
}

/** ReadCursor()：读物理光标位置 */
export function readCursor(api: NativeApi): Point {
  requirePhysicalContext(api)
  const point = api.getPhysicalCursorPos()
  if (!point) throw new Error('Cannot read physical cursor; input refused')
  return point
}

/** PointOnWidget()：落点归属谓词（WindowFromPoint == 目标 widget） */
export function pointOnWidget(api: NativeApi, widget: number, x: number, y: number): boolean {
  requirePhysicalContext(api)
  return widget !== 0 && api.windowFromPoint(x, y) === widget
}

/** MoveCursor()：移动后回读校验，不一致即拒绝 */
export function moveCursor(api: NativeApi, x: number, y: number): void {
  requirePhysicalContext(api)
  if (!api.setCursorPos(x, y)) throw new Error('Cannot move cursor; input refused')
  const actual = readCursor(api)
  if (actual.x !== x || actual.y !== y) {
    throw new Error('Physical cursor differs from requested point; input refused')
  }
}

/**
 * RequireCursorOnWidget()：mouse DOWN / 每次 WHEEL 前立即调用（所有 settling 延迟之后）。
 * 两项谓词（光标坐标 / WindowFromPoint 归属）任一失配即拒绝。
 */
export function requireCursorOnWidget(api: NativeApi, widget: number, x: number, y: number): void {
  const actual = readCursor(api)
  const cursorMatch = actual.x === x && actual.y === y
  const ownershipMatch = pointOnWidget(api, widget, x, y)
  if (!cursorMatch || !ownershipMatch) {
    throw new Error(cursorAndOwnershipFailure(api, widget, x, y, actual, cursorMatch, ownershipMatch))
  }
}

/** 守卫失败取证行（只含原生几何/身份，与 BossWindowExperiment.ClickGuardFailure 同口径） */
export function cursorAndOwnershipFailure(
  api: NativeApi,
  widget: number,
  expectedX: number,
  expectedY: number,
  actual: Point,
  cursorMatch: boolean,
  ownershipMatch: boolean,
): string {
  const hit = api.windowFromPoint(expectedX, expectedY)
  const foreground = api.getForegroundWindow()
  return (
    `Cursor or render-widget ownership changed; input refused; expected_screen=(${expectedX},${expectedY}); ` +
    `actual_cursor=(${actual.x},${actual.y}); hit=${formatHwnd(hit)}; widget=${formatHwnd(widget)}; ` +
    `foreground=${formatHwnd(foreground)}; cursor_match=${cursorMatch ? 1 : 0}; ownership_match=${ownershipMatch ? 1 : 0}`
  )
}

/** 落点遮挡守卫的取证（不读标题/内容） */
export function occlusionDiagnostics(api: NativeApi, widget: number, x: number, y: number): GuardDiagnostics {
  return {
    cursorMatch: false,
    ownershipMatch: false,
    expectedX: x,
    expectedY: y,
    widget,
    hit: api.windowFromPoint(x, y),
    foreground: api.getForegroundWindow(),
  }
}

export function formatHwnd(hwnd: number): string {
  return `0x${(hwnd >>> 0).toString(16)}`
}
