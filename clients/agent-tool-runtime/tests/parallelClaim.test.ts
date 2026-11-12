/**
 * 契约 2.20 Phase 2：服务端排队，Runtime 领取能开工的任务（并行接单）。
 *
 * - 支持协商：心跳读到 parallel_claim 后才发送 parallel_only；旧服务端行为与现状一致
 * - 领取状态机：无串行占用 → 空参数（FIFO 不变）；串行占用 → parallel_only=true；
 *   并行作业后台执行不阻塞领取循环、不解除串行占用；串行结束恢复空参数
 * - 执行语义：并行作业不取桌面锁；同 Provider 串行 busy 期间并行调用可并发；
 *   stopAdmission/waitForIdle 覆盖并行在途；回执/取消沿原通路
 */
import assert from 'node:assert/strict'
import net from 'node:net'
import { test } from 'node:test'
import { desktopLockName } from '../src/desktopLock.js'
import { ProviderManager, ProviderSet, ProviderNotAvailableError, ProviderBusyError } from '../src/providerManager.js'
import { startTestStack, fakeProviderEntry, type TestStack } from './helpers/runtimeStack.js'

const PARALLEL_TOOLS = { 'boss-recruiting': ['boss_filter'] }

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

/** 串行占用期间 claim 请求的 parallel_only 序列（含与串行终态时序断言） */
function parallelOnlySequence(stack: TestStack): boolean[] {
  return stack.cloud.claimLog.map((c) => c.parallel_only)
}

test('空闲领取不带 parallel_only；串行执行中带 parallel_only 并领走并行作业；串行结束恢复空参数', async () => {
  const stack = await startTestStack({
    parallelTools: PARALLEL_TOOLS,
    claimWaitSeconds: 1,
  })
  stack.cloud.parallelClaimSupported = true
  try {
    // FIFO：A 串行（长）→ C 并行（快）→ B 串行（快）。串行占用期间 B 必须原地等待，
    // 即便 C 已完成（并行完成不解除串行占用）
    const a = stack.cloud.enqueueInvocation('boss_goto', { durationMs: 3000, stepMs: 200 })
    const c = stack.cloud.enqueueInvocation('boss_filter', { durationMs: 100 }, { parallel: true })
    const b = stack.cloud.enqueueInvocation('boss_goto', { durationMs: 100 })

    await stack.cloud.waitFor(() => stack.cloud.getInvocation(c)!.state === 'succeeded', 15_000, '并行 C 在串行 A 执行中完成')
    assert.equal(stack.cloud.getInvocation(a)!.state === 'succeeded', false, '串行 A 尚未结束时 C 已完成（两调用并发在途）')

    await stack.cloud.waitFor(() => stack.cloud.getInvocation(b)!.state === 'succeeded', 15_000, '串行 B 在 A 结束后领取完成')

    const seq = parallelOnlySequence(stack)
    assert.equal(seq[0], false, '空闲首领不带 parallel_only')
    assert.ok(seq.includes(true), '串行占用期间至少一次 parallel_only=true 领取')

    // 与串行 A 终态时序对照：A result 之前存在 true 请求、之后存在 false 请求
    const aResultAt = stack.cloud.callsFor(a).find((entry) => entry.type === 'result')!.at
    assert.ok(stack.cloud.claimLog.some((r) => r.parallel_only && r.at < aResultAt), 'A 执行中的领取带 parallel_only')
    assert.ok(stack.cloud.claimLog.some((r) => !r.parallel_only && r.at > aResultAt), 'A 结束后的领取不带 parallel_only')

    // B 也是串行作业：其终态之后（在途长轮询返回后）恢复空参数领取
    const bResultAt = stack.cloud.callsFor(b).find((entry) => entry.type === 'result')!.at
    await stack.cloud.waitFor(
      () => stack.cloud.claimLog.some((r) => !r.parallel_only && r.at > bResultAt),
      5_000, '串行 B 结束后的领取恢复空参数',
    )

    // started 顺序：A → C → B（B 严格在 A result 之后）
    const startedOrder = [a, c, b].map((id) => stack.cloud.callsFor(id).find((e) => e.type === 'started')!.at)
    assert.ok(startedOrder[0]! < startedOrder[1]! && startedOrder[1]! < startedOrder[2]!, `started 顺序应为 A,C,B`)
    const bStartedAt = stack.cloud.callsFor(b).find((e) => e.type === 'started')!.at
    assert.ok(bStartedAt > aResultAt, 'B 在 A 终态之后才开始（串行占用期间 B 原地等待）')

    // 回执沿原通路：并行 C 的 result 已上报且成功
    const cResult = stack.cloud.callsFor(c).find((e) => e.type === 'result')!
    assert.equal(cResult.payload['success'], true)
  } finally {
    await stack.stop()
  }
})

