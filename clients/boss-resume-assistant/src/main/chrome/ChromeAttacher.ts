/**
 * Chrome attach 探测器（CLI 场景，设计 §16 决策 6 修订）。
 * 背景：spawn 全新临时 profile Chrome 扫码登录触发 BOSS 风控封号，CLI 已废弃该路径，
 * 改为 attach 用户日常使用的、带 --remote-debugging-port 启动的 Chrome。
 * 本模块只做一件事：通过 http://127.0.0.1:{port}/json/version 探测并解析
 * webSocketDebuggerUrl；绝不启动/杀死任何 Chrome 进程、绝不触碰任何 profile。
 *
 * fail-loud：连接拒绝 / 超时 / 响应非法三类故障各自抛出带 kind 的 ChromeAttachError，
 * 由 CLI 层转译为用户可操作的诊断文案。
 */
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

/** 默认调试端口（可用 --cdp-port 覆盖） */
export const DEFAULT_CDP_PORT = 9222

/** Windows 常见 Chrome 安装路径 */
function defaultChromeCandidates(): string[] {
  const pf = process.env['ProgramFiles'] ?? 'C:\\Program Files'
  const pfx86 = process.env['ProgramFiles(x86)'] ?? 'C:\\Program Files (x86)'
  const local = process.env['LOCALAPPDATA'] ?? path.join(os.homedir(), 'AppData', 'Local')
  return [
    path.join(pf, 'Google', 'Chrome', 'Application', 'chrome.exe'),
    path.join(pfx86, 'Google', 'Chrome', 'Application', 'chrome.exe'),
    path.join(local, 'Google', 'Chrome', 'Application', 'chrome.exe'),
  ]
}

/** 探测系统 Chrome 路径（仅用于生成启动命令提示），找不到 fail-loud */
export function findChromeExecutable(candidates?: string[]): string {
  const list = candidates ?? defaultChromeCandidates()
  for (const p of list) {
    try {
      if (p && fs.existsSync(p)) return p
    } catch {
      // 单个路径探测失败继续
    }
  }
  throw new Error(`未找到 Chrome 可执行文件（已探测 ${list.length} 个常见路径），请安装 Chrome 或配置路径`)
}

export type ChromeAttachErrorKind = 'refused' | 'timeout' | 'invalid'

export class ChromeAttachError extends Error {
  constructor(
    readonly kind: ChromeAttachErrorKind,
    message: string,
    readonly cause?: unknown,
  ) {
    super(message)
    this.name = 'ChromeAttachError'
  }
}

export interface ChromeVersionInfo {
  webSocketDebuggerUrl: string
  /** Chrome 版本串（如 Chrome/131.0.0.0），展示用 */
  browser: string
}

/**
 * 探测调试端点：GET {httpEndpoint}/json/version，解析 webSocketDebuggerUrl。
 * - 连接拒绝（ECONNREFUSED 等）→ kind='refused'（Chrome 没起 / 端口不对）
 * - 超时 → kind='timeout'（端口被占用但无响应 / Chrome 卡死）
 * - HTTP 非 2xx、JSON 非法、缺 webSocketDebuggerUrl → kind='invalid'（端口被其他程序占用）
 */
export async function probeChromeDebugEndpoint(
  httpEndpoint: string,
  opts: { timeoutMs?: number; fetchImpl?: typeof fetch } = {},
): Promise<ChromeVersionInfo> {
  const fetchImpl = opts.fetchImpl ?? fetch
  const timeoutMs = opts.timeoutMs ?? 5000
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  try {
    let res: Response
    try {
      res = await fetchImpl(`${httpEndpoint}/json/version`, { signal: controller.signal })
    } catch (e) {
      if (e instanceof Error && e.name === 'AbortError') {
        throw new ChromeAttachError('timeout', `连接 ${httpEndpoint} 超过 ${timeoutMs}ms 无响应`, e)
      }
      // Node fetch 网络层错误（ECONNREFUSED/ECONNRESET/ENOTFOUND 等）统一归为「连不上」
      throw new ChromeAttachError('refused', `无法连接 ${httpEndpoint}`, e)
    }
    if (!res.ok) {
      throw new ChromeAttachError('invalid', `${httpEndpoint} 返回 HTTP ${res.status}，不是合法的 CDP 端点`)
    }
    let body: unknown
    try {
      body = await res.json()
    } catch (e) {
      throw new ChromeAttachError('invalid', `${httpEndpoint} 响应不是合法 JSON`, e)
    }
    const wsUrl = (body as { webSocketDebuggerUrl?: unknown }).webSocketDebuggerUrl
    if (typeof wsUrl !== 'string' || !wsUrl.startsWith('ws')) {
      throw new ChromeAttachError('invalid', `${httpEndpoint} 响应缺少 webSocketDebuggerUrl`)
    }
    const browser = (body as { Browser?: unknown }).Browser
    return { webSocketDebuggerUrl: wsUrl, browser: typeof browser === 'string' ? browser : '' }
  } finally {
    clearTimeout(timer)
  }
}

/** 把探测/连接错误转译为用户可操作的中文诊断（fail-loud，不静默重试） */
export function diagnoseAttachError(err: unknown, httpEndpoint: string): string {
  if (err instanceof ChromeAttachError) {
    switch (err.kind) {
      case 'refused':
        return `无法连接 ${httpEndpoint}：Chrome 未启动，或未带 --remote-debugging-port 启动（或端口号不一致）。`
      case 'timeout':
        return `连接 ${httpEndpoint} 超时：端口可能被其他程序占用，或 Chrome 无响应，请检查端口后重试。`
      case 'invalid':
        return `${httpEndpoint} 响应非法：该端口不像是 Chrome 调试端口（可能被其他程序占用），请更换端口后重试。`
    }
  }
  return `连接失败：${err instanceof Error ? err.message : String(err)}`
}

/** 「用日常 Chrome 带调试端口启动」的命令示例：优先探测本机实际安装路径 */
export function chromeLaunchCommandHint(port: number): string {
  try {
    return `"${findChromeExecutable()}" --remote-debugging-port=${port}`
  } catch {
    // 探测不到安装路径时给出最常见路径占位，用户自行调整
    return `"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" --remote-debugging-port=${port}`
  }
}
