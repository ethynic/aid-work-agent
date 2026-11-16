/**
 * R9 修复3（契约「停止与接续」）：受管停止动作前的最后复判——started 幂等重发
 * 触发服务端「scope 原调用已全部可信终态 → 停止无需派发」复判；拒绝时不再执行
 * 受管停止（不关受管资源），回 CANCELLED 终态（服务端已按 superseded 路径落终态
 * 并接续）；复判放行（无回执）照常受管停止（回归）。
 */
import assert from 'node:assert/strict'
import { mkdtempSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { randomUUID } from 'node:crypto'
import { test } from 'node:test'
import { runInvocation } from '../src/invocationRunner.js'
import type { RunnerDeps } from '../src/invocationRunner.js'
import { recoveryObservation } from '../src/recoveryObservation.js'
import { ApiError } from '../src/apiClient.js'
import type { ApiClient, ClaimedInvocation, InvocationResultPayload } from '../src/apiClient.js'
import type { ProviderSet } from '../src/providerManager.js'
import { deriveResourceKey } from '../src/desktopLock.js'

interface Harness {
  deps: RunnerDeps
  startedCalls: number
  stopCalls: number
  results: Array<Record<string, unknown>>
  /** 模拟服务端复判：置 true 后 started 幂等重发返回 404（scope 原调用已可信终态） */
  supersede: () => void
}

function makeHarness(): Harness {
  const state = { startedCalls: 0, stopCalls: 0, superseded: false }
  const results: Array<Record<string, unknown>> = []
  const api = {
    started: async () => {
      state.startedCalls += 1
      if (state.startedCalls > 1 && state.superseded) {
        throw new ApiError(404, 'invocation 不存在或 claim token 不匹配')
      }
    },
    progress: async () => ({ seq: 1, cancel: false }),
    result: async (_id: string, payload: InvocationResultPayload) => {
      results.push({ ...payload } as Record<string, unknown>)
    },
    operationResult: async () => ({}),
  } as unknown as ApiClient
  const providers = {
    async recoverIdlePool(action: () => Promise<unknown>) {
      return action()
    },
    get(key: string) {
      assert.equal(key, 'boss-recruiting')
      throw new Error('page probe unavailable in this fixture (page_target_unavailable)')
    },
    has() {
      return true
    },
  } as unknown as ProviderSet
  const supervision = {
    async stopManagedResources() {
      state.stopCalls += 1
      return { stop_evidence: 'supervised_device_boundary', resource_scope: 'managed_provider_pool', runtime_generation: randomUUID() }
    },
  } as Parameters<typeof recoveryObservation>[1]
  const deps: RunnerDeps = {
    api,
    providers,
    recoveryObservation: recoveryObservation(providers, supervision),
    desktopCheck: async () => true,
    desktopResourceKey: deriveResourceKey(randomUUID()),
    retryBaseMs: 10,
    retryMaxMs: 50,
    runtimeDataDir: mkdtempSync(path.join(os.tmpdir(), 'aidwork-r9-stop-')),
    progressIntervalMs: 40,
    onEvent: () => {},
  }
  return { deps, get startedCalls() { return state.startedCalls }, get stopCalls() { return state.stopCalls }, results, supersede: () => { state.superseded = true } }
}

function stopInvocation(): ClaimedInvocation {
  return {
    invocation_id: `inv-r9-stop-${randomUUID().slice(0, 8)}`,
    tool_name: 'runtime_recovery_observe',
    arguments: { version: 3, scope_id: randomUUID(), device_id: randomUUID(),
      original_invocation_ids: [randomUUID()], stop_resources: true, page_target_unavailable: true },
    claim_token: 'tok-r9-stop',
    provider: 'runtime-recovery',
  }
}

test('回执已到（复判拒绝）：不执行受管停止，回执不宣称停稳', async () => {
  const harness = makeHarness()
  // 停止动作前回执落库：服务端复判开始拒绝（superseded 落终态 + 接续照常）
  harness.supersede()
  await runInvocation(stopInvocation(), harness.deps)
  assert.equal(harness.startedCalls, 2, 'started 确认 + 停止动作前复判各一次')
  assert.equal(harness.stopCalls, 0, '不执行受管停止（不关受管资源）')
  assert.equal(harness.results.length, 1)
  assert.equal(harness.results[0]!['code'], 'CANCELLED')
  assert.equal(harness.results[0]!['effect'], 'none')
  const data = harness.results[0]!['data'] as Record<string, unknown> | undefined
  assert.equal(data, undefined, '未停止不得产生任何停稳证明数据')
})

test('无回执（复判放行）：照常受管停止并携带停稳证明（回归）', async () => {
  const harness = makeHarness()
  await runInvocation(stopInvocation(), harness.deps)
  assert.equal(harness.startedCalls, 2)
  assert.equal(harness.stopCalls, 1, '照常执行一次受管停止')
  assert.equal(harness.results.length, 1)
  const data = harness.results[0]!['data'] as { recovery_observation?: Record<string, unknown> }
  assert.equal(data.recovery_observation!['resource_stopped'], true)
  assert.equal(data.recovery_observation!['stop_evidence'], 'supervised_device_boundary')
})
