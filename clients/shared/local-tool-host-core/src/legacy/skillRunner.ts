/**
 * skill-runner 通用执行器 Provider（M2，进程内 handler，非 MCP stdio）。
 *
 * 计划 docs/plans/plan-external-skill-plugin-m2.md §4.3.3：
 * - 目录发现：skills dir 一级子目录 SKILL.md 轻量 frontmatter 子集解析（自研，不引入
 *   yaml 依赖；解析不了即 fail-closed 拒执行）；hash 镜像云端 compute_skill_exec_hash
 *   算法（排序相对路径 + 每文件 sha256 + \x00 分隔聚合，排除 __pycache__/shots/log/
 *   *.pyc/.DS_Store + metadata.mutable 声明路径）；
 * - 命令门禁（逐项 fail-closed）：payload schema → skill 已安装 → exec_hash 对账 →
 *   entry ∈ 本地已验证 SKILL.md metadata.entry 白名单 → entry ∉ mutable → entry
 *   resolve 后必须落在 skill 目录内 → args 数量/长度上限 → timeout 截顶；
 * - 执行：spawn(解释器, [entryAbs, ...args], { shell: false, stdin: 'ignore' }) argv
 *   直传，绝不 shell 拼接；解释器路径只来自设备本地 config（云端不可下发）；
 * - 桌面锁：skill_script_run 归 write_tools，锁屏前置/崩溃 EXECUTION_UNKNOWN/
 *   与三 CLI 同互斥域由 invocationRunner 既有链路承担（manifest 声明）。
 *
 * 安全论证（计划 §3.6）：exec_hash 覆盖除 mutable 外全部文件（含 SKILL.md——SKILL.md
 * 不可入 mutable，设备侧同样强制）；本地 SKILL.md 通过 exec_hash 验证后，其
 * metadata.entry/mutable 即为可信审批快照（审批 entries 从 SKILL.md 快照而来，
 * entries ∩ mutable = ∅ 保证入口脚本恒受 hash 锁定），payload.entry 仅与该集对账。
 */
import { spawn, spawnSync, type SpawnOptions } from 'node:child_process'
import { createHash } from 'node:crypto'
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs'
import path from 'node:path'
import { ProviderBusyError, type ProviderCallOptions } from './providerManager.js'
import { logError, logInfo } from './log.js'

export const SKILL_RUNNER_PROVIDER_KEY = 'skill-runner'
export const SKILL_RUNNER_TOOL_NAME = 'skill_script_run'

/** 参数上限（计划 §3.1：云端入队前 + 设备执行前双端强制，取值一致） */
export const SKILL_ARGS_LIMITS = { maxArgs: 16, maxArgChars: 500, maxTotalChars: 4000 } as const
/** 设备执行超时硬顶（秒）：取 min(云端下发值, 硬顶)，双端各自强制 */
export const SKILL_TIMEOUT_HARD_CAP_SECONDS = 1800
/** 云端未下发 timeout_seconds 时的缺省（对齐云端 SkillDeviceExecutionConfig 默认 900） */
export const SKILL_TIMEOUT_DEFAULT_SECONDS = 900
/** stdout/stderr 累计字符上限（保尾部） */
export const SKILL_STDOUT_LIMIT_CHARS = 200_000
export const SKILL_STDERR_LIMIT_CHARS = 64_000
/** capabilities skills 清单上报上限（计划 §3.2：≤50 条，超出截断 + 告警） */
export const SKILL_CAPABILITY_LIST_LIMIT = 50

// ---------------------------------------------------------------------------
// frontmatter 子集解析（≤100 行自研；解析不了返回 null = fail-closed）
// ---------------------------------------------------------------------------

export interface ParsedSkillFrontmatter {
  readonly name: string
  readonly version?: string
  /** 入口白名单（metadata.entry 优先，平铺 entry 兜底；string 归一为单元素数组） */
  readonly entry: readonly string[]
  readonly mutable: readonly string[]
}

/** 块标量指示（内容行缩进更深且无键结构，按未知键子块跳过） */
function isBlockScalarIndicator(value: string): boolean {
  return value === '|' || value === '>' || value === '|-' || value === '>-' || value === '|+' || value === '>+'
}

