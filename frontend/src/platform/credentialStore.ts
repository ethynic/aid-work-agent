const TOKEN_KEY = /^(demo_token|user_info|saas_(token|admin|tenant)(_[a-zA-Z0-9_-]{1,128})?)$/
const desktopMemory = new Map<string, string>()
const credentialVersions = new Map<string, number>()
let desktopHydrated = false

export function isCredentialKey(key: string): boolean {
  return TOKEN_KEY.test(key) && !key.startsWith('portal_')
}

export async function hydrateCredentialStore(): Promise<void> {
  const bridge = window.agentDesktop
  if (!bridge) return
  const values = await bridge.credentials.hydrate()
  desktopMemory.clear()
  credentialVersions.clear()
  for (const [key, value] of Object.entries(values)) {
    if (isCredentialKey(key) && typeof value === 'string') desktopMemory.set(key, value)
  }
  desktopHydrated = true
}

export function credentialGet(key: string): string | null {
  if (!window.agentDesktop) return localStorage.getItem(key)
  if (!desktopHydrated) throw new Error('Desktop credential store is not hydrated')
  if (!isCredentialKey(key)) return null
  return desktopMemory.get(key) ?? null
}

export async function credentialSet(key: string, value: string): Promise<void> {
  if (!window.agentDesktop) {
    localStorage.setItem(key, value)
    return
  }
  if (!desktopHydrated || !isCredentialKey(key)) throw new Error('Desktop credential write rejected')
  const version = (credentialVersions.get(key) ?? 0) + 1
  credentialVersions.set(key, version)
  await window.agentDesktop.credentials.set(key, value)
  if (credentialVersions.get(key) === version) desktopMemory.set(key, value)
}

export async function credentialRemove(key: string): Promise<void> {
  if (!window.agentDesktop) {
    localStorage.removeItem(key)
    return
  }
  if (!desktopHydrated || !isCredentialKey(key)) return
  const version = (credentialVersions.get(key) ?? 0) + 1
  credentialVersions.set(key, version)
  await window.agentDesktop.credentials.delete(key)
  if (credentialVersions.get(key) === version) desktopMemory.delete(key)
}

export function clearCredentialMemory(): void {
  desktopMemory.clear()
  credentialVersions.clear()
  desktopHydrated = false
}
