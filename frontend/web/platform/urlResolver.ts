import { getRuntime } from './runtime'

export function resolveApiUrl(value: string): string {
  if (/^https?:\/\//i.test(value)) return value
  const runtime = getRuntime()
  if (runtime.target === 'web') return value
  if (!value.startsWith('/')) throw new Error('relative API URL must start with /')
  const base = new URL(runtime.apiBaseUrl)
  if (value === '/api' || value.startsWith('/api/')) return new URL(value, base.origin).toString()
  return new URL(`${base.pathname.replace(/\/$/, '')}/${value.replace(/^\//, '')}`, base.origin).toString()
}

export async function openExternalUrl(value: string): Promise<void> {
  if (window.agentDesktop) {
    await window.agentDesktop.system.openExternal(value)
    return
  }
  window.open(value, '_blank', 'noopener,noreferrer')
}

export async function saveDownloadUrl(url: string, suggestedName: string, headers: Record<string, string> = {}): Promise<boolean> {
  if (!window.agentDesktop) return false
  const result = await window.agentDesktop.system.saveDownload({
    url: resolveApiUrl(url),
    suggestedName,
    authorization: headers.Authorization,
    tenantId: headers['X-Tenant-Id']
  })
  return result.saved
}
