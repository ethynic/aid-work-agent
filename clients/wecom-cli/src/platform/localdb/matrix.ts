/**
 * C4 版本有效性矩阵加载/比对 + 本地动态状态读写（设计 §3.1、§3.2 C4；状态 shape 见 §2）。
 *
 * - version-matrix.json 是随代码发布的只读权威文件（exact-version 精确匹配，无前缀/通配；
 *   未命中一律 unknown——未知版本禁止硬扫，§3.1）；
 * - localdb-state.json 是本机动态状态（{mode, kind, reason, version, checked_at} + 附加字段，
 *   §2 通道模式），写入走「同目录临时文件 + rename 覆盖」原子路径；读取失败/JSON 损坏/
 *   缺 mode 一律返回 null（视为 unset，不抛出）；
 * - 全程 best-effort，对齐 environment.ts 注入式探测模式；path/dir/localAppData 均可注入。
 */
import { mkdirSync, readFileSync, renameSync, unlinkSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

/**
 * 默认矩阵路径解析顺序（loadMatrix 缺省时依序尝试，首个可读者生效，全部失败 → null）：
 * 1. 发行布局优先：dist/src/platform/localdb/version-matrix.json（与编译产物 matrix.js 同目录）。
 *    构建时由 scripts/copy-matrix.mjs 复制到位；打包器整拷 dist/src 即随包发布，发行包内可达；
 * 2. 仓库源码态回退：包根 src/platform/localdb/version-matrix.json——仓库内直接以源码运行时可用
 *   （与 manifest.ts 解析 provider-manifest.json、operations 的 DRIVER_PATH 同一约定）。
 * 两处均缺失 → loadMatrix 返回 null → lookup 全 unknown，兜底方向安全（版本不在矩阵 →
 * 引导走 UI，绝不硬扫，§3.1）。测试一律显式传 path/对象注入，不依赖该默认值。
 */
const MATRIX_PATH_CANDIDATES = [
  fileURLToPath(new URL('./version-matrix.json', import.meta.url)),
  fileURLToPath(new URL('../../../../src/platform/localdb/version-matrix.json', import.meta.url)),
]

/** 本地状态文件名（位于 localDbStateDir 目录下） */
export const STATE_FILE_NAME = 'localdb-state.json'

/**
 * 本模块唯一接受的矩阵 schema 版本（version-matrix.json 顶层 schema_version）。
 * 不匹配（含缺失/更高版本）→ loadMatrix 返回 null，调用方按全 unknown 处理（保守禁扫）。
 */
export const SUPPORTED_SCHEMA_VERSION = 1

/**
 * status='validated' 条目必须齐备且取值合法的字段（扫描分派 C6 依赖的决策输入，以
 * version-matrix.json 实际字段为准）：
 * - 下述字段必须为非空字符串；
 * - page_size（PAGE_SIZE_FIELD）必须为正整数。
 * validated 但任一字段缺失/类型不符/值非法 → 降级 status='unknown'（保守禁扫），
 * 条目本身保留供诊断。
 */
const REQUIRED_VALIDATED_STRING_FIELDS = [
  'algorithm',
  'db_magic',
  'key_form',
  'scan_strategy',
  'data_dir_pattern',
  'schema_profile',
] as const
/** page_size 单独按正整数校验（0/负数/非整数都无法支撑按页扫描决策） */
const PAGE_SIZE_FIELD = 'page_size'

/** 矩阵条目 status 语义（§3.1：validated=已验证可用；stale=待复核；broken=确认不可用；unknown=未知） */
export type MatrixEntryStatus = 'validated' | 'stale' | 'broken' | 'unknown'

/** 矩阵条目：字段随实验版 schema 透传（algorithm/page_size/db_magic/…），不在此强校验 */
export interface MatrixEntry {
  status?: unknown
  [key: string]: unknown
}

export interface VersionMatrix {
  schema_version?: number
  updated_at?: string
  entries: Record<string, MatrixEntry>
}

export interface VersionLookupResult {
  entry: MatrixEntry | null
  status: MatrixEntryStatus
}

/**
 * 本机动态状态（§2 通道模式）：mode 表达允许采用的路径；kind/reason 表达临时不可用分类。
 * kind 仅 mode='ui' 时有意义（'compat'/'transient'）；读回时非 ui 模式的 kind 会被丢弃归一，
 * 返回结果中不会出现 null kind。
 */
export interface LocalDbState {
  mode: 'db' | 'ui' | 'unset'
  kind?: 'compat' | 'transient'
  reason?: string
  version?: string
  checked_at?: string
  /** 允许附加字段（负缓存、计数等后续扩展，§2） */
  [key: string]: unknown
}

/**
 * 归一化任意输入为 VersionMatrix；非对象形态 → null。
 * entries 缺失/非对象归一为空表（矩阵合法但无条目 ≠ 文件损坏，语义分开）。
 * 契约校验：status='validated' 条目必需字段缺失或类型/值非法 → 降级 status='unknown'
 * （保守禁扫，条目保留供诊断）；schema_version 门槛在 loadMatrix 统一判定。
 */
function normalizeMatrix(raw: unknown): VersionMatrix | null {
  if (raw === null || typeof raw !== 'object' || Array.isArray(raw)) return null
  const obj = raw as Record<string, unknown>
  const entries: Record<string, MatrixEntry> = {}
  const entriesRaw = obj.entries
  if (entriesRaw !== null && typeof entriesRaw === 'object' && !Array.isArray(entriesRaw)) {
    for (const [key, value] of Object.entries(entriesRaw)) {
      if (key === '__proto__') continue // JSON 的 __proto__ 自有键经赋值会改写对象原型而非存储条目，跳过
      if (value !== null && typeof value === 'object' && !Array.isArray(value)) {
        entries[key] = downgradeIncompleteValidated(value as MatrixEntry)
      }
    }
  }
  return {
    ...(typeof obj.schema_version === 'number' ? { schema_version: obj.schema_version } : {}),
    ...(typeof obj.updated_at === 'string' ? { updated_at: obj.updated_at } : {}),
    entries,
  }
}

/** validated 条目必需字段缺失或类型/值非法 → 降级 unknown（条目保留，供诊断）；其余条目原样透传 */
function downgradeIncompleteValidated(entry: MatrixEntry): MatrixEntry {
  if (entry.status !== 'validated') return entry
  for (const field of REQUIRED_VALIDATED_STRING_FIELDS) {
    const v = entry[field]
    if (typeof v !== 'string' || v.length === 0) return { ...entry, status: 'unknown' }
  }
  const pageSize = entry[PAGE_SIZE_FIELD]
  if (typeof pageSize !== 'number' || !Number.isInteger(pageSize) || pageSize <= 0) {
    return { ...entry, status: 'unknown' }
  }
  return entry
}

/**
 * 加载版本矩阵（同步）。source 三态：
 * - 缺省 → 依序读 MATRIX_PATH_CANDIDATES（发行布局 dist 位置优先，仓库 src 位置回退）；
 * - 字符串 → 显式文件路径（测试/维护工具注入）；
 * - 对象 → 已解析矩阵直接归一（测试注入）。
 * 文件缺失/读失败/JSON 损坏/非对象 → null，不抛出。
 * schema 契约：schema_version 必须 === SUPPORTED_SCHEMA_VERSION（含缺失/更高版本 → null，
 * 调用方按全 unknown 处理）；validated 条目缺必需字段或类型/值非法在 normalizeMatrix 降级为 unknown。
 */
export function loadMatrix(source?: string | unknown): VersionMatrix | null {
  try {
    let raw: unknown
    if (typeof source === 'string') {
      raw = JSON.parse(readFileSync(source, 'utf8'))
    } else if (source === undefined) {
      raw = readDefaultMatrixRaw()
    } else {
      raw = source
    }
    const matrix = normalizeMatrix(raw)
    if (matrix === null || matrix.schema_version !== SUPPORTED_SCHEMA_VERSION) return null
    return matrix
  } catch {
    return null
  }
}

/** 依序尝试候选路径读原始 JSON 文本并解析；全部不可读/损坏 → 抛出（由 loadMatrix 捕获落 null） */
function readDefaultMatrixRaw(): unknown {
  let lastError: unknown = new Error('version-matrix.json not found in any candidate path')
  for (const candidate of MATRIX_PATH_CANDIDATES) {
    try {
      return JSON.parse(readFileSync(candidate, 'utf8'))
    } catch (err) {
      lastError = err
    }
  }
  throw lastError
}

/**
 * 精确字符串匹配查版本（exact-version 矩阵，无前缀/无通配/不 trim——严格匹配，
 * 防止把 "5.0.11" 误当 "5.0.11.6029" 的前缀命中；只认自有属性，原型键不算条目）。
 * 命中条目透传条目自身 status；
 * status 字段缺失/非法值归并 unknown（防御性）；未命中/矩阵为 null → {entry:null, status:'unknown'}。
 */
export function lookupVersion(matrix: VersionMatrix | null, version: string | null | undefined): VersionLookupResult {
  if (matrix === null || typeof version !== 'string' || version.length === 0) {
    return { entry: null, status: 'unknown' }
  }
  const entry = Object.hasOwn(matrix.entries, version) ? matrix.entries[version] : null
  if (!entry) return { entry: null, status: 'unknown' }
  const status = entry.status
  if (status === 'validated' || status === 'stale' || status === 'broken') {
    return { entry, status }
  }
  return { entry, status: 'unknown' }
}

/**
 * 本地状态目录：%LOCALAPPDATA%\AidWorkAgent\wecom-cli\localdb（对齐 environment.ts artifactDir 风格）。
 * 参数三态：缺省 → 读环境变量；显式字符串 → 注入根；显式 null → 模拟 LOCALAPPDATA 缺失 → null。
 */
export function localDbStateDir(localAppData?: string | null): string | null {
  const base = localAppData === undefined ? process.env.LOCALAPPDATA : localAppData
  if (!base) return null
  return join(base, 'AidWorkAgent', 'wecom-cli', 'localdb')
}

/**
 * 本地动态状态合法取值（§2 通道模式）：
 * - mode：通道决策核心字段，只接受 'db'/'ui'/'unset'；
 * - kind：临时不可用分类，仅 mode='ui' 时有意义，只接受 'compat'/'transient'。
 */
const VALID_STATE_MODES = new Set(['db', 'ui', 'unset'])
const VALID_STATE_KINDS = new Set(['compat', 'transient'])

/**
 * 读取本地动态状态。dir 缺省 → localDbStateDir()（环境变量定位）；
 * 目录不可定位/文件缺失/JSON 损坏/非对象 → null。
 * 契约校验（统一「非法 → null，视为 unset」语义，不抛出）：
 * - mode 非法（含缺失、非字符串、不在取值集）→ null——调用方按 unset 处理，不猜；
 * - mode='ui' 时 kind 必须为 'compat'/'transient'：缺省/null/其他值 → null（整体视为损坏）。
 *   ui 态缺 kind 就没有临时不可用分类语义，宁缺毋滥，不猜默认分类；
 * - mode='db'/'unset' 时 kind 仅是历史噪声：带值（含 null/非法值）→ 归一为缺省（丢弃 kind），
 *   防止下游把无关字段误读为分类；返回结果中不会出现 null kind。
 * 扩展字段（负缓存、计数等）不做校验、原样保留。
 */
export function readLocalState(dir?: string | null): LocalDbState | null {
  const d = dir === undefined ? localDbStateDir() : dir
  if (!d) return null
  try {
    const parsed: unknown = JSON.parse(readFileSync(join(d, STATE_FILE_NAME), 'utf8'))
    if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) return null
    const obj = parsed as Record<string, unknown>
    if (typeof obj.mode !== 'string' || !VALID_STATE_MODES.has(obj.mode)) return null // mode 非法/缺失 → 视为损坏（unset）
    if (obj.mode === 'ui') {
      if (typeof obj.kind !== 'string' || !VALID_STATE_KINDS.has(obj.kind)) {
        return null // ui 态必须带合法 kind：缺省/null/非法 → 整体视为损坏，不部分采纳
      }
      return obj as LocalDbState
    }
    if (obj.kind !== undefined) {
      // kind 仅 ui 模式有意义：db/unset 态带值（含 null）→ 归一为缺省（丢弃）
      const normalized: Record<string, unknown> = { ...obj }
      delete normalized.kind
      return normalized as LocalDbState
    }
    return obj as LocalDbState
  } catch {
    return null
  }
}

/**
 * 原子写入本地动态状态：同目录写临时文件后 rename 覆盖（Windows 上 rename 走
 * MOVEFILE_REPLACE_EXISTING 语义，同卷替换为目标保证观察者只见完整新内容或旧内容）。
 * 不改写调用方数据（checked_at 由调用方负责盖戳）；目录不可定位/写入失败 → false，不抛出，
 * 失败时清理临时文件（清理失败再忽略——不能因清扫掩盖主错误）。
 */
export function writeLocalState(state: LocalDbState, dir?: string | null): boolean {
  const d = dir === undefined ? localDbStateDir() : dir
  if (!d) return false
  const target = join(d, STATE_FILE_NAME)
  const tmp = join(d, `.${STATE_FILE_NAME}.${process.pid}.${Date.now()}.tmp`)
  try {
    mkdirSync(d, { recursive: true })
    writeFileSync(tmp, `${JSON.stringify(state, null, 2)}\n`, 'utf8')
    renameSync(tmp, target)
    return true
  } catch {
    try {
      unlinkSync(tmp)
    } catch {
      // 临时文件清理失败忽略
    }
    return false
  }
}
