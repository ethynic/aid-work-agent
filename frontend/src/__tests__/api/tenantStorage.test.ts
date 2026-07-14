import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { getTenantScopedKey } from '@/api/tenantStorage'

/**
 * 验证不同路由下 localStorage key 的解析规则。
 *
 * 核心保证：平台管理员同时打开多个 /t/:tenant_id 标签页时，
 * 每个 tab 拥有独立的 saas_token_{tid} / saas_admin_{tid} / saas_tenant_{tid} 槽位，
 * 互不覆盖。
 */
describe('tenantStorage.getTenantScopedKey', () => {
  const originalLocation = window.location

  beforeEach(() => {
    // 用 defineProperty 重写 location.pathname，因为 pathname 是只读
    Object.defineProperty(window, 'location', {
      value: { ...originalLocation, pathname: '/' },
      writable: true,
      configurable: true,
    })
    localStorage.clear()
  })

  afterEach(() => {
    Object.defineProperty(window, 'location', {
      value: originalLocation,
      writable: true,
      configurable: true,
    })
  })

  function setPath(path: string) {
    Object.defineProperty(window, 'location', {
      value: { ...originalLocation, pathname: path },
      writable: true,
      configurable: true,
    })
  }

  it.each([
    ['/t/tenant_55430d86c2f5', 'saas_token_tenant_55430d86c2f5'],
    ['/t/tenant_55430d86c2f5/chat/abc', 'saas_token_tenant_55430d86c2f5'],
    ['/t/tenant_a/some/deep/path', 'saas_token_tenant_a'],
  ])('租户前台 /t/:tenant_id/... → %s 应解析为 %s', (path, expected) => {
    setPath(path)
    expect(getTenantScopedKey('saas_token')).toBe(expected)
    expect(getTenantScopedKey('saas_admin')).toBe(expected.replace('token', 'admin'))
    expect(getTenantScopedKey('saas_tenant')).toBe(expected.replace('token', 'tenant'))
  })

  it('URL 编码字符会被解码并做安全过滤', () => {
    setPath('/t/abc%2Fdef')
    // %2F 解码为 /，但被白名单过滤为 _
    expect(getTenantScopedKey('saas_token')).toBe('saas_token_abc_def')
  })

  it('tenant_id 中含非法字符会被替换为下划线', () => {
    setPath('/t/tenant.abc/123')
    // . 不在白名单中，被替换为 _
    expect(getTenantScopedKey('saas_token')).toBe('saas_token_tenant_abc')
  })

  it('平台管理后台 /portal → portal_token / portal_admin / portal_tenant', () => {
    setPath('/portal/dashboard')
    expect(getTenantScopedKey('saas_token')).toBe('portal_token')
    expect(getTenantScopedKey('saas_admin')).toBe('portal_admin')
    expect(getTenantScopedKey('saas_tenant')).toBe('portal_tenant')
  })

  it('演示模式 / 保持原 saas_token / saas_admin / saas_tenant', () => {
    setPath('/')
    expect(getTenantScopedKey('saas_token')).toBe('saas_token')
    expect(getTenantScopedKey('saas_admin')).toBe('saas_admin')
    expect(getTenantScopedKey('saas_tenant')).toBe('saas_tenant')
  })

  it('演示模式 /chat 保持原 saas_*', () => {
    setPath('/chat/some-agent')
    expect(getTenantScopedKey('saas_token')).toBe('saas_token')
  })

  it('两个不同租户的 token 写入不会互相覆盖（隔离验证）', () => {
    setPath('/t/tenant_aaa')
    localStorage.setItem(getTenantScopedKey('saas_token'), 'token-aaa')
    setPath('/t/tenant_bbb')
    localStorage.setItem(getTenantScopedKey('saas_token'), 'token-bbb')

    // 切换回 tenant_aaa 应该读到原 token
    setPath('/t/tenant_aaa')
    expect(localStorage.getItem(getTenantScopedKey('saas_token'))).toBe('token-aaa')
    // 切到 tenant_bbb 应读到自己的 token
    setPath('/t/tenant_bbb')
    expect(localStorage.getItem(getTenantScopedKey('saas_token'))).toBe('token-bbb')
  })
})
