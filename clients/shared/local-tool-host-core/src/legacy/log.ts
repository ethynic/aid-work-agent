/**
 * stderr 日志（脱敏）+ 进程内文件落盘。
 *
 * 硬约束：绝不输出 device_token / claim_token / 配对码 / 简历内容。
 * 调用方约定不把敏感值拼进消息；redact() 是兜底（长 hex/base64 串打码）。
 *
 * 文件落盘（2026-09-01 真机事故补强）：此前日志只写 stderr，只有经 start_runtime.bat
 * （输出重定向）启动才有文件；用户手动控制台跑 aid-runtime 时日志随窗口关闭全丢，
 * 排障只能靠服务端 DB 反推。现在进程内自行 append 到
 * %APPDATA%/aidwork-tool-runtime/logs/runtime.log（>5MB 轮转 runtime.old.log，
 * 与 start_runtime.bat 轮转命名一致），任何启动方式都有日志。
 * 文件写失败不再静默：限频 stderr 告警 + 健康记录（Phase 7，详见 warnFileWriteFailure），
 * 每行带本地时间戳。健康记录持久化到 logs/log-health.json；logs 目录不可写时降级写
 * home 根 log-health.json（Phase 7 CR：桌面 supervisor 只 resume stderr 不保存，
 * 导出/诊断链路靠该文件读到失败计数与原因）。
 */
