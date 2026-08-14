export interface AuthUser {
  user_id: string
  username: string
  phone?: string
  avatar_url?: string
}

export interface DesktopAuthSession {
  token: string
  tenantId: string
  tenantCode: string
  user: AuthUser
}

export interface CaptchaResponse {
  success: boolean
  captcha_id?: string
  svg_base64?: string
  message?: string
}

export interface UnifiedLoginRequest {
  tenant_code: string
  identifier: string
  password: string
  captcha_code: string
  captcha_id: string
}

export interface AuthFieldError {
  field: string
  message: string
}

export interface UnifiedLoginResponse {
  success: boolean
  token?: string
  user?: AuthUser
  tenant_id?: string
  message?: string
  errors?: AuthFieldError[]
}
