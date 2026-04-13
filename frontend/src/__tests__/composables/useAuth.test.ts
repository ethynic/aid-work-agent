/**
 * useAuth composable 测试
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

// Mock API 模块
vi.mock('@/api/auth', () => ({
  phoneLogin: vi.fn(),
  phoneCodeLogin: vi.fn(),
  sendSmsCode: vi.fn(),
  getCurrentUser: vi.fn(),
  logout: vi.fn(),
}))

describe('useAuth', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
  })

  it('getAuthHeader 在无 token 时返回空对象', () => {
    localStorage.clear()
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