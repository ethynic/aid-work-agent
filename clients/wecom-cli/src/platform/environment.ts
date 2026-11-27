/**
 * 只读环境探测原语（doctor 只允许的检查项 + wecom_probe 能力矩阵的进程级部分）。
 *
 * 严格只读：只查询平台/会话/PowerShell/进程列表，绝不激活窗口、发送输入或改剪贴板。
 * 全部检查 best-effort：单项失败只落 false，不抛出（probe/doctor 都要快速返回结构化结果）。
 *
 * 测试可注入 Windows 桌面探测、平台、目录和系统命令，模拟非 win32、
 * 服务会话、WXWork.exe 进程存在性等场景。
 */
import { execFile } from 'node:child_process'
import { mkdirSync, writeFileSync, readFileSync, unlinkSync } from 'node:fs'
import { join } from 'node:path'

export interface ExecFileResult {
  stdout: string
}

export type ExecFileFn = (file: string, args: string[], timeoutMs?: number) => Promise<ExecFileResult>

/** 默认命令执行：只读命令，5s 超时，不弹窗 */
const defaultExecFile: ExecFileFn = (file, args, timeout = 5000) =>
  new Promise((resolve, reject) => {
    execFile(file, args, { timeout, windowsHide: true, encoding: 'utf8' }, (err, stdout) => {
      if (err) reject(err)
      else resolve({ stdout: String(stdout) })
    })
  })

export interface ProbeEnvironmentOptions {
  platform?: NodeJS.Platform
  desktopProbeFn?: () => Promise<WindowsDesktopStatus>
  localAppData?: string
  execFileFn?: ExecFileFn
  /** 每个检查步骤的回调（probe operation 用它发 progress） */
  onStep?: (step: string) => void
}

export interface EnvironmentProbe {
  platform_ok: boolean
  platform: string
  /** null = 非 win32 无法判定 */
  interactive_session: boolean | null
  /** SESSIONNAME 仅供旧诊断字段展示。 */
  session_name: string | null
  desktop_issue?: string
  powershell_available: boolean
  powershell_path: string | null
  wecom_running: boolean
  wecom_processes: number
}

type WindowsDesktopStatus = 'interactive' | 'session-inactive' | 'input-unavailable' | 'probe-failed'

const WINDOWS_DESKTOP_CHECK = String.raw`
$ErrorActionPreference = 'Stop'
Add-Type -Namespace WeCom -Name DesktopCheck -MemberDefinition @'
[System.Runtime.InteropServices.DllImport("wtsapi32.dll", ExactSpelling = true)]
private static extern bool WTSQuerySessionInformationW(IntPtr server, int session, int infoClass, out IntPtr value, out int size);
[System.Runtime.InteropServices.DllImport("wtsapi32.dll")]
private static extern void WTSFreeMemory(IntPtr value);
[System.Runtime.InteropServices.DllImport("user32.dll")]
private static extern IntPtr OpenInputDesktop(uint flags, bool inherit, uint access);
[System.Runtime.InteropServices.DllImport("user32.dll")]
private static extern bool CloseDesktop(IntPtr desktop);

public static string Probe(int session) {
    if (session == 0) return "session-inactive";
    IntPtr value;
    int size;
    const int WTSConnectState = 8;
    if (!WTSQuerySessionInformationW(IntPtr.Zero, session, WTSConnectState, out value, out size)) return "probe-failed";
    try {
        if (size < sizeof(int) || value == IntPtr.Zero) return "probe-failed";
        const int WTSActive = 0;
        if (System.Runtime.InteropServices.Marshal.ReadInt32(value) != WTSActive) return "session-inactive";
    } finally {
        WTSFreeMemory(value);
    }
    const uint GenericAll = 0x10000000;
    IntPtr desktop = OpenInputDesktop(0, false, GenericAll);
    if (desktop == IntPtr.Zero) return "input-unavailable";
    CloseDesktop(desktop);
    return "interactive";
}
'@
[Console]::Out.Write([WeCom.DesktopCheck]::Probe([Diagnostics.Process]::GetCurrentProcess().SessionId))
`

const WINDOWS_DESKTOP_ISSUES = {
  'session-inactive': 'Windows 会话未连接到交互桌面',
  'input-unavailable': 'Windows 输入桌面不可用，请解锁后重试',
  'probe-failed': '无法检测 Windows 交互桌面，请核对 PowerShell 运行条件',
} as const

