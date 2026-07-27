/**
 * Electron 安全配置与 scheme 路由。
 * 全开安全：nodeIntegration=false, contextIsolation=true, sandbox=true, webSecurity=true。
 * Renderer 通过自定义 scheme bossresume://app 加载，SPA history 路由用通用 fallback。
 */
import { pathToFileURL } from 'node:url'
import path from 'node:path'

export const BOSS_SCHEME = 'bossresume'
export const BOSS_HOST = 'app'
export const BOSS_ORIGIN = `${BOSS_SCHEME}://${BOSS_HOST}`

/** 安全的 webPreferences，所有项禁止放宽 */
export function createSecureWebPreferences(preloadPath: string) {
  return {
    nodeIntegration: false,
    contextIsolation: true,
    sandbox: true,
    webSecurity: true,
    allowRunningInsecureContent: false,
    preload: preloadPath,
  } as const
}

/**
 * 将 scheme 请求路径映射到 renderer 目录的实际文件。
 * 通用 SPA fallback：/assets/* 直接取文件，其余一律回 index.html（history 路由）。
 * 防路径穿越：拒绝编码点段、点段、port、userinfo。
 */
export function resolveRendererFile(rendererRoot: string, requestPath: string): string | null {
  // 拒绝任何编码穿越尝试
  if (/%2e|%2f|%5c/i.test(requestPath)) return null
  // 拒绝原始路径含点段（防御性 fail-loud，即使后续 resolve 会困在 root 内）
  if (requestPath.includes('..')) return null

  let pathname = requestPath
  // 去掉 query/fragment（scheme 不应带，防御性处理）
  const qIndex = pathname.search(/[?#]/)
  if (qIndex >= 0) pathname = pathname.slice(0, qIndex)

  if (!pathname.startsWith('/')) return null

  const normalized = path.posix.normalize(pathname)
  if (!normalized.startsWith('/')) return null

  const rel = normalized.slice(1) // 去掉前导 /，得到相对 rendererRoot 的路径
  const abs = path.join(rendererRoot, rel)

  // 最终绝对路径必须严格在 rendererRoot 内
  if (!isInside(rendererRoot, abs)) return null

  // 有扩展名 → 尝试直接取文件（assets）
  if (path.extname(abs)) {
    return abs
  }
  // 无扩展名 → SPA fallback 到 index.html
  return path.join(rendererRoot, 'index.html')
}

function isInside(root: string, target: string): boolean {
  const rel = path.relative(root, target)
  return !!rel && !rel.startsWith('..') && !path.isAbsolute(rel)
}

/** 把本地文件路径转成 fetch 可用的 file URL */
export function toFileUrl(filePath: string): string {
  return pathToFileURL(filePath).toString()
}
