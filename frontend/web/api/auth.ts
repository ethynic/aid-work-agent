/**
 * 认证相关 API
 */

import { getTenantScopedKey } from './tenantStorage'
import { credentialGet, credentialRemove } from '@/platform/credentialStore'

const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/auth`

export interface LoginResponse {
  success: boolean
  token?: string
  user?: {
    user_id: string
    username: string
    phone?: string
    avatar_url?: string
  }
  message?: string
}

export interface SendCodeResponse {
  success: boolean
  message?: string
  expires_in?: number
}

// export interface WxQrcodeResponse {
//   qrcode_url: string
//   scene_str: string
//   expire_seconds: number
// }

// export interface WxStatusResponse {
//   status: 'waiting' | 'scanned' | 'confirmed' | 'expired'
//   openid?: string
//   unionid?: string
// }

// /**
//  * 获取微信登录二维码
//  */
// export async function getWxQrcode(): Promise<WxQrcodeResponse> {
//   const res = await fetch(`${API_BASE}/wx/qrcode`)
//   return res.json()
// }

// /**
//  * 检查微信扫码状态
//  */
// export async function checkWxQrcodeStatus(sceneStr: string): Promise<WxStatusResponse> {
//   const res = await fetch(`${API_BASE}/wx/qrcode/${sceneStr}/status`)
//   return res.json()
// }

// /**
//  * 微信登录
//  */
// export async function wxLogin(wxOpenid: string, wxUnionid?: string): Promise<LoginResponse> {
//   const res = await fetch(`${API_BASE}/wx/login`, {
//     method: 'POST',
//     headers: { 'Content-Type': 'application/json' },
//     body: JSON.stringify({ wx_openid: wxOpenid, wx_unionid: wxUnionid })
//   })
//   return res.json()
// }

/**
 * 发送短信验证码
 */
export async function sendSmsCode(phone: string): Promise<SendCodeResponse> {
  const res = await fetch(`${API_BASE}/phone/send-code`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ phone })
  })
  return res.json()
}

/**
 * 手机号密码登录
 */
export async function phoneLogin(phone: string, password: string): Promise<LoginResponse> {
  const res = await fetch(`${API_BASE}/phone/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ phone, password })
  })
  return res.json()
}

/**
 * 手机号验证码登录
 */
export async function phoneCodeLogin(phone: string, code: string): Promise<LoginResponse> {
  const res = await fetch(`${API_BASE}/phone/code-login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ phone, code })
  })
  return res.json()
}

/**
 * 获取当前用户信息
 */
export async function getCurrentUser(): Promise<any> {
  // 按当前路由选择正确的 token key（租户前台按 tenant_id 隔离）
  const tokenKey = getTenantScopedKey('saas_token')
  const token = credentialGet(tokenKey)
  if (!token) return null

  const res = await fetch(`${API_BASE}/me`, {
    headers: { 'Authorization': `Bearer ${token}` }
  })

  if (!res.ok) return null
  return res.json()
}

/**
 * 登出
 */
export async function logout(): Promise<void> {
  const tokenKey = getTenantScopedKey('saas_token')
  const token = credentialGet(tokenKey)
  if (token) {
    try {
      await fetch(`${API_BASE}/logout`, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${token}` }
      })
    } catch {
      // 服务端注销失败不能阻止安全凭证从本机删除。
    }
  }
  await credentialRemove('user_info')
  await credentialRemove(tokenKey)
}

/**
 * 获取认证请求头
 * 在租户前台模式 (/t/:tenant_id) 下自动添加 X-Tenant-Id header
 */
function getCurrentTenantId(): string | null {
  const path = window.location.pathname
  const match = path.match(/^\/t\/([^/]+)/)
  return match ? match[1] : null
}

export function getAuthHeader(): Record<string, string> {
  const path = window.location.pathname
  let tokenKey: string
  if (path.startsWith('/t/')) {
    tokenKey = getTenantScopedKey('saas_token')
  } else if (import.meta.env.VITE_DESKTOP_TARGET !== 'true' && path.startsWith('/portal')) {
    tokenKey = 'portal_token'
  } else {
    // 桌面端及非租户路径：读无后缀的 saas_token
    tokenKey = 'saas_token'
  }
  const token = credentialGet(tokenKey)
  const headers: Record<string, string> = {}
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }
  // 在租户前台模式下添加 X-Tenant-Id header
  const tenantId = getCurrentTenantId()
  if (tenantId) {
    headers['X-Tenant-Id'] = tenantId
  }
  return headers
}

// ============== 图形验证码 ==============

export interface CaptchaResponse {
  success: boolean
  captcha_id?: string
  svg_base64?: string  // SVG 图片 base64 编码
  message?: string
}

/**
 * 获取图形验证码
 */
export async function getCaptcha(): Promise<CaptchaResponse> {
  const res = await fetch(`${API_BASE}/captcha`)
  return res.json()
}

// ============== 登录（新方式） ==============

export interface NewLoginRequest {
  identifier: string  // 手机号或用户名
  password: string
  captcha_code: string
  captcha_id: string
}

/**
 * 新登录接口：手机号/用户名 + 密码 + 图形验证码
 */
export async function login(request: NewLoginRequest): Promise<LoginResponse> {
  const res = await fetch(`${API_BASE}/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request)
  })
  return res.json()
}

// ============== 统一登录 ==============

export interface UnifiedLoginRequest {
  tenant_code: string
  identifier: string
  password: string
  captcha_code: string
  captcha_id: string
}

export interface UnifiedLoginResponse {
  success: boolean
  token?: string
  user?: {
    user_id: string
    username: string
    phone?: string
    avatar_url?: string
  }
  tenant_id?: string
  redirect_url?: string
  message?: string
  errors?: Array<{
    field: string
    message: string
  }>
}

/**
 * 统一登录接口：租户代码 + 手机号/用户名 + 密码 + 图形验证码
 */
export async function unifiedLogin(request: UnifiedLoginRequest): Promise<UnifiedLoginResponse> {
  const res = await fetch(`${API_BASE}/unified-login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request)
  })
  return res.json()
}

// ============== 忘记密码 ==============

export interface SendResetCodeRequest {
  phone: string
  captcha_code: string
  captcha_id: string
  tenant_id?: string
}

export interface ResetPasswordRequest {
  phone: string
  sms_code: string
  new_password: string
  tenant_id?: string
}

/**
 * 发送重置密码短信验证码（需先通过图形验证码）
 */
export async function sendResetPasswordCode(request: SendResetCodeRequest): Promise<SendCodeResponse> {
  const res = await fetch(`${API_BASE}/reset-password/send-code`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request)
  })
  return res.json()
}

/**
 * 重置密码
 */
export async function resetPassword(request: ResetPasswordRequest): Promise<{ success: boolean, message?: string }> {
  const res = await fetch(`${API_BASE}/reset-password`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request)
  })
  return res.json()
}
