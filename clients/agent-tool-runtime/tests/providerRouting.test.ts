/**
 * Provider 路由：invocation 级 provider_key → 对应 Provider 实例；
 * 未安装 provider → PROVIDER_NOT_AVAILABLE（不 started）；无 provider 字段 → boss（现状）。
 * M2 扩展：provider=skill-runner → deps.skillRunner 进程内 handler 代替 ProviderSet/MCP
 * 实例（v1 result 链路/manifest 白名单/桌面锁/锁屏检测全部复用 invocationRunner 既有链路）。
 */
import assert from 'node:assert/strict'
import { mkdtempSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { randomUUID } from 'node:crypto'
import { test } from 'node:test'
import { startTestStack, fakeProviderEntry } from './helpers/runtimeStack.js'
import { runInvocation } from '../src/invocationRunner.js'
import type { RunnerDeps } from '../src/invocationRunner.js'
import type { ApiClient, ClaimedInvocation, InvocationResultPayload } from '../src/apiClient.js'
import { ProviderSet } from '../src/providerManager.js'
import type { SkillRunnerHandler } from '../src/skillRunner.js'
import { deriveResourceKey } from '../src/desktopLock.js'

/** M2 直连 runner 轻量栈：FakeApi（记录 started/result/operationResult）+ 空 ProviderSet + 可注入 skillRunner */
interface RunnerHarness {
  deps: RunnerDeps
  startedIds: string[]
  results: Array<{ id: string; payload: InvocationResultPayload }>
  v2Results: Array<Record<string, unknown>>
}

function makeRunnerHarness(overrides: { skillRunner?: SkillRunnerHandler; desktopCheck?: () => Promise<boolean> } = {}): RunnerHarness {
  const startedIds: string[] = []
  const results: Array<{ id: string; payload: InvocationResultPayload }> = []
  const v2Results: Array<Record<string, unknown>> = []
  const api = {
    started: async (id: string) => {
      startedIds.push(id)
    },
    progress: async () => ({ seq: 0, cancel: false }),
    result: async (id: string, payload: InvocationResultPayload) => {
      results.push({ id, payload })
    },
    // v2 路径终态走 operation-result（outbox 投递）；缺失会让 deliverOutboxEntry
    // 把 TypeError 当网络错误无限退避重试（测试挂死），必须实现
    operationResult: async (id: string, payload: Record<string, unknown>) => {
      v2Results.push({ id, ...payload })
    },
  } as unknown as ApiClient
  const deps: RunnerDeps = {
    api,
    providers: new ProviderSet({}),
    ...(overrides.skillRunner ? { skillRunner: overrides.skillRunner } : {}),
    desktopCheck: overrides.desktopCheck ?? (async () => true),
    desktopResourceKey: deriveResourceKey(randomUUID()),
    retryBaseMs: 10,
    retryMaxMs: 50,
    runtimeDataDir: mkdtempSync(path.join(os.tmpdir(), 'aidwork-sr-route-')),
  }
  return { deps, startedIds, results, v2Results }
}

function skillInvocation(args: Record<string, unknown> = {}, provider = 'skill-runner'): ClaimedInvocation {
  return {
    invocation_id: `inv-sr-${randomUUID().slice(0, 8)}`,
    tool_name: 'skill_script_run',
    arguments: { skill: 'demo-skill', entry: 'scripts/main.js', exec_hash: 'abc', ...args },
    claim_token: 'tok-sr',
    provider,
  }
}

/** 记录调用的 skill-runner stub handler */
function recordingSkillHandler(calls: Array<{ tool: string; args: Record<string, unknown> }>): SkillRunnerHandler {
  return {
    callTool: async (tool: string, args: Record<string, unknown>) => {
      calls.push({ tool, args })
      return { success: true, effect: 'applied', data: { stdout: 'ok', exit_code: 0, duration_ms: 5 } }
    },
  } as unknown as SkillRunnerHandler
}

test('claim 路由：provider=weixin 的 invocation 路由到 weixin Provider 实例执行成功', async () => {
  const stack = await startTestStack({
    providerEntries: { weixin: fakeProviderEntry() },
  })
  try {
    const id = stack.cloud.enqueueInvocation('weixin_probe', { durationMs: 100 }, { provider: 'weixin' })
    await stack.cloud.waitFor(() => stack.cloud.getInvocation(id)?.state === 'succeeded', 10_000, 'weixin succeeded')

    const calls = stack.cloud.callsFor(id)
    const result = calls.find((c) => c.type === 'result')!
    assert.equal(result.payload['success'], true)
    assert.equal((result.payload['data'] as Record<string, unknown>)['echo_tool'], 'weixin_probe')
    // 路由到独立 Provider 实例：weixin Manager 已 spawn，且与 boss Manager 是不同进程
    const weixinManager = stack.providers.get('weixin')
    assert.ok(weixinManager.childPid !== null, 'weixin Provider 应已 spawn')
    assert.ok(stack.provider.childPid === null || weixinManager.childPid !== stack.provider.childPid,
      'weixin 与 boss 应是不同 Provider 子进程')
  } finally {
    await stack.stop()
  }
})

test('claim 路由：provider=weixin 且未配置入口 → PROVIDER_NOT_AVAILABLE，不 started', async () => {
  const stack = await startTestStack() // 默认仅 boss
  try {
    const id = stack.cloud.enqueueInvocation('weixin_probe', {}, { provider: 'weixin' })
    await stack.cloud.waitFor(
      () => stack.cloud.callsFor(id).some((c) => c.type === 'result'),
      10_000,
      'PROVIDER_NOT_AVAILABLE result',
    )
    const calls = stack.cloud.callsFor(id)
    assert.ok(!calls.some((c) => c.type === 'started'), '未安装 provider 不得标记 started')
    assert.ok(!calls.some((c) => c.type === 'progress'), '未安装 provider 不得有进度')
    const result = calls.find((c) => c.type === 'result')!
    assert.equal(result.payload['success'], false)
    assert.equal(result.payload['code'], 'PROVIDER_NOT_AVAILABLE')
    assert.equal(result.payload['effect'], 'none')
    assert.equal(result.payload['retryable'], true)
    assert.equal(stack.cloud.getInvocation(id)!.state, 'failed')
  } finally {
    await stack.stop()
  }
})

test('claim 路由：未知 provider key → PROVIDER_NOT_AVAILABLE（不 spawn 任何进程）', async () => {
  const stack = await startTestStack()
  try {
    const id = stack.cloud.enqueueInvocation('boss_greet', {}, { provider: 'mystery-provider' })
    await stack.cloud.waitFor(
      () => stack.cloud.callsFor(id).some((c) => c.type === 'result'),
      10_000,
      'unknown provider result',
    )
    const result = stack.cloud.callsFor(id).find((c) => c.type === 'result')!
    assert.equal(result.payload['code'], 'PROVIDER_NOT_AVAILABLE')
    assert.ok(!stack.cloud.callsFor(id).some((c) => c.type === 'started'))
  } finally {
    await stack.stop()
  }
})

test('claim 路由：claim 回包无 provider 字段（旧服务端）→ 路由 boss，行为与现状一致', async () => {
  const stack = await startTestStack()
  try {
    const id = stack.cloud.enqueueInvocation('boss_goto', { durationMs: 100 }, { provider: null })
    await stack.cloud.waitFor(() => stack.cloud.getInvocation(id)?.state === 'succeeded', 10_000, 'boss fallback succeeded')
    const result = stack.cloud.callsFor(id).find((c) => c.type === 'result')!
    assert.equal((result.payload['data'] as Record<string, unknown>)['echo_tool'], 'boss_goto')
  } finally {
    await stack.stop()
  }
})

test('claim 路由：provider=wecom 的 invocation 路由到 wecom Provider 实例执行成功（M11b）', async () => {
  const stack = await startTestStack({
    providerEntries: { wecom: fakeProviderEntry() },
  })
  try {
    // 只读工具（probe）+ 写工具（message_send：manifest 写集合驱动锁屏前置与桌面锁）
    const readId = stack.cloud.enqueueInvocation('wecom_probe', { durationMs: 50 }, { provider: 'wecom' })
    await stack.cloud.waitFor(() => stack.cloud.getInvocation(readId)?.state === 'succeeded', 10_000, 'wecom probe succeeded')
    const readResult = stack.cloud.callsFor(readId).find((c) => c.type === 'result')!
    assert.equal((readResult.payload['data'] as Record<string, unknown>)['echo_tool'], 'wecom_probe')

    const writeId = stack.cloud.enqueueInvocation('wecom_message_send', { durationMs: 50 }, { provider: 'wecom' })
    await stack.cloud.waitFor(() => stack.cloud.getInvocation(writeId)?.state === 'succeeded', 10_000, 'wecom send succeeded')
    const writeResult = stack.cloud.callsFor(writeId).find((c) => c.type === 'result')!
    assert.equal((writeResult.payload['data'] as Record<string, unknown>)['echo_tool'], 'wecom_message_send')

    // 路由到独立 Provider 实例：wecom Manager 已 spawn，且与 boss Manager 是不同进程
    const wecomManager = stack.providers.get('wecom')
    assert.ok(wecomManager.childPid !== null, 'wecom Provider 应已 spawn')
    assert.ok(stack.provider.childPid === null || wecomManager.childPid !== stack.provider.childPid,
      'wecom 与 boss 应是不同 Provider 子进程')
  } finally {
    await stack.stop()
  }
})

test('claim 路由：provider=wecom 未接入的工具（search/watch_poll）→ TOOL_NOT_ALLOWED', async () => {
  const stack = await startTestStack({
    providerEntries: { wecom: fakeProviderEntry() },
  })
  try {
    for (const tool of ['wecom_chat_search', 'wecom_watch_poll']) {
      const id = stack.cloud.enqueueInvocation(tool, { durationMs: 50 }, { provider: 'wecom' })
      await stack.cloud.waitFor(
        () => stack.cloud.callsFor(id).some((c) => c.type === 'result'),
        10_000,
        `${tool} rejected`,
      )
      const calls = stack.cloud.callsFor(id)
      assert.ok(!calls.some((c) => c.type === 'started'), '白名单拒绝不得标记 started')
      const result = calls.find((c) => c.type === 'result')!
      assert.equal(result.payload['code'], 'TOOL_NOT_ALLOWED')
      assert.equal(result.payload['effect'], 'none')
      assert.equal(result.payload['retryable'], false)
    }
  } finally {
    await stack.stop()
  }
})

test('claim 路由：provider=wecom 且未配置入口 → PROVIDER_NOT_AVAILABLE，不 started', async () => {
  const stack = await startTestStack() // 默认仅 boss
  try {
    const id = stack.cloud.enqueueInvocation('wecom_probe', {}, { provider: 'wecom' })
    await stack.cloud.waitFor(
      () => stack.cloud.callsFor(id).some((c) => c.type === 'result'),
      10_000,
      'wecom PROVIDER_NOT_AVAILABLE result',
    )
    const calls = stack.cloud.callsFor(id)
    assert.ok(!calls.some((c) => c.type === 'started'), '未安装 provider 不得标记 started')
    const result = calls.find((c) => c.type === 'result')!
    assert.equal(result.payload['success'], false)
    assert.equal(result.payload['code'], 'PROVIDER_NOT_AVAILABLE')
    assert.equal(result.payload['retryable'], true)
  } finally {
    await stack.stop()
  }
})

test('ProviderSet：同一 provider 复用实例，未配置 key 抛 ProviderNotAvailableError，shutdownAll 回收', async () => {
  const { ProviderSet, ProviderNotAvailableError } = await import('../src/providerManager.js')
  const providers = new ProviderSet({ 'boss-recruiting': fakeProviderEntry() }, { shutdownTimeoutMs: 1_000 })
  const a = providers.get('boss-recruiting')
  const b = providers.get('boss-recruiting')
  assert.equal(a, b, '同一 provider key 应复用 Manager 实例')
  assert.throws(() => providers.get('weixin'), (err: unknown) => err instanceof ProviderNotAvailableError)
  assert.equal(providers.has('weixin'), false)
  assert.equal(providers.has('boss-recruiting'), true)
  assert.deepEqual(providers.keys(), ['boss-recruiting'])
  await providers.shutdownAll() // 未 spawn 过也应安全完成
})

// ---------------------------------------------------------------------------
// M2 skill-runner 路由（进程内 handler，不走 ProviderSet/MCP）
// ---------------------------------------------------------------------------

test('M2 路由：provider=skill-runner → deps.skillRunner 被调用，结果经既有 v1 result 链路回传', async () => {
  const calls: Array<{ tool: string; args: Record<string, unknown> }> = []
  const h = makeRunnerHarness({ skillRunner: recordingSkillHandler(calls) })
  const inv = skillInvocation({ args: ['seq', 'verify'] })
  await runInvocation(inv, h.deps)
  assert.equal(calls.length, 1, 'handler 应被调用一次')
  assert.equal(calls[0]!.tool, 'skill_script_run')
  assert.equal(calls[0]!.args['skill'], 'demo-skill')
  assert.deepEqual(calls[0]!.args['args'], ['seq', 'verify'])
  assert.ok(h.startedIds.includes(inv.invocation_id), 'started 应回传')
  assert.equal(h.results.length, 1)
  const r = h.results[0]!.payload
  assert.equal(r['success'], true)
  assert.equal(r['effect'], 'applied')
  assert.equal((r['data'] as Record<string, unknown>)['stdout'], 'ok')
})

test('M2 路由：deps 缺 skillRunner（skills.python 未配置）→ PROVIDER_NOT_AVAILABLE，不 started', async () => {
  const h = makeRunnerHarness() // 无 skillRunner；ProviderSet 亦无该 key
  await runInvocation(skillInvocation(), h.deps)
  assert.equal(h.startedIds.length, 0, '未安装 provider 不得标记 started')
  const r = h.results[0]!.payload
  assert.equal(r['code'], 'PROVIDER_NOT_AVAILABLE')
  assert.equal(r['effect'], 'none')
  assert.equal(r['retryable'], true)
})

test('M2 路由：skill-runner 的 v2 invocation → PROTOCOL_NOT_SUPPORTED（manifest v1 受控形态）', async () => {
  const calls: Array<{ tool: string; args: Record<string, unknown> }> = []
  const h = makeRunnerHarness({ skillRunner: recordingSkillHandler(calls) })
  await runInvocation(skillInvocation({ protocol_version: 2, request_id: 'req-1' }), h.deps)
  assert.equal(calls.length, 0, '协议不支持不得调用 handler')
  assert.equal(h.startedIds.length, 0)
  // v2 终态经 operation-result（outbox）回传，不在旧 /result 链路
  assert.equal(h.results.length, 0)
  const r = h.v2Results[0]!
  assert.equal(r['code'], 'PROTOCOL_NOT_SUPPORTED')
  assert.equal(r['effect'], 'none')
})

test('M2 路由：provider=skill-runner + 非 manifest 工具（boss_greet）→ TOOL_NOT_ALLOWED', async () => {
  const calls: Array<{ tool: string; args: Record<string, unknown> }> = []
  const h = makeRunnerHarness({ skillRunner: recordingSkillHandler(calls) })
  const inv = skillInvocation()
  inv.tool_name = 'boss_greet'
  await runInvocation(inv, h.deps)
  assert.equal(calls.length, 0, '白名单拒绝不得调用 handler')
  assert.equal(h.startedIds.length, 0)
  const r = h.results[0]!.payload
  assert.equal(r['code'], 'TOOL_NOT_ALLOWED')
  assert.equal(r['retryable'], false)
})

test('M2 路由：skill_script_run 归写集合——锁屏（desktopCheck=false）→ DESKTOP_NOT_INTERACTIVE，不执行', async () => {
  const calls: Array<{ tool: string; args: Record<string, unknown> }> = []
  const h = makeRunnerHarness({ skillRunner: recordingSkillHandler(calls), desktopCheck: async () => false })
  await runInvocation(skillInvocation(), h.deps)
  assert.equal(calls.length, 0, '锁屏前置拦截，handler 不得被调用')
  const r = h.results[0]!.payload
  assert.equal(r['code'], 'DESKTOP_NOT_INTERACTIVE')
  assert.equal(r['effect'], 'none')
  assert.equal(r['retryable'], true)
})

test('M2 路由：门禁失败（SKILL_ENTRY_NOT_ALLOWED）→ 结构化失败终态回传（started 后、handler 返回值直传）', async () => {
  const failing = {
    callTool: async () => ({
      success: false,
      code: 'SKILL_ENTRY_NOT_ALLOWED',
      message: "入口 'x' 不在白名单",
      effect: 'none',
      retryable: false,
    }),
  } as unknown as SkillRunnerHandler
  const h = makeRunnerHarness({ skillRunner: failing })
  await runInvocation(skillInvocation({ entry: 'scripts/other.js' }), h.deps)
  const r = h.results[0]!.payload
  assert.equal(r['success'], false)
  assert.equal(r['code'], 'SKILL_ENTRY_NOT_ALLOWED')
  assert.equal(r['effect'], 'none')
  assert.equal(r['retryable'], false)
})
