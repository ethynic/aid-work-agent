/** C3：当前主窗口进程的消息库句柄确认账号；CEF/Documents/OneDrive 仅提供未确认候选。
 * Config.cfg 的 LoginCompanyId 是公司 id，不替代账号归属；只读，不解析其 GBK 值。
 */
import { closeSync, existsSync, openSync, readFileSync, readdirSync, readSync, fstatSync, statSync } from 'node:fs'
import { join, win32 } from 'node:path'

/**
 * 命中的回退层：
 * - 'cef-log'：CEF 日志解析命中（最高优先，最新登录上下文）；
 * - 'documents'：%USERPROFILE%\Documents\WXWork（Documents 未重定向）；
 * - 'onedrive-documents'：OneDrive 重定向（%OneDrive%\文档\WXWork / %OneDrive%\Documents\WXWork）。
 */
export type DataDirSource = 'process-handle' | 'cef-log' | 'documents' | 'onedrive-documents'

/** Config.cfg 仅诊断；process-handle-match 是唯一确认来源。 */
export type DataDirCrossCheck = 'config-missing' | 'id-present-in-config' | 'no-id-key' | 'process-handle-match'
export type DataDirIdentityReason = 'active-account-unavailable' | 'account-mismatch' | null

/** 一条 CEF 日志解析命中（root + 纯数字账号段 + 行内 user_id 字段） */
export interface CefLogHit {
  root: string
  user_id: string
  /**
   * 同一条日志行内的 `user_id: <纯数字>` 字段（活进程写入的账号 id，与 cache_path 同行——
   * 账号↔目录绑定证据；行内无该字段/非数字 → 缺省，不伪造绑定）。
   */
  account_id?: string
  /**
   * 行级完整时间戳（epoch ms）：行首 HH:MM:SS.mmm + 文件名会话起始日期合成（cefLineTimeMs）；
   * 行无时间戳或文件名不可解析 → 缺省（无法定时间，不作为「当前会话」证据，保守跳过）。
   */
  line_time_ms?: number
}

/** 带来源文件证据的 CEF 命中（collectCefHits 产物；CefLogHit 解析结果 + 来源文件名） */
export interface CefLogHitWithSource extends CefLogHit {
  /** 来源日志文件名（仅诊断，不作为事件排序依据） */
  source_file: string
}

export interface LocateDataDirDeps {
  /** %APPDATA% 根（CEF 日志目录定位；缺省读环境变量） */
  appData?: string
  /** %USERPROFILE%（Documents 候选根；缺省读环境变量） */
  userProfile?: string
  /** %OneDrive%（重定向候选根；缺省读环境变量） */
  oneDrive?: string
  /** CEF 日志目录注入覆盖（默认 <appData>\Tencent\WXWork\Log\CEF）；显式 null=禁用 CEF 层（测试/无日志机器语义） */
  cefLogDir?: string | null
  /** 回退候选根注入覆盖（带来源标记，默认 defaultCandidateRoots()；显式注入时完全替换默认序列） */
  candidateRoots?: Array<{ root: string; source: Exclude<DataDirSource, 'cef-log' | 'process-handle'> }>
  /** 仅接受登录探针选中主窗口 PID 的完整句柄枚举结果（active-account 模式）。 */
  activeAccount?: { pid: number; message_db: string } | null

}

export interface DataDirAccount {
  user_id: string
  /** <root>\<user_id> */
  dir: string
  /** <dir>\Data\message.db 存在（数据主库在位） */
  has_data_db: boolean
  /** <dir>\Config.cfg 存在（外层文件在位，不代表可解析） */
  config_found: boolean
  /** Config.cfg 的 LoginCompanyId（纯数字 ASCII 值时输出；公司 id，≠目录 user_id） */
  login_company_id?: string
  /** Config.cfg 的 HasEverLoginKey（短 ASCII 值时输出，如 '1'/'0'） */
  has_ever_login?: string
  /** 账号目录 mtime（毫秒）；stat 失败缺省 */
  dir_mtime_ms?: number
}

