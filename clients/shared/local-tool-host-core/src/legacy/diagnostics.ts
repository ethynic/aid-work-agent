/**
 * 诊断导出的 Runtime home 定位（Phase 7：installation-v2 指针跟随 + legacy 兼容）。
 *
 * 背景（2026-10-20 复盘）：新版安装代次下 Runtime 实际数据 home 在
 * `<userData>/installation-v2/generations/<安装UUID>/connections/<连接UUID>`，由
 * `current.json` 指针登记（写入方：clients/agent-desktop/electron/runtimeInstallation.ts，
 * 唯一写入者）；诊断工具若固定读 legacy 目录（%APPDATA%/aidwork-tool-runtime[-acceptance]），
 * 会在新版运行正常的机器上误报 homeExists=false（config/device 全部读不到）。
 *
 * 本模块只做**只读**定位：读指针、按 runtimeInstallation 的指针形状规则校验、
 * 按存在性回退 legacy 路径；绝不调用 prepareRuntimeInstallation 等安装初始化
 * （会停进程/迁移/重写指针——取证禁止，复盘明确要求不修改 current.json）。
 */
import { accessSync, constants, existsSync, lstatSync, readFileSync } from 'node:fs'
import path from 'node:path'

/** 产品档案（镜像 clients/agent-desktop/electron/productConfiguration.ts 的目录约定） */
export interface RuntimeHomeProfile {
  profile: 'acceptance' | 'production'
  /** Electron userData 目录名（installation-v2/ 所在） */
  userDataDir: string
  /** legacy Runtime home 目录名（固定路径兼容） */
  legacyHomeDir: string
}

/** 全部产品档案（顺序即候选优先级：验收版在前——与 release/runtime-ui 采集工具的验收语境一致） */
export const RUNTIME_HOME_PROFILES: readonly RuntimeHomeProfile[] = [
  { profile: 'acceptance', userDataDir: 'aidwork-runtime-app-acceptance', legacyHomeDir: 'aidwork-tool-runtime-acceptance' },
  { profile: 'production', userDataDir: 'aidwork-runtime-app', legacyHomeDir: 'aidwork-tool-runtime' },
] as const

const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/

export type RuntimeHomeSource = 'installation-v2-pointer' | 'legacy-path'

/** 一个产品档案的 home 定位结果（全部字段只读事实，不做任何写操作） */
export interface RuntimeHomeCandidate {
  profile: RuntimeHomeProfile['profile']
  /** 实际选定的 home（指针 home 存在则跟随指针，否则 legacy 路径） */
  home: string
  source: RuntimeHomeSource
  homeExists: boolean
  /** 指针登记的 home（指针不存在/不可读/形状非法为 null；可能不存在于磁盘） */
  pointerHome: string | null
  legacyHome: string
  legacyHomeExists: boolean
  /** 指针读取异常描述（损坏/形状非法；用于 errors 上报，null = 无异常） */
  pointerError: string | null
}

/** 只读读取并校验指针（形状规则对齐 runtimeInstallation.readPointer/assertHome/ownedHome：
 *  home 必须位于 `<userData>/installation-v2/generations/<uuid>/connections/<uuid>`，
 *  防指针被改写指向任意路径；诊断语境下任何异常都降级为 pointerError + legacy 回退，不抛出） */