/** 剥离成对引号；空串返回 null（YAML 引号内不再解释） */
function unquote(value: string): string | null {
  const v = value.trim()
  if (v === '') return null
  if ((v.startsWith('"') && v.endsWith('"') && v.length >= 2) || (v.startsWith("'") && v.endsWith("'") && v.length >= 2)) {
    return v.slice(1, -1)
  }
  return v
}

/** flow 列表解析：[a, b] → [a, b]；非法返回 null */
function parseFlowList(value: string): string[] | null {
  const v = value.trim()
  if (!(v.startsWith('[') && v.endsWith(']'))) return null
  const inner = v.slice(1, -1).trim()
  if (inner === '') return []
  const items: string[] = []
  for (const part of inner.split(',')) {
    const item = unquote(part)
    if (item === null) return null
    items.push(item)
  }
  return items
}

/**
 * SKILL.md frontmatter 子集解析：name/version 平铺键 + entry/mutable（平铺或
 * metadata 一层嵌套），标量 / flow 列表 / block 列表（- item）。
 * 未知键的子块（缩进更深）整体跳过（真实 SKILL.md 常含 description/references 等）；
 * 已知键路径上超出子集的结构（嵌套 map、无法识别的行）返回 null（fail-closed）。
 */
export function parseSkillFrontmatter(text: string): ParsedSkillFrontmatter | null {
  const lines = text.split(/\r?\n/)
  if (lines[0]?.trim() !== '---') return null
  let end = -1
  for (let i = 1; i < lines.length; i++) {
    if (lines[i]!.trim() === '---') {
      end = i
      break
    }
  }
  if (end < 0) return null

  let name: string | null = null
  let version: string | undefined
  let rootEntry: string[] | null = null
  let rootMutable: string[] | null = null
  let metaEntry: string[] | null = null
  let metaMutable: string[] | null = null
  let inMetadata = false
  let pendingList: { kind: 'entry' | 'mutable'; section: 'root' | 'metadata' } | null = null
  let skipDeeperThan = -1 // 未知键子块跳过中：缩进 > 该值的行一律跳过

  type PendingList = { kind: 'entry' | 'mutable'; section: 'root' | 'metadata' }

  /** entry/mutable 值归一：空 = block 列表开始；[..] = flow 列表；否则单标量。返回 ok 与列表开始描述 */
  const parseEntryValue = (holder: 'root' | 'metadata', key: 'entry' | 'mutable', value: string): { ok: boolean; pending: PendingList | null } => {
    const assign = (list: string[] | null): void => {
      if (holder === 'root') {
        if (key === 'entry') rootEntry = list
        else rootMutable = list
      } else if (key === 'entry') metaEntry = list
      else metaMutable = list
    }
    if (value === '') {
      assign([])
      return { ok: true, pending: { kind: key, section: holder } }
    }
    if (value.startsWith('[')) {
      const list = parseFlowList(value)
      if (!list) return { ok: false, pending: null }
      assign(list)
      return { ok: true, pending: null }
    }
    const v = unquote(value)
    if (v === null) return { ok: false, pending: null }
    assign([v])
    return { ok: true, pending: null }
  }

  for (let i = 1; i < end; i++) {
    const raw = lines[i]!
    if (raw.trim() === '' || raw.trimStart().startsWith('#')) continue
    const indent = raw.length - raw.trimStart().length
    if (skipDeeperThan >= 0) {
      if (indent > skipDeeperThan) continue
      skipDeeperThan = -1 // 回到结构层，正常处理本行
    }
    const item = raw.match(/^\s*-\s+(.+?)\s*$/)
    if (item) {
      const pending = pendingList as PendingList | null
      if (!pending) return null // 已知键路径外的裸列表项 → fail-closed
      const v = unquote(item[1]!)
      if (v === null) return null
      const target = (pending.section === 'metadata'
        ? (pending.kind === 'entry' ? metaEntry : metaMutable)
        : (pending.kind === 'entry' ? rootEntry : rootMutable)) as string[] | null
      target?.push(v)
      continue
    }
    const kv = raw.match(/^(\s*)([A-Za-z0-9_-]+):\s*(.*)$/)
    if (!kv) return null
    const key = kv[2]!
    const value = kv[3]!.trim()
    pendingList = null

    if (indent === 0) {
      inMetadata = false
      if (key === 'metadata') {
        if (value !== '') return null // flow map（metadata: {...}）不在子集内
        inMetadata = true
      } else if (key === 'name') {
        const v = unquote(value)
        if (v === null) return null
        name = v
      } else if (key === 'version') {
        version = unquote(value) ?? undefined
      } else if (key === 'entry' || key === 'mutable') {
        const r = parseEntryValue('root', key, value)
        if (!r.ok) return null
        pendingList = r.pending
      } else {
        // 未知键：内联值直接跳过；空值/块标量（块开始）跳过其全部缩进子块
        skipDeeperThan = value === '' || isBlockScalarIndicator(value) ? 0 : -1
      }
      continue
    }
    if (inMetadata && indent === 2 && (key === 'entry' || key === 'mutable')) {
      const r = parseEntryValue('metadata', key, value)
      if (!r.ok) return null
      pendingList = r.pending
      continue
    }
    if (inMetadata && indent === 2) {
      // metadata 内未知键：内联值跳过；块开始则跳过其子块
      skipDeeperThan = value === '' || isBlockScalarIndicator(value) ? 2 : -1
      continue
    }
    return null // 无法建模的缩进结构（如根级缩进键/更深嵌套）→ fail-closed
  }
  if (!name) return null
  const entry = metaEntry ?? rootEntry
  const mutable = metaMutable ?? rootMutable
  return { name, version, entry: entry ?? [], mutable: mutable ?? [] }
}

