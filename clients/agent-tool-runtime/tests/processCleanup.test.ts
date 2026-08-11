/**
 * 用例 7：进程清理——runtime 停止后断言无遗留子进程（spawn 的 node 句柄全部关闭，fake provider 退出）。
 *
 * 覆盖两种停止路径：
 * a. 空闲时 shutdown：provider 子进程被回收
 * b. 执行中 shutdown（SIGINT 语义）：协作式中止当前 invocation 后回收 provider
 */
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { isProcessAlive, startTestStack, type TestStack } from './helpers/runtimeStack.js'

/** finally 兜底回收：断言失败也必须停 loop，否则悬挂的 PollLoop 定时器会拖死整个测试进程 */
async function forceStop(stack: TestStack): Promise<void> {
  stack.loop.shutdown()
  await stack.runPromise.catch(() => {})
  await stack.provider.shutdown()
  await stack.cloud.stop()
}

test('进程清理：空闲 shutdown 后无遗留 Provider 子进程', async () => {
  const stack = await startTestStack()
  try {
    // 触发 provider 启动
    const id = stack.cloud.enqueueInvocation('boss_goto', { durationMs: 100 })
    await stack.cloud.waitFor(() => stack.cloud.getInvocation(id)?.state === 'succeeded', 10_000, 'warmup')
    const pid = stack.provider.childPid
    assert.ok(pid !== null && isProcessAlive(pid), 'Provider 子进程应在运行')

    await stack.stop()
    assert.ok(!isProcessAlive(pid!), `Provider 子进程 pid=${pid} 应已退出`)
  } finally {
    await forceStop(stack)
  }
})

test('进程清理：执行中 shutdown（SIGINT 语义）协作式中止 + 子进程回收', async () => {
  const stack = await startTestStack()
  try {
    const id = stack.cloud.enqueueInvocation('boss_greet', { durationMs: 5_000, stepMs: 200 })
    // 等 Provider 进程真正起来（云端 started 先于 spawn，不能拿 started 当 spawn 信号）
    await stack.cloud.waitFor(() => stack.provider.childPid !== null, 10_000, 'provider spawned')
    const pid = stack.provider.childPid!
    assert.ok(isProcessAlive(pid))

    // 模拟 SIGINT：停止 claim 新任务 + 当前 invocation 协作式中止
    stack.loop.shutdown()
    // runPromise 应在中止 + 终态回传后退出
    await stack.runPromise
    await stack.provider.shutdown()
    assert.ok(!isProcessAlive(pid), `Provider 子进程 pid=${pid} 应已退出`)

    // 当前 invocation 应有终态（Runtime 关闭 → CANCELLED → 云端 failed）
    const inv = stack.cloud.getInvocation(id)!
    assert.ok(['failed', 'succeeded'].includes(inv.state), `invocation 应有终态，实际 ${inv.state}`)
  } finally {
    await forceStop(stack)
  }
})

test('进程清理：Provider 关闭在限时内完成（不卡死）', async () => {
  const stack = await startTestStack()
  try {
    const id = stack.cloud.enqueueInvocation('boss_goto', { durationMs: 100 })
    await stack.cloud.waitFor(() => stack.cloud.getInvocation(id)?.state === 'succeeded', 10_000, 'warmup')
    const pid = stack.provider.childPid!
    stack.loop.shutdown()
    await stack.runPromise
    const started = Date.now()
    await stack.provider.shutdown()
    const elapsed = Date.now() - started
    assert.ok(elapsed < 8_000, `shutdown 应远小于兜底限时完成，实际 ${elapsed}ms`)
    assert.ok(!isProcessAlive(pid))
  } finally {
    await forceStop(stack)
  }
})