test('旧服务端（心跳无 parallel_claim）：永远不带 parallel_only，行为与现状一致（串行阻塞领取循环）', async () => {
  const stack = await startTestStack({
    parallelTools: PARALLEL_TOOLS,
    claimWaitSeconds: 1,
  })
  try {
    // FIFO：A 串行（长）→ C 声明并行（中）→ B 串行（短）。旧服务端下执行通道同受协商
    // 门禁（修复 1）：C 不得走并行通道与后续串行 B 并发，三作业严格顺序执行
    const a = stack.cloud.enqueueInvocation('boss_goto', { durationMs: 300, stepMs: 60 })
    const c = stack.cloud.enqueueInvocation('boss_filter', { durationMs: 150 }, { parallel: true })
    const b = stack.cloud.enqueueInvocation('boss_goto', { durationMs: 80 })

    await stack.cloud.waitFor(() => stack.cloud.getInvocation(a)!.state === 'succeeded', 15_000, 'A 完成')
    await stack.cloud.waitFor(() => stack.cloud.getInvocation(c)!.state === 'succeeded', 15_000, 'C 完成')
    await stack.cloud.waitFor(() => stack.cloud.getInvocation(b)!.state === 'succeeded', 15_000, 'B 完成')

    for (const claim of stack.cloud.claimLog) {
      assert.equal(claim.parallel_only, false, '旧服务端下任何领取都不带 parallel_only')
    }
    const startedA = stack.cloud.callsFor(a).find((e) => e.type === 'started')!.at
    const aResult = stack.cloud.callsFor(a).find((e) => e.type === 'result')!.at
    const startedC = stack.cloud.callsFor(c).find((e) => e.type === 'started')!.at
    const cResult = stack.cloud.callsFor(c).find((e) => e.type === 'result')!.at
    const startedB = stack.cloud.callsFor(b).find((e) => e.type === 'started')!.at
    // FIFO + 无执行重叠：每条作业在上一条终态之后才开始（开始时间互斥）
    assert.ok(startedA < startedC, 'FIFO：A 先于 C')
    assert.ok(startedC > aResult, '旧服务端下声明并行作业不得与串行 A 并发（执行通道同门禁）')
    assert.ok(startedB > cResult, '串行 B 不得与声明并行 C 并发（旧服务端下 C 走串行通道）')
    // 原串行循环形态：串行执行期间零领取请求（阻塞领取循环，不空转）
    assert.ok(!stack.cloud.claimLog.some((r) => r.at > startedA && r.at < aResult),
      'A 执行期间零领取请求（原串行循环）')
  } finally {
    await stack.stop()
  }
})

test('并行作业不取桌面锁：外部占锁时串行 DESKTOP_RESOURCE_BUSY、并行照常成功', async () => {
  const lockKey = 'a'.repeat(64)
  const holder = net.createServer((sock) => sock.destroy())
  await new Promise<void>((resolve) => holder.listen(desktopLockName(lockKey), () => resolve()))
  try {
    const stack = await startTestStack({
      parallelTools: PARALLEL_TOOLS,
      desktopResourceKey: lockKey,
      desktopLockTimeoutMs: 1500,
      claimWaitSeconds: 1,
    })
    try {
      stack.cloud.parallelClaimSupported = true
      const a = stack.cloud.enqueueInvocation('boss_goto', { durationMs: 100 })
      const c = stack.cloud.enqueueInvocation('boss_filter', { durationMs: 50 }, { parallel: true })

      await stack.cloud.waitFor(() => stack.cloud.getInvocation(c)!.state === 'succeeded', 15_000, '并行 C 完成（未受锁阻塞）')
      await stack.cloud.waitFor(() => ['failed'].includes(stack.cloud.getInvocation(a)!.state), 15_000, '串行 A 锁超时失败')
      const aResult = stack.cloud.callsFor(a).find((e) => e.type === 'result')!
      assert.equal(aResult.payload['code'], 'DESKTOP_RESOURCE_BUSY')
      // 若并行 C 也走了桌面锁，它同样会超时失败——成功即证明未取锁
      assert.equal(stack.cloud.callsFor(c).find((e) => e.type === 'result')!.payload['success'], true)
    } finally {
      await stack.stop()
    }
  } finally {
    await new Promise<void>((resolve) => holder.close(() => resolve()))
  }
})

