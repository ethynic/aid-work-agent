import { appendFileSync, closeSync, fstatSync, lstatSync, mkdirSync, openSync, readSync, renameSync, rmSync } from 'node:fs'
import path from 'node:path'
import { readLogFileHealth, resolveRuntimeHome, type RuntimeHomeProfile } from './diagnostics.js'

export type LogHours = 1 | 24 | 168
export type RuntimeLogLocation = 'active' | 'prior' | 'desktop'
export interface RuntimeDiagnosticFields {
  protocol?: 'http' | 'https'
  reason?: 'redirect' | 'dns' | 'tls' | 'network' | 'authorization' | 'server' | 'refused' | 'local'
  httpStatus?: number
}
export interface RuntimeLogEntry {
  at: string; source: 'runtime' | 'desktop'; level: 'info' | 'error'; event: string; summary: string
  invocationId?: string; provider?: string; tool?: string; durationMs?: number
  location?: RuntimeLogLocation; protocol?: RuntimeDiagnosticFields['protocol']; reason?: RuntimeDiagnosticFields['reason']; httpStatus?: number
}
export interface RuntimeLogSource {
  name: string; status: 'read' | 'missing' | 'unreadable' | 'linked'; bytesRead: number; totalBytes: number
  skippedLines: number; partialLines: number; truncated: boolean
  location?: RuntimeLogLocation; observedFrom?: string | null; observedTo?: string | null; lastWriteAt?: string | null; stale?: boolean
}
export interface RuntimeLogHealth {
  fileWriteFailures: number; lastFileWriteErrorAt: string | null; degraded: boolean; source: string | null; error: string | null
}
export interface RuntimeLogSnapshot {
  version: 1; generatedAt: string; hours: LogHours
  versions: { app: string | null; host: string | null; core: string | null }
  range: { from: string; to: string; observedFrom: string | null; observedTo: string | null }
  entries: RuntimeLogEntry[]; sources: RuntimeLogSource[]; health: RuntimeLogHealth | null; truncated: boolean
}
const RUNTIME_LOGIN_FAILURES = {
  'runtime-login-resume-failed': 'Runtime 自动启动失败',
  'runtime-login-handshake-failed': 'Runtime 管理握手失败',
  'runtime-login-state-read-failed': 'Runtime 自动启动时无法读取状态',
  'runtime-login-stop-proof-pending': 'Runtime 自动启动前执行停稳待核对',
  'runtime-login-host-blocked': 'Runtime 管理状态阻止自动启动',
  'runtime-login-device-revoked': '设备授权已失效，Runtime 未自动启动',
  'runtime-login-start-request-failed': 'Runtime 自动启动请求未获确认',
  'runtime-login-start-confirmation-failed': 'Runtime 自动启动操作失败或无法读取结果',
  'runtime-login-start-unconfirmed': 'Runtime 自动启动结果未确认',
} as const
export type RuntimeLoginDiagnosticEvent = keyof typeof RUNTIME_LOGIN_FAILURES
export const DESKTOP_DIAGNOSTIC_EVENTS: Readonly<Record<string, string>> = Object.freeze({
  'startup-failed': '客户端启动失败', 'startup-configuration-failed': '客户端配置读取失败',
  'api-configuration-loaded': '客户端配置已加载', 'runtime-host-initialization-failed': 'Runtime 初始化失败',
  'renderer-gone': '客户端页面进程退出', 'load-failed': '客户端页面加载失败',
  ...RUNTIME_LOGIN_FAILURES,
  'runtime-login-registration-failed': '自动启动设置失败',
  'runtime-pair-prepare-started': '正在提交配对准备', 'runtime-pair-prepare-succeeded': '服务端已接受配对准备', 'runtime-pair-prepare-failed': '配对准备失败',
  'runtime-pair-confirm-started': '正在确认配对', 'runtime-pair-confirm-succeeded': '配对已确认', 'runtime-pair-confirm-failed': '配对确认未完成',
  'runtime-pair-status-started': '正在只读检查配对结果', 'runtime-pair-status-failed': '配对状态检查失败',
  'runtime-pair-status-prepared': '配对仍在准备状态', 'runtime-pair-status-committed': '配对已提交',
  'runtime-pair-status-expired': '配对准备已过期', 'runtime-pair-status-rejected': '配对确认已拒绝',
  'runtime-pair-legacy-request-retired': '旧配对请求已移出活动入口',
  'runtime-diagnostic-heartbeat-started': '正在发送一次诊断心跳', 'runtime-diagnostic-heartbeat-succeeded': '诊断心跳成功',
  'runtime-diagnostic-heartbeat-failed': '诊断心跳失败',
  'runtime-update-intent-clear-failed': '更新启动状态清理失败', 'updater-disabled': '自动更新未启用',
  'window-state-save-failed': '窗口状态保存失败', 'activate-failed': '客户端窗口打开失败',
  'deep-link-rejected': '客户端链接未被接受', 'smoke-timeout': '客户端启动检查超时', 'smoke-failed': '客户端启动检查失败',
})
const MAX_FILE_BYTES = 512 * 1024
const MAX_ENTRIES = 500
const uuid = '[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'
const identifier = '[a-z][a-z0-9_-]{0,79}'
const started = new RegExp(`^invocation (${uuid}) 已开始执行 tool=(${identifier})$`)
const perf = new RegExp(`^\\[runtime-perf\\] invocation=(${uuid}) tool=(${identifier}) provider=(${identifier}) started_ms=(\\d+) lock_wait_ms=(\\d+) lock_prep_ms=(\\d+) provider_call_ms=(\\d+)$`)
const invocationFailure = new RegExp(`^invocation (${uuid}) (执行器异常|桌面资源锁等待超时): `)
const providerMissing = new RegExp(`^invocation (${uuid}) provider=(${identifier}) 未安装或未配置入口，拒绝执行$`)
const toolRejected = new RegExp(`^invocation (${uuid}) tool=(${identifier}) 不在受信 manifest，拒绝执行$`)
const simpleRuntime: Readonly<Record<string, string>> = {
  'Provider 进程已退出/stdio 断开': 'Provider 进程或通信已断开',
  'MCP transport error': 'Provider 通信发生错误',
  '云端连接已恢复（进度回传）': '云端连接已恢复', '收到云端取消请求，协作式中止': '收到取消请求',
  '云端连接中断（进度暂无法回传，本地继续执行）': '云端连接暂时中断，本地执行继续',
  '设备 token 已失效或被撤销，主循环停止。请重新 pair。': '设备授权已失效，需要重新连接',
}
const loopEvents: Readonly<Record<string, string>> = {
  'heartbeat-online': '运行心跳已建立', 'heartbeat-disconnected': '运行心跳失败，进入退避', 'heartbeat-recovered': '运行心跳已恢复',
  'claim-ready': '任务领取通道已建立', 'claim-disconnected': '任务领取请求失败，进入退避', 'claim-recovered': '任务领取通道已恢复',
}
const structuredDesktop = (event: string) => event.startsWith('runtime-pair-') || event.startsWith('runtime-diagnostic-heartbeat-')
function diagnosticFields(value: RuntimeDiagnosticFields): RuntimeDiagnosticFields {
  return {
    ...(value.protocol === 'http' || value.protocol === 'https' ? { protocol: value.protocol } : {}),
    ...(value.reason && ['redirect', 'dns', 'tls', 'network', 'authorization', 'server', 'refused', 'local'].includes(value.reason) ? { reason: value.reason } : {}),
    ...(Number.isInteger(value.httpStatus) && value.httpStatus! >= 300 && value.httpStatus! <= 599 ? { httpStatus: value.httpStatus } : {}),
  }
}

