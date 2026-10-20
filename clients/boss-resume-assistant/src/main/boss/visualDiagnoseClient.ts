/** 只调用 Agent 后台视觉 API；模型、提示词及费率均由后台负责。 */
import { readFile, stat } from 'node:fs/promises'
import { pngMetadata } from '../operations/bossScreenshot.js'
import { loadWindowObservation, type WindowObservation } from '../input/windowExperiment.js'

export interface VisualDiagnoseOptions {
  requestId: string
  image?: string
  observation?: string
  goal?: string
  retrieveOnly?: boolean
  contextFile?: string
  apiVersion?: 1 | 2
  /** Trusted operation composition only; MCP never accepts image bytes or timestamps. */
  imageBytes?: Buffer
  context?: Record<string, unknown>
  capturedAt?: string
  /** From captureWindow only, never an MCP input. Bytes must match the original metadata. */
  windowObservation?: WindowObservation
  signal?: AbortSignal
}

export interface VisualDiagnoseDeps {
  env?: NodeJS.ProcessEnv
  fetch?: typeof globalThis.fetch
  timeoutMs?: number
}

export class VisualDiagnoseError extends Error {
  constructor(message: string, readonly requestId: string, readonly status?: number, readonly receiptUnknown = false) { super(message) }
}

export async function visualDiagnose(opts: VisualDiagnoseOptions, deps: VisualDiagnoseDeps = {}): Promise<{ status: number; body: unknown }> {
  const fail = (message: string): never => { throw new VisualDiagnoseError(message, opts.requestId) }
  if (!/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(opts.requestId)) fail('需要 UUID 格式的 --request-id')
  if (opts.image && opts.imageBytes || opts.context && opts.contextFile) fail('图片与上下文来源不能重复')
  if (opts.retrieveOnly ? !!(opts.image || opts.imageBytes || opts.goal || opts.observation || opts.contextFile || opts.context) : !(opts.image || opts.imageBytes)) fail('--retrieve-only 与图片/上下文互斥；新诊断需要图片')
  if (opts.goal !== undefined && (!opts.goal.trim() || opts.goal.length > 1000)) fail('--goal 需要非空文本（最多 1000 字符）')
  const version = opts.apiVersion ?? (opts.contextFile || opts.context ? 2 : 1)
  if (version !== 1 && version !== 2) fail('--api-version 只能为 1 或 2')
  if ((opts.contextFile || opts.context) && (version !== 2 || opts.goal !== undefined)) fail('上下文使用 v2，且与 --goal 互斥')
  if (version === 2 && !opts.retrieveOnly && !(opts.contextFile || opts.context)) fail('v2 新诊断需要上下文')
  const env = deps.env ?? process.env
  // Injected by the paired Runtime Host, never a separate user-managed API credential.
  const server = env.AIDWORK_RUNTIME_SERVER?.trim()
  const token = env.AIDWORK_RUNTIME_DEVICE_TOKEN?.trim()
  if (!server || !token) fail('缺少Runtime配对上下文，请通过已配对的Runtime启动BOSS工具，并确认Runtime与BOSS插件版本匹配')
  let url: URL
  try { url = new URL(server!) } catch { return fail('Runtime配对服务器地址无效') }
  const loopback = ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)
  if ((url.protocol !== 'https:' && !(url.protocol === 'http:' && loopback)) || url.username || url.password || url.search || url.hash) {
    fail('后台 URL 需 HTTPS（本机允许 HTTP），不可包含凭据、查询参数或片段')
  }
  url.pathname = `${url.pathname.replace(/\/$/, '')}/api/local-tools/runtime/boss/visual-diagnose`
  const body: Record<string, unknown> = { api_version: version, request_id: opts.requestId }
  if (opts.retrieveOnly) body.retrieve_only = true
  else {
    // 先检查大小再读，避免将任意大文件加载进内存。后台仍须独立校验 PNG。
    const imageStat = opts.image ? await stat(opts.image) : undefined
    if ((imageStat?.size ?? opts.imageBytes!.length) > 10 * 1024 * 1024) fail('PNG 图片不能超过 10 MiB')
    const image = opts.imageBytes ?? await readFile(opts.image!)
    const metadata = pngMetadata(image)
    let space: 'page_image_px' | 'window_image_px' = metadata.space
    let capturedAt = opts.capturedAt ?? imageStat?.mtime.toISOString() ?? new Date().toISOString()
    if (!Number.isFinite(Date.parse(capturedAt))) fail('截图采集时间无效')
    if (opts.observation || opts.windowObservation) {
      if (opts.observation && opts.windowObservation) fail('窗口观察来源不能重复')
      const observation = opts.windowObservation ?? await loadWindowObservation(opts.observation!)
      if (observation.sha256 !== metadata.sha256 || observation.width !== metadata.width || observation.height !== metadata.height) fail('--observation 与 --image 不是同一张原图')
      space = observation.space
      capturedAt = observation.captured_at
    }
    body.context = { mode: 'standalone' }
    body.operation = { kind: 'overlay', goal: opts.goal ?? '关闭阻挡弹窗，恢复页面操作' }
    body.observation = { image_base64: image.toString('base64'), image_sha256: metadata.sha256,
      width: metadata.width, height: metadata.height, space }
    if (version === 2) {
      // 上下文只携带操作事实；图片与采集信息由 CLI 从原文件计算，不能被 JSON 覆盖。
      if (opts.contextFile && (await stat(opts.contextFile)).size > 64 * 1024) fail('实验上下文不能超过 64 KiB')
      let context: Record<string, unknown>
      try {
        const value: unknown = opts.context ?? JSON.parse(await readFile(opts.contextFile!, 'utf8'))
        if (!value || typeof value !== 'object' || Array.isArray(value)) return fail('--context-file 需要 JSON 对象')
        context = value as Record<string, unknown>
        if (Buffer.byteLength(JSON.stringify(context)) > 64 * 1024) return fail('上下文不能超过 64 KiB')
      } catch { return fail('--context-file 不是有效 JSON 对象') }
      const fields = ['purpose', 'operation', 'execution', 'error', 'verification', 'available_actions']
      if (Object.keys(context).some(key => !fields.includes(key))) fail('--context-file 包含不支持的字段')
      const execution = context.execution as Record<string, unknown> | undefined
      if (!execution || !Number.isInteger(execution.attempts_used) || Number(execution.attempts_used) < 1) fail('execution.attempts_used 需要正整数')
      Object.assign(body, context)
      Object.assign(body.observation as object, { captured_at: capturedAt, after_attempt: execution!.attempts_used })
    }
  }
  let response: Response | undefined
  try {
    response = await (deps.fetch ?? globalThis.fetch)(url, {
      method: 'POST', redirect: 'error', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify(body), signal: opts.signal ? AbortSignal.any([opts.signal, AbortSignal.timeout(deps.timeoutMs ?? 150_000)]) : AbortSignal.timeout(deps.timeoutMs ?? 150_000),
    })
    const result: unknown = await response.json()
    return { status: response.status, body: result }
  } catch {
    // 底层网络错误可能携带 URL/headers；不输出它，也不把超时误认为后台未受理。
    throw new VisualDiagnoseError(`${response ? `后台 HTTP ${response.status} ` : '后台调用'}未取得有效 JSON 回执，是否受理未知；请使用相同 request_id 加 --retrieve-only 查询，勿自动新建请求`, opts.requestId, response?.status, true)
  }
}
