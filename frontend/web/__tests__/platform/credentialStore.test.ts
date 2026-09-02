import { afterEach, describe, expect, it, vi } from 'vitest'
import { clearCredentialMemory, credentialGet, credentialRemove, credentialSet, hydrateCredentialStore } from '@/platform/credentialStore'
import { configureRuntime } from '@/platform/runtime'
import { resolveApiUrl } from '@/platform/urlResolver'

afterEach(() => {
  clearCredentialMemory()
  Reflect.deleteProperty(window, 'agentDesktop')
  localStorage.clear()
  configureRuntime({ target: 'web', apiBaseUrl: '/api' })
})

describe('CredentialStore contract', () => {
  it('Web 保持原 localStorage key 和同步读写语义，避免现有登录回归', () => {
    credentialSet('saas_token', 'web-value')
    expect(credentialGet('saas_token')).toBe('web-value')
    expect(localStorage.getItem('saas_token')).toBe('web-value')
    credentialRemove('saas_token')
    expect(localStorage.getItem('saas_token')).toBeNull()
  })

  it('Desktop 启动 hydrate 后只使用内存并通过白名单桥持久化，不写明文 localStorage', async () => {
    const set = vi.fn().mockResolvedValue(undefined)
    const remove = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(window, 'agentDesktop', {
      configurable: true,
      value: {
        version: 1,
        runtime: { target: 'desktop', apiBaseUrl: 'https://api.example.test/api' },
        credentials: { hydrate: vi.fn().mockResolvedValue({ saas_token: 'hydrated-value', portal_token: 'blocked' }), set, delete: remove },
        system: {}
      }
    })
    await hydrateCredentialStore()
    expect(credentialGet('saas_token')).toBe('hydrated-value')
    expect(credentialGet('portal_token')).toBeNull()
    await credentialSet('saas_token_acme', 'tenant-value')
    expect(localStorage.getItem('saas_token_acme')).toBeNull()
    expect(set).toHaveBeenCalledWith('saas_token_acme', 'tenant-value')
    await credentialRemove('saas_token_acme')
    expect(remove).toHaveBeenCalledWith('saas_token_acme')
  })
})

describe('Desktop credential failure and logout ordering', () => {
  it('fails before hydrate and does not fall back to plaintext localStorage when hydrate fails', async () => {
    localStorage.setItem('saas_token', 'plaintext-must-not-be-used')
    Object.defineProperty(window, 'agentDesktop', {
      configurable: true,
      value: { credentials: { hydrate: vi.fn().mockRejectedValue(new Error('decrypt failed')) } }
    })
    await expect(hydrateCredentialStore()).rejects.toThrow('decrypt failed')
    expect(() => credentialGet('saas_token')).toThrow(/not hydrated/)
  })

  it('rolls back a failed current write without letting an older failure erase a newer login', async () => {
    let rejectFirst!: (error: Error) => void
    const first = new Promise<void>((_resolve, reject) => { rejectFirst = reject })
    const set = vi.fn().mockReturnValueOnce(first).mockResolvedValueOnce(undefined)
    Object.defineProperty(window, 'agentDesktop', {
      configurable: true,
      value: { credentials: { hydrate: vi.fn().mockResolvedValue({}), set, delete: vi.fn().mockResolvedValue(undefined) } }
    })
    await hydrateCredentialStore()
    const oldWrite = credentialSet('saas_token', 'old-login')
    await credentialSet('saas_token', 'new-login')
    rejectFirst(new Error('old write failed'))
    await expect(oldWrite).rejects.toThrow('old write failed')
    await vi.waitFor(() => expect(credentialGet('saas_token')).toBe('new-login'))
  })

  it('keeps logout memory cleared when persistent delete fails loudly', async () => {
    Object.defineProperty(window, 'agentDesktop', {
      configurable: true,
      value: {
        credentials: {
          hydrate: vi.fn().mockResolvedValue({ saas_token: 'remembered' }),
          set: vi.fn().mockResolvedValue(undefined),
          delete: vi.fn().mockRejectedValue(new Error('disk failure'))
        }
      }
    })
    await hydrateCredentialStore()
    await expect(credentialRemove('saas_token')).rejects.toThrow('disk failure')
    expect(credentialGet('saas_token')).toBe('remembered')
  })
})

describe('runtime URL resolver', () => {
  it('Web 保留相对 URL；Desktop 将 /api 文件地址解析到已验证 API origin', () => {
    expect(resolveApiUrl('/api/files/1/download')).toBe('/api/files/1/download')
    configureRuntime({ target: 'desktop', apiBaseUrl: 'https://api.example.test/api' })
    expect(resolveApiUrl('/api/files/1/download')).toBe('https://api.example.test/api/files/1/download')
    expect(resolveApiUrl('/knowledge/documents/1')).toBe('https://api.example.test/api/knowledge/documents/1')
  })
})
