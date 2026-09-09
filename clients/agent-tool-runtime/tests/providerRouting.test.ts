/**
 * Provider 路由：invocation 级 provider_key → 对应 Provider 实例；
 * 未安装 provider → PROVIDER_NOT_AVAILABLE（不 started）；无 provider 字段 → boss（现状）。
 */
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { startTestStack, fakeProviderEntry } from './helpers/runtimeStack.js'

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
