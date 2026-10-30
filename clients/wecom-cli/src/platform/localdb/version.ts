/**
 * C1 安装检测：版本只认【实际运行进程】的 FileVersion；安装目录探测仅作诊断（设计 §3.2 C1）。
 *
 * 为什么以进程 executable 为准（设计 §3.2 C1 / §3.1）：本地库解密算法与实际运行进程的版本
 * 强相关（exact-version 矩阵、未知版本禁止硬扫）；安装目录可能残留未清理的旧版本——若把
 * 安装目录版本回填进 version，残留版本会冒充运行版本被矩阵判 validated，未来安全网会拿它
 * 当运行准入。因此 version/version_source 只在进程探测命中且读到 FileVersion 时非空；
 * 进程不可得时安装目录结果仅落诊断字段（exe_path/install_dir/install_version），不参与准入。
 *
 * 进程选择：PowerShell 侧按枚举顺序取首个 WXWork.exe 作为 PID/exe_path（与后续 C6 keyscan
 * 内存扫描共用同一 PID 选择——keyscan 以 InstallInfo.pid 为入参，保证版本验证与扫描对象一致），
 * 同时输出全部 WXWork 进程的创建时刻（CreationDate → FILETIME 行内透出，TS 侧转 epoch ms），
 * 仅作运行时刻诊断（InstallInfo.process_started_at），不参与 C3 身份确认。
 *
 * 严格只读：PowerShell 只做 WMI 进程查询与 exe 文件 VersionInfo 读取，不触发升级、不改状态。
 * 全程 best-effort：任何一步失败落空值/false，不抛出（doctor 深检要快速返回结构化结果）。
 * 非 win32 不做平台预判，由 exec/文件系统探测自然落空（平台门禁归 doctor 的 environment 检查）。
 *
 * 测试可注入 execFileFn / installRoot / appData 替身，模拟进程命中、进程缺失、
 * WXWorkUpgrader/upgrade 升级信号等场景（无需真实企微）。
 */
import { execFile } from 'node:child_process'
import { readdirSync, statSync } from 'node:fs'
import { dirname, join } from 'node:path'
import type { ExecFileFn } from '../environment.js'

/** 默认命令执行：只读命令，5s 超时，不弹窗（与 environment.ts 的 defaultExecFile 同语义；该 helper 未导出，为不动现有文件在此等义复制） */
const defaultExecFile: ExecFileFn = (file, args) =>
  new Promise((resolve, reject) => {
    execFile(file, args, { timeout: 5000, windowsHide: true, encoding: 'utf8' }, (err, stdout) => {
      if (err) reject(err)
      else resolve({ stdout: String(stdout) })
    })
  })

/**
 * 进程 executable + PID + 创建时刻 + 版本一次性只读探测（单次 spawn，四者来自同一 CIM 快照）：
 * Get-CimInstance 枚举全部运行中 WXWork.exe（不截断——实例锚点需要全部进程的创建时刻），
 * 每进程输出一行 `PID=<pid>|CREATED=<FILETIME>|PATH=<exe路径>`（字段缺失落空段），首个带
 * 路径的进程再读该文件 VersionInfo.FileVersion 以 `VERSION=` 行输出；TS 侧
 * parseProcessProbeOutput 解析（PID 仍取首个进程对象，与 keyscan 内存扫描共用同一 PID 选择）。
 * CREATED 输出 Windows FILETIME（100ns 自 1601-01-01 UTC，纯数字，无本地时区歧义），
 * TS 侧换算 epoch ms——换算放解析层可注入可测。
 * 脚本内刻意不含双引号：execFile 在 Windows 上的参数转义与 PowerShell 引号规则存在差异，
 * 全单引号（WQL 内用 '' 转义）规避转义歧义；-NonInteractive 防任何形式的交互挂起。
 */
