/**
 * 用例 4：取消——fake cloud 在 progress 响应返回 cancel=true → runner 协作式中止并回 CANCELLED 终态。
 */
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { startTestStack } from './helpers/runtimeStack.js'

test('取消：progress 响应 cancel=true → 协作式中止 → result(CANCELLED)', async () => {
  const stack = await startTestStack()
  try {
    const id = stack.cloud.enqueueInvocation('boss_greet', { durationMs: 5_000, stepMs: 150 })

    // 等第一条 progress 到云端后发起取消
    await stack.cloud.waitFor(
      () => stack.cloud.callsFor(id).some((c) => c.type === 'progress'),
      10_000,
      'first progress',
    )
    stack.cloud.cancelInvocation(id)

    // 下一条 progress ack 带 cancel=true → 中止 → 终态
    await stack.cloud.waitFor(
      () => stack.cloud.callsFor(id).some((c) => c.type === 'result'),
      10_000,
      'cancelled result',
    )
    const inv = stack.cloud.getInvocation(id)!
    assert.equal(inv.state, 'failed', '云端映射：非 EXECUTION_UNKNOWN 失败 → failed')
    const result = stack.cloud.callsFor(id).find((c) => c.type === 'result')!
    assert.equal(result.payload['success'], false)
    assert.equal(result.payload['code'], 'CANCELLED')
    // SDK 客户端 abort 会立即 reject callTool（server 的 structured 结果被丢弃），effect 无法带回 → none
    assert.equal(result.payload['effect'], 'none')
  } finally {
    await stack.stop()
  }
})
