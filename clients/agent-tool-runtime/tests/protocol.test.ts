/**
 * 用例 2：完整协议——fake cloud 注入 invocation → runtime 领取 → started → progress（seq 递增）
 * → result → fake cloud 断言 Runtime 实际上报的顺序与内容。
 */
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { startTestStack } from './helpers/runtimeStack.js'

test('完整协议：claim → started → progress(seq 递增) → result(succeeded)，顺序正确', async () => {
  const stack = await startTestStack()
  try {
    const id = stack.cloud.enqueueInvocation('boss_greet', { durationMs: 400, stepMs: 100 })
    await stack.cloud.waitFor(() => stack.cloud.getInvocation(id)?.state === 'succeeded', 10_000, 'invocation succeeded')

    const calls = stack.cloud.callsFor(id)
    const types = calls.map((c) => c.type)
    assert.equal(types[0], 'started', `首个调用应为 started，实际: ${types.join(',')}`)
    assert.equal(types[types.length - 1], 'result', '最后一个调用应为 result')

    const progressSeqs = calls.filter((c) => c.type === 'progress').map((c) => Number(c.payload['seq']))
    assert.ok(progressSeqs.length >= 1, '至少一条 progress')
    for (let i = 1; i < progressSeqs.length; i++) {
      assert.ok(progressSeqs[i]! > progressSeqs[i - 1]!, `progress seq 应递增: ${progressSeqs}`)
    }

    const result = calls.find((c) => c.type === 'result')!
    assert.equal(result.payload['success'], true)
    assert.equal(result.payload['code'], 'OK')
    assert.equal(result.payload['effect'], 'applied')
  } finally {
    await stack.stop()
  }
})