function readPointerHome(userDataPath: string): { home: string | null; error: string | null } {
  const file = path.join(userDataPath, 'installation-v2', 'current.json')
  let raw: string
  try {
    if (!existsSync(file)) return { home: null, error: null }
    const stat = lstatSync(file)
    if (!stat.isFile() || stat.isSymbolicLink() || stat.size > 65_536) return { home: null, error: 'pointer-not-regular-file' }
    raw = readFileSync(file, 'utf8').replace(/^\uFEFF/, '')
  } catch (err) {
    return { home: null, error: `pointer-unreadable: ${err instanceof Error ? err.message : String(err)}` }
  }
  let record: unknown
  try {
    record = JSON.parse(raw)
  } catch {
    return { home: null, error: 'pointer-corrupt-json' }
  }
  if (
    !record || typeof record !== 'object' || Array.isArray(record)
    || (record as Record<string, unknown>)['version'] !== 2
    || typeof (record as Record<string, unknown>)['home'] !== 'string'
  ) {
    return { home: null, error: 'pointer-invalid-shape' }
  }
  const home = (record as Record<string, unknown>)['home'] as string
  // 形状校验（对齐 ownedHome 的 relative 三段规则 + assertHome 的绝对路径要求）
  const relative = path.relative(path.join(userDataPath, 'installation-v2', 'generations'), home)
  const parts = relative.split(path.sep)
  const shapeOk =
    path.isAbsolute(home)
    && relative !== ''
    && !relative.startsWith('..')
    && !path.isAbsolute(relative)
    && parts.length === 3
    && UUID.test(parts[0] ?? '')
    && parts[1] === 'connections'
    && UUID.test(parts[2] ?? '')
  if (!shapeOk) return { home: null, error: 'pointer-home-outside-generations' }
  return { home, error: null }
}

function directoryExists(target: string): boolean {
  try {
    return existsSync(target) && lstatSync(target).isDirectory()
  } catch {
    return false
  }
}

/** 解析一个产品档案的实际 home：指针存在且指向的目录存在 → 跟随指针；
 *  否则回退 legacy 固定路径（新版未装/指针缺失时保持旧行为） */
export function resolveRuntimeHome(appDataPath: string, profileDef: RuntimeHomeProfile): RuntimeHomeCandidate {
  const userDataPath = path.join(appDataPath, profileDef.userDataDir)
  const legacyHome = path.join(appDataPath, profileDef.legacyHomeDir)
  const legacyHomeExists = directoryExists(legacyHome)
  const { home: pointerHome, error: pointerError } = readPointerHome(userDataPath)
  const pointerHomeExists = pointerHome !== null && directoryExists(pointerHome)
  if (pointerHomeExists) {
    return {
      profile: profileDef.profile,
      home: pointerHome,
      source: 'installation-v2-pointer',
      homeExists: true,
      pointerHome,
      legacyHome,
      legacyHomeExists,
      pointerError,
    }
  }
  return {
    profile: profileDef.profile,
    home: legacyHome,
    source: 'legacy-path',
    homeExists: legacyHomeExists,
    pointerHome,
    legacyHome,
    legacyHomeExists,
    pointerError,
  }
}

/** 解析全部产品档案的 home 候选（顺序同 RUNTIME_HOME_PROFILES） */
export function resolveRuntimeHomeCandidates(appDataPath: string): RuntimeHomeCandidate[] {
  return RUNTIME_HOME_PROFILES.map((profileDef) => resolveRuntimeHome(appDataPath, profileDef))
}

/** 主 home：第一个实际存在的候选（诊断导出的采集目标）；全部不存在返回 null */
export function resolvePrimaryRuntimeHome(appDataPath: string): RuntimeHomeCandidate | null {
  return resolveRuntimeHomeCandidates(appDataPath).find((candidate) => candidate.homeExists) ?? null
}

/** 健康文件命中位置：主位置 <home>/logs/ 或降级位置 <home>/（logs 目录不可写时写端降级） */
export type LogFileHealthSource = 'logs' | 'home-root'

/** log-health.json 现场形状（写入方：legacy/log.ts persistLogFileHealth，唯一写入者；
 *  与 runtime-diagnostic-summary.ps1 的 logHealth 采集同文件同口径） */
export interface LogFileHealthRecord {
  fileWriteFailures: number
  lastFileWriteErrorAt: string | null
  lastFileWriteError: string | null
  /** 健康文件是否处于降级位置（主位置 logs/ 不可写，写端降级到 home 根） */
  degraded?: boolean
  /** 降级原因：主位置写入失败的原始错误（redact 后）；未降级为 null */
  degradedReason?: string | null
  /** 快照落盘时刻（ISO；新版写入，旧文件可能缺省） */
  updatedAt?: string
}