export interface DataDirIdentity {
  config_found: boolean
  login_company_id: string | null
  cross_check: DataDirCrossCheck | null
  identity_confirmed: boolean
  reason: DataDirIdentityReason
  /** 直接证据所属的操作主窗口进程；日志候选不能提供此证据。 */
  process_pid: number | null
}

export interface DataDirInfo {
  source: DataDirSource | null
  /** 如 C:\Users\PC\OneDrive\文档\WXWork */
  root: string | null
  /** 目录段账号 id（source 命中的账号） */
  user_id: string | null
  /** <root>\<user_id> */
  dir: string | null
  /** 选中账号的四个库实际存在性核验后的绝对路径，不存在落 null；无选中目录 → null */
  db_paths: {
    message: string | null
    session: string | null
    user: string | null
    message_lookup: string | null
  } | null
  /** 全部候选账号目录（CEF 命中账号排第一；无命中根 → 空数组） */
  accounts: DataDirAccount[]
  identity: DataDirIdentity
}

/** Config.cfg 解析结果：只取键存在性与 ASCII 安全值（GBK 值不解析、不致命） */
export interface ConfigCfgSummary {
  /** 外层 latin-1 JSON 解析成功（GBK 值中含引号/反斜杠字节可致解析失败 → false，不致命） */
  parsed: boolean
  /** 内层键总数（解析成功时） */
  total_keys: number
  /** 登录相关键中实际存在的键名（值可能是 GBK 乱码，只证明存在性） */
  login_keys_present: string[]
  /** ASCII 安全值的键值对（值全可见 ASCII 且 ≤ ASCII_VALUE_MAX_CHARS；GBK 乱码值天然排除） */
  ascii_values: Record<string, string>
}

/**
 * CEF 日志默认目录（相对 %APPDATA%）。真机位置：...\Tencent\WXWork\Log\CEF\，
 * 父目录 Log 下的 *_encrypt.log 实测不含 cache_path 行，不在探测范围。
 */
export const DEFAULT_CEF_LOG_DIR_SEGMENTS = ['Tencent', 'WXWork', 'Log', 'CEF'] as const

/**
 * Config.cfg 登录相关键白名单（P0 勘察确认的登录证据键；只证明存在性，登录判定属 1.10）。
 */
export const CONFIG_LOGIN_KEYS = [
  'DbKeyVerKey',
  'LoginCompanyId',
  'HasEverLoginKey',
  'kHasBindWeChatKey',
  'kPollCorpMessageSyncKey',
  'LastStorageBackupTimeKey',
] as const

/** ASCII 安全值上限（字符）：LoginCompanyId 等长数字 id 足够，排除超长 GBK 乱码值 */
const ASCII_VALUE_MAX_CHARS = 128

/**
 * 默认回退候选根序列（设计明确顺序；全部可经 deps 注入替换）：
 * ① %USERPROFILE%\Documents\WXWork（未重定向）
 * ② %OneDrive%\文档\WXWork（中文系统 OneDrive 重定向，本机实际命中形态）
 * ③ %OneDrive%\Documents\WXWork（英文形态）
 * env 任一缺失时对应候选跳过（undefined 不拼路径）。
 */
export function defaultCandidateRoots(
  userProfile?: string,
  oneDrive?: string,
): Array<{ root: string; source: Exclude<DataDirSource, 'cef-log' | 'process-handle'> }> {
  const candidates: Array<{ root: string; source: Exclude<DataDirSource, 'cef-log' | 'process-handle'> }> = []
  if (userProfile) candidates.push({ root: join(userProfile, 'Documents', 'WXWork'), source: 'documents' })
  if (oneDrive) candidates.push({ root: join(oneDrive, '文档', 'WXWork'), source: 'onedrive-documents' })
  if (oneDrive) candidates.push({ root: join(oneDrive, 'Documents', 'WXWork'), source: 'onedrive-documents' })
  return candidates
}

