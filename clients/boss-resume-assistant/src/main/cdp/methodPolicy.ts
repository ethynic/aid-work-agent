/**
 * CDP 协议策略（设计文档 §6.1 白名单 + §6.2 硬拒绝）。
 * 未知 method 默认拒绝。禁止 Runtime.* / Debugger.* / 脚本注入 / RemoteObject。
 *
 * 白名单来源：spike/boss-native-cdp 已真机验证过的最小只读集合。
 * 新增 method 必须先做单变量真机实验、更新设计文档、加协议审计测试。
 */

/** 允许的 CDP method 白名单 */
export const ALLOWED_METHODS = new Set<string>([
  'Target.getTargets',
  'Target.attachToTarget',
  'Target.detachFromTarget',
  'Page.enable',
  'Page.disable',
  'Page.getFrameTree',
  'Page.captureScreenshot',
  'Network.enable',
  'Network.disable',
  'Network.getResponseBody',
  'DOMSnapshot.captureSnapshot',
  'Input.dispatchMouseEvent',
  'Input.dispatchKeyEvent',
])

/** 硬拒绝前缀：任何此前缀的方法一律拒绝（即使误加进白名单） */
const FORBIDDEN_PREFIXES = ['Runtime.', 'Debugger.']

/** 硬拒绝精确方法：返回/操作 RemoteObject 或注入脚本 */
const FORBIDDEN_EXACT = new Set<string>([
  'Page.addScriptToEvaluateOnNewDocument',
  'Page.removeScriptToEvaluateOnNewDocument',
  'Page.createIsolatedWorld',
  'DOM.resolveNode',
  'Runtime.evaluate',
  'Runtime.callFunctionOn',
  'Runtime.releaseObject',
  'Runtime.releaseObjectGroup',
])

export class ForbiddenCdpMethodError extends Error {
  constructor(method: string) {
    super(`CDP method is forbidden: ${method}`)
    this.name = 'ForbiddenCdpMethodError'
  }
}

/** 校验 method 是否允许。禁止则抛 ForbiddenCdpMethodError（不可恢复，应暂停任务）。 */
export function assertMethodAllowed(method: unknown): void {
  if (typeof method !== 'string') {
    throw new ForbiddenCdpMethodError(String(method))
  }
  for (const prefix of FORBIDDEN_PREFIXES) {
    if (method.startsWith(prefix)) {
      throw new ForbiddenCdpMethodError(method)
    }
  }
  if (FORBIDDEN_EXACT.has(method)) {
    throw new ForbiddenCdpMethodError(method)
  }
  if (!ALLOWED_METHODS.has(method)) {
    throw new ForbiddenCdpMethodError(method)
  }
}

/** 判断是否禁止（不抛错，供测试/诊断用） */
export function isForbidden(method: string): boolean {
  try {
    assertMethodAllowed(method)
    return false
  } catch {
    return true
  }
}
