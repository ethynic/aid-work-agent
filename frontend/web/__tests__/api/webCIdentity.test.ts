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

  it('签名参数不全（缺 sig）时按 userid 链接透传（uid 渠道可用，其余渠道后端 403）', () => {
    const identity = buildIdentityQuery('cfg1', { u: 'u1001', ts: '1731000000' })
    expect(identity.u).toBe('u1001')
    expect(identity.uid).toBeUndefined()
  })
})

describe('web_c uid 链接身份（设计 §3.5）', () => {
  it('仅有 u 无签名时按 userid 链接透传（可用性由后端 identity_mode 门控）', () => {
    const identity = buildIdentityQuery('cfg1', { u: 'u1001' })
    expect(identity.u).toBe('u1001')
    expect(identity.uid).toBeUndefined()
  })

  it('u + 部分签名参数同样透传（非 uid 渠道由后端 403）', () => {
    const identity = buildIdentityQuery('cfg1', { u: 'u1001', ts: '1731000000' })
    expect(identity.u).toBe('u1001')
    expect(identity.ts).toBe('1731000000')
    expect(identity.sig).toBe('')
  })

  it('uid 模式当前会话 sessionStorage 持有：写入/读取/清除', async () => {
    const { readStoredSessionId, writeStoredSessionId, clearStoredSessionId } = await import('@/api/webC')
    expect(readStoredSessionId('cfg1')).toBe('')
    writeStoredSessionId('cfg1', 'tenant_x_web_c_uid_u1_main_s0a1b2c3d4e5')
    expect(readStoredSessionId('cfg1')).toBe('tenant_x_web_c_uid_u1_main_s0a1b2c3d4e5')
    expect(sessionStorage.getItem('web_c_sess_cfg1')).toBe('tenant_x_web_c_uid_u1_main_s0a1b2c3d4e5')
    clearStoredSessionId('cfg1')
    expect(readStoredSessionId('cfg1')).toBe('')
  })

  it('非法/超长 session id 不写入', async () => {
    const { writeStoredSessionId, readStoredSessionId } = await import('@/api/webC')
    writeStoredSessionId('cfg1', '')
    expect(readStoredSessionId('cfg1')).toBe('')
    writeStoredSessionId('cfg1', 'x'.repeat(201))
    expect(readStoredSessionId('cfg1')).toBe('')
  })
})
