/**
 * 用例 5：MCP 崩溃——fake provider 执行中退出 → EXECUTION_UNKNOWN（写动作）回传 + provider 自动 respawn。
 */
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { startTestStack } from './helpers/runtimeStack.js'

test('Provider 崩溃：写动作执行中退出 → EXECUTION_UNKNOWN；下一个 invocation 自动 respawn 成功', async () => {
  const stack = await startTestStack()
  try {
    const crashId = stack.cloud.enqueueInvocation('boss_greet', { crash: true })
    await stack.cloud.waitFor(
      () => stack.cloud.callsFor(crashId).some((c) => c.type === 'result'),
      10_000,
      'crash result',
    )
    const crashResult = stack.cloud.callsFor(crashId).find((c) => c.type === 'result')!
    assert.equal(crashResult.payload['success'], false)
    assert.equal(crashResult.payload['code'], 'EXECUTION_UNKNOWN')
    assert.equal(crashResult.payload['effect'], 'unknown')
    assert.equal(stack.cloud.getInvocation(crashId)!.state, 'unknown')

    // Provider respawn：下一个正常 invocation 应成功
    const okId = stack.cloud.enqueueInvocation('boss_goto', { durationMs: 100 })
    await stack.cloud.waitFor(() => stack.cloud.getInvocation(okId)?.state === 'succeeded', 10_000, 'respawn success')
    assert.ok(stack.provider.isRunning, '崩溃后 Provider 应已 respawn')
  } finally {
    await stack.stop()
  }
})

test('Provider 崩溃：只读工具执行中退出 → INTERNAL_ERROR', async () => {
  const stack = await startTestStack()
  try {
    const id = stack.cloud.enqueueInvocation('boss_goto', { crash: true })
    await stack.cloud.waitFor(
      () => stack.cloud.callsFor(id).some((c) => c.type === 'result'),
      10_000,
      'crash result',
    )
    const result = stack.cloud.callsFor(id).find((c) => c.type === 'result')!
    assert.equal(result.payload['code'], 'INTERNAL_ERROR')
    assert.equal(result.payload['effect'], 'none')
  } finally {
    await stack.stop()
  }
})
