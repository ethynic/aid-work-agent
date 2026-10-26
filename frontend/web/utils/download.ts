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
 * 兼容下载入口
 *
 * 微信内置浏览器（X5/XWeb 内核）对 HTML5 download 属性支持残缺：
 * a[download] 直链会被微信下载器接管并以 0B 失败。微信内改用整页导航，
 * 由微信自身的下载器/文件预览接管（响应已带 Content-Disposition: attachment，
 * 不会跳离页面；iOS 微信弹出内置文档预览供保存）；非微信保持原生直链下载，
 * 浏览器下载管理器自带进度条。
 */
export function triggerCompatibleDownload(url: string, filename?: string): void {
  if (isWeChatBrowser()) {
    window.location.href = url
    return
  }
  triggerNativeDownload(url, filename)
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
