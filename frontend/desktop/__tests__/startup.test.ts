import { describe, expect, it } from 'vitest'
import { compareVersions, initialStartupState, reduceStartup } from '@desktop/state/startup'

describe('Desktop startup reducer', () => {
  it('moves through secure store, authentication and connectivity independently', () => {
    let state = reduceStartup(initialStartupState(), { type: 'SECURE_STORE_READY' })
    state = reduceStartup(state, { type: 'AUTH_REQUIRED' })
    state = reduceStartup(state, { type: 'CONNECTIVITY_CHANGED', connectivity: 'online' })
    expect(state).toMatchObject({ phase: 'auth-required', connectivity: 'online' })
    state = reduceStartup(state, { type: 'AUTHENTICATED', session: { token: 'secret', tenantId: 't1', tenantCode: 'ACME', user: { user_id: 'u1', username: 'User' } } })
    expect(state.phase).toBe('authenticated')
  })

  it('keeps fatal-local terminal and models minimum version ordering', () => {
    const fatal = reduceStartup(initialStartupState(), { type: 'FATAL_LOCAL', message: 'secure store unavailable' })
    expect(reduceStartup(fatal, { type: 'AUTH_REQUIRED' })).toEqual(fatal)
    expect(compareVersions('1.9.9', '1.10.0')).toBe(-1)
    expect(compareVersions('2.0', '2.0.0')).toBe(0)
    expect(compareVersions('2.0.0-beta.1', '2.0.0')).toBe(-1)
    expect(() => compareVersions('release-2', '2.0.0')).toThrow('版本策略格式无效')
    expect(reduceStartup(initialStartupState(), { type: 'UPDATE_REQUIRED', currentVersion: '1.0.0', minimumVersion: '2.0.0' })).toMatchObject({ phase: 'update-required', minimumVersion: '2.0.0' })
  })

  it('ignores stale startup/auth events and preserves error priority', () => {
    const session = { token: 'secret', tenantId: 't1', tenantCode: 'ACME', user: { user_id: 'u1', username: 'User' } }
    const authenticated = reduceStartup(initialStartupState(), { type: 'AUTHENTICATED', session })
    expect(reduceStartup(authenticated, { type: 'SECURE_STORE_READY' })).toEqual(authenticated)

    const updateRequired = reduceStartup(authenticated, { type: 'UPDATE_REQUIRED', currentVersion: '1.0.0', minimumVersion: '2.0.0' })
    expect(reduceStartup(updateRequired, { type: 'SIGNED_OUT' })).toEqual(updateRequired)
    expect(reduceStartup(updateRequired, { type: 'AUTHENTICATED', session })).toEqual(updateRequired)
    expect(reduceStartup(updateRequired, { type: 'CONNECTIVITY_CHANGED', connectivity: 'offline' })).toMatchObject({ phase: 'update-required', connectivity: 'offline' })
    expect(reduceStartup(updateRequired, { type: 'FATAL_LOCAL', message: 'credential store failed' })).toMatchObject({ phase: 'fatal-local', message: 'credential store failed' })
  })
})
