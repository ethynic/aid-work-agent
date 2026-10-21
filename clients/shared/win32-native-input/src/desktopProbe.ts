/**
 * 锁屏/不可交互桌面探测（host-core legacy/desktopCheck.ts 原生路径的底层实现）。
 * 与原 PowerShell+Add-Type 单调用语义一致：OpenInputDesktop(0, false, GENERIC_ALL) 返回 0
 * 视为不可交互；成功打开则立即 CloseDesktop。
 */
import type { NativeApi } from './types.js'

export interface DesktopProbeResult {
  interactive: boolean
}

export function probeInputDesktop(api: NativeApi): DesktopProbeResult {
  const handle = api.openInputDesktop()
  if (handle === 0) return { interactive: false }
  api.closeDesktop(handle)
  return { interactive: true }
}