/**
 * 解析 CEF 日志文本中的 cache_path 行（单独导出供 fixture 单测）。
 * 行样例（[RISK] 为日志自带的字面标记）：
 * `20:00:26.014 I:34328 browser_process_impl(113)  [SetCurrentContext] user_id: **** cache_path:[RISK]<root>\<user_id>\WXWorkCefCache`。
 * 规则：行内需同时出现 `cache_path:[RISK]` 标记与 `\纯数字\` 账号段（且其后还有最后一段
 * 缓存目录名）；[RISK] 缺失的行、无数字段（如 qtCef 行）、空行/畸形行一律跳过。
 * 同行 `user_id: <纯数字>` 字段一并提取进 account_id（活进程同 行写入的账号↔目录绑定
 * 证据；字段缺失/非数字 → 缺省，不影响命中本身）。
 * 传入 fileStartMs（文件名会话起始时刻）时，行首 HH:MM:SS.mmm 与之合成为行级完整时间戳
 * line_time_ms（cefLineTimeMs）；行无时间戳/合成不可得 → 缺省。返回按出现顺序的全部命中
 * （多账号多行不合并——消费侧统一按事件时间排序）。
 */
export function parseCefLog(content: string, fileStartMs?: number): CefLogHit[] {
  const hits: CefLogHit[] = []
  for (const rawLine of content.split(/\r?\n/)) {
    const line = rawLine.replace(/\r$/, '').trim()
    if (!line) continue
    // 锚定「倒数第二段为纯数字账号段」：非贪心 root + \数字\最后一段，防止把行内其他路径当账号
    const m = /cache_path:\[RISK\](.*?)\\(\d+)\\[^\\]+$/.exec(line)
    if (!m) continue
    const root = m[1]
    const userId = m[2]
    if (!root || !userId) continue
    // 同行 user_id 字段：真实日志行形如 `user_id: 123456 cache_path:[RISK]...`（同一行同写）
    const acct = /\buser_id:\s*(\d+)\b/.exec(line)
    const base: CefLogHit = acct ? { root, user_id: userId, account_id: acct[1] } : { root, user_id: userId }
    if (fileStartMs === undefined) {
      hits.push(base)
      continue
    }
    const lineTimeMs = cefLineTimeMs(line, fileStartMs)
    hits.push(lineTimeMs !== null ? { ...base, line_time_ms: lineTimeMs } : base)
  }
  return hits
}

/**
 * CEF 日志读取窗口（字节）：
 * - ≤ WHOLE（2MB，观测最大 251KB 远小于此）：整读；
 * - 更大：尾窗 1MB（最新追加/切号行在尾部）+ 头窗 64KB（实测 cache_path 行出现在会话文件
 *   起始的启动初始化，大文件时会被尾部窗口推出）。两窗拼接后按行解析，窗口切断的残行
 *   因不含完整 [RISK]+路径锚点自然不命中，best-effort 可接受。
 */
const CEF_LOG_WHOLE_READ_MAX_BYTES = 2 * 1024 * 1024
const CEF_LOG_TAIL_BYTES = 1024 * 1024
const CEF_LOG_HEAD_BYTES = 64 * 1024

/**
 * 单次定位最多扫描的日志文件数（按文件名字典序取最新 N 个）：CEF 日志按会话滚动累积、
 * 数量无上限，doctor 深检要快速返回，不能随历史文件数线性增长同步读取；文件名日期前缀
 * 只作为读取预算；交叠日志的事件仍需另按行时间排序，截断可能丢候选，不构成身份证据。
 */
export const CEF_LOG_MAX_FILES = 20

/**
 * 从 CEF 日志文件名前缀解析会话起始时刻（本地时区 epoch ms）。
 * 真机形态：`2026-10-14_16-43-22(5.0.81.5503).log`（日期-时间，分隔符 -/_；文件名即会话
 * 文件创建时刻），兼容紧凑形态 `YYYYMMDDHHMMSS`。解析失败/字段越界 → null：该文件的全部
 * 命中无法定时间，不作为「当前会话」证据（评审 v7 二次意见：保守跳过，不猜日期）。
 */
