import type { DesktopAuthSession } from '@shared/auth/contracts'

export type StartupPhase = 'booting' | 'secure-store-ready' | 'auth-required' | 'authenticated' | 'update-required' | 'fatal-local'
export type Connectivity = 'online' | 'offline'

export interface StartupState {
  phase: StartupPhase
  connectivity: Connectivity
  session: DesktopAuthSession | null
  currentVersion: string
  minimumVersion?: string
  message?: string
}

export type StartupEvent =
  | { type: 'SECURE_STORE_READY' }
  | { type: 'AUTH_REQUIRED' }
  | { type: 'AUTHENTICATED'; session: DesktopAuthSession }
  | { type: 'CONNECTIVITY_CHANGED'; connectivity: Connectivity }
  | { type: 'UPDATE_REQUIRED'; currentVersion: string; minimumVersion: string }
  | { type: 'FATAL_LOCAL'; message: string }
  | { type: 'SIGNED_OUT' }

export function initialStartupState(): StartupState {
  return { phase: 'booting', connectivity: 'offline', session: null, currentVersion: '0.0.0' }
}

export function reduceStartup(state: StartupState, event: StartupEvent): StartupState {
  if (state.phase === 'fatal-local' && event.type !== 'FATAL_LOCAL') return state
  if (state.phase === 'update-required' && event.type !== 'FATAL_LOCAL' && event.type !== 'CONNECTIVITY_CHANGED' && event.type !== 'UPDATE_REQUIRED') return state
  switch (event.type) {
    case 'SECURE_STORE_READY': return state.phase === 'booting' ? { ...state, phase: 'secure-store-ready', message: undefined } : state
    case 'AUTH_REQUIRED': return state.phase === 'booting' || state.phase === 'secure-store-ready' ? { ...state, phase: 'auth-required', session: null } : state
    case 'AUTHENTICATED': return { ...state, phase: 'authenticated', session: event.session }
    case 'CONNECTIVITY_CHANGED': return { ...state, connectivity: event.connectivity }
    case 'UPDATE_REQUIRED': return { ...state, phase: 'update-required', currentVersion: event.currentVersion, minimumVersion: event.minimumVersion }
    case 'FATAL_LOCAL': return { ...state, phase: 'fatal-local', message: event.message, session: null }
    case 'SIGNED_OUT': return { ...state, phase: 'auth-required', session: null }
  }
}

export function compareVersions(left: string, right: string): number {
  const parse = (value: string) => {
    const match = /^(0|[1-9]\d*)(?:\.(0|[1-9]\d*))?(?:\.(0|[1-9]\d*))?(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$/.exec(value)
    if (!match) throw new Error('版本策略格式无效，无法安全启动。')
    return {
      core: [Number(match[1]), Number(match[2] ?? 0), Number(match[3] ?? 0)],
      prerelease: match[4]?.split('.'),
    }
  }
  const a = parse(left)
  const b = parse(right)
  for (let index = 0; index < a.core.length; index += 1) {
    const difference = a.core[index] - b.core[index]
    if (difference !== 0) return difference > 0 ? 1 : -1
  }
  if (!a.prerelease && !b.prerelease) return 0
  if (!a.prerelease) return 1
  if (!b.prerelease) return -1
  for (let index = 0; index < Math.max(a.prerelease.length, b.prerelease.length); index += 1) {
    const leftIdentifier = a.prerelease[index]
    const rightIdentifier = b.prerelease[index]
    if (leftIdentifier === undefined) return -1
    if (rightIdentifier === undefined) return 1
    if (leftIdentifier === rightIdentifier) continue
    const leftNumeric = /^\d+$/.test(leftIdentifier)
    const rightNumeric = /^\d+$/.test(rightIdentifier)
    if (leftNumeric && rightNumeric) return Number(leftIdentifier) > Number(rightIdentifier) ? 1 : -1
    if (leftNumeric !== rightNumeric) return leftNumeric ? -1 : 1
    return leftIdentifier > rightIdentifier ? 1 : -1
  }
  return 0
}