// ---------------------------------------------------------------------------
// 目录 hash 镜像（与云端 skill_plugin_gate.compute_skill_dir_hash 同算法 +
// metadata.mutable 排除 = compute_skill_exec_hash；两侧共享测试向量防漂移）
// ---------------------------------------------------------------------------

const EXCLUDED_DIR_NAMES = new Set(['__pycache__', 'shots', 'log'])
const EXCLUDED_FILE_NAMES = new Set(['.DS_Store'])

interface HashableFile {
  readonly rel: string
  readonly abs: string
}

function collectHashableFiles(root: string): HashableFile[] {
  const out: HashableFile[] = []
  const walk = (dir: string, parts: string[]): void => {
    for (const e of readdirSync(dir, { withFileTypes: true })) {
      if (EXCLUDED_DIR_NAMES.has(e.name)) continue
      if (e.isDirectory()) walk(path.join(dir, e.name), [...parts, e.name])
      else if (e.isFile() && !EXCLUDED_FILE_NAMES.has(e.name) && !e.name.endsWith('.pyc')) {
        out.push({ rel: [...parts, e.name].join('/'), abs: path.join(dir, e.name) })
      }
    }
  }
  walk(root, [])
  out.sort((a, b) => (a.rel < b.rel ? -1 : a.rel > b.rel ? 1 : 0))
  return out
}

/** skill 目录 hash：可 hash 文件（排除集 + mutable）按相对路径排序，rel + \0 + 文件 sha256 hex + \0 聚合 */
export function skillDirHash(dir: string, mutable: readonly string[] = []): string {
  const mutableSet = new Set(mutable)
  const digest = createHash('sha256')
  for (const f of collectHashableFiles(dir)) {
    if (mutableSet.has(f.rel)) continue
    const fileHash = createHash('sha256').update(readFileSync(f.abs)).digest('hex')
    digest.update(f.rel, 'utf8')
    digest.update('\x00', 'utf8')
    digest.update(fileHash, 'utf8')
    digest.update('\x00', 'utf8')
  }
  return digest.digest('hex')
}

// ---------------------------------------------------------------------------
// 目录发现（mtime/size 快照缓存：目录未变更不重算 hash / 重新解析）
// ---------------------------------------------------------------------------

export interface SkillDirInfo {
  readonly dir: string
  readonly name: string
  readonly version?: string
  readonly entry: readonly string[]
  readonly mutable: readonly string[]
  readonly execHash: string
}

export interface InstalledSkill {
  readonly name: string
  readonly hash: string
}

const dirCache = new Map<string, { snapshot: string; info: SkillDirInfo | null }>()