export function parseCefLogFileNameStart(name: string): number | null {
  const m = /^(\d{4})[-_]?(\d{2})[-_]?(\d{2})[-_]?(\d{2})[-_]?(\d{2})[-_]?(\d{2})/.exec(name)
  if (!m) return null
  // 正则各分组必命中，索引访问安全（! 断言）
  const y = Number(m[1]!)
  const mo = Number(m[2]!)
  const d = Number(m[3]!)
  const h = Number(m[4]!)
  const mi = Number(m[5]!)
  const s = Number(m[6]!)
  if (mo < 1 || mo > 12 || d < 1 || d > 31 || h > 23 || mi > 59 || s > 59) return null
  const dayStart = new Date(y, mo - 1, d)
  // Date 构造会把越界分量静默翻滚进下月——回读校验防伪日期文件名（如 2026-02-31）
  if (dayStart.getFullYear() !== y || dayStart.getMonth() !== mo - 1 || dayStart.getDate() !== d) return null
  const t = dayStart.setHours(h, mi, s, 0)
  return Number.isFinite(t) ? t : null
}

/**
 * 行时间戳（行首 `HH:MM:SS.mmm`）+ 文件名会话起始时刻 → 行级完整时间戳（epoch ms）。
 * 行时刻以文件起始日的本地日历为基准合成；行时刻早于文件起始时刻（跨午夜续写）→ 日期 +1 天。
 * 行无时间戳/字段越界 → null（无法定时间，不作为证据）。时区标尺：行与文件名同为写入端本地
 * 时区，合成结果与 CIM CreationDate 换算的绝对 epoch 同一标尺；无夏令时部署无偏移（DST 地区
 * 的 1 小时量级误差由评审口径的保守方向兜底——错判只会拒绝确认，不会放行）。
 */
export function cefLineTimeMs(line: string, fileStartMs: number): number | null {
  const m = /^(\d{2}):(\d{2}):(\d{2})\.(\d{3})\s/.exec(line)
  if (!m) return null
  // 正则各分组必命中，索引访问安全（! 断言）
  const h = Number(m[1]!)
  const mi = Number(m[2]!)
  const s = Number(m[3]!)
  const ms = Number(m[4]!)
  if (h > 23 || mi > 59 || s > 59) return null
  const start = new Date(fileStartMs)
  const midnight = new Date(start.getFullYear(), start.getMonth(), start.getDate()).getTime()
  const todFile = fileStartMs - midnight
  const todLine = h * 3_600_000 + mi * 60_000 + s * 1_000 + ms
  return midnight + todLine + (todLine < todFile ? 86_400_000 : 0)
}

/** 读取单个 CEF 日志文件的解析用文本（头尾窗口策略，注释见常量）；打不开/读失败 → null */
function readCefLogText(path: string): string | null {
  let fd: number
  try {
    fd = openSync(path, 'r') // 只读打开
  } catch {
    return null
  }
  try {
    const size = fstatSync(fd).size
    if (size <= CEF_LOG_WHOLE_READ_MAX_BYTES) {
      const buf = Buffer.alloc(size)
      const n = readSync(fd, buf, 0, size, 0)
      return buf.subarray(0, n).toString('utf8')
    }
    const tailLen = Math.min(size, CEF_LOG_TAIL_BYTES)
    const tail = Buffer.alloc(tailLen)
    readSync(fd, tail, 0, tailLen, size - tailLen)
    const headLen = Math.min(size, CEF_LOG_HEAD_BYTES)
    const head = Buffer.alloc(headLen)
    readSync(fd, head, 0, headLen, 0)
    return head.toString('utf8') + '\n' + tail.toString('utf8')
  } catch {
    return null
  } finally {
    try {
      closeSync(fd)
    } catch {
      // 关闭失败忽略（只读 fd，不影响结果）
    }
  }
}

/**
 * 目录下按文件名字典序（日期前缀=时间序）排列的 .log 文件，只取最新 CEF_LOG_MAX_FILES 个
 * （日志按会话累积无上限，截断旧文件防深检变慢）；目录不可读 → 空数组
 */
