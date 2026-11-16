import { describe, it, expect, beforeEach } from 'vitest'
import { buildIdentityQuery, resolveAnonUid, type IdentityQuery } from '@/api/webC'

describe('web_c 匿名身份持久化', () => {
  beforeEach(() => {
    localStorage.clear()
    document.cookie.split(';').forEach(c => {
      const name = c.split('=')[0].trim()
      if (name.startsWith('web_c_anon_')) document.cookie = `${name}=; max-age=0; path=/`
    })
  })

  it('生成 anon_uid 并双写 localStorage 与 cookie', () => {
    const uid = resolveAnonUid('cfg1')
    expect(uid).toMatch(/^anon_[0-9a-f-]{36}$/)
    expect(localStorage.getItem('web_c_anon_cfg1')).toBe(uid)
    expect(document.cookie).toContain('web_c_anon_cfg1=')
  })

  it('同一 config 复用同一 uid', () => {
    const first = resolveAnonUid('cfg1')
    expect(resolveAnonUid('cfg1')).toBe(first)
  })

  it('不同 config 不串', () => {
    const a = resolveAnonUid('cfg1')
    const b = resolveAnonUid('cfg2')
    expect(a).not.toBe(b)
  })

  it('localStorage 丢失时 cookie 兜底并回写', () => {
    const first = resolveAnonUid('cfg1')
    localStorage.clear()
    expect(resolveAnonUid('cfg1')).toBe(first)
    expect(localStorage.getItem('web_c_anon_cfg1')).toBe(first)
  })

  it('非法形态 uid 被重新生成', () => {
    localStorage.setItem('web_c_anon_cfg1', 'garbage')
    const uid = resolveAnonUid('cfg1')
    expect(uid).toMatch(/^anon_[0-9a-f-]{36}$/)
    expect(uid).not.toBe('garbage')
  })
})

describe('web_c 身份凭证构建', () => {
  it('URL 携带签名三元组时走 ext 身份', () => {
    const identity = buildIdentityQuery('cfg1', { u: 'u1001', ts: '1731000000', sig: 'ab12', name: '张三' })
    expect(identity.u).toBe('u1001')
    expect(identity.uid).toBeUndefined()
  })

  it('无签名参数时回退匿名 uid', () => {
    const identity: IdentityQuery = buildIdentityQuery('cfg1', {})
    expect(identity.uid).toMatch(/^anon_[0-9a-f-]{36}$/)
  })

  it('签名参数不全（缺 sig）时回退匿名', () => {
    const identity = buildIdentityQuery('cfg1', { u: 'u1001', ts: '1731000000' })
    expect(identity.uid).toMatch(/^anon_/)
  })
})
