/**
 * v2 能力门禁：protocol_version===2 或 tool 名 _v2 结尾 → manifest 未协商 v2 即拒绝
 * PROTOCOL_NOT_SUPPORTED（effect=none、不可重试、不 started、不降级旧发送）。
 */
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { startTestStack, fakeProviderEntry } from './helpers/runtimeStack.js'

async function waitForResult(stack: Awaited<ReturnType<typeof startTestStack>>, id: string, label: string) {
  await stack.cloud.waitFor(
    // v2 invocation 终态走 operation-result 端点（P1-C 接线），旧 invocation 走 /result——两者都等
    () => stack.cloud.callsFor(id).some((c) => c.type === 'result' || c.type === 'operation_result'),
    10_000,
    label,
  )
  return stack.cloud.callsFor(id).find((c) => c.type === 'result' || c.type === 'operation_result')!
}

test('v2 门禁：boss invocation 带 protocol_version=2 → PROTOCOL_NOT_SUPPORTED，不 started 不执行', async () => {
  const stack = await startTestStack()
  try {
    const id = stack.cloud.enqueueInvocation('boss_send_to', { protocol_version: 2, candidate_id: 'x' }, { provider: 'boss-recruiting' })
    const result = await waitForResult(stack, id, 'boss v2 rejected')
    const calls = stack.cloud.callsFor(id)
    assert.ok(!calls.some((c) => c.type === 'started'), 'v2 拒绝不得标记 started')
    assert.ok(!calls.some((c) => c.type === 'progress'), 'v2 拒绝不得有执行进度')
    assert.equal(result.payload['success'], false)
    assert.equal(result.payload['code'], 'PROTOCOL_NOT_SUPPORTED')
    assert.equal(result.payload['effect'], 'none')
    assert.equal(result.payload['retryable'], false)
    assert.equal(stack.cloud.getInvocation(id)!.state, 'failed')
  } finally {
    await stack.stop()
  }
})

test('v2 门禁：tool 名以 _v2 结尾（即使 provider 已安装）→ PROTOCOL_NOT_SUPPORTED 优先于白名单拒绝', async () => {
  const stack = await startTestStack({ providerEntries: { weixin: fakeProviderEntry() } })
  try {
    const id = stack.cloud.enqueueInvocation('weixin_message_send_v2', { target_handle: 't', payload_ref: 'p' }, { provider: 'weixin' })
    const result = await waitForResult(stack, id, 'weixin v2 tool rejected')
    // _v2 工具不在 v1 白名单：若门禁缺失会误报 TOOL_NOT_ALLOWED；正确行为是协议能力拒绝
    assert.equal(result.payload['code'], 'PROTOCOL_NOT_SUPPORTED')
    assert.ok(!stack.cloud.callsFor(id).some((c) => c.type === 'started'))
  } finally {
    await stack.stop()
  }
})

test('v2 门禁：v1 invocation 不受影响，weixin 工具正常执行（门禁只拦 v2 形态）', async () => {
  const stack = await startTestStack({ providerEntries: { weixin: fakeProviderEntry() } })
  try {
    const id = stack.cloud.enqueueInvocation('weixin_message_send', { durationMs: 50 }, { provider: 'weixin' })
    await stack.cloud.waitFor(() => stack.cloud.getInvocation(id)?.state === 'succeeded', 10_000, 'v1 weixin success')
  } finally {
    await stack.stop()
  }
})

test('v2 门禁：protocol_version=1（或非数字）不触发门禁，按 v1 链路处理', async () => {
  const stack = await startTestStack()
  try {
    const id = stack.cloud.enqueueInvocation('boss_goto', { durationMs: 50, protocol_version: 1 }, { provider: 'boss-recruiting' })
    await stack.cloud.waitFor(() => stack.cloud.getInvocation(id)?.state === 'succeeded', 10_000, 'pv1 success')
    const asString = stack.cloud.enqueueInvocation('boss_goto', { durationMs: 50, protocol_version: '2' })
    await stack.cloud.waitFor(() => stack.cloud.getInvocation(asString)?.state === 'succeeded', 10_000, 'pv string not gated')
  } finally {
    await stack.stop()
  }
})
