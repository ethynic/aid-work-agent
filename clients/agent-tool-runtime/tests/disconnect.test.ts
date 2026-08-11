/**
 * 用例 3：断线——执行中 fake cloud 断网 → runtime 完成本地动作后恢复回传；
 * heartbeat 退避序列断言（1x→2x→4x 递增）。
 */
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { startTestStack } from './helpers/runtimeStack.js'

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms))
}

test('断线：执行中断网 → 本地继续完成 → 恢复网络后终态回传成功', async () => {
  const stack = await startTestStack({ backoffBaseMs: 50, backoffMaxMs: 400 })
  try {
    const id = stack.cloud.enqueueInvocation('boss_greet', { durationMs: 2_000, stepMs: 250 })
    // 等执行开始
    await stack.cloud.waitFor(() => stack.cloud.callsFor(id).some((c) => c.type === 'started'), 10_000, 'started')

    // 断网 600ms（覆盖多次心跳退避与多次进度失败）
    stack.cloud.offline = true
    await sleep(600)
    stack.cloud.offline = false

    // 本地动作完成后恢复回传终态
    await stack.cloud.waitFor(() => stack.cloud.getInvocation(id)?.state === 'succeeded', 15_000, 'recovered result')
    const result = stack.cloud.callsFor(id).find((c) => c.type === 'result')!
    assert.equal(result.payload['success'], true)
    assert.equal(result.payload['effect'], 'applied')
  } finally {
    await stack.stop()
  }
})

test('断线：heartbeat 退避序列递增（指数 1→2→4），恢复后重置', async () => {
  const attempts: number[] = []
  const stack = await startTestStack({
    backoffBaseMs: 50,
    backoffMaxMs: 400,
    heartbeatIntervalMs: 60,
    onHeartbeatAttempt: (ts) => attempts.push(ts),
  })
  try {
    // 先让心跳成功一次
    await stack.cloud.waitFor(() => stack.cloud.heartbeatCount(stack.token) >= 1, 5_000, 'first heartbeat')
    attempts.length = 0

    stack.cloud.offline = true
    await sleep(700) // 50 + 100 + 200 + 400 退避窗口内应有 ≥4 次尝试
    stack.cloud.offline = false

    await stack.cloud.waitFor(() => stack.cloud.heartbeatCount(stack.token) >= 2, 5_000, 'heartbeat recovered')

    const offlineAttempts = attempts.slice(0, 4)
    assert.ok(offlineAttempts.length >= 4, `断线期应至少 4 次心跳尝试，实际 ${offlineAttempts.length}`)
    const gaps = offlineAttempts.slice(1).map((t, i) => t - offlineAttempts[i]!)
    // 退避 50 → 100 → 200：间隔应显著递增（允许 40% 抖动）
    assert.ok(gaps[1]! > gaps[0]! * 1.3, `第二次间隔(${gaps[1]})应大于第一次(${gaps[0]})的1.3倍`)
    assert.ok(gaps[2]! > gaps[1]! * 1.3, `第三次间隔(${gaps[2]})应大于第二次(${gaps[1]})的1.3倍`)

    // 恢复后间隔回落到心跳周期级别（< 最大退避 400ms）
    await sleep(300)
    const last = attempts[attempts.length - 1]!
    const prev = attempts[attempts.length - 2]!
    assert.ok(last - prev < 400, `恢复后心跳间隔应回落（实际 ${last - prev}ms）`)
  } finally {
    await stack.stop()
  }
})
