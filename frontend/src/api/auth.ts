/**
 * 认证相关 API
 */

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

export interface WxQrcodeResponse {
  qrcode_url: string
  scene_str: string
  expire_seconds: number
}

export interface WxStatusResponse {
  status: 'waiting' | 'scanned' | 'confirmed' | 'expired'
  openid?: string
  unionid?: string
}

/**
 * 获取微信登录二维码
 */
export async function getWxQrcode(): Promise<WxQrcodeResponse> {
  const res = await fetch(`${API_BASE}/wx/qrcode`)
  return res.json()
}

/**
 * 检查微信扫码状态
 */
export async function checkWxQrcodeStatus(sceneStr: string): Promise<WxStatusResponse> {
  const res = await fetch(`${API_BASE}/wx/qrcode/${sceneStr}/status`)
  return res.json()
}

/**
 * 微信登录
 */
export async function wxLogin(wxOpenid: string, wxUnionid?: string): Promise<LoginResponse> {
  const res = await fetch(`${API_BASE}/wx/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ wx_openid: wxOpenid, wx_unionid: wxUnionid })
  })
  return res.json()
}

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
 * 用户注册
 */
export async function register(phone: string, password: string, code: string): Promise<LoginResponse> {
  const res = await fetch(`${API_BASE}/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ phone, password, code })
  })
  return res.json()
}

/**
 * 绑定手机号（微信用户绑定手机）
 */
export async function bindPhone(userId: string, phone: string, code: string): Promise<{ success: boolean, message?: string }> {
  const res = await fetch(`${API_BASE}/bind-phone`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id: userId, phone, code })
  })
  return res.json()
}

/**
 * 获取当前用户信息
 */
export async function getCurrentUser(): Promise<any> {
  const token = localStorage.getItem('auth_token')
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
  const token = localStorage.getItem('auth_token')
  if (token) {
    await fetch(`${API_BASE}/logout`, {
      method: 'POST',
      headers: { 'Authorization': `Bearer ${token}` }
    })
  }
  localStorage.removeItem('auth_token')
  localStorage.removeItem('user_info')
}

/**
 * 获取认证请求头
 */
export function getAuthHeader(): Record<string, string> {
  const token = localStorage.getItem('auth_token')
  if (token) {
    return { 'Authorization': `Bearer ${token}` }
  }
  return {}
}
