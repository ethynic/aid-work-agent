/**
 * Auth API 测试
 */
import { describe, it, expect, vi, beforeAll } from 'vitest'

// 在 API 测试中 msw 会自动拦截请求

describe('Auth API', () => {
  beforeAll(() => {
    // 确保测试环境中有 fetch
  })

  it('API 模块可以导入', async () => {
    const auth = await import('@/api/auth')
    expect(auth).toBeDefined()
    expect(typeof auth.login).toBe('function')
    expect(typeof auth.logout).toBe('function')
    expect(typeof auth.getCurrentUser).toBe('function')
  })

  it('API 模块导出正确的方法', async () => {
    const auth = await import('@/api/auth')
    const methods = ['login', 'loginWithPassword', 'sendSmsCode', 'getCurrentUser', 'logout']
    for (const method of methods) {
      expect(typeof (auth as any)[method]).toBe('function')
    }
  })
})