function listCefLogFiles(dir: string): string[] {
  try {
    return readdirSync(dir, { withFileTypes: true })
      .filter((e) => e.isFile() && /\.log$/i.test(e.name))
      .map((e) => e.name)
      .sort()
      .slice(-CEF_LOG_MAX_FILES)
  } catch {
    return []
  }
}

/**
 * 扫描有限的 CEF 日志，返回按行级事件时间排序的候选命中，
 * 每条命中附来源文件名；文件名可解析会话起始时刻时，行时间戳由解析层合成（line_time_ms）。
 * 文件名不可解析 → 该文件命中不带 line_time_ms（无法定时间，不作为「当前会话」证据）。
 * 目录缺失/无日志/读取失败 → 空数组，不抛出。
 */
function collectCefHits(cefLogDir: string): CefLogHitWithSource[] {
  const hits: CefLogHitWithSource[] = []
  for (const name of listCefLogFiles(cefLogDir)) {
    const path = join(cefLogDir, name)
    const fileStartMs = parseCefLogFileNameStart(name)
    const text = readCefLogText(path)
    if (text === null) continue
    for (const hit of parseCefLog(text, fileStartMs === null ? undefined : fileStartMs)) {
      hits.push({ ...hit, source_file: name })
    }
  }
  return hits.sort((a, b) => (a.line_time_ms ?? -Infinity) - (b.line_time_ms ?? -Infinity))
}

/**
 * 解析 Config.cfg 文本（单独导出供 fixture 单测）。入参为 latin-1 解码后的外层文本：
 * 外层 JSON {"config": {...}}（无 config 包裹的扁平形态按同层兜底），内层值可能是 GBK 字节
 * 的 latin-1 乱码——只收集【全可见 ASCII 且 ≤128 字符】的安全值，乱码值不进 ascii_values
 * 但不影响其键计入 login_keys_present。外层非 JSON（含 GBK 值含引号/反斜杠字节的破坏）→
 * parsed=false、空结果，不抛出。__proto__ 键跳过（对齐 matrix.ts 原型键防御）。
 */
export function parseConfigCfg(content: string): ConfigCfgSummary {
  const empty: ConfigCfgSummary = { parsed: false, total_keys: 0, login_keys_present: [], ascii_values: {} }
  let inner: Record<string, unknown>
  try {
    const obj: unknown = JSON.parse(content)
    if (obj === null || typeof obj !== 'object' || Array.isArray(obj)) return empty
    const outer = obj as Record<string, unknown>
    const config = Object.hasOwn(outer, 'config') ? outer['config'] : outer
    if (config === null || typeof config !== 'object' || Array.isArray(config)) return empty
    inner = config as Record<string, unknown>
  } catch {
    return empty
  }
  const loginKeys = new Set<string>(CONFIG_LOGIN_KEYS)
  const loginKeysPresent: string[] = []
  const asciiValues: Record<string, string> = {}
  let total = 0
  for (const [key, value] of Object.entries(inner)) {
    if (key === '__proto__') continue
    total++
    if (loginKeys.has(key)) loginKeysPresent.push(key)
    if (typeof value !== 'string' || value.length === 0 || value.length > ASCII_VALUE_MAX_CHARS) continue
    if (/[^\x20-\x7e]/.test(value)) continue // 含非可见 ASCII（GBK 乱码/控制字符）→ 不收
    asciiValues[key] = value
  }
  return { parsed: true, total_keys: total, login_keys_present: loginKeysPresent, ascii_values: asciiValues }
}

/** 读 Config.cfg 并解析（latin-1 字节层）；读失败 → null（config_found=false 场景不进这里） */
function readConfigSummary(dir: string): ConfigCfgSummary | null {
  try {
    return parseConfigCfg(readFileSync(join(dir, 'Config.cfg'), 'latin1'))
  } catch {
    return null
  }
}

/** 目录 mtime（毫秒）；不存在/无权限 → undefined（对齐 version.ts dirMtimeMs 语义） */
function dirMtimeMs(dir: string): number | undefined {
  try {
    return statSync(dir).mtimeMs
  } catch {
    return undefined
  }
}