import { appendFileSync, mkdirSync, renameSync, statSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import { runtimeHomeDir } from './config.js'

/** 单文件上限 5MB（与 start_runtime.bat 轮转阈值一致） */
const LOG_MAX_BYTES = 5 * 1024 * 1024

/** 文件写失败告警限频：磁盘满/权限坏时每次写都会失败，不限频会刷爆 stderr（Phase 7） */
const FILE_WRITE_WARN_INTERVAL_MS = 5 * 60_000

/** 健康文件落盘节流下限（Phase 7 CR：磁盘满/文件被占时每行日志都失败，逐失败同步写
 *  健康文件会造成写放大；1s 一写 + 尾写补齐，计数精度损失 <1s） */
const LOG_HEALTH_PERSIST_MIN_INTERVAL_MS = 1_000

/** 文件写失败健康记录（Phase 7 可观测性）：诊断导出/测试读取；持续累计，不因恢复清零 */
export interface LogFileHealth {
  fileWriteFailures: number
  lastFileWriteErrorAt: string | null
  lastFileWriteError: string | null
  /** 健康文件是否处于降级位置（logs 目录不可写，快照落在 home 根）——Phase 7 CR 二轮 */
  degraded: boolean
  /** 降级原因：健康文件主位置写入失败的原始错误（redact 后）；未降级为 null */
  degradedReason: string | null
}

const logFileHealth: LogFileHealth = {
  fileWriteFailures: 0,
  lastFileWriteErrorAt: null,
  lastFileWriteError: null,
  degraded: false,
  degradedReason: null,
}
let lastFileWriteWarnAt = 0

/** 只读快照（诊断导出用）：文件日志通道是否发生过写失败及最近一次原因 */
export function logFileHealthSnapshot(): Readonly<LogFileHealth> {
  return { ...logFileHealth }
}

function logFilePath(): string {
  return path.join(runtimeHomeDir(), 'logs', 'runtime.log')
}

/** 健康快照持久化文件（主位置）：诊断导出链路（runtime-diagnostic-summary.ps1 与
 *  legacy/diagnostics.readLogFileHealth）读同一文件同一口径——supervisor 只 resume
 *  Host stderr 不保存（runtimeSupervisor.ts），该文件是桌面/导出侧唯一能读到的通道 */
function logHealthFilePath(): string {
  return path.join(runtimeHomeDir(), 'logs', 'log-health.json')
}

/** 健康快照降级位置（Phase 7 CR 二轮）：logs 目录无法创建/不可写（如被同名文件占位）时，
 *  主位置与 runtime.log 一起失效——降级写到 home 根，独立于 logs 目录，健康记录才不丢 */
function logHealthFallbackFilePath(): string {
  return path.join(runtimeHomeDir(), 'log-health.json')
}

let lastHealthPersistAt = 0
let healthPersistTimer: ReturnType<typeof setTimeout> | null = null
let healthPersistPending = false

/** 快照 JSON（落盘内容；错误类文本过 redact 兜底，与 stderr 告警同口径） */
function healthRecordJson(degraded: boolean, degradedReason: string | null): string {
  return (
    JSON.stringify({
      ...logFileHealthSnapshot(),
      lastFileWriteError: logFileHealth.lastFileWriteError === null ? null : redact(logFileHealth.lastFileWriteError),
      degraded,
      degradedReason: degradedReason === null ? null : redact(degradedReason),
      updatedAt: new Date().toISOString(),
    }) + '\n'
  )
}

function writeHealthFileNow(): void {
  lastHealthPersistAt = Date.now()
  healthPersistPending = false
  // 主位置（logs 目录）先行：可写时优先写主位置并自动解除降级标记
  try {
    const file = logHealthFilePath()
    mkdirSync(path.dirname(file), { recursive: true })
    writeFileSync(file, healthRecordJson(false, null), 'utf8')
    logFileHealth.degraded = false
    logFileHealth.degradedReason = null
    return
  } catch (primaryError) {
    logFileHealth.degraded = true
    logFileHealth.degradedReason = primaryError instanceof Error ? primaryError.message : String(primaryError)
  }
  // 降级位置（home 根，独立于 logs 目录）：两处都失败才放弃（内存计数与 stderr 告警仍有效）
  try {
    const fallback = logHealthFallbackFilePath()
    mkdirSync(path.dirname(fallback), { recursive: true })
    writeFileSync(fallback, healthRecordJson(true, logFileHealth.degradedReason), 'utf8')
  } catch {
    // 主位置与降级位置均不可写（如整盘满/home 根异常）：健康快照无法落盘；
    // 内存计数与 stderr 告警仍有效，读取端（readLogFileHealth）按目录异常如实报错
  }
}

/** 健康快照落盘（best-effort，节流 1s + unref 尾写定时器补最后一个失败） */
function persistLogFileHealth(): void {
  const since = Date.now() - lastHealthPersistAt
  if (since < LOG_HEALTH_PERSIST_MIN_INTERVAL_MS) {
    healthPersistPending = true
    if (!healthPersistTimer) {
      healthPersistTimer = setTimeout(
        () => {
          healthPersistTimer = null
          if (healthPersistPending) writeHealthFileNow()
        },
        LOG_HEALTH_PERSIST_MIN_INTERVAL_MS - since,
      )
      healthPersistTimer.unref()
    }
    return
  }
  writeHealthFileNow()
}

/** 立即完成待写的健康快照落盘（测试/诊断采集前显式调用；生产路径由节流尾写覆盖） */
export function flushLogFileHealth(): void {
  if (healthPersistTimer) {
    clearTimeout(healthPersistTimer)
    healthPersistTimer = null
  }
  if (healthPersistPending) writeHealthFileNow()
}

/** 文件写失败可见告警（Phase 7，2026-10-20 复盘）：不再静默——Electron supervisor
 * 只消费不保存 Host stdout/stderr（runtimeSupervisor.ts），壳层输出不是兜底证据，
 * 文件日志是唯一持久通道；写失败必须可见，否则排障只剩服务端 DB 反推。
 * 告警走 stderr 限频输出（直接 write，不经 write() 防递归），健康记录持续累计。 */
function warnFileWriteFailure(err: unknown): void {
  logFileHealth.fileWriteFailures += 1
  logFileHealth.lastFileWriteErrorAt = new Date().toISOString()
  logFileHealth.lastFileWriteError = err instanceof Error ? err.message : String(err)
  // 健康快照落盘（Phase 7 CR 修复：stderr 告警桌面 supervisor 不保存——child.stderr 仅
  // resume；文件是导出/诊断唯一可读通道，详见 persistLogFileHealth）
  persistLogFileHealth()
  const now = Date.now()
  if (now - lastFileWriteWarnAt < FILE_WRITE_WARN_INTERVAL_MS) return
  lastFileWriteWarnAt = now
  process.stderr.write(
    `[runtime][ERROR] 日志文件写入失败（已累计 ${logFileHealth.fileWriteFailures} 次，最近原因: ` +
      `${redact(logFileHealth.lastFileWriteError)}）: ${logFilePath()} ——文件日志不可用，当前仅 stderr 可见；` +
      `请检查磁盘空间与目录权限。此告警每 ${Math.round(FILE_WRITE_WARN_INTERVAL_MS / 1000)}s 至多一条\n`,
  )
}

/** append 一行到日志文件；目录不存在则创建，超限则轮转；失败不反噬主流程，但必须告警（见 warnFileWriteFailure） */
function appendFile(line: string): void {
  try {
    const file = logFilePath()
    mkdirSync(path.dirname(file), { recursive: true })
    try {
      if (statSync(file).size > LOG_MAX_BYTES) {
        renameSync(file, `${file}.old.log`)
      }
    } catch {
      // stat/rename 失败（文件不存在 / 被占用）不影响本次 append
    }
    appendFileSync(file, line)
  } catch (err) {
    warnFileWriteFailure(err)
  }
}

function write(level: 'INFO' | 'ERROR', message: string): void {
  const safe = redact(message)
  // stderr 保持历史格式（INFO 不带级别标签），已有日志消费方 grep 不受影响
  process.stderr.write(level === 'ERROR' ? `[runtime][ERROR] ${safe}\n` : `[runtime] ${safe}\n`)
  appendFile(`${new Date().toISOString()} [runtime][${level}] ${safe}\n`)
}

/** 兜底脱敏：32 位以上的 hex / base64 长串打码（token 形态）；UUID（含连字符）不算敏感，放行 */
export function redact(message: string): string {
  return message.replace(/[A-Za-z0-9+/=_-]{32,}/g, (m) => {
    if (/^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/.test(m)) return m
    return m.slice(0, 6) + '***'
  })
}

export function logInfo(message: string): void {
  write('INFO', message)
}

export function logError(message: string): void {
  write('ERROR', message)
}

/**
 * Provider（boss CLI 子进程）stderr chunk 转发：控制台可见 + 落盘。
 * 2026-09-01：provider stderr 原为 'inherit' 直通，脱离 start_runtime.bat 重定向时
 * [boss-mcp] 工具调用行（排障关键证据）全丢；改为 pipe 后经此逐行转发（过 redact 兜底）。
 */
export function logProviderChunk(chunk: string): void {
  for (const line of chunk.split('\n')) {
    const t = line.replace(/\r$/, '').trim()
    if (!t) continue
    const safe = redact(t)
    process.stderr.write(`${safe}\n`)
    appendFile(`${new Date().toISOString()} ${safe}\n`)
  }
}