const PROCESS_PROBE_SCRIPT =
  "$ErrorActionPreference='SilentlyContinue';" +
  "$procs=@(Get-CimInstance Win32_Process -Filter 'Name=''WXWork.exe''');" +
  '$first=$true;' +
  'foreach($c in $procs){' +
  "$ft='';if($c.CreationDate){$ft=$c.CreationDate.ToFileTime()};" +
  "$p='';if($c.ExecutablePath){$p=$c.ExecutablePath};" +
  "'PID='+$c.ProcessId+'|CREATED='+$ft+'|PATH='+$p;" +
  "if($first -and $p){$first=$false;$v=(Get-Item -LiteralPath $p).VersionInfo.FileVersion;if($v){'VERSION='+$v}}" +
  '}'

/** 企微默认安装根（回退探测目标：其下版本子目录与 WXWork.exe） */
export const DEFAULT_INSTALL_ROOT = 'C:\\Program Files (x86)\\WXWork'

/**
 * 升级信号「近期 mtime」窗口：7 天（启发式，非精确语义）。升级器活动会刷新目录 mtime，
 * 窗口覆盖一个使用周期，又不至于把陈年残留当活动信号。
 */
export const UPGRADE_SIGNAL_RECENT_MS = 7 * 24 * 60 * 60 * 1000

export interface DetectInstallDeps {
  execFileFn?: ExecFileFn
  /** 指定操作主窗口或待扫描候选 PID，防其他进程版本冒充准入。 */
  processPid?: number
  /** 安装根目录（默认 DEFAULT_INSTALL_ROOT；WXWorkUpgrader 信号也在其下查找） */
  installRoot?: string
  /** %APPDATA% 根（upgrade 信号目录定位；缺省读环境变量） */
  appData?: string
}

export interface InstallInfo {
  installed: boolean
  /** 进程命中=运行进程 executable；进程不可得时为安装目录探测结果（仅诊断） */
  exe_path: string | null
  /**
   * 只表示运行中进程的 FileVersion。进程查询失败/无进程/进程缺 VERSION → null，
   * 绝不回填安装目录版本——安装残留版本不得冒充运行版本参与矩阵准入。
   */
  version: string | null
  /** version 的来源标记：'process'=运行进程 FileVersion；null=无已确认来源（此时 version 必为 null） */
  version_source: 'process' | null
  /**
   * 进程命中时的 WXWork.exe PID（供后续 C6 keyscan 内存扫描复用同一进程，保持验证与扫描
   * 对象一致）；无进程=null。pid 与 version 独立：进程命中但缺 FileVersion 时仍有 pid。
   */
  pid: number | null
  /** 枚举进程的最早创建时刻，仅诊断；不能证明实例或登录账号归属。 */
  process_started_at: number | null
  /** exe 所在目录（进程目录，或回退探测到的安装目录/版本子目录——诊断字段） */
  install_dir: string | null
  /**
   * 安装目录探测到的版本子目录名（诊断字段）：安装残留可能停留在旧版本，
   * 【不得用于运行中本地库准入】——运行准入只认 version_source==='process' 的 version。
   */
  install_version?: string
  /**
   * 升级器活动信号，空数组=无（设计 §3.2 C1）：
   * - 'upgrader_dir'：安装根下 WXWorkUpgrader 目录存在（存在或活动均算）
   * - 'upgrade_dir_active'：%APPDATA%\Tencent\WXWork\upgrade 存在且 mtime 在近期窗口内
   *   （存在但陈旧不算，避免陈年残留误报）
   */
  upgrade_signals: string[]
}

/** Windows FILETIME（100ns 自 1601-01-01 UTC）→ Unix epoch 毫秒；非有限/非正结果 → null */
export function filetimeToEpochMs(raw: string): number | null {
  if (!/^\d+$/.test(raw)) return null
  const epochMs = Math.round(Number(raw) / 10_000) - 11_644_473_600_000
  return Number.isFinite(epochMs) && epochMs > 0 ? epochMs : null
}

/** 解析产物单进程记录（PID=/CREATED=/PATH= 段均可缺失，独立落 null） */
interface ProcessProbeRecord {
  pid: number | null
  created_at_ms: number | null
  exe_path: string | null
}