/** 纯数字目录段校验（学 running.ts/matrix.ts 严格校验：不 parseInt 松解析） */
function isNumericId(name: string): boolean {
  return /^\d+$/.test(name)
}

/** 账号 id 排序键：id 均 ≤ 2^53 内的整数形态，数值比较；异常大值退化为字符串比较仍稳定 */
function compareUserId(a: string, b: string): number {
  const na = Number(a)
  const nb = Number(b)
  if (Number.isSafeInteger(na) && Number.isSafeInteger(nb)) return na - nb
  return a < b ? -1 : a > b ? 1 : 0
}

/** 枚举根下形如纯数字 id 的账号目录（非数字目录跳过；readdir 失败 → 空数组） */
function listAccountDirs(root: string): string[] {
  try {
    return readdirSync(root, { withFileTypes: true })
      .filter((e) => e.isDirectory() && isNumericId(e.name))
      .map((e) => e.name)
      .sort(compareUserId)
  } catch {
    return []
  }
}

/** 候选根是否命中：存在至少一个含 Data\message.db 的数字账号目录（设计明确的命中判据） */
function rootHasDataAccount(root: string): boolean {
  return listAccountDirs(root).some((id) => existsSync(join(root, id, 'Data', 'message.db')))
}

/** 由 Config.cfg 概况提取账号级证据（login_company_id 数字串、has_ever_login 短 ASCII 值） */
function extractAccountConfigEvidence(summary: ConfigCfgSummary): { login_company_id?: string; has_ever_login?: string } {
  const evidence: { login_company_id?: string; has_ever_login?: string } = {}
  const companyId = summary.ascii_values['LoginCompanyId']
  if (companyId !== undefined && /^\d+$/.test(companyId)) evidence.login_company_id = companyId
  const hasEver = summary.ascii_values['HasEverLoginKey']
  if (hasEver !== undefined) evidence.has_ever_login = hasEver
  return evidence
}

/** 组装单个账号概况（目录存在已由调用方保证；Config.cfg 概况/mtime best-effort） */
function buildAccount(root: string, userId: string): DataDirAccount {
  const dir = join(root, userId)
  const configSummary = readConfigSummary(dir)
  const account: DataDirAccount = {
    user_id: userId,
    dir,
    has_data_db: existsSync(join(dir, 'Data', 'message.db')),
    config_found: configSummary !== null,
  }
  if (configSummary !== null && configSummary.parsed) {
    const evidence = extractAccountConfigEvidence(configSummary)
    if (evidence.login_company_id !== undefined) account.login_company_id = evidence.login_company_id
    if (evidence.has_ever_login !== undefined) account.has_ever_login = evidence.has_ever_login
  }
  const mtime = dirMtimeMs(dir)
  if (mtime !== undefined) account.dir_mtime_ms = mtime
  return account
}

/** 选中账号的四库存在性核验（不存在落 null，不猜） */
function verifyDbPaths(dir: string): DataDirInfo['db_paths'] {
  const dbPath = (name: string): string | null => {
    const p = join(dir, 'Data', name)
    return existsSync(p) ? p : null
  }
  return {
    message: dbPath('message.db'),
    session: dbPath('session.db'),
    user: dbPath('user.db'),
    message_lookup: dbPath('message_lookup.db'),
  }
}

/** Config.cfg 不影响直接证据，缺失或无法解析仍可以确认活进程持有的目录。 */
function buildIdentity(dir: string, userId: string, processPid: number | null): DataDirIdentity {
  const summary = readConfigSummary(dir)
  const companyId = summary?.ascii_values['LoginCompanyId']
  const confirmed = processPid !== null
  return {
    config_found: summary !== null,
    login_company_id: companyId !== undefined && /^\d+$/.test(companyId) ? companyId : null,
    cross_check: confirmed ? 'process-handle-match' : summary === null ? 'config-missing'
      : Object.values(summary.ascii_values).includes(userId) ? 'id-present-in-config' : 'no-id-key',
    identity_confirmed: confirmed,
    reason: confirmed ? null : 'active-account-unavailable',
    process_pid: processPid,
  }
}