/** Fixed source files only; never follow a junction to another user's or connection's material. */
export function diagnosticPathLinked(file: string): boolean {
  for (let current = file; ; current = path.dirname(current)) {
    try { if (lstatSync(current).isSymbolicLink()) return true } catch { /* Missing paths are reported by the reader. */ }
    if (path.dirname(current) === current) return false
  }
}

/** Shell metadata only. Logging failure must never become a business or startup gate. */
export function recordDesktopDiagnostic(directory: string, event: string, now = Date.now(), details: RuntimeDiagnosticFields = {}): boolean {
  if (!Object.hasOwn(DESKTOP_DIAGNOSTIC_EVENTS, event) || diagnosticPathLinked(directory)) return false
  try {
    mkdirSync(directory, { recursive: true })
    const current = path.join(directory, 'desktop-diagnostics.jsonl'), old = path.join(directory, 'desktop-diagnostics.old.jsonl')
    if (diagnosticPathLinked(current) || diagnosticPathLinked(old)) return false
    try {
      if (lstatSync(current).size >= 1024 * 1024) {
        rmSync(old, { force: true }); renameSync(current, old)
      }
    } catch (error) { if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error }
    appendFileSync(current, JSON.stringify({ at: new Date(now).toISOString(), event, ...(structuredDesktop(event) ? diagnosticFields(details) : {}) }) + '\n')
    return true
  } catch { return false }
}

