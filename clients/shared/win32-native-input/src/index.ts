/**
 * @aid/win32-native-input：Win32 物理输入的进程内 FFI 能力（共享层）。
 *
 * 首个接入方：clients/boss-resume-assistant（WinMouseClicker 原生后端，无PowerShell输入回退）。
 * 普通输入语义：docs/design/recruiting/boss-resume-assistant-native-cdp-design.md §10.11。
 */
export type {
  GuardDiagnostics,
  ContentViewport,
  LogSink,
  NativeApi,
  PhysicalInputRequest,
  PhysicalInputResult,
  Point,
  ProcessEntry,
  Rect,
} from './types.js'
export { NativeInputError, isNativeInputError } from './errors.js'
export type { NativeInputFailureKind } from './errors.js'
export { DpiPhysicalScope, moveCursor, pointOnWidget, readCursor, readRect, requireCursorOnWidget, requirePhysicalContext } from './physical.js'
export {
  belongsTo,
  boundFind,
  boundForeground,
  findRenderWidget,
  findWindowByTitle,
  requireBoundForeground,
} from './windows.js'
export { findBrowserPidByDebugPort } from './processes.js'
export { probeInputDesktop } from './desktopProbe.js'
export { performPhysicalInput, psInt } from './sequence.js'
export type { SequenceContext } from './sequence.js'
export { SegmentTimer } from './timing.js'
export type { InputPhase } from './timing.js'
export { createKoffiApi } from './bindings/koffi.js'
