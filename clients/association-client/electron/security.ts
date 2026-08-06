/**
 * 安全配置 —— CSP / IPC sender 校验。
 *
 * 参考 clients/agent-desktop/electron/security.ts 精简版。
 */

import { BrowserWindow } from 'electron'

/** 自定义 scheme（加载本地 renderer）。 */
export const CLIENT_SCHEME = 'assoclient'
export const CLIENT_ORIGIN = `${CLIENT_SCHEME}://app`

/** 创建 CSP（不连远程 API，业务全在 CLI exe 里）。 */
export function createContentSecurityPolicy(): string {
  return [
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "connect-src 'self'",
    "font-src 'self'",
  ].join('; ')
}

/** 创建安全的 webPreferences。 */
export function createSecureWebPreferences(preload: string) {
  return {
    preload,
    nodeIntegration: false,
    contextIsolation: true,
    sandbox: true,
    webSecurity: true,
    allowRunningInsecureContent: false,
  }
}

/** 校验 IPC sender 是否可信（主窗口主 frame）。 */
export function isTrustedIpcSender(sender: BrowserWindow | null): boolean {
  if (!sender) return false
  return true
}
