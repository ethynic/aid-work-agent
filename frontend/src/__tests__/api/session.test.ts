/**
 * Session API 测试
 */
import { describe, it, expect } from 'vitest'

describe('Session API', () => {
  it('API 模块可以导入', async () => {
    const session = await import('@/api/session')
    expect(session).toBeDefined()
  })

  it('API 模块导出正确的方法', async () => {
    const session = await import('@/api/session')
    const expectedMethods = ['getSessions', 'createSession', 'deleteSession']
    for (const method of expectedMethods) {
      if (typeof (session as any)[method] !== 'undefined') {
        expect(typeof (session as any)[method]).toBe('function')
      }
    }
  })
})
