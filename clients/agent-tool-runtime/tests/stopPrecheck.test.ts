/**
 * R9 修复3（契约「停止与接续」）：受管停止动作前的最后复判——started 幂等重发
 * 触发服务端「scope 原调用已全部可信终态 → 停止无需派发」复判；拒绝时不再执行
 * 受管停止（不关受管资源），回 CANCELLED 终态（服务端已按 superseded 路径落终态
 * 并接续）；复判放行（无回执）照常受管停止（回归）。
 * R10 修复2：复判传输中断（响应未送达，执行状态未知）不得当作「继续停止」——
 * 沿既有传输重试机制有界重发 /started（claim_token 幂等）；明确响应后才分派；
 * 重试耗尽仍无明确响应 → 不执行受管停止，按 stop-unproven 形态如实回传。
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
import { ApiError, NetworkError } from '../src/apiClient.js'
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
  /** 预设停止前复判（第 2 次起的 started）按序抛出的错误；耗尽后回落默认语义 */
  failPrecheckWith: (...errors: Array<unknown>) => void
}

function makeHarness(): Harness {
  const state = { startedCalls: 0, stopCalls: 0, superseded: false, precheckErrors: [] as Array<unknown> }
  const results: Array<Record<string, unknown>> = []
  const api = {
    started: async () => {
      state.startedCalls += 1
      if (state.startedCalls === 1) return
      if (state.precheckErrors.length > 0) throw state.precheckErrors.shift()
      if (state.superseded) {
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
  return {
    deps,
    get startedCalls() { return state.startedCalls },
    get stopCalls() { return state.stopCalls },
    results,
    supersede: () => { state.superseded = true },
    failPrecheckWith: (...errors: Array<unknown>) => { state.precheckErrors.push(...errors) },
  }
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

test('R10 复判传输中断一次、重试后明确 settled：跳过受管停止', async () => {
  const harness = makeHarness()
  harness.supersede()
  harness.failPrecheckWith(new NetworkError('网络请求失败: 模拟传输中断'))
  await runInvocation(stopInvocation(), harness.deps)
  assert.equal(harness.startedCalls, 3, 'started 确认 + 复判首发（传输中断）+ 重试拿到明确 404')
  assert.equal(harness.stopCalls, 0, '明确 settled 后不执行受管停止')
  assert.equal(harness.results.length, 1)
  assert.equal(harness.results[0]!['code'], 'CANCELLED')
  const data = harness.results[0]!['data'] as Record<string, unknown> | undefined
  assert.equal(data, undefined, '未停止不得产生任何停稳证明数据')
})

test('R10 复判传输中断持续到耗尽：不执行受管停止，回 stop-unproven 形态', async () => {
  const harness = makeHarness()
  harness.failPrecheckWith(
    new NetworkError('网络请求失败: 模拟传输中断'),
    new NetworkError('网络请求失败: 模拟传输中断'),
    new NetworkError('网络请求失败: 模拟传输中断'),
    new NetworkError('网络请求失败: 模拟传输中断'),
  )
  await runInvocation(stopInvocation(), harness.deps)
  assert.equal(harness.startedCalls, 5, 'started 确认 + 复判有界重试共 4 次尝试')
  assert.equal(harness.stopCalls, 0, '重试耗尽仍无明确响应：不执行受管停止（宁可少关不误关）')
  assert.equal(harness.results.length, 1)
  assert.equal(harness.results[0]!['code'], 'LOCAL_RECOVERY_STOP_UNPROVED')
  assert.equal(harness.results[0]!['effect'], 'none')
  const data = harness.results[0]!['data'] as Record<string, unknown> | undefined
  assert.equal(data, undefined, '状态未知时不得产生任何停稳证明数据（不产生假完成）')
})

test('R10 复判传输中断一次、重试后明确放行：照常受管停止', async () => {
  const harness = makeHarness()
  harness.failPrecheckWith(new NetworkError('网络请求失败: 模拟传输中断'))
  await runInvocation(stopInvocation(), harness.deps)
  assert.equal(harness.startedCalls, 3, 'started 确认 + 复判首发（传输中断）+ 重试明确放行')
  assert.equal(harness.stopCalls, 1, '明确「仍在执行」后照常受管停止')
  assert.equal(harness.results.length, 1)
  const data = harness.results[0]!['data'] as { recovery_observation?: Record<string, unknown> }
  assert.equal(data.recovery_observation!['resource_stopped'], true)
  assert.equal(data.recovery_observation!['stop_evidence'], 'supervised_device_boundary')
})

test('R10 复判明确 401：保持既有语义（不执行受管停止，回 CANCELLED）', async () => {
  const harness = makeHarness()
  harness.failPrecheckWith(new ApiError(401, '设备 token 无效'))
  await runInvocation(stopInvocation(), harness.deps)
  assert.equal(harness.startedCalls, 2, '401 为明确响应：不重试')
  assert.equal(harness.stopCalls, 0)
  assert.equal(harness.results.length, 1)
  assert.equal(harness.results[0]!['code'], 'CANCELLED')
})