/** 直接证据优先；其余来源只定位候选，不凭历史日志或 mtime 确认身份。 */
export async function locateDataDir(deps: LocateDataDirDeps = {}): Promise<DataDirInfo> {
  const allNull: DataDirInfo = {
    source: null,
    root: null,
    user_id: null,
    dir: null,
    db_paths: null,
    accounts: [],
    identity: {
      config_found: false,
      login_company_id: null,
      cross_check: null,
      identity_confirmed: false,
      reason: null,
      process_pid: null,
    },
  }
  try {
    const appData = deps.appData ?? process.env.APPDATA
    const cefLogDir =
      deps.cefLogDir !== undefined ? deps.cefLogDir : appData ? join(appData, ...DEFAULT_CEF_LOG_DIR_SEGMENTS) : null
    const candidates =
      deps.candidateRoots ??
      defaultCandidateRoots(deps.userProfile ?? process.env.USERPROFILE, deps.oneDrive ?? process.env.OneDrive)

    // ① 操作主窗口进程当前持有的唯一消息库（调用方已完成 PID 与枚举核验）
    let root: string | null = null
    let selectedUserId: string | null = null
    let source: DataDirSource | null = null
    let cefHits: CefLogHitWithSource[] = []
    const proof = deps.activeAccount
    let processPid: number | null = null
    if (proof && Number.isInteger(proof.pid) && proof.pid > 0) {
      const match = /^([A-Za-z]:\\.*)\\(\d+)\\Data\\message\.db$/i.exec(win32.normalize(proof.message_db))
      if (match && existsSync(proof.message_db)) {
        root = match[1]!
        selectedUserId = match[2]!
        processPid = proof.pid
        source = 'process-handle'
      }
    }
    // ② CEF 候选统一按事件时刻选取；文件起始时间不代表其中的最后事件。
    if (root === null && cefLogDir) {
      cefHits = collectCefHits(cefLogDir)
      for (let i = cefHits.length - 1; i >= 0; i--) {
        const hit = cefHits[i]!
        if (existsSync(join(hit.root, hit.user_id))) {
          root = hit.root
          selectedUserId = hit.user_id
          source = 'cef-log'
          break
        }
      }
    }

    // ② Documents/OneDrive 候选根回退
    if (root === null) {
      for (const candidate of candidates) {
        if (rootHasDataAccount(candidate.root)) {
          root = candidate.root
          source = candidate.source
          break
        }
      }
    }
    if (root === null || source === null) return allNull
    const hitRoot = root // 收窄后的选中根（后续闭包内使用，避免非空断言散落）

    // ③ 多账号枚举：CEF 命中账号排第一，其余按 id 升序
    const accountIds = listAccountDirs(hitRoot)
    if (selectedUserId !== null && !accountIds.includes(selectedUserId)) accountIds.unshift(selectedUserId)
    const orderedIds =
      selectedUserId !== null
        ? [selectedUserId, ...accountIds.filter((id) => id !== selectedUserId)]
        : [...accountIds].sort((a, b) => {
            // 无 CEF 依据：按目录 mtime 新→旧选 selected（最近活跃启发式）；mtime 不可得按 id 升序
            const ma = dirMtimeMs(join(hitRoot, a)) ?? -1
            const mb = dirMtimeMs(join(hitRoot, b)) ?? -1
            if (ma !== mb) return mb - ma
            return compareUserId(a, b)
          })
    const accounts = orderedIds.map((id) => buildAccount(hitRoot, id))
    const selected = accounts[0] ?? null
    if (selected === null) return allNull

    return {
      source,
      root: hitRoot,
      user_id: selected.user_id,
      dir: selected.dir,
      db_paths: verifyDbPaths(selected.dir),
      accounts,
      identity: buildIdentity(selected.dir, selected.user_id, processPid),
    }
  } catch {
    return allNull
  }
}
