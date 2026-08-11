/**
 * 只读环境探测原语（设计 §4.1 doctor 只允许的检查项 + §5.1 weixin_probe 能力矩阵）。
 *
 * 严格只读：只查询平台/会话/PowerShell/进程列表，绝不激活窗口、发送输入或改剪贴板。
 * 全部检查 best-effort：单项失败只落 false，不抛出（probe/doctor 都要快速返回结构化结果）。
 *
 * 测试可注入 platform/sessionName/localAppData/execFileFn 替身，模拟非 win32、
 * 服务会话、微信进程存在性等场景。
 */
import { execFile } from 'node:child_process'
import { mkdirSync, writeFileSync, readFileSync, unlinkSync } from 'node:fs'
import { join } from 'node:path'

export interface ExecFileResult {
  stdout: string
}

export type ExecFileFn = (file: string, args: string[]) => Promise<ExecFileResult>

/** 默认命令执行：只读命令，5s 超时，不弹窗 */
const defaultExecFile: ExecFileFn = (file, args) =>
  new Promise((resolve, reject) => {
    execFile(file, args, { timeout: 5000, windowsHide: true, encoding: 'utf8' }, (err, stdout) => {
      if (err) reject(err)
      else resolve({ stdout: String(stdout) })
    })
  })

export interface ProbeEnvironmentOptions {
  platform?: NodeJS.Platform
  /** SESSIONNAME 环境变量（win32 交互会话判定）；显式传 undefined 模拟服务会话 */
  sessionName?: string
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
  session_name: string | null
  powershell_available: boolean
  powershell_path: string | null
  weixin_running: boolean
  weixin_processes: number
}

/** win32 交互会话判定：Console（本地登录）或 RDP-Tcp*（远程桌面）为交互；Services/缺失为非交互 */
export function isInteractiveSession(sessionName: string | undefined): boolean {
  if (!sessionName) return false
  if (sessionName === 'Services') return false
  return sessionName === 'Console' || sessionName.startsWith('RDP-Tcp')
}

/** tasklist CSV 输出中的 Weixin.exe 进程数（输出可能是 GBK，但进程名是 ASCII，按子串匹配安全） */
export function countWeixinProcesses(tasklistCsv: string): number {
  return tasklistCsv.split(/\r?\n/).filter((line) => /"?Weixin\.exe"?/i.test(line)).length
}

/** artifact 根目录（设计 §8：%LOCALAPPDATA%\AidWorkAgent\weixin-cli\artifacts） */
export function artifactDir(localAppData: string | undefined = process.env.LOCALAPPDATA): string | null {
  if (!localAppData) return null
  return join(localAppData, 'AidWorkAgent', 'weixin-cli', 'artifacts')
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
  const sessionName = platformOk ? (opts.sessionName !== undefined ? opts.sessionName : process.env.SESSIONNAME) : undefined
  const interactive = platformOk ? isInteractiveSession(sessionName) : null

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

  // Weixin.exe 进程存在性（tasklist 只读枚举；绝不代表登录态，仅进程层面）
  step('检查 Weixin.exe 进程')
  let weixinProcesses = 0
  if (platformOk) {
    try {
      const { stdout } = await execFileFn('tasklist', ['/FI', 'IMAGENAME eq Weixin.exe', '/FO', 'CSV', '/NH'])
      weixinProcesses = countWeixinProcesses(stdout)
    } catch {
      weixinProcesses = 0
    }
  }

  return {
    platform_ok: platformOk,
    platform,
    interactive_session: interactive,
    session_name: sessionName ?? null,
    powershell_available: powershellAvailable,
    powershell_path: powershellPath,
    weixin_running: weixinProcesses > 0,
    weixin_processes: weixinProcesses,
  }
}