test('Provider 单飞：串行 busy 期间并行调用成功并发；串行撞 busy 门行为不变', async () => {
  const manager = new ProviderManager(fakeProviderEntry(), { shutdownTimeoutMs: 3_000 })
  try {
    const serial = manager.callTool('boss_greet', { durationMs: 600, stepMs: 100 })
    // 并行 lane：同 Provider 串行执行中仍可发起（MCP stdio 按 id 关联并发）
    const parallel = await manager.callTool('boss_filter', { durationMs: 80 }, { parallel: true })
    assert.equal(parallel['success'], true)
    assert.equal((await serial)['success'], true)

    // 串行单飞门不变：串行 lane 并发第二调用仍被拒
    const s1 = manager.callTool('boss_goto', { durationMs: 300 })
    await assert.rejects(() => manager.callTool('boss_filter', { durationMs: 50 }), ProviderBusyError)
    assert.equal((await s1)['success'], true)
  } finally {
    await manager.shutdown()
  }
})

test('waitForIdle 等待并行在途；stopAdmission 拒绝新调用', async () => {
  const providers = new ProviderSet({ 'boss-recruiting': fakeProviderEntry() }, { shutdownTimeoutMs: 3_000 })
  try {
    const manager = providers.get('boss-recruiting')
    const inflight = manager.callTool('boss_filter', { durationMs: 500, stepMs: 100 }, { parallel: true })
    let idle = false
    const idlePromise = providers.waitForIdle().then(() => { idle = true })
    await sleep(150)
    assert.equal(idle, false, 'waitForIdle 必须等待并行在途调用结束')

    providers.stopAdmission()
    assert.throws(() => providers.get('boss-recruiting'), ProviderNotAvailableError, '停止接纳后拒绝新调用')

    const result = await inflight
    assert.equal(result['success'], true, '已接纳的并行在途调用不受 stopAdmission 影响')
    await idlePromise
    assert.equal(idle, true, '并行在途结束后 waitForIdle 放行')
  } finally {
    await providers.shutdownAll()
  }
})

test('并行作业取消沿原通路：云端取消 → 协作式中止 → CANCELLED 终态上报', async () => {
  const stack = await startTestStack({
    parallelTools: PARALLEL_TOOLS,
    claimWaitSeconds: 1,
  })
  stack.cloud.parallelClaimSupported = true
  try {
    const c = stack.cloud.enqueueInvocation('boss_filter', { durationMs: 10_000, stepMs: 100 }, { parallel: true })
    await stack.cloud.waitFor(() => stack.cloud.callsFor(c).some((e) => e.type === 'started'), 15_000, '并行 C 开始执行')
    stack.cloud.cancelInvocation(c)
    await stack.cloud.waitFor(() => ['failed', 'unknown'].includes(stack.cloud.getInvocation(c)!.state), 15_000, '并行 C 终态')
    const result = stack.cloud.callsFor(c).find((e) => e.type === 'result')!
    assert.equal(result.payload['code'], 'CANCELLED')
    assert.equal(result.payload['success'], false)
  } finally {
    await stack.stop()
  }
})

test('并行作业 Provider 崩溃按原 effect 语义落账：写工具 EXECUTION_UNKNOWN（不重放）', async () => {
  const stack = await startTestStack({
    parallelTools: PARALLEL_TOOLS,
    claimWaitSeconds: 1,
  })
  stack.cloud.parallelClaimSupported = true
  try {
    // boss_filter 在受信 manifest 属写工具（readOnlyHint !== true）：崩溃无法证明效果 → unknown
    const c = stack.cloud.enqueueInvocation('boss_filter', { durationMs: 5_000, crash: true }, { parallel: true })
    await stack.cloud.waitFor(() => ['failed', 'unknown'].includes(stack.cloud.getInvocation(c)!.state), 15_000, '并行 C 崩溃落账')
    const result = stack.cloud.callsFor(c).find((e) => e.type === 'result')!
    assert.equal(result.payload['code'], 'EXECUTION_UNKNOWN')
    assert.equal(result.payload['effect'], 'unknown')
    assert.equal(result.payload['retryable'], false)
    // 不重放：崩溃后没有第二次 started/调用
    assert.equal(stack.cloud.callsFor(c).filter((e) => e.type === 'started').length, 1)
  } finally {
    await stack.stop()
  }
})

// ----- 契约 2.20 修复轮：长轮询与串行队列互不影响 -----