export function projectRuntimeLog(line: string): RuntimeLogEntry | null {
  const header = /^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z) \[runtime\]\[(INFO|ERROR)\] (.*)$/.exec(line)
  if (!header || !Number.isFinite(Date.parse(header[1]!))) return null
  const base = { at: header[1]!, source: 'runtime' as const, level: header[2] === 'ERROR' ? 'error' as const : 'info' as const }
  const message = header[3]!, begin = started.exec(message), timing = perf.exec(message)
  const legacyFailure = /^(心跳|领取任务)失败（(.*)），\d+ms 后重试$/.exec(message)
  const transition = /^\[runtime-connection\] ([a-z-]+)(?: http=([345]\d\d)| reason=(dns|tls|network))?$/.exec(message)
  const loop = transition?.[1] ?? (legacyFailure?.[1] === '心跳' ? 'heartbeat-disconnected' : legacyFailure ? 'claim-disconnected' : null)
  if (loop && Object.hasOwn(loopEvents, loop)) {
    const status = transition?.[2] ?? /^云端返回 HTTP ([345]\d\d)(?:$|: )/.exec(legacyFailure?.[2] ?? '')?.[1]
    return { ...base, level: loop.endsWith('-disconnected') ? 'error' : 'info', event: loop, summary: loopEvents[loop]!,
      ...(status ? { httpStatus: Number(status) } : {}), ...(transition?.[3] ? { reason: transition[3] as RuntimeDiagnosticFields['reason'] } : {}) }
  }
  if (begin) return { ...base, event: 'invocation-started', summary: '本地工具调用开始', invocationId: begin[1], tool: begin[2] }
  if (timing) return { ...base, event: 'invocation-timing', summary: '本地工具调用阶段耗时', invocationId: timing[1], tool: timing[2], provider: timing[3], durationMs: Number(timing[7]) }
  const failure = invocationFailure.exec(message), missing = providerMissing.exec(message), rejected = toolRejected.exec(message)
  if (failure) return { ...base, level: 'error', event: 'invocation-failed', summary: failure[2] === '执行器异常' ? '本地执行发生异常；原始错误文本未展示' : '等待桌面资源超时', invocationId: failure[1] }
  if (missing) return { ...base, level: 'error', event: 'provider-unavailable', summary: '调用需要的插件未安装或未配置', invocationId: missing[1], provider: missing[2] }
  if (rejected) return { ...base, level: 'error', event: 'tool-unavailable', summary: '客户端尚未支持此次工具调用', invocationId: rejected[1], tool: rejected[2] }
  if (/^MCP stdio 接收超出预算（\d+ bytes）$/.test(message)) return { ...base, level: 'error', event: 'provider-response-limit', summary: 'Provider 返回超过当前接收上限' }
  if (Object.hasOwn(simpleRuntime, message)) return { ...base, event: 'runtime-status', summary: simpleRuntime[message]! }
  return null
}

function projectDesktop(line: string): RuntimeLogEntry | null {
  let record: Record<string, unknown>
  try { record = JSON.parse(line) } catch { return null }
  if (!record || typeof record !== 'object' || typeof record.at !== 'string' || !Number.isFinite(Date.parse(record.at))
    || typeof record.event !== 'string' || !Object.hasOwn(DESKTOP_DIAGNOSTIC_EVENTS, record.event)) return null
  return { at: new Date(record.at).toISOString(), source: 'desktop', level: record.event.includes('failed') || record.event === 'renderer-gone' || Object.hasOwn(RUNTIME_LOGIN_FAILURES, record.event) ? 'error' : 'info', event: record.event, summary: DESKTOP_DIAGNOSTIC_EVENTS[record.event]!,
    ...(structuredDesktop(record.event) ? diagnosticFields(record as RuntimeDiagnosticFields) : {}) }
}

