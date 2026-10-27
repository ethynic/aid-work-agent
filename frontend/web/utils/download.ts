/**
 * 文件下载公共工具
 *
 * 浏览器原生下载（<a> 直链）自带下载进度条，用户体验远优于 fetch+blob
 * （后者要等整个文件下载完才弹保存框）。直链无法携带认证 header 的下载点
 * 走「下载票据」：先经认证 POST 换取短期 HMAC 票据，再拼到直链 query 上。
 * 规范见 .claude/rules/backend_dev.md「下载接口规范」。
 */
import { getAuthHeader } from '@/api/auth'

const API_ROOT = import.meta.env.VITE_API_BASE_URL || '/api'

/**
 * 微信/企业微信内置浏览器检测
 *
 * 微信与企业微信内置浏览器（X5/XWeb 内核）的 UA 均含 MicroMessenger 标识
 * （企业微信 UA 为 wxwork + MicroMessenger 组合）
 */
export function isWeChatBrowser(): boolean {
  return /MicroMessenger/i.test(navigator.userAgent)
}

/**
 * 移动端下载策略（2026-10-27 分享页 Excel 下载事故沉淀）
 *
 * 原生下载依赖浏览器自愿实现「attachment 响应 → 下载管理器」移交，移动端各壳
 * 实现残缺：iOS 微信 WKWebView 收到 attachment 响应后静默丢弃；iOS Safari 成功
 * 但无提示（文件进下载管理器）；部分国产安卓内核对 filename*-only 的
 * Content-Disposition 解析失败直接放弃。服务端日志证实响应全部 200 完整传输。
 *
 * 策略（规范见 .claude/rules/backend_dev.md「下载接口规范」§2）：
 * - wechat_guide：微信/企微内置浏览器无论大小一律引导「在浏览器打开」（WKWebView
 *   无下载管道，任何技术方案都不可靠）
 * - blob：非微信移动端且 <10MB → fetch+blob 绕开 attachment 移交环节（最可靠）；
 *   大小未知也按 blob 处理（业务数据 size 几乎总有值）
 * - native：PC 端、移动端 ≥10MB → 原生直链（常规移动浏览器可用，大文件不适合
 *   整包进内存）
 */
export const MOBILE_BLOB_DOWNLOAD_MAX_BYTES = 10 * 1024 * 1024

export type DownloadStrategy = 'native' | 'blob' | 'wechat_guide'

export function isMobileBrowser(): boolean {
  const ua = navigator.userAgent
  return /iPhone|iPad|iPod|Android|Windows Phone|Mobile/i.test(ua)
    || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1)
}

export function resolveDownloadStrategy(sizeBytes?: number | null): DownloadStrategy {
  if (isWeChatBrowser()) return 'wechat_guide'
  if (isMobileBrowser() && (sizeBytes == null || sizeBytes < MOBILE_BLOB_DOWNLOAD_MAX_BYTES)) return 'blob'
  return 'native'
}

/**
 * 移动端小文件 fetch + blob 下载：绕开浏览器对 attachment 响应的移交环节，
 * blob URL + a[download] 是浏览器必然支持的本地下载路径。仅限 <10MB 小文件。
 */
export async function downloadViaBlob(url: string, filename?: string, headers?: Record<string, string>): Promise<void> {
  const resp = await fetch(url, { headers })
  if (!resp.ok) {
    throw new Error(`下载失败: ${resp.status}`)
  }
  const blob = await resp.blob()
  const objectUrl = URL.createObjectURL(blob)
  try {
    triggerNativeDownload(objectUrl, filename)
  } finally {
    // 立即 revoke 在部分移动内核会让未启动的下载中断，延迟到下一轮点击之后
    setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000)
  }
}

/**
 * 触发浏览器原生下载（同源直链，浏览器读取 Content-Disposition 作为文件名，
 * filename 参数仅作兜底提示）
 */
export function triggerNativeDownload(url: string, filename?: string): void {
  const a = document.createElement('a')
  a.href = url
  if (filename) a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
}

/**
 * 下载票据流程：POST `{downloadPath}_ticket`（带认证 header）换取票据，
 * 然后原生下载 `{downloadPath}?ticket=xxx`。
 *
 * @param downloadPath 含 /api 前缀的完整下载路径，
 *   如 `/api/knowledge/documents/3587/download`（对应签发端点为
 *   `POST /api/knowledge/documents/3587/download_ticket`）
 */
export async function downloadViaTicket(downloadPath: string, filename?: string): Promise<void> {
  const resp = await fetch(`${API_ROOT}${downloadPath}_ticket`, {
    method: 'POST',
    headers: { ...getAuthHeader() }
  })
  if (!resp.ok) {
    const body = await resp.json().catch(() => null)
    throw new Error(body?.detail || `下载失败: ${resp.status}`)
  }
  const { ticket } = await resp.json()
  if (!ticket) throw new Error('获取下载票据失败')
  triggerNativeDownload(`${API_ROOT}${downloadPath}?ticket=${encodeURIComponent(ticket)}`, filename)
}