test('BOSS-only（无并行工具）新服务端：原串行循环形态——执行中零领取请求，第二条 <1s 启动', async () => {
  // parallelTools 缺省 = 无并行工具设备（BOSS-only 实况）：即便服务端支持 parallel_claim，
  // 也不发送 parallel_only 长轮询（不空转），完全沿用原串行循环
  const stack = await startTestStack({ claimWaitSeconds: 1 })
  stack.cloud.parallelClaimSupported = true
  try {
    const a = stack.cloud.enqueueInvocation('boss_goto', { durationMs: 400, stepMs: 80 })
    const b = stack.cloud.enqueueInvocation('boss_goto', { durationMs: 100 })
    await stack.cloud.waitFor(() => stack.cloud.getInvocation(b)!.state === 'succeeded', 15_000, '两条串行作业完成')

    const startedA = stack.cloud.callsFor(a).find((e) => e.type === 'started')!.at
    const aResult = stack.cloud.callsFor(a).find((e) => e.type === 'result')!.at
    const startedB = stack.cloud.callsFor(b).find((e) => e.type === 'started')!.at
    assert.ok(!stack.cloud.claimLog.some((r) => r.at > startedA && r.at < aResult),
      'A 执行期间零领取请求（原串行循环，无空转长轮询）')
    assert.ok(startedB - aResult < 1_000,
      `第二条串行作业应在 A 结果回传后立即启动（实际 ${startedB - aResult}ms，不得被在途空轮询拖住）`)
    for (const claim of stack.cloud.claimLog) {
      assert.equal(claim.parallel_only, false, '无并行工具设备不发送 parallel_only')
    }
  } finally {
    await stack.stop()
  }
})

test('混合设备：串行结束立即领取下一条串行作业——在途 parallel_only 轮询脱挂，不拖慢串行队列', async () => {
  const stack = await startTestStack({ parallelTools: PARALLEL_TOOLS, claimWaitSeconds: 5 })
  stack.cloud.parallelClaimSupported = true
  try {
    // A 串行（400ms）执行中循环发 parallel_only 长轮询（wait=5s 在途）；A 结束时轮询未返回
    // → 脱挂并立即发空参数领取，B（串行队头）<1s 内启动，不等在途轮询超时
    const a = stack.cloud.enqueueInvocation('boss_goto', { durationMs: 400, stepMs: 80 })
    const b = stack.cloud.enqueueInvocation('boss_goto', { durationMs: 100 })
    await stack.cloud.waitFor(() => stack.cloud.getInvocation(b)!.state === 'succeeded', 15_000, 'B 完成领取执行')

    const aResult = stack.cloud.callsFor(a).find((e) => e.type === 'result')!.at
    const startedB = stack.cloud.callsFor(b).find((e) => e.type === 'started')!.at
    assert.ok(startedB - aResult < 1_000,
      `串行队头第二条应在 A 结果回传后立即领取（实际 ${startedB - aResult}ms，不得等在途长轮询）`)
  } finally {
    await stack.stop()
  }
})

test('脱挂轮询随后返回的并行作业照常后台执行：无丢失、无 unknown、串行队列时效不受影响', async () => {
  const stack = await startTestStack({ parallelTools: PARALLEL_TOOLS, claimWaitSeconds: 4 })
  stack.cloud.parallelClaimSupported = true
  try {
    const a = stack.cloud.enqueueInvocation('boss_goto', { durationMs: 300, stepMs: 60 })
    const b = stack.cloud.enqueueInvocation('boss_goto', { durationMs: 250 })
    await stack.cloud.waitFor(() => stack.cloud.getInvocation(a)!.state === 'succeeded', 15_000, 'A 完成')
    // A 结果回传后脱挂的 parallel_only 轮询仍在途（wait=4s）：此时入队并行 C——
    // 脱挂轮询返回它照常后台执行；B（串行队头）同时保持 <1s 启动
    const c = stack.cloud.enqueueInvocation('boss_filter', { durationMs: 150 }, { parallel: true })
    await stack.cloud.waitFor(() => stack.cloud.callsFor(c).some((e) => e.type === 'started'), 15_000, '并行 C 被领取执行')
    await stack.cloud.waitFor(() => stack.cloud.getInvocation(c)!.state === 'succeeded', 15_000, 'C 终态')

    const aResult = stack.cloud.callsFor(a).find((e) => e.type === 'result')!.at
    const startedB = stack.cloud.callsFor(b).find((e) => e.type === 'started')!.at
    assert.ok(startedB - aResult < 1_000, `串行 B 不被并行 C 入队与在途轮询拖住（实际 ${startedB - aResult}ms）`)
    assert.equal(stack.cloud.getInvocation(c)!.state, 'succeeded', '脱挂轮询交付的并行作业正常完成（不丢作业、无 unknown）')
    const cResult = stack.cloud.callsFor(c).find((e) => e.type === 'result')!
    assert.equal(cResult.payload['success'], true, '回执沿原通路')
    for (const id of [a, b, c]) {
      assert.notEqual(stack.cloud.getInvocation(id)!.state, 'unknown', `${id} 无卡租约 unknown`)
    }
  } finally {
    await stack.stop()
  }
})
