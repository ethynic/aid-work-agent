/**
 * 契约 2.20 候选「正常结束的事实」（Phase 3）：Runtime Host 在纯成功路径为 boss 调用
 * 附加 _runtime_completion={version:1} 结束事实。
 *
 * - 判据 = 原可信 Provider 结构化终态已到达（settled 非空）+ 执行已退出 + 桌面锁已释放
 *   （enterLock 返回的控制流保证）；插件回执同名字段先剥离（Host 唯一附加，防伪造残留）
 * - 五类禁止路径（锁内前置失败 / DESKTOP_RESOURCE_BUSY / Provider 崩溃 / 调用错误 /
 *   shutdown 取消）payload data 零写入；并行作业（不取锁）不附加；非 boss Provider 不附加
 * - legacy /result 与 v2 operation-result 两条回传通路 data 同源携带
 *
 * 直连 runner 轻量栈（进程内 fake Provider），E2E 一例走 fakeCloud + runtimeStack + fakeProvider。
 */
import assert from 'node:assert/strict'
import net from 'node:net'
import { mkdtempSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { randomUUID } from 'node:crypto'
import { test } from 'node:test'
import { runInvocation } from '../src/invocationRunner.js'
import type { RunnerDeps } from '../src/invocationRunner.js'
import { ProviderCrashError } from '../src/providerManager.js'
import type { ProviderManager, ProviderSet } from '../src/providerManager.js'
import type { ApiClient, ClaimedInvocation, InvocationResultPayload } from '../src/apiClient.js'
import type { ProviderManifest } from '../src/providers.js'
import { desktopLockName, deriveResourceKey } from '../src/desktopLock.js'
import { startTestStack } from './helpers/runtimeStack.js'

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms))

/** 插件伪造回执：data 内携带同名字段（真实 CLI 不会；Host 必须剥离后重加） */
const FORGED_DATA = { echo: 1, _runtime_completion: { version: 1, forged: 'plugin' } }
const SUCCESS_RESULT = { success: true, code: 'OK', effect: 'applied', data: FORGED_DATA, retryable: false, run_id: 'r-1' }

interface Harness {
  deps: RunnerDeps
  results: Array<{ id: string; payload: InvocationResultPayload }>
  operationResults: Array<{ id: string; payload: Record<string, unknown> }>
}

function fakeProviders(key: string, manager: Pick<ProviderManager, 'callTool'>): ProviderSet {
  return { has: (k: string) => k === key, get: (k: string) => (k === key ? manager : undefined) } as unknown as ProviderSet
}

function makeHarness(opts: {
  providerKey?: string
  manager?: Pick<ProviderManager, 'callTool'>
  shutdownSignal?: AbortSignal
  onStarted?: () => void
  parallel?: boolean
  manifests?: RunnerDeps['manifests']
  desktopResourceKey?: string
  desktopLockTimeoutMs?: number
} = {}): Harness {
  const results: Harness['results'] = []
  const operationResults: Harness['operationResults'] = []
  const api = {
    started: async () => {
      opts.onStarted?.()
      return {}
    },
    progress: async () => ({ seq: 1, cancel: false }),
    result: async (id: string, payload: InvocationResultPayload) => {
      results.push({ id, payload })
    },
    operationResult: async (id: string, payload: Record<string, unknown>) => {
      operationResults.push({ id, payload })
    },
  } as unknown as ApiClient
  const providerKey = opts.providerKey ?? 'boss-recruiting'
  const deps: RunnerDeps = {
    api,
    providers: fakeProviders(providerKey, opts.manager ?? { callTool: async () => SUCCESS_RESULT }),
    desktopCheck: async () => true,
    desktopResourceKey: opts.desktopResourceKey ?? deriveResourceKey(randomUUID()),
    retryBaseMs: 10,
    retryMaxMs: 50,
    runtimeDataDir: mkdtempSync(path.join(os.tmpdir(), 'aidwork-rc-fact-')),
    ...(opts.parallel ? { parallel: true } : {}),
    ...(opts.manifests ? { manifests: opts.manifests } : {}),
    ...(opts.desktopLockTimeoutMs !== undefined ? { desktopLockTimeoutMs: opts.desktopLockTimeoutMs } : {}),
    ...(opts.shutdownSignal ? { shutdownSignal: opts.shutdownSignal } : {}),
  }
  return { deps, results, operationResults }
}