export interface LogFileHealthReading {
  /** null = 两位置均无健康记录（无失败记录、或健康通道自身不可用——见 error/logDirError） */
  health: LogFileHealthRecord | null
  /** 读取/校验异常描述（null = 正常）：两位置均无记录且日志目录异常时为目录异常码
   *  （log-dir-missing / log-dir-not-directory / log-dir-linked / log-dir-unwritable），
   *  绝不在日志目录异常时返回 {health:null,error:null} 把已发生失败呈现为无记录 */
  error: string | null
  /** 实际选中的健康记录位置（两位置按新鲜度选择，见 readLogFileHealth；null = 未命中任何位置） */
  source: LogFileHealthSource | null
  /** 日志目录（<home>/logs）异常探测（码同 error 中的 log-dir-*；null = 正常）。
   *  健康记录命中时也如实携带——说明 logs 目录本身仍异常（如 runtime.log 不可写的原因） */
  logDirError: string | null
}

/** 只读探测日志目录异常：不存在 / 被同名文件占位（或其它非目录形态）/ 链接 / 不可写。
 *  writability 用 accessSync(W_OK) 权限查询，不做任何写操作（只读取证）。 */
function probeLogDirError(home: string): string | null {
  const logsDir = path.join(home, 'logs')
  try {
    if (!existsSync(logsDir)) return 'log-dir-missing'
    const stat = lstatSync(logsDir)
    if (stat.isSymbolicLink()) return 'log-dir-linked'
    if (!stat.isDirectory()) return 'log-dir-not-directory'
    accessSync(logsDir, constants.W_OK)
    return null
  } catch {
    return 'log-dir-unwritable'
  }
}

type HealthFileParse =
  | { status: 'ok'; health: LogFileHealthRecord; mtimeMs: number }
  | { status: 'missing' }
  | { status: 'error'; error: string }

/** 读取并校验单个健康文件（任何异常降级为 status=error，不抛出）；ok 时附带文件 mtime
 *  （旧格式记录无 updatedAt 时的 freshness 兜底键） */
function parseHealthFile(file: string): HealthFileParse {
  let raw: string
  let mtimeMs = 0
  try {
    if (!existsSync(file)) return { status: 'missing' }
    const stat = lstatSync(file)
    if (!stat.isFile() || stat.isSymbolicLink() || stat.size > 65_536) return { status: 'error', error: 'log-health-not-regular-file' }
    mtimeMs = stat.mtimeMs
    raw = readFileSync(file, 'utf8').replace(/^\uFEFF/, '')
  } catch (err) {
    return { status: 'error', error: `log-health-unreadable: ${err instanceof Error ? err.message : String(err)}` }
  }
  let record: unknown
  try {
    record = JSON.parse(raw)
  } catch {
    return { status: 'error', error: 'log-health-corrupt-json' }
  }
  const value = record as Record<string, unknown> | null
  if (
    !value || typeof value !== 'object' || Array.isArray(value)
    || !Number.isSafeInteger(value['fileWriteFailures']) || (value['fileWriteFailures'] as number) < 0
    || !(typeof value['lastFileWriteErrorAt'] === 'string' || value['lastFileWriteErrorAt'] === null)
    || !(typeof value['lastFileWriteError'] === 'string' || value['lastFileWriteError'] === null)
    || !(typeof value['degraded'] === 'boolean' || value['degraded'] === undefined)
    || !(typeof value['degradedReason'] === 'string' || value['degradedReason'] === null || value['degradedReason'] === undefined)
  ) {
    return { status: 'error', error: 'log-health-invalid-shape' }
  }
  return {
    status: 'ok',
    mtimeMs,
    health: {
      fileWriteFailures: value['fileWriteFailures'] as number,
      lastFileWriteErrorAt: value['lastFileWriteErrorAt'] as string | null,
      lastFileWriteError: value['lastFileWriteError'] as string | null,
      ...(value['degraded'] === undefined ? {} : { degraded: value['degraded'] as boolean }),
      ...(value['degradedReason'] === undefined ? {} : { degradedReason: value['degradedReason'] as string | null }),
      ...(typeof value['updatedAt'] === 'string' ? { updatedAt: value['updatedAt'] as string } : {}),
    },
  }
}

