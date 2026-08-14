import type { CaptchaResponse, DesktopAuthSession, UnifiedLoginRequest, UnifiedLoginResponse } from '@shared/auth/contracts'
import type { ApiResolver, ApiTransport, CredentialStore } from '@shared/platform/contracts'

export const DESKTOP_AUTH_KEY = 'desktop_auth'

export interface DesktopAuthService {
  restore(): Promise<DesktopAuthSession | null>
  captcha(): Promise<CaptchaResponse>
  login(request: UnifiedLoginRequest): Promise<DesktopAuthSession>
  logout(): Promise<void>
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function isBoundedString(value: unknown, maximum = 4096): value is string {
  return typeof value === 'string' && value.length > 0 && value.length <= maximum
}

function parseUser(value: unknown): DesktopAuthSession['user'] | null {
  if (!isRecord(value) || !isBoundedString(value.user_id, 256) || !isBoundedString(value.username, 256)) return null
  if (value.phone !== undefined && typeof value.phone !== 'string') return null
  if (value.avatar_url !== undefined && typeof value.avatar_url !== 'string') return null
  return {
    user_id: value.user_id,
    username: value.username,
    ...(value.phone === undefined ? {} : { phone: value.phone }),
    ...(value.avatar_url === undefined ? {} : { avatar_url: value.avatar_url }),
  }
}

function parseSession(value: string | undefined): DesktopAuthSession | null {
  if (!value) return null
  try {
    const parsed: unknown = JSON.parse(value)
    if (!isRecord(parsed)) return null
    const user = parseUser(parsed.user)
    if (!isBoundedString(parsed.token, 32 * 1024) || !isBoundedString(parsed.tenantId, 256) || !isBoundedString(parsed.tenantCode, 8) || !user) return null
    return { token: parsed.token, tenantId: parsed.tenantId, tenantCode: parsed.tenantCode, user }
  } catch {
    return null
  }
}

function loginFailure(response: UnifiedLoginResponse): Error {
  const knownFields = new Set(['tenant_code', 'identifier', 'password', 'captcha_code'])
  const fieldErrors = Array.isArray(response.errors)
    ? response.errors.filter((entry) => isRecord(entry) && isBoundedString(entry.field, 64) && knownFields.has(entry.field) && isBoundedString(entry.message, 1024))
      .map((entry) => ({ field: entry.field as string, message: entry.message as string }))
    : []
  const serverMessage = isBoundedString(response.message, 1024) ? response.message : undefined
  const firstServerError = Array.isArray(response.errors) && isRecord(response.errors[0]) && isBoundedString(response.errors[0].message, 1024)
    ? response.errors[0].message
    : undefined
  const error = new Error(serverMessage || fieldErrors[0]?.message || firstServerError || '登录失败')
  Object.assign(error, { fieldErrors })
  return error
}

export function createDesktopAuthService(store: CredentialStore, resolver: ApiResolver, transport: ApiTransport): DesktopAuthService {
  let hydrated: Record<string, string> = {}
  return {
    async restore() {
      hydrated = await store.hydrate()
      return parseSession(hydrated[DESKTOP_AUTH_KEY])
    },
    async captcha() {
      const response = await transport.request<CaptchaResponse>(resolver.resolve('/auth/captcha'))
      if (!response || typeof response !== 'object' || response.success !== true || !isBoundedString(response.captcha_id, 256) || !isBoundedString(response.svg_base64, 256 * 1024)) {
        throw new Error('验证码响应格式无效')
      }
      return response
    },
    async login(request) {
      const response = await transport.request<UnifiedLoginResponse>(resolver.resolve('/auth/unified-login'), {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: request,
      })
      if (!response || typeof response !== 'object' || response.success !== true) throw loginFailure(response ?? { success: false })
      const user = parseUser(response.user)
      if (!isBoundedString(response.token, 32 * 1024) || !isBoundedString(response.tenant_id, 256) || !user) throw new Error('登录响应格式无效')
      const session: DesktopAuthSession = {
        token: response.token,
        tenantId: response.tenant_id,
        tenantCode: request.tenant_code,
        user,
      }
      await store.set(DESKTOP_AUTH_KEY, JSON.stringify(session))
      hydrated[DESKTOP_AUTH_KEY] = JSON.stringify(session)
      return session
    },
    async logout() {
      await store.delete(DESKTOP_AUTH_KEY)
      delete hydrated[DESKTOP_AUTH_KEY]
    },
  }
}