/**
 * 解析 PROCESS_PROBE_SCRIPT 的 stdout（单独导出供单测喂罐头输出）。
 * 只认 `PID=<pid>|CREATED=<filetime>|PATH=<路径>` 每进程一行与独立 `VERSION=` 前缀行
 * （剥离 BOM 与前后空白），其余行是 PowerShell 噪声，忽略；PID/CREATED 做完整数字校验，
 * 非法值落 null（CREATED 为 FILETIME，经 filetimeToEpochMs 转 epoch ms）。
 * - pid/exe_path：取首个带合法值记录的对应字段（与 keyscan 共用同一进程选择）；
 * - process_started_at：全部记录 created_at_ms 的最小值（earliest=本实例启动时刻），无有效值 → null。
 */
export function parseProcessProbeOutput(stdout: string): {
  exe_path: string | null
  version: string | null
  pid: number | null
  process_started_at: number | null
} {
  let exe_path: string | null = null
  let version: string | null = null
  let pid: number | null = null
  let startedAt: number | null = null
  for (const rawLine of stdout.split(/\r?\n/)) {
    const line = rawLine.replace(/^\uFEFF/, '').trim()
    if (line.startsWith('PID=')) {
      const segs = line.split('|')
      const pidRaw = segs[0]?.slice('PID='.length).trim() ?? ''
      const createdRaw = segs.find((s) => s.startsWith('CREATED='))?.slice('CREATED='.length).trim() ?? ''
      const pathRaw = segs.find((s) => s.startsWith('PATH='))?.slice('PATH='.length).trim() ?? ''
      const record: ProcessProbeRecord = {
        pid: /^\d+$/.test(pidRaw) ? Number(pidRaw) : null,
        created_at_ms: createdRaw ? filetimeToEpochMs(createdRaw) : null,
        exe_path: pathRaw || null,
      }
      if (pid === null && record.pid !== null) pid = record.pid
      if (exe_path === null && record.exe_path !== null) exe_path = record.exe_path
      if (record.created_at_ms !== null && (startedAt === null || record.created_at_ms < startedAt)) {
        startedAt = record.created_at_ms
      }
    } else if (version === null && line.startsWith('VERSION=')) {
      const v = line.slice('VERSION='.length).trim()
      if (v) version = v
    }
  }
  return { exe_path, version, pid, process_started_at: startedAt }
}

/** 点分版本号逐段数值比较（a>b 返回正数）；段数不齐按 0 补齐，非数字段按 0 */
function compareDottedVersion(a: string, b: string): number {
  const pa = a.split('.').map((s) => parseInt(s, 10) || 0)
  const pb = b.split('.').map((s) => parseInt(s, 10) || 0)
  const len = Math.max(pa.length, pb.length)
  for (let i = 0; i < len; i++) {
    const d = (pa[i] ?? 0) - (pb[i] ?? 0)
    if (d !== 0) return d
  }
  return 0
}

/** 目录内大小写不敏感查找 WXWork.exe（Windows FS 本就不区分大小写；Linux 上的假目录树测试同样可命中） */
function findExeInDir(dir: string): string | null {
  try {
    const exe = readdirSync(dir, { withFileTypes: true }).find(
      (e) => e.isFile() && /^wxwork\.exe$/i.test(e.name),
    )
    return exe ? join(dir, exe.name) : null
  } catch {
    return null
  }
}

/** 安装根探测：① 规范布局 root\<版本号>\WXWork.exe 取最高版本；② 根目录直置 WXWork.exe（version 落空） */
function probeInstallRoot(root: string): { exePath: string; version: string | null } | null {
  try {
    const entries = readdirSync(root, { withFileTypes: true })
    const versionDirs = entries
      .filter((e) => e.isDirectory() && /^\d+(\.\d+)+$/.test(e.name))
      .map((e) => e.name)
      .sort((a, b) => compareDottedVersion(b, a)) // 高版本在前
    for (const v of versionDirs) {
      const exe = findExeInDir(join(root, v))
      if (exe) return { exePath: exe, version: v }
    }
    const rootExe = entries.find((e) => e.isFile() && /^wxwork\.exe$/i.test(e.name))
    if (rootExe) return { exePath: join(root, rootExe.name), version: null }
    return null
  } catch {
    return null
  }
}