/** 单个 skill 目录加载（带快照缓存）：目录缺失/解析失败/SKILL.md 入 mutable → null（fail-closed） */
export function loadSkillDir(skillDir: string): SkillDirInfo | null {
  const abs = path.resolve(skillDir)
  let files: HashableFile[]
  try {
    files = collectHashableFiles(abs)
  } catch {
    return null
  }
  const snapshotParts: string[] = []
  try {
    for (const f of files) {
      const st = statSync(f.abs)
      snapshotParts.push(`${f.rel}:${st.mtimeMs}:${st.size}`)
    }
  } catch {
    return null
  }
  const snapshot = snapshotParts.join('\n')
  const cached = dirCache.get(abs)
  if (cached && cached.snapshot === snapshot) return cached.info
  let info: SkillDirInfo | null = null
  try {
    const skillMd = path.join(abs, 'SKILL.md')
    const fm = existsSync(skillMd) ? parseSkillFrontmatter(readFileSync(skillMd, 'utf8')) : null
    // SKILL.md 是 exec_hash 的信任锚（计划 §3.6：恒参与 hash、不可入 mutable），
    // 声明 SKILL.md ∈ mutable 视为门禁违规 → 整目录不可用（fail-closed）
    if (fm && !fm.mutable.includes('SKILL.md')) {
      info = {
        dir: abs,
        name: fm.name,
        version: fm.version,
        entry: fm.entry,
        mutable: fm.mutable,
        execHash: skillDirHash(abs, fm.mutable),
      }
    }
  } catch {
    info = null
  }
  dirCache.set(abs, { snapshot, info })
  return info
}

/** 按 frontmatter name 查找已安装 skill（skills dir 一级子目录扫描；条目缓存复用） */
export function findSkillByName(skillsDir: string, name: string): SkillDirInfo | null {
  const abs = path.resolve(skillsDir)
  let dirs
  try {
    dirs = readdirSync(abs, { withFileTypes: true })
  } catch {
    return null
  }
  for (const d of dirs) {
    if (!d.isDirectory() || d.name.startsWith('.')) continue
    const info = loadSkillDir(path.join(abs, d.name))
    if (info?.name === name) return info
  }
  return null
}

/** capabilities 上报清单：已安装 skills（name + exec_hash，≤上限截断 + 告警） */
export function discoverInstalledSkills(skillsDir: string, limit: number = SKILL_CAPABILITY_LIST_LIMIT): InstalledSkill[] {
  const abs = path.resolve(skillsDir)
  let dirs
  try {
    dirs = readdirSync(abs, { withFileTypes: true })
  } catch {
    return []
  }
  dirs.sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0))
  const out: InstalledSkill[] = []
  let truncated = false
  for (const d of dirs) {
    if (!d.isDirectory() || d.name.startsWith('.')) continue
    const info = loadSkillDir(path.join(abs, d.name))
    if (!info) {
      logError(`[skill-runner] 技能目录无法解析（SKILL.md 缺失/frontmatter 不合规），不上报: ${d.name}`)
      continue
    }
    if (out.length >= limit) {
      truncated = true
      break
    }
    out.push({ name: info.name, hash: info.execHash })
  }
  if (truncated) logError(`[skill-runner] 已安装技能超过 ${limit} 条，capabilities 清单截断（云端对账将视为未安装）`)
  return out
}

// ---------------------------------------------------------------------------
// 命令门禁 + 执行 handler
// ---------------------------------------------------------------------------

/** entry 相对路径合法性：非绝对、无 `..`/空段，且 resolve 后仍落在 skill 目录内 */
export function isEntryInsideDir(baseDir: string, entry: string): boolean {
  if (entry === '' || path.isAbsolute(entry)) return false
  const parts = entry.replace(/\\/g, '/').split('/')
  if (parts.some((p) => p === '' || p === '.' || p === '..')) return false
  const base = path.resolve(baseDir)
  const resolved = path.resolve(base, ...parts)
  const rel = path.relative(base, resolved)
  return rel !== '' && !rel.startsWith('..') && !path.isAbsolute(rel)
}

/** 进程树终止：win32 taskkill /T /F；posix 进程组 kill（spawn detached 起组） */
function killProcessTree(pid: number | undefined, platform: NodeJS.Platform = process.platform): void {
  if (pid === undefined) return
  if (platform === 'win32') {
    spawnSync('taskkill', ['/PID', String(pid), '/T', '/F'], { windowsHide: true })
    return
  }
  try {
    process.kill(-pid, 'SIGKILL')
  } catch {
    try {
      process.kill(pid, 'SIGKILL')
    } catch {
      // 已退出
    }
  }
}

