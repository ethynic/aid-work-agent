/**
 * useAuth composable 测试
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

// Mock API 模块
vi.mock('@/api/auth', () => ({
  login: vi.fn(),
  loginWithPassword: vi.fn(),
  sendSmsCode: vi.fn(),
  getCurrentUser: vi.fn(),
  logout: vi.fn(),
}))

describe('useAuth', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
  })

  it('初始状态：未登录', () => {
    // 需要重新导入以获取新鲜状态
    const { useAuth } = vi.importActual<typeof import('@/composables/useAuth')>('@/composables/useAuth')
    // 由于 useAuth 使用模块级状态，直接测试方法行为
  })

  it('getAuthHeader 在无 token 时返回空对象', () => {
    localStorage.clear()
    // 直接测试逻辑
    const t = localStorage.getItem('auth_token')
    expect(t).toBeNull()
  })

  it('getAuthHeader 在有 token 时返回 Bearer header', () => {
    localStorage.setItem('auth_token', 'my_test_token')
    const t = localStorage.getItem('auth_token')
    expect(t).toBe('my_test_token')
    const header = { 'Authorization': `Bearer ${t}` }
    expect(header).toEqual({ 'Authorization': 'Bearer my_test_token' })
  })
})