/** 目录 mtime（毫秒）；不存在/无权限 → null */
function dirMtimeMs(dir: string): number | null {
  try {
    return statSync(dir).mtimeMs
  } catch {
    return null
  }
}

/** 升级信号收集（启发式环境事实，供 doctor 提示「版本可能被自动升级」；失败静默落空） */
function collectUpgradeSignals(installRoot: string, appData: string | undefined): string[] {
  const signals: string[] = []
  if (dirMtimeMs(join(installRoot, 'WXWorkUpgrader')) !== null) signals.push('upgrader_dir')
  if (appData) {
    const mtime = dirMtimeMs(join(appData, 'Tencent', 'WXWork', 'upgrade'))
    if (mtime !== null && Date.now() - mtime <= UPGRADE_SIGNAL_RECENT_MS) signals.push('upgrade_dir_active')
  }
  return signals
}

/**
 * 安装检测入口：进程探测 → 安装目录回退链 + 升级信号（独立于 installed 与否收集）。
 * 版本契约：version/version_source 只反映运行中进程；进程不可得时安装目录结果仅落
 * exe_path/install_dir/install_version 诊断字段。installed=false 时其余字段 null/空；全程不抛出。
 */
export async function detectInstall(deps: DetectInstallDeps = {}): Promise<InstallInfo> {
  const execFileFn = deps.execFileFn ?? defaultExecFile
  const installRoot = deps.installRoot ?? DEFAULT_INSTALL_ROOT
  const appData = deps.appData ?? process.env.APPDATA

  // ① 进程探测（版本权威来源；PowerShell 缺失/超时/输出异常一律视为无进程）
  let processInfo = {
    exe_path: null,
    version: null,
    pid: null,
    process_started_at: null,
  } as {
    exe_path: string | null
    version: string | null
    pid: number | null
    process_started_at: number | null
  }
  try {
    const { stdout } = await execFileFn('powershell.exe', [
      '-NoProfile',
      '-NonInteractive',
      '-Command',
      deps.processPid === undefined ? PROCESS_PROBE_SCRIPT
        : Number.isInteger(deps.processPid) && deps.processPid > 0
          ? PROCESS_PROBE_SCRIPT.replace("Name=''WXWork.exe''", `Name=''WXWork.exe'' AND ProcessId=${deps.processPid}`)
          : "throw 'invalid process PID'",
    ])
    processInfo = parseProcessProbeOutput(stdout)
  } catch {
    // 回退安装目录探测
  }

  let exePath: string | null = null
  let version: string | null = null
  let versionSource: 'process' | null = null
  let pid: number | null = null
  let installVersion: string | undefined
  if (processInfo.exe_path !== null) {
    // 进程命中：版本只认进程 exe 的 FileVersion（version_source='process'），缺 VERSION 行就落空
    //（不与安装目录版本混拼）；PID 独立输出，供后续 keyscan 复用同一进程。
    exePath = processInfo.exe_path
    pid = processInfo.pid
    if (processInfo.version !== null) {
      version = processInfo.version
      versionSource = 'process'
    }
  } else {
    // ② 回退：安装目录探测结果仅作诊断（exe_path/install_dir/install_version），
    //    版本不回填 version——安装残留的旧版本不得冒充运行版本参与矩阵准入。
    const fsHit = probeInstallRoot(installRoot)
    if (fsHit) {
      exePath = fsHit.exePath
      if (fsHit.version !== null) installVersion = fsHit.version
    }
  }

  return {
    installed: exePath !== null,
    exe_path: exePath,
    version,
    version_source: versionSource,
    pid,
    process_started_at: processInfo.process_started_at,
    install_dir: exePath !== null ? dirname(exePath) : null,
    ...(installVersion !== undefined ? { install_version: installVersion } : {}),
    upgrade_signals: collectUpgradeSignals(installRoot, appData),
  }
}
