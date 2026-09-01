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
 * 文件写失败静默降级（日志绝不能反噬主流程），每行带本地时间戳。
 */
import { appendFileSync, mkdirSync, renameSync, statSync } from 'node:fs'
import path from 'node:path'
import { runtimeHomeDir } from './config.js'

/** 单文件上限 5MB（与 start_runtime.bat 轮转阈值一致） */
const LOG_MAX_BYTES = 5 * 1024 * 1024

function logFilePath(): string {
  return path.join(runtimeHomeDir(), 'logs', 'runtime.log')
}

/** append 一行到日志文件；目录不存在则创建，超限则轮转；任何失败静默（不影响 stderr） */
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
  } catch {
    // 磁盘满/权限等：静默降级，stderr 仍可用
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