async function probeWindowsDesktop(execFileFn: ExecFileFn): Promise<WindowsDesktopStatus> {
  try {
    const { stdout } = await execFileFn('powershell.exe', [
      '-NoProfile', '-NonInteractive', '-EncodedCommand',
      Buffer.from(WINDOWS_DESKTOP_CHECK, 'utf16le').toString('base64'),
    ], 2500)
    switch (stdout.trim()) {
      case 'interactive': return 'interactive'
      case 'session-inactive': return 'session-inactive'
      case 'input-unavailable': return 'input-unavailable'
      default: return 'probe-failed'
    }
  } catch {
    return 'probe-failed'
  }
}

/** tasklist CSV 输出中的 WXWork.exe 进程数（输出可能是 GBK，但进程名是 ASCII，按子串匹配安全） */
export function countWecomProcesses(tasklistCsv: string): number {
  return tasklistCsv.split(/\r?\n/).filter((line) => /"?WXWork\.exe"?/i.test(line)).length
}

/** artifact 根目录（%LOCALAPPDATA%\AidWorkAgent\wecom-cli\artifacts） */
export function artifactDir(localAppData: string | undefined = process.env.LOCALAPPDATA): string | null {
  if (!localAppData) return null
  return join(localAppData, 'AidWorkAgent', 'wecom-cli', 'artifacts')
}

/** artifact 目录可创建/可写检查（写入后立即删除探针文件，无业务副作用） */
export function checkArtifactDirWritable(dir: string): { ok: boolean; detail: string } {
  try {
    mkdirSync(dir, { recursive: true })
    const probe = join(dir, `.doctor-probe-${process.pid}`)
    writeFileSync(probe, 'ok', 'utf8')
    readFileSync(probe, 'utf8')
    unlinkSync(probe)
    return { ok: true, detail: dir }
  } catch (err) {
    return { ok: false, detail: `${dir}：${err instanceof Error ? err.message : String(err)}` }
  }
}

export async function probeEnvironment(opts: ProbeEnvironmentOptions = {}): Promise<EnvironmentProbe> {
  const platform = opts.platform ?? process.platform
  const execFileFn = opts.execFileFn ?? defaultExecFile
  const step = (s: string) => opts.onStep?.(s)
  const platformOk = platform === 'win32'

  // 交互会话（非 win32 无法判定，置 null）
  step('检查平台与交互桌面会话')
  const desktopCheck = platformOk ? (opts.desktopProbeFn?.() ?? probeWindowsDesktop(execFileFn)) : null

  // PowerShell 可用性（where.exe 只读查找）
  step('检查 PowerShell 可用性')
  let powershellAvailable = false
  let powershellPath: string | null = null
  if (platformOk) {
    try {
      const { stdout } = await execFileFn('where.exe', ['powershell.exe'])
      const first = stdout.split(/\r?\n/).map((l) => l.trim()).find(Boolean)
      if (first) {
        powershellAvailable = true
        powershellPath = first
      }
    } catch {
      powershellAvailable = false
    }
  }

  // WXWork.exe 进程存在性（tasklist 只读枚举；绝不代表登录态，仅进程层面）
  step('检查 WXWork.exe 进程')
  let wecomProcesses = 0
  if (platformOk) {
    try {
      const { stdout } = await execFileFn('tasklist', ['/FI', 'IMAGENAME eq WXWork.exe', '/FO', 'CSV', '/NH'])
      wecomProcesses = countWecomProcesses(stdout)
    } catch {
      wecomProcesses = 0
    }
  }

  const desktop = await desktopCheck
  return {
    platform_ok: platformOk,
    platform,
    interactive_session: desktop === null ? null : desktop === 'interactive',
    session_name: platformOk ? (process.env.SESSIONNAME ?? null) : null,
    ...(desktop !== null && desktop !== 'interactive' ? { desktop_issue: WINDOWS_DESKTOP_ISSUES[desktop] } : {}),
    powershell_available: powershellAvailable,
    powershell_path: powershellPath,
    wecom_running: wecomProcesses > 0,
    wecom_processes: wecomProcesses,
  }
}