/** 新鲜度比较键（Phase 7 CR 三轮）：updatedAt 有无 + ISO 字典序 + 文件 mtime 兜底 */
interface FreshnessKey {
  hasUpdatedAt: boolean
  updatedAt: string
  mtimeMs: number
}

function freshnessKey(health: LogFileHealthRecord, mtimeMs: number): FreshnessKey {
  return { hasUpdatedAt: typeof health.updatedAt === 'string', updatedAt: health.updatedAt ?? '', mtimeMs }
}

/** a 是否严格比 b 新（TS 读取端与 runtime-diagnostic-summary.ps1 同源同规则）：
 *  1) 优先比较记录内 updatedAt（写端每次落盘都写，ISO 字符串字典序即时间序）；
 *     只有一方有 updatedAt 时，有的一方一律更新——旧格式（首轮格式无该字段）视为更旧。
 *  2) 双方 updatedAt 持平（写端节流 ≥1s，理论罕见）或双方均为旧格式 → 用文件 mtime 兜底。
 *  3) 仍持平 → 不判定更新（调用方稳定保留先序位置 = logs 主位置；写端主位置可写即自愈）。 */
function isFresher(a: FreshnessKey, b: FreshnessKey): boolean {
  if (a.hasUpdatedAt !== b.hasUpdatedAt) return a.hasUpdatedAt
  if (a.hasUpdatedAt) {
    if (a.updatedAt !== b.updatedAt) return a.updatedAt > b.updatedAt
  }
  return a.mtimeMs > b.mtimeMs
}

/** 只读读取日志健康快照（诊断导出用）：文件日志通道写失败的健康记录。
 *  两位置（logs/log-health.json 主位置、home 根 log-health.json 降级位置）都解析有效记录，
 *  按新鲜度规则（isFresher）选择当前快照——不能仅因主位置可读就认定它是当前记录：
 *  写端降级后主位置会残留旧快照（Phase 7 CR 三轮：主位置旧 1 次 + 根位置新 2 次
 *  曾被遮蔽为 1 次）。任一位置损坏都如实上报 error；目录异常由 logDirError 携带；
 *  两位置均无记录且目录异常时 error 必为目录异常码，绝不 {health:null,error:null}。
 *  只读取证：任何异常降级为 error，绝不抛出。 */
export function readLogFileHealth(home: string): LogFileHealthReading {
  const logDirError = probeLogDirError(home)
  const primary = parseHealthFile(path.join(home, 'logs', 'log-health.json'))
  const fallback = parseHealthFile(path.join(home, 'log-health.json'))
  const candidates: Array<{ parse: Extract<HealthFileParse, { status: 'ok' }>; source: LogFileHealthSource }> = []
  if (primary.status === 'ok') candidates.push({ parse: primary, source: 'logs' })
  if (fallback.status === 'ok') candidates.push({ parse: fallback, source: 'home-root' })
  // 任一位置损坏（存在但不可解析）都如实上报，即便另一位置的记录已被选中
  const error = primary.status === 'error' ? primary.error : fallback.status === 'error' ? fallback.error : null
  if (candidates.length === 0) {
    // 两位置均无健康记录：损坏错误优先（最具体），其次目录异常——不允许 {health:null,error:null}
    // 掩盖日志目录异常（此时 runtime.log 大概率同样不可写，失败可能已发生但无法落盘）
    return { health: null, error: error ?? logDirError, source: null, logDirError }
  }
  let chosen = candidates[0]!
  for (const candidate of candidates.slice(1)) {
    if (isFresher(freshnessKey(candidate.parse.health, candidate.parse.mtimeMs), freshnessKey(chosen.parse.health, chosen.parse.mtimeMs))) {
      chosen = candidate
    }
  }
  return { health: chosen.parse.health, error, source: chosen.source, logDirError }
}
