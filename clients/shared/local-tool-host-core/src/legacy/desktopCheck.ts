/**
 * 锁屏/不可交互桌面检测（MVP 版）。
 *
 * PowerShell Add-Type P/Invoke 调 user32!OpenInputDesktop：失败（返回 0）视为锁屏/不可交互桌面。
 * 触发点：doctor 必检 + 每次 invocation 开始前检一次。
 * 检测本身出错（PS 不可用等）按不可交互处理（写动作宁可拒绝不可误动）。
 */
import { execFile } from 'node:child_process'

const CHECK_SCRIPT = [
  // C# 源放 PS 单引号字符串内（双引号经 argv 转义后还原，不受 -Command 解析影响）
  '$def = \'[System.Runtime.InteropServices.DllImport("user32.dll")] public static extern System.IntPtr OpenInputDesktop(uint dwFlags, bool fInherit, uint dwDesiredAccess); [System.Runtime.InteropServices.DllImport("user32.dll")] public static extern bool CloseDesktop(System.IntPtr hDesktop);\'',
  'Add-Type -Namespace Win32 -Name InputDesktop -MemberDefinition $def',
  '$h = [Win32.InputDesktop]::OpenInputDesktop(0, $false, 0x10000000)',
  'if ($h -eq [System.IntPtr]::Zero) { [Console]::Out.Write("0") } else { [Win32.InputDesktop]::CloseDesktop($h) | Out-Null; [Console]::Out.Write("1") }',
].join('; ')

export interface DesktopCheckResult {
  interactive: boolean
  /** 检测失败原因（interactive=false 且非锁屏场景时给出） */
  error?: string
}

export function checkDesktopInteractive(): Promise<DesktopCheckResult> {
  if (process.platform !== 'win32') {
    // 非 Windows：无锁屏概念（Runtime 目标平台为 win32，开发/测试放行）
    return Promise.resolve({ interactive: true })
  }
  return new Promise((resolve) => {
    execFile(
      'powershell.exe',
      ['-NoProfile', '-NonInteractive', '-Command', CHECK_SCRIPT],
      { timeout: 15_000 },
      (err, stdout) => {
        if (err) {
          resolve({ interactive: false, error: `桌面检测失败: ${err.message}` })
          return
        }
        resolve({ interactive: stdout.trim() === '1' })
      },
    )
  })
}