export interface SkillRunnerHandlerOptions {
  /** skills 目录绝对路径（cli 解析默认值 <runtimeHome>/skills 后传入） */
  readonly skillsDir: string
  /** 受管 Python 解释器绝对路径（设备本地 config，云端不可下发） */
  readonly pythonPath: string
  readonly stdoutLimitChars?: number
  readonly stderrLimitChars?: number
}

interface GateFailure {
  readonly success: false
  readonly code: string
  readonly message: string
  readonly effect: 'none'
  readonly retryable: boolean
}

/** payload 门禁结果：通过时携带解析后的执行参数 */
type GateResult = GateFailure | { ok: true; info: SkillDirInfo; entry: string; args: string[]; timeoutSeconds: number }

/** skill_script_run payload（计划 §3.1；version 仅日志展示，不参与对账） */
export interface SkillScriptRunPayload {
  skill: string
  entry: string
  exec_hash: string
  args?: unknown[]
  version?: string
  timeout_seconds?: number
}

/** 命令门禁（纯逻辑，逐项 fail-closed；测试直调覆盖矩阵） */
export function gateSkillScriptRun(
  payload: unknown,
  skillsDir: string,
  defaults: { timeoutSeconds?: number } = {},
): GateResult {
  const fail = (code: string, message: string, retryable: boolean): GateFailure => ({
    success: false, code, message, effect: 'none', retryable,
  })
  const p = payload as Partial<SkillScriptRunPayload> | null
  if (!p || typeof p !== 'object') return fail('INVALID_SKILL_PAYLOAD', 'skill_script_run 参数缺失或不是对象', false)
  if (typeof p.skill !== 'string' || p.skill === '') {
    return fail('INVALID_SKILL_PAYLOAD', 'skill_script_run 参数非法：skill 必须为非空字符串', false)
  }
  if (typeof p.entry !== 'string' || p.entry === '') {
    return fail('INVALID_SKILL_PAYLOAD', 'skill_script_run 参数非法：entry 必须为非空字符串', false)
  }
  if (typeof p.exec_hash !== 'string' || p.exec_hash === '') {
    return fail('INVALID_SKILL_PAYLOAD', 'skill_script_run 参数非法：exec_hash 必须为非空字符串', false)
  }
  if (p.args !== undefined && (!Array.isArray(p.args) || p.args.some((a) => typeof a !== 'string'))) {
    return fail('INVALID_SKILL_PAYLOAD', 'skill_script_run 参数非法：args 必须为字符串数组', false)
  }
  if (p.timeout_seconds !== undefined && (typeof p.timeout_seconds !== 'number' || !Number.isFinite(p.timeout_seconds) || p.timeout_seconds <= 0)) {
    return fail('INVALID_SKILL_PAYLOAD', 'skill_script_run 参数非法：timeout_seconds 必须为正数', false)
  }

  const info = findSkillByName(skillsDir, p.skill)
  if (!info) {
    return fail('SKILL_NOT_INSTALLED', `技能 '${p.skill}' 未安装到本设备（或目录无法解析），请先在设备 skills 目录安装`, true)
  }
  if (info.execHash !== p.exec_hash) {
    return fail('SKILL_VERSION_MISMATCH', `设备上的技能 '${p.skill}' 内容与云端审批版本不一致（exec_hash 对账失败），请更新设备端技能或重新审批`, true)
  }
  // 入口白名单：本地已通过 exec_hash 验证的 SKILL.md metadata.entry（= 审批快照；
  // payload 携带的 entry 必须命中，云端审批 entries 的设备侧等价对账）
  if (!info.entry.includes(p.entry)) {
    return fail(
      'SKILL_ENTRY_NOT_ALLOWED',
      `入口 '${p.entry}' 不在技能 '${p.skill}' 的执行入口白名单内（合法入口：${info.entry.length > 0 ? info.entry.join('、') : '（无）'}）`,
      false,
    )
  }
  // entries ∩ mutable = ∅（计划 §3.6 安全闭环）：入口脚本是被执行代码，必须受 exec_hash 锁定
  if (info.mutable.includes(p.entry)) {
    return fail('SKILL_GATE_REJECTED', `入口 '${p.entry}' 被技能声明为 mutable 自学习路径，入口脚本不可豁免 hash 锁定，拒绝执行`, false)
  }
  if (!isEntryInsideDir(info.dir, p.entry)) {
    return fail('INVALID_ENTRY_PATH', `入口 '${p.entry}' 不是 skill 目录内合法相对路径（禁止绝对路径/越界），拒绝执行`, false)
  }
  const args = (p.args ?? []) as string[]
  if (args.length > SKILL_ARGS_LIMITS.maxArgs) {
    return fail('SKILL_ARGS_LIMIT_EXCEEDED', `参数数量超限（${args.length} > ${SKILL_ARGS_LIMITS.maxArgs}）`, false)
  }
  for (const a of args) {
    if (a.length > SKILL_ARGS_LIMITS.maxArgChars) {
      return fail('SKILL_ARGS_LIMIT_EXCEEDED', `单个参数超长（${a.length} > ${SKILL_ARGS_LIMITS.maxArgChars} 字符）`, false)
    }
  }
  const total = args.reduce((sum, a) => sum + a.length, 0)
  if (total > SKILL_ARGS_LIMITS.maxTotalChars) {
    return fail('SKILL_ARGS_LIMIT_EXCEEDED', `参数总长超限（${total} > ${SKILL_ARGS_LIMITS.maxTotalChars} 字符）`, false)
  }
  const timeoutSeconds = Math.min(p.timeout_seconds ?? defaults.timeoutSeconds ?? SKILL_TIMEOUT_DEFAULT_SECONDS, SKILL_TIMEOUT_HARD_CAP_SECONDS)
  return { ok: true, info, entry: p.entry, args, timeoutSeconds }
}

