/**
 * Phase 5 调用层分段计时（2026-10-20，计划 plan-recruiting-error-containment-and-input-performance）：
 * runInvocation 输出 [runtime-perf] 单行（桌面锁等待 / 锁内前置（Provider 就绪）/ Provider 调用本体）：
 * - 成功路径：字段齐全且为非负毫秒；desktopCheck 本体不计时（行中无 desktop_check 字段）
 * - 失败路径：Provider 调用抛错仍输出 [runtime-perf]（失败路径不崩、不丢段）
 * - 锁竞争路径：lock_wait_ms 反映真实等待（预先占住同一 resource_key 的桌面锁）
 *
 * 直连 runner 轻量栈（FakeApi + wecom 写工具的进程内 Provider stub），不依赖真实 Provider 子进程。
 */
import assert from 'node:assert/strict'
import { mkdtempSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { randomUUID } from 'node:crypto'
import { test } from 'node:test'
import { runInvocation } from '../src/invocationRunner.js'
import type { RunnerDeps } from '../src/invocationRunner.js'
import type { ApiClient, ClaimedInvocation, InvocationResultPayload } from '../src/apiClient.js'
import { deriveResourceKey, desktopLockName, withDesktopLock } from '../src/desktopLock.js'

function makeHarness(overrides: {
  callTool?: () => Promise<Record<string, unknown>>
  events?: string[]
  resourceKey?: string
} = {}): { deps: RunnerDeps; results: Array<{ id: string; payload: InvocationResultPayload }> } {
  const results: Array<{ id: string; payload: InvocationResultPayload }> = []
  const api = {
    started: async () => {},
    progress: async () => ({ seq: 0, cancel: false }),
    result: async (id: string, payload: InvocationResultPayload) => {
      results.push({ id, payload })
    },
    operationResult: async () => {},
  } as unknown as ApiClient
  const provider = { callTool: overrides.callTool ?? (async () => ({ success: true, effect: 'applied', data: {} })) }
  const deps: RunnerDeps = {
    api,
    providers: { has: (key: string) => key === 'wecom', get: (key: string) => key === 'wecom' ? provider : undefined } as unknown as RunnerDeps['providers'],
    desktopCheck: async () => true,
    desktopResourceKey: overrides.resourceKey ?? deriveResourceKey(randomUUID()),
    retryBaseMs: 10,
    retryMaxMs: 50,
    runtimeDataDir: mkdtempSync(path.join(os.tmpdir(), 'aidwork-rt-perf-')),
    ...(overrides.events ? { onEvent: (msg: string) => { overrides.events!.push(msg) } } : {}),
  }
  return { deps, results }
}

function sendInvocation(): ClaimedInvocation {
  return {
    invocation_id: `inv-perf-${randomUUID().slice(0, 8)}`,
    tool_name: 'wecom_message_send',
    arguments: {},
    claim_token: 'tok-perf',
    provider: 'wecom',
  }
}

function parsePerfLine(events: string[], invocationId: string): Record<string, string> {
  const line = events.find((m) => m.startsWith(`[runtime-perf] invocation=${invocationId} `))
  assert.ok(line, `应输出 [runtime-perf] 行，events：${events.join(' | ')}`)
  const fields: Record<string, string> = {}
  for (const part of line.split(' ').slice(1)) {
    const eq = part.indexOf('=')
    if (eq > 0) fields[part.slice(0, eq)] = part.slice(eq + 1)
  }
  return fields
}

test('成功路径：[runtime-perf] 字段齐全、毫秒非负，且不含 desktop_check 字段', async () => {
  const events: string[] = []
  const h = makeHarness({ events, callTool: async () => ({ success: true, effect: 'applied', data: { stdout: 'ok' } }) })
  const inv = sendInvocation()
  await runInvocation(inv, h.deps)
  assert.equal(h.results[0]!.payload['success'], true)
  const f = parsePerfLine(events, inv.invocation_id)
  assert.equal(f['tool'], 'wecom_message_send')
  assert.equal(f['provider'], 'wecom')
  // desktopCheck 本体由 Phase 1 进程内化消除，不在此计时（有意的缺口）
  assert.ok(!('desktop_check_ms' in f), `[runtime-perf] 行不应含 desktop_check 字段：${JSON.stringify(f)}`)
  for (const key of ['started_ms', 'lock_wait_ms', 'lock_prep_ms', 'provider_call_ms']) {
    const value = Number(f[key])
    assert.ok(f[key] !== undefined && Number.isFinite(value) && value >= 0, `${key} 应为非负毫秒，实际：${f[key]}`)
  }
})

test('失败路径：Provider 调用抛错 → [runtime-perf] 仍输出，终态不受埋点影响', async () => {
  const events: string[] = []
  const h = makeHarness({ events, callTool: async () => { throw new Error('provider exploded') } })
  const inv = sendInvocation()
  await runInvocation(inv, h.deps)
  const payload = h.results[0]!.payload
  assert.equal(payload['success'], false)
  assert.equal(payload['code'], 'INTERNAL_ERROR')
  assert.equal(payload['effect'], 'unknown') // 写动作（wecom_message_send）失败效果不可证明
  const f = parsePerfLine(events, inv.invocation_id)
  assert.ok(Number(f['provider_call_ms']) >= 0, '失败路径 provider_call_ms 也应记录')
})

test('锁竞争路径：预先占住同一桌面锁 → lock_wait_ms 反映真实等待', async () => {
  const events: string[] = []
  const resourceKey = deriveResourceKey(`perf-lock-${randomUUID()}`)
  let release!: () => void
  const held = new Promise<void>((resolve) => { release = resolve })
  // 占住同一 resource_key 的桌面锁 ~250ms，runInvocation 必须排队等待
  const holder = withDesktopLock(desktopLockName(resourceKey), async () => {
    await new Promise<void>((resolve) => setTimeout(resolve, 250))
    release()
  })
  const h = makeHarness({ events, resourceKey })
  const inv = sendInvocation()
  await runInvocation(inv, h.deps)
  await held
  assert.equal(h.results[0]!.payload['success'], true)
  const f = parsePerfLine(events, inv.invocation_id)
  assert.ok(Number(f['lock_wait_ms']) >= 100, `lock_wait_ms 应反映排队等待（≥100ms），实际：${f['lock_wait_ms']}`)
})
