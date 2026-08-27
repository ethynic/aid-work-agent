/**
 * PowerShell 驱动执行器（M2 统一架构：TS operation → powershell.exe 驱动脚本）。
 *
 * 约定：
 * - spawn powershell.exe -NoProfile -ExecutionPolicy Bypass -File <script> [args]；
 * - 驱动脚本最后一行 stdout 输出 `DRIVER_JSON: {...}`（单行 JSON），结构为
 *   { ok: true, data: {...} } 或 { ok: false, code: <稳定错误码>, message: <中文> }；
 * - operation 只认 DRIVER_JSON，禁止解析自然语言输出；
 * - 驱动业务失败一律 DRIVER_JSON(ok=false) + 退出码 0；非零退出且无 DRIVER_JSON
 *   视为未预期崩溃 → INTERNAL_ERROR（带 stderr 摘要）；
 * - 默认超时 300s（超时 kill 子进程 → RESULT_TIMEOUT）；OpContext.signal abort 时
 *   kill 子进程并抛 CancelledError（errorMapping 映射 CANCELLED）。
 */
import { spawn } from 'node:child_process'
import { CancelledError, CodedOperationError, type ErrorCode } from '../operations/types.js'

export const DRIVER_JSON_PREFIX = 'DRIVER_JSON:'
export const DEFAULT_DRIVER_TIMEOUT_MS = 300_000

export interface PowerShellScriptOptions {
  /** .ps1 绝对路径 */
  script: string
  args?: string[]
  timeoutMs?: number
  signal?: AbortSignal
}

export interface PowerShellScriptResult {
  stdout: string
  stderr: string
  exitCode: number | null
}

export type RunPowerShellScriptFn = (opts: PowerShellScriptOptions) => Promise<PowerShellScriptResult>
export type RunPowerShellDriverFn = (opts: PowerShellScriptOptions) => Promise<Record<string, unknown>>

/** 驱动允许透传的错误码（白名单；其余一律归并 INTERNAL_ERROR，防止驱动自造不稳定 code） */
const DRIVER_ERROR_CODES: ReadonlySet<string> = new Set<ErrorCode>([
  'WEIXIN_NOT_FOUND',
  'NOT_LOGGED_IN',
  'WINDOW_AMBIGUOUS',
  'FOREGROUND_LOST',
  'UI_CHANGED',
  'RESULT_TIMEOUT',
  'CONTENT_UNAVAILABLE',
  'TARGET_NOT_FOUND',
  'TARGET_AMBIGUOUS',
  'BUSY',
  'EXECUTION_UNKNOWN',
  'INTERNAL_ERROR',
])

/**
 * 运行 PowerShell 脚本，收集 stdout/stderr。
 * 不抛业务错误：非零退出码由调用方判定；只有取消/超时才抛（CancelledError / RESULT_TIMEOUT）。
 */
export async function runPowerShellScript(opts: PowerShellScriptOptions): Promise<PowerShellScriptResult> {
  const timeoutMs = opts.timeoutMs ?? DEFAULT_DRIVER_TIMEOUT_MS
  const args = ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', opts.script, ...(opts.args ?? [])]
  return new Promise<PowerShellScriptResult>((resolve, reject) => {
    if (opts.signal?.aborted) {
      reject(new CancelledError())
      return
    }
    const child = spawn('powershell.exe', args, { windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] })
    let stdout = ''
    let stderr = ''
    let settled = false
    let timedOut = false
    child.stdout.setEncoding('utf8')
    child.stderr.setEncoding('utf8')
    child.stdout.on('data', (d: string) => (stdout += d))
    child.stderr.on('data', (d: string) => (stderr += d))

    const timer = setTimeout(() => {
      timedOut = true
      child.kill()
    }, timeoutMs)
    const onAbort = () => {
      child.kill()
    }
    opts.signal?.addEventListener('abort', onAbort, { once: true })

    const cleanup = () => {
      clearTimeout(timer)
      opts.signal?.removeEventListener('abort', onAbort)
    }
    child.on('error', (err) => {
      if (settled) return
      settled = true
      cleanup()
      reject(err) // spawn ENOENT 等由 errorMapping 归并 INTERNAL_ERROR
    })
    child.on('close', (code) => {
      if (settled) return
      settled = true
      cleanup()
      if (opts.signal?.aborted) {
        reject(new CancelledError())
        return
      }
      if (timedOut) {
        reject(new CodedOperationError('RESULT_TIMEOUT', `PowerShell 驱动执行超时（>${Math.round(timeoutMs / 1000)}s），已终止子进程`))
        return
      }
      resolve({ stdout, stderr, exitCode: code })
    })
  })
}

/**
 * 从脚本输出解析 DRIVER_JSON 并映射为 data / CodedOperationError（纯函数，可单测）。
 * - ok=true → 返回 data（缺省 {}）
 * - ok=false → 抛 CodedOperationError（code 走白名单，未知 code 归并 INTERNAL_ERROR）
 * - 无 DRIVER_JSON：非零退出 → INTERNAL_ERROR（带 stderr 摘要）；零退出 → INTERNAL_ERROR（驱动违约）
 */
export function parseDriverOutcome(result: PowerShellScriptResult): Record<string, unknown> {
  const lines = result.stdout.split(/\r?\n/)
  let jsonLine: string | undefined
  for (let i = lines.length - 1; i >= 0; i--) {
    const line = lines[i]!.replace(/^﻿/, '').trim() // 剥离可能的 BOM（\uFEFF）
    if (line.startsWith(DRIVER_JSON_PREFIX)) {
      jsonLine = line.slice(DRIVER_JSON_PREFIX.length).trim()
      break
    }
  }
  if (jsonLine === undefined) {
    const stderrSummary = result.stderr.trim().slice(0, 300)
    const detail = stderrSummary ? `：${stderrSummary}` : ''
    if (result.exitCode !== 0) {
      throw new CodedOperationError('INTERNAL_ERROR', `驱动脚本异常退出（exit=${result.exitCode}）${detail}`)
    }
    throw new CodedOperationError('INTERNAL_ERROR', '驱动脚本未输出 DRIVER_JSON（驱动契约违约）')
  }
  let parsed: { ok?: unknown; code?: unknown; message?: unknown; data?: unknown }
  try {
    parsed = JSON.parse(jsonLine) as typeof parsed
  } catch {
    throw new CodedOperationError('INTERNAL_ERROR', `DRIVER_JSON 解析失败：${jsonLine.slice(0, 200)}`)
  }
  if (parsed.ok === true) {
    if (parsed.data !== null && typeof parsed.data === 'object' && !Array.isArray(parsed.data)) {
      return parsed.data as Record<string, unknown>
    }
    return {}
  }
  const rawCode = typeof parsed.code === 'string' ? parsed.code : 'INTERNAL_ERROR'
  const code = (DRIVER_ERROR_CODES.has(rawCode) ? rawCode : 'INTERNAL_ERROR') as ErrorCode
  const message =
    typeof parsed.message === 'string' && parsed.message.length > 0 ? parsed.message : `驱动执行失败（${rawCode}）`
  throw new CodedOperationError(code, message)
}

/** 运行驱动脚本并返回 DRIVER_JSON data（失败抛 CodedOperationError） */
export async function runPowerShellDriver(opts: PowerShellScriptOptions): Promise<Record<string, unknown>> {
  return parseDriverOutcome(await runPowerShellScript(opts))
}