function bossInvocation(tool = 'boss_filter', args: Record<string, unknown> = {}): ClaimedInvocation {
  return {
    invocation_id: `inv-rc-${randomUUID().slice(0, 8)}`,
    tool_name: tool,
    arguments: args,
    claim_token: 'tok-rc',
    provider: 'boss-recruiting',
  }
}

function assertLegacyFacts(results: Harness['results'], expectAttached: boolean): Record<string, unknown> | undefined {
  assert.equal(results.length, 1)
  const payload = results[0]!.payload
  const data = payload['data'] as Record<string, unknown> | undefined
  if (expectAttached) {
    assert.ok(data, '纯成功路径必须携带 data（结束事实）')
    assert.deepEqual(data['_runtime_completion'], { version: 1 })
  } else {
    assert.ok(!data || data['_runtime_completion'] === undefined, 'data 内不得出现结束事实')
  }
  return data
}

test('boss 纯成功：附加结束事实并剥离插件同名字段（legacy /result）', async () => {
  const { deps, results } = makeHarness()
  await runInvocation(bossInvocation('boss_filter'), deps)
  const data = assertLegacyFacts(results, true) as Record<string, unknown>
  assert.equal(data['echo'], 1)
  assert.equal(data['run_id'], 'r-1')
})

test('boss 纯成功：插件未携带 data 时事实独立成键（data 从无到有）', async () => {
  const manager = { callTool: async () => ({ success: true, code: 'OK', effect: 'none', data: null, retryable: false }) }
  const { deps, results } = makeHarness({ manager })
  await runInvocation(bossInvocation('boss_filter'), deps)
  assertLegacyFacts(results, true)
})

test('boss 纯成功：v2 operation-result 与 legacy 同源携带', async () => {
  const v2BossReadManifest: ProviderManifest = {
    provider_key: 'boss-recruiting',
    provider_id: 'ai.aidwork.boss-recruiting',
    tools: ['boss_filter'],
    execution_target: 'local_required',
    protocol_version: 2,
    shared_lock_capable: true,
    write_tools: new Set<string>(),
  }
  const { deps, operationResults } = makeHarness({ manifests: { 'boss-recruiting': v2BossReadManifest } })
  await runInvocation(bossInvocation('boss_filter', { protocol_version: 2 }), deps)
  assert.equal(operationResults.length, 1)
  const data = operationResults[0]!.payload['data'] as Record<string, unknown>
  assert.deepEqual(data['_runtime_completion'], { version: 1 })
  assert.equal((data['_runtime_completion'] as Record<string, unknown>)['forged'], undefined)
})

test('非 boss Provider 成功：不附加且剥离伪造字段（wecom）', async () => {
  const manager = { callTool: async () => ({ success: true, code: 'OK', effect: 'none', data: { ...FORGED_DATA }, retryable: false }) }
  const { deps, results } = makeHarness({ providerKey: 'wecom', manager })
  await runInvocation({ ...bossInvocation('wecom_probe'), provider: 'wecom' }, deps)
  const data = assertLegacyFacts(results, false) as Record<string, unknown>
  assert.equal(data['echo'], 1, '非 boss 其余 data 字段原样透传（零改写）')
})

test('并行作业（deps.parallel，不取锁）成功也不附加', async () => {
  const { deps, results } = makeHarness({ parallel: true })
  await runInvocation(bossInvocation('boss_filter'), deps)
  const data = assertLegacyFacts(results, false) as Record<string, unknown>
  assert.equal(data['echo'], 1)
})

// ---------- 五类禁止路径矩阵：payload data 零写入 ----------

test('禁止路径① 锁内前置失败（进锁前已中止 → lockPreabort）', async () => {
  const controller = new AbortController()
  const { deps, results } = makeHarness({
    shutdownSignal: controller.signal,
    onStarted: () => controller.abort(),
  })
  await runInvocation(bossInvocation('boss_filter'), deps)
  assert.equal(results.length, 1)
  assert.equal(results[0]!.payload['code'], 'CANCELLED')
  assert.equal(results[0]!.payload['data'], undefined, '前置失败路径不得携带 data/结束事实')
})