/** stdout 尾部 JSON effect 覆盖（合法枚举才生效；计划 §4.3.3） */
function overrideEffectFromStdout(stdout: string): 'applied' | 'none' | null {
  const lines = stdout.trimEnd().split('\n')
  const last = lines[lines.length - 1]?.trim()
  if (!last || !last.startsWith('{')) return null
  try {
    const parsed = JSON.parse(last) as { effect?: unknown }
    if (parsed && typeof parsed === 'object' && (parsed.effect === 'none' || parsed.effect === 'applied')) {
      return parsed.effect
    }
  } catch {
    // 非 JSON 尾行 → 不覆盖
  }
  return null
}

/** 进程内 skill-runner handler（接口对齐 ProviderManager.callTool + 单飞） */
export class SkillRunnerHandler {
  private busy = false

  constructor(private readonly opts: SkillRunnerHandlerOptions) {}

  async callTool(name: string, args: Record<string, unknown>, callOpts: ProviderCallOptions = {}): Promise<Record<string, unknown>> {
    if (this.busy) throw new ProviderBusyError('skill-runner 正在执行其他调用（单飞兜底）')
    this.busy = true
    try {
      return await this.run(name, args, callOpts)
    } finally {
      this.busy = false
    }
  }

  private async run(name: string, args: Record<string, unknown>, callOpts: ProviderCallOptions): Promise<Record<string, unknown>> {
    if (name !== SKILL_RUNNER_TOOL_NAME) {
      return { success: false, code: 'TOOL_NOT_ALLOWED', message: `工具 ${name} 不在 skill-runner 工具面（仅 ${SKILL_RUNNER_TOOL_NAME}）`, effect: 'none', retryable: false }
    }
    const gate = gateSkillScriptRun(args, this.opts.skillsDir)
    if (!('ok' in gate)) {
      logInfo(`[skill-runner] 门禁拒绝: ${gate.code} ${gate.message}`)
      return { ...gate }
    }
    const { info, entry, args: argv, timeoutSeconds } = gate
    const entryAbs = path.resolve(info.dir, ...entry.replace(/\\/g, '/').split('/'))
    const stdoutLimit = this.opts.stdoutLimitChars ?? SKILL_STDOUT_LIMIT_CHARS
    const stderrLimit = this.opts.stderrLimitChars ?? SKILL_STDERR_LIMIT_CHARS
    const startedAt = Date.now()
    logInfo(`[skill-runner] 执行技能 '${info.name}'${info.version ? ` v${info.version}` : ''} entry=${entry} args=[${argv.join(' ')}] timeout=${timeoutSeconds}s`)

    return await new Promise<Record<string, unknown>>((resolve) => {
      // argv 直传（shell:false 绝不拼接命令串）；stdio[0]='ignore'：读 stdin 的脚本
      // 立即 EOF 快速失败（§3.1 stdin_content 不透传，spawn stdin ignore）
      const spawnOpts: SpawnOptions = {
        cwd: info.dir,
        shell: false,
        windowsHide: true,
        stdio: ['ignore', 'pipe', 'pipe'],
        detached: process.platform !== 'win32', // posix 进程组：kill(-pid) 树杀
        env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' } as NodeJS.ProcessEnv,
      }
      const child = spawn(this.opts.pythonPath, [entryAbs, ...argv], spawnOpts)
      let stdout = ''
      let stderr = ''
      let pendingLine = ''
      let timedOut = false
      let externallyAborted = false
      let settled = false

      const finish = (result: Record<string, unknown>): void => {
        if (settled) return
        settled = true
        clearTimeout(timer)
        callOpts.signal?.removeEventListener('abort', onAbort)
        resolve(result)
      }
      const timer = setTimeout(() => {
        timedOut = true
        killProcessTree(child.pid)
      }, timeoutSeconds * 1000)
      const onAbort = (): void => {
        externallyAborted = true
        killProcessTree(child.pid)
      }
      callOpts.signal?.addEventListener('abort', onAbort, { once: true })

      child.on('error', (err: Error) => {
        // spawn 失败（解释器/入口不存在等）：进程未起，effect none
        finish({
          success: false,
          code: 'SKILL_SPAWN_FAILED',
          message: `技能脚本进程启动失败: ${err.message}`,
          effect: 'none',
          retryable: false,
          data: { exit_code: null, duration_ms: Date.now() - startedAt },
        })
      })
      child.stdout?.on('data', (chunk: Buffer) => {
        const text = chunk.toString('utf8')
        stdout = stdout.length + text.length > stdoutLimit ? (stdout + text).slice(-stdoutLimit) : stdout + text
        // 进度：stdout 按行转发（轻量：仅 message）
        pendingLine += text
        const lines = pendingLine.split('\n')
        pendingLine = lines.pop() ?? ''
        for (const line of lines) {
          if (line.trim() !== '') callOpts.onProgress?.({ message: line })
        }
      })
      child.stderr?.on('data', (chunk: Buffer) => {
        const text = chunk.toString('utf8')
        stderr = stderr.length + text.length > stderrLimit ? (stderr + text).slice(-stderrLimit) : stderr + text
      })
      child.on('close', (code: number | null, signal: NodeJS.Signals | null) => {
        const durationMs = Date.now() - startedAt
        const base = { stdout, stderr, exit_code: code, duration_ms: durationMs }
        if (pendingLine.trim() !== '') callOpts.onProgress?.({ message: pendingLine })
        if (timedOut) {
          finish({
            success: false,
            code: 'SKILL_TIMEOUT',
            message: `技能脚本执行超时（${timeoutSeconds}s），进程树已终止`,
            effect: 'unknown',
            retryable: false,
            data: base,
          })
          return
        }
        if (externallyAborted) {
          finish({
            success: false,
            code: 'SKILL_ABORTED',
            message: `技能脚本执行已被中止（signal=${signal ?? '-'}），进程树已终止`,
            effect: 'unknown',
            retryable: false,
            data: base,
          })
          return
        }
        if (code === 0) {
          const override = overrideEffectFromStdout(stdout)
          finish({
            success: true,
            effect: override ?? 'applied',
            retryable: false,
            data: { ...base, exit_code: 0 },
          })
          return
        }
        finish({
          success: false,
          code: 'SKILL_EXIT_NONZERO',
          message: `技能脚本退出码 ${code ?? 'null'}（signal=${signal ?? '-'}）`,
          effect: 'unknown', // RPA 可能已部分操作桌面：unknown + 禁自动重试（Provider 规范 §5.3）
          retryable: false,
          data: base,
        })
      })
    })
  }
}