function emptySource(name: string, location: RuntimeLogLocation, status: RuntimeLogSource['status'] = 'missing'): RuntimeLogSource {
  return { name, location, status, bytesRead: 0, totalBytes: 0, skippedLines: 0, partialLines: 0, truncated: false,
    observedFrom: null, observedTo: null, lastWriteAt: null, stale: false }
}
function readSource(file: string, name: string, location: RuntimeLogLocation, project: (line: string) => RuntimeLogEntry | null, from: number, to: number): { source: RuntimeLogSource; entries: RuntimeLogEntry[] } {
  const source = emptySource(name, location)
  const entries: RuntimeLogEntry[] = []
  if (diagnosticPathLinked(file)) return { source: { ...source, status: 'linked' }, entries }
  let fd: number | undefined
  try {
    const stat = lstatSync(file)
    if (!stat.isFile()) return { source: { ...source, status: 'unreadable' }, entries }
    fd = openSync(file, 'r')
    if (diagnosticPathLinked(file)) return { source: { ...source, status: 'linked' }, entries }
    const opened = fstatSync(fd)
    if (!opened.isFile() || opened.dev !== stat.dev || opened.ino !== stat.ino) return { source: { ...source, status: 'unreadable' }, entries }
    source.totalBytes = opened.size
    source.lastWriteAt = opened.mtime.toISOString()
    const length = Math.min(opened.size, MAX_FILE_BYTES), start = opened.size - length
    source.truncated = start > 0
    const buffer = Buffer.alloc(length)
    source.bytesRead = readSync(fd, buffer, 0, length, start)
    source.status = 'read'
    let text = buffer.subarray(0, source.bytesRead).toString('utf8')
    if (start > 0) { const newline = text.indexOf('\n'); text = newline < 0 ? '' : text.slice(newline + 1); source.partialLines++ }
    if (text && !text.endsWith('\n')) { text = text.slice(0, text.lastIndexOf('\n') + 1); source.partialLines++ }
    const lastLoop = new Map<string, string>()
    for (const line of text.split('\n')) {
      if (!line.trim()) continue
      const entry = project(line.replace(/\r$/, ''))
      if (!entry) { source.skippedLines++; continue }
      if (source.observedFrom == null || entry.at < source.observedFrom) source.observedFrom = entry.at
      if (source.observedTo == null || entry.at > source.observedTo) source.observedTo = entry.at
      const at = Date.parse(entry.at)
      if (at < from || at > to) continue
      if (Object.hasOwn(loopEvents, entry.event)) {
        const lane = entry.event.split('-')[0]!
        if (entry.event.endsWith('-disconnected') && lastLoop.get(lane) === entry.event) continue
        lastLoop.set(lane, entry.event)
      }
      entries.push({ ...entry, location })
    }
    source.stale = Date.parse(source.observedTo ?? source.lastWriteAt) < from
  } catch (error) { source.status = (error as NodeJS.ErrnoException).code === 'ENOENT' ? 'missing' : 'unreadable' }
  finally { if (fd !== undefined) closeSync(fd) }
  return { source, entries }
}

export interface RuntimeLogCollectionOptions {
  home?: string | null; activeStatus?: RuntimeLogSource['status']; priorHome?: string | null; priorStatus?: RuntimeLogSource['status']; desktopDirectory: string
  appDataPath?: string; profile?: RuntimeHomeProfile; hours?: LogHours; now?: number
}
export function collectRuntimeLogs(options: RuntimeLogCollectionOptions): RuntimeLogSnapshot {
  const hours = options.hours ?? 24, now = options.now ?? Date.now(), from = now - hours * 3600_000
  const home = options.home ?? (options.appDataPath && options.profile ? resolveRuntimeHome(options.appDataPath, options.profile).home : null)
  const runtimeSources = (directory: string | null, location: 'active' | 'prior', status?: RuntimeLogSource['status']) =>
    ['runtime.log.old.log', 'runtime.old.log', 'runtime.log'].map(name => directory
      ? readSource(path.join(directory, 'logs', name), name, location, projectRuntimeLog, from, now)
      : { source: emptySource(name, location, status), entries: [] })
  const collected = [...runtimeSources(home, 'active', options.activeStatus),
    ...(options.priorHome !== undefined || options.priorStatus ? runtimeSources(options.priorHome ?? null, 'prior', options.priorStatus) : []),
    ...['desktop-diagnostics.old.jsonl', 'desktop-diagnostics.jsonl'].map(name => readSource(path.join(options.desktopDirectory, name), name, 'desktop', projectDesktop, from, now)),
  ]
  const all = collected.flatMap(item => item.entries).sort((a, b) => a.at.localeCompare(b.at))
  const entries = all.slice(-MAX_ENTRIES), sources = collected.map(item => item.source)
  let health: RuntimeLogHealth | null = null
  if (home && !diagnosticPathLinked(path.join(home, 'logs'))) {
    const read = readLogFileHealth(home)
    const errorAt = read.health?.lastFileWriteErrorAt
    health = { fileWriteFailures: read.health?.fileWriteFailures ?? 0, lastFileWriteErrorAt: errorAt && Number.isFinite(Date.parse(errorAt)) ? new Date(errorAt).toISOString() : null, degraded: read.health?.degraded ?? false, source: read.source, error: read.error?.split(':')[0] ?? read.logDirError }
  }
  return { version: 1, generatedAt: new Date(now).toISOString(), hours, versions: { app: null, host: null, core: null }, range: { from: new Date(from).toISOString(), to: new Date(now).toISOString(), observedFrom: entries[0]?.at ?? null, observedTo: entries.at(-1)?.at ?? null }, entries, sources, health, truncated: all.length > MAX_ENTRIES || sources.some(source => source.truncated) }
}