test('禁止路径② 桌面资源锁等待超时（DESKTOP_RESOURCE_BUSY）', async () => {
  const resourceKey = deriveResourceKey(randomUUID())
  const holder = net.createServer((socket) => socket.destroy())
  await new Promise<void>((resolve) => holder.listen(desktopLockName(resourceKey), resolve))
  try {
    const { deps, results } = makeHarness({ desktopResourceKey: resourceKey, desktopLockTimeoutMs: 100 })
    await runInvocation(bossInvocation('boss_filter'), deps)
    assert.equal(results.length, 1)
    assert.equal(results[0]!.payload['code'], 'DESKTOP_RESOURCE_BUSY')
    assert.equal(results[0]!.payload['data'], undefined)
  } finally {
    holder.close()
  }
})

test('禁止路径③ Provider 执行中崩溃（写工具 EXECUTION_UNKNOWN / 只读 INTERNAL_ERROR）', async () => {
  for (const tool of ['boss_greet', 'boss_filter_options']) {
    const manager = {
      callTool: async () => {
        await sleep(20)
        throw new ProviderCrashError(`pid crashed (${tool})`)
      },
    }
    const { deps, results } = makeHarness({ manager })
    await runInvocation(bossInvocation(tool), deps)
    assert.equal(results.length, 1, tool)
    assert.equal(results[0]!.payload['data'], undefined, `${tool} 崩溃路径不得携带结束事实`)
    assert.equal(results[0]!.payload['code'], tool === 'boss_greet' ? 'EXECUTION_UNKNOWN' : 'INTERNAL_ERROR')
  }
})

test('禁止路径④ Provider 调用错误（callError → INTERNAL_ERROR）', async () => {
  const manager = { callTool: async () => { throw new Error('boom') } }
  const { deps, results } = makeHarness({ manager })
  await runInvocation(bossInvocation('boss_filter'), deps)
  assert.equal(results.length, 1)
  assert.equal(results[0]!.payload['code'], 'INTERNAL_ERROR')
  assert.equal(results[0]!.payload['data'], undefined)
})

test('禁止路径⑤ shutdown 取消（结构化终态已到但关闭中止）', async () => {
  const controller = new AbortController()
  let enteredResolve: () => void = () => {}
  const entered = new Promise<void>((resolve) => { enteredResolve = resolve })
  const manager: Pick<ProviderManager, 'callTool'> = {
    callTool: async () => {
      enteredResolve()
      // 等测试 abort shutdown 后再返回结构化终态（settled 非空但伴随取消信号）
      return new Promise<Record<string, unknown>>((done) => {
        controller.signal.addEventListener('abort', () => done(SUCCESS_RESULT), { once: true })
      })
    },
  }
  const { deps, results } = makeHarness({ manager, shutdownSignal: controller.signal })
  const run = runInvocation(bossInvocation('boss_filter'), deps)
  await entered
  controller.abort()
  await run
  assert.equal(results.length, 1)
  assert.equal(results[0]!.payload['code'], 'CANCELLED')
  assert.equal(results[0]!.payload['data'], undefined)
})

test('E2E（fakeCloud + runtimeStack）：boss 成功回执 data 含结束事实且插件伪造字段被剥离', async () => {
  const stack = await startTestStack()
  try {
    const id = stack.cloud.enqueueInvocation('boss_filter', { completion_echo: true, durationMs: 20 }, { provider: 'boss-recruiting' })
    await stack.cloud.waitFor(() => stack.cloud.callsFor(id).some((c) => c.type === 'result'), 15_000, 'boss result')
    const result = stack.cloud.callsFor(id).find((c) => c.type === 'result')!
    assert.equal(result.payload['success'], true)
    const data = result.payload['data'] as Record<string, unknown>
    assert.deepEqual(data['_runtime_completion'], { version: 1 })
    assert.equal((data['_runtime_completion'] as Record<string, unknown>)['forged'], undefined)
    assert.equal(data['echo_tool'], 'boss_filter', '业务回执字段原样保留')
  } finally {
    await stack.stop()
  }
})
