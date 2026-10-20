/** Pure Host producer; original process completion is deliberately independent. */
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { randomUUID } from 'node:crypto'
import { recoveryObservation } from '../src/recoveryObservation.js'
import type { ProviderSet } from '../src/providerManager.js'

function args() { return { version: 2, scope_id: randomUUID(), device_id: randomUUID(), original_invocation_ids: [randomUUID()] } }

test('first observation reads immediately without native supervision, reboot or Runtime restart', async () => {
  const calls: unknown[] = []
  const providers = { get(key: string) { assert.equal(key, 'boss-recruiting'); return { async callTool(name: string, body: unknown) {
    calls.push([name, body]); return { success: true, effect: 'none', data: {
      target_relation: 'current_unbound', browser_target_ref: randomUUID(), page_summary: { observed: true, page_kind: 'recommend', result: 'unknown', filter_count: 2, panel_open: false },
      resource_stopped: true, stop_evidence: 'provider cannot invent Host stop evidence',
    } }
  } } } } as unknown as ProviderSet
  const request = args()
  const result = await recoveryObservation(providers).callTool('runtime_recovery_observe', request, {})
  const value = result.data!.recovery_observation as Record<string, unknown>
  assert.equal(result.success, true)
  assert.deepEqual(calls, [['boss_recovery_probe', {}]])
  assert.equal(value.scope_id, request.scope_id)
  assert.equal(value.resource_stopped, false)
  assert.equal(value.stop_error_code, 'LOCAL_RECOVERY_STOP_UNPROVED')
  assert.equal(value.stop_evidence, undefined)
  assert.deepEqual(value.original_invocation_ids, request.original_invocation_ids)
})

test('original target and minimal expectation flow only into the strict page probe', async () => {
  const request = { ...args(), browser_target_ref: randomUUID(), expectation: { kind: 'clear_filter' } }
  const providers = { get() { return { async callTool(name: string, body: unknown) {
    assert.equal(name, 'boss_recovery_probe')
    assert.deepEqual(body, { _boss_target_ref: request.browser_target_ref, expectation: request.expectation })
    return { success: true, effect: 'none', data: { target_relation: 'original', browser_target_ref: request.browser_target_ref,
      page_summary: { observed: true, page_kind: 'recommend', result: 'match', filter_count: 0, panel_open: false } } }
  } } } } as unknown as ProviderSet
  const result = await recoveryObservation(providers).callTool('runtime_recovery_observe', request, {})
  assert.equal(result.success, true)
  assert.equal((result.data!.recovery_observation as Record<string, unknown>).resource_stopped, false)
})

test('page failure reports page unavailable and stop unknown separately', async () => {
  const providers = { get() { throw new Error('disconnected') } } as unknown as ProviderSet
  const result = await recoveryObservation(providers).callTool('runtime_recovery_observe', args(), {})
  const value = result.data!.recovery_observation as Record<string, unknown>
  assert.equal(result.success, false)
  assert.equal(result.code, 'LOCAL_PAGE_OBSERVATION_FAILED')
  assert.equal(value.resource_stopped, false)
  assert.deepEqual(value.page_summary, { observed: false, page_kind: 'unavailable', result: 'unknown' })
})
