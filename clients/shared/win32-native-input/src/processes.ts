/**
 * 绑定模式的调试 Chrome 进程定位（win-click.ps1 Get-CimInstance WMI 反查的进程内替代）。
 *
 * 语义 1:1：chrome.exe 且命令行匹配 `--remote-debugging-port <port>`、非 `--type=` 子进程的
 * 浏览器主进程必须恰好一个；数量 != 1 抛 STALE_OBSERVATION（与 ps1 完全一致）。
 * 实现：Toolhelp32 枚举 + NtQueryInformationProcess/ReadProcessMemory 读 PEB 命令行，
 * 不再起任何子进程、不走 WMI。命令行读不到的进程与旧 WMI 的 null CommandLine 同语义：
 * 不匹配、跳过，不因权限/其他用户会话/枚举后退出的无关 chrome.exe 阻断本次输入；
 * 目标进程自身读不到时由 matched != 1 兜底拒绝（等价于 WMI 过滤后 0 命中）。
 */
import { NativeInputError } from './errors.js'
import type { NativeApi } from './types.js'

function stale(message: string): NativeInputError {
  return new NativeInputError(`STALE_OBSERVATION: ${message}`, 2, 'STALE_OBSERVATION')
}

/** 命中唯一浏览器主进程返回 pid；数量 != 1 抛 STALE_OBSERVATION（命令行读不到视为不匹配） */
export function findBrowserPidByDebugPort(api: NativeApi, port: number): number {
  const chrome = api.snapshotProcesses().filter((entry) => entry.exeName.toLowerCase() === 'chrome.exe')
  const matched: number[] = []
  for (const entry of chrome) {
    const commandLine = api.readProcessCommandLine(entry.pid)
    // 旧 WMI：CommandLine 为 null 的进程被 Where-Object 直接过滤，不报错
    if (commandLine === undefined) continue
    const portPattern = new RegExp(`(?:^|\\s)--remote-debugging-port(?:=|\\s+)${port}(?:\\s|$)`)
    if (!portPattern.test(commandLine)) continue
    if (/(?:^|\s)--type=/.test(commandLine)) continue
    matched.push(entry.pid)
  }
  if (matched.length !== 1) {
    throw stale(`debug Chrome process is ambiguous; input refused (matched=${matched.length})`)
  }
  return matched[0]!
}
