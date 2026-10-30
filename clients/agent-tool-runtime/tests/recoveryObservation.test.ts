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

test('WeCom recovery uses the existing managed pool proof without requiring or probing BOSS', async () => {
  let stopped = 0
  const providers = { async recoverIdlePool(action: () => Promise<unknown>) { return action() },
    get() { throw new Error('WeCom recovery must not open a BOSS page') } } as unknown as ProviderSet
  const request = { ...args(), version: 3, provider_key: 'wecom', stop_resources: true }
  const result = await recoveryObservation(providers, { async stopManagedResources() {
    stopped++; return { stop_evidence: 'supervised_device_boundary', resource_scope: 'managed_provider_pool', runtime_generation: 'fixture-generation' }
  } } as any).callTool('runtime_recovery_observe', request, {})
  const value = result.data!.recovery_observation as Record<string, unknown>
  assert.equal(stopped, 1); assert.equal(result.success, true); assert.equal(result.effect, 'none')
  assert.equal(value.resource_stopped, true); assert.equal(value.provider_scope, 'wecom')
  assert.equal(value.target_relation, 'not_applicable'); assert.equal(value.browser_target_ref, null)
  assert.deepEqual(value.page_summary, { observed: false, page_kind: 'not_applicable', result: 'unknown' })
  assert.deepEqual(value.original_invocation_ids, request.original_invocation_ids)
  assert.equal(value.send_status, undefined, 'resource stop cannot claim the original message was sent')
})

test('WeCom busy pool remains unproved without touching BOSS or pretending another account can recover it', async () => {
  const providers = { async recoverIdlePool() { throw new Error('LOCAL_RECOVERY_BUSY') },
    get() { throw new Error('wrong provider') } } as unknown as ProviderSet
  const result = await recoveryObservation(providers, {} as any).callTool('runtime_recovery_observe',
    { ...args(), version: 3, provider_key: 'wecom', stop_resources: true }, {})
  const value = result.data!.recovery_observation as Record<string, unknown>
  assert.equal(result.success, false); assert.equal(value.resource_stopped, false)
  assert.equal(value.stop_error_code, 'LOCAL_RECOVERY_BUSY'); assert.equal(value.provider_scope, 'wecom')
})
