import path from 'node:path'

export const DESKTOP_SCHEME = 'aidagent'
export const DESKTOP_HOST = 'app'
export const DESKTOP_ORIGIN = `${DESKTOP_SCHEME}://${DESKTOP_HOST}`

const AGENT_ROUTE_ROOTS = new Set([
  'after-sales',
  'all-sessions',
  'chat',
  'complaint',
  'customer-followup',
  'customer-info',
  'data-sources',
  'knowledge-base',
  'my-agents',
  'scheduled-tasks',
  'social-media',
  'trade-specialist',
  'travel-consultant',
])

export interface SecureWebPreferences {
  preload: string
  nodeIntegration: false
  contextIsolation: true
  sandbox: true
  webSecurity: true
}

export type SchemeRequestTarget =
  | { kind: 'file'; relativePath: string }
  | { kind: 'history-fallback'; relativePath: 'index.html' }
  | { kind: 'reject'; reason: string }

function decodePathname(pathname: string): string | null {
  let decoded = pathname
  for (let depth = 0; depth < 4; depth += 1) {
    try {
      const next = decodeURIComponent(decoded)
      if (next === decoded) {
        if (decoded.includes('\0') || decoded.includes('\\')) return null
        return decoded
      }
      decoded = next
    } catch {
      return null
    }
  }
  return null
}

export function isAllowedAgentRoute(pathname: string): boolean {
  const decoded = decodePathname(pathname)
  if (!decoded || !decoded.startsWith('/')) return false

  const segments = decoded.split('/').filter(Boolean)
  if (segments.some((segment) => segment === '.' || segment === '..')) return false
  if (segments.length === 0) return true
  if (segments[0] === 'portal') return false
  if (segments[0] === 't') return segments.length >= 2 && Boolean(segments[1])
  return AGENT_ROUTE_ROOTS.has(segments[0] ?? '')
}

export function normalizeApiBaseUrl(value: string): string {
  const trimmed = value.trim()
  if (!trimmed) throw new Error('AID_AGENT_API_BASE_URL 未配置')

  let url: URL
  try {
    url = new URL(trimmed)
  } catch {
    throw new Error('API base URL 格式无效')
  }

  if (url.username || url.password) throw new Error('API base URL 禁止包含凭证')
  const isLocalhost = ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)
  if (url.protocol !== 'https:' && !(url.protocol === 'http:' && isLocalhost)) {
    throw new Error('API base URL 仅允许 HTTPS；本机开发可使用 HTTP localhost')
  }
  if (url.search || url.hash) throw new Error('API base URL 禁止包含 query 或 fragment')

  return url.toString().replace(/\/$/, '')
}

export type ApiBaseConfiguration =
  | { kind: 'valid'; apiBaseUrl: string }
  | { kind: 'reject'; reason: 'missing-api-base-url' | 'invalid-api-base-url' }

export function parseApiBaseConfiguration(value: string): ApiBaseConfiguration {
  if (!value.trim()) return { kind: 'reject', reason: 'missing-api-base-url' }
  try {
    return { kind: 'valid', apiBaseUrl: normalizeApiBaseUrl(value) }
  } catch {
    return { kind: 'reject', reason: 'invalid-api-base-url' }
  }
}

export function mapSchemeRequest(requestUrl: string): SchemeRequestTarget {
  // 在 URL 标准化之前拒绝编码后的路径分隔符和 traversal 片段。
  if (/%(?:2e|2f|5c)/i.test(requestUrl)) {
    return { kind: 'reject', reason: 'encoded path traversal is not allowed' }
  }

  let url: URL
  try {
    url = new URL(requestUrl)
  } catch {
    return { kind: 'reject', reason: 'invalid URL' }
  }
  if (url.protocol !== `${DESKTOP_SCHEME}:` || url.hostname !== DESKTOP_HOST) {
    return { kind: 'reject', reason: 'unexpected scheme origin' }
  }
  if (url.username || url.password || url.port) {
    return { kind: 'reject', reason: 'unexpected scheme origin components' }
  }
  if (url.search || url.hash) {
    return { kind: 'reject', reason: 'scheme URL query and fragment are not allowed' }
  }

  // WHATWG URL 会在暴露 pathname 前折叠点路径。先检查原始路径，避免
  // `/portal/../chat` 或双重编码路径在标准化后伪装成允许的 Agent 路由。
  const originPrefix = `${DESKTOP_ORIGIN}/`
  if (!requestUrl.toLowerCase().startsWith(originPrefix)) {
    return { kind: 'reject', reason: 'non-canonical scheme URL' }
  }
  const rawPath = requestUrl.slice(DESKTOP_ORIGIN.length).split(/[?#]/, 1)[0] ?? ''
  const decodedRawPath = decodePathname(rawPath)
  if (!decodedRawPath) return { kind: 'reject', reason: 'invalid encoded scheme path' }
  const rawSegments = decodedRawPath.split('/').filter(Boolean)
  if (rawSegments.some((segment) => segment === '.' || segment === '..')) {
    return { kind: 'reject', reason: 'path traversal is not allowed' }
  }
  if (rawSegments[0]?.toLowerCase() === 'portal') {
    return { kind: 'reject', reason: 'Portal is not available in Agent Desktop' }
  }

  if (url.pathname.startsWith('/assets/')) {
    return { kind: 'file', relativePath: url.pathname.slice(1) }
  }
  if (!isAllowedAgentRoute(url.pathname)) {
    return { kind: 'reject', reason: 'route is not available in Agent Desktop' }
  }
  return { kind: 'history-fallback', relativePath: 'index.html' }
}

export function isAllowedMainFrameNavigation(url: string): boolean {
  return mapSchemeRequest(url).kind === 'history-fallback'
}

export function isTrustedIpcSender(senderUrl: string, isMainFrame: boolean, isMainWindow: boolean): boolean {
  return isMainFrame && isMainWindow && isAllowedMainFrameNavigation(senderUrl)
}

export function parseAgentDeepLink(argumentsList: readonly string[]): string | null {
  const candidate = argumentsList.find((argument) => argument.toLowerCase().startsWith(`${DESKTOP_ORIGIN}/`))
  return candidate && isAllowedMainFrameNavigation(candidate) ? candidate : null
}

export function createContentSecurityPolicy(apiBaseUrl: string): string {
  const apiOrigin = new URL(normalizeApiBaseUrl(apiBaseUrl)).origin
  return [
    "default-src 'self'",
    "base-uri 'none'",
    "object-src 'none'",
    "frame-src 'none'",
    "form-action 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    `connect-src 'self' ${apiOrigin}`,
    `img-src 'self' data: blob: ${apiOrigin}`,
    `media-src 'self' blob: ${apiOrigin}`,
    "font-src 'self' data:",
    "worker-src 'self' blob:",
  ].join('; ')
}

export function resolveInsideRoot(root: string, relativePath: string): string {
  const resolvedRoot = path.resolve(root)
  const resolvedPath = path.resolve(resolvedRoot, relativePath)
  const relative = path.relative(resolvedRoot, resolvedPath)
  if (relative.startsWith('..') || path.isAbsolute(relative)) {
    throw new Error('scheme path escapes renderer root')
  }
  return resolvedPath
}

export function createSecureWebPreferences(preload: string): SecureWebPreferences {
  return {
    preload,
    nodeIntegration: false,
    contextIsolation: true,
    sandbox: true,
    webSecurity: true,
  }
}
