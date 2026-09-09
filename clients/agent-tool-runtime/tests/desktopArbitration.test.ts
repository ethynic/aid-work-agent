/**
 * 仲裁入口（E2E）：全部 Provider tool call 经桌面资源锁执行；外部占锁 + 缩短超时 →
 * DESKTOP_RESOURCE_BUSY（effect=none、retryable=true），绝不复用旧 'BUSY' 码；
 * 释放后后续 invocation 可正常获得锁执行。
 */
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import { test } from 'node:test'
import { desktopLockName, deriveResourceKey } from '../src/desktopLock.js'
import { startTestStack, fakeProviderEntry } from './helpers/runtimeStack.js'

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

/** 外部进程占住指定锁名（不经被测代码，保证真实进程互斥不被 mock） */
async function holdLockExternally(name: string, holdMs: number): Promise<{ exited: Promise<void> }> {
  const holder = spawn(process.execPath, ['--input-type=module', '-e', `
    import net from 'node:net'
    const server = net.createServer((s) => s.destroy())
    server.listen(${JSON.stringify(name)}, () => {
      process.stdout.write('HELD\\n')
      setTimeout(() => process.exit(0), ${holdMs})
    })
    server.on('error', (err) => { process.stderr.write(String(err)); process.exit(1) })
  `], { stdio: ['ignore', 'pipe', 'ignore'] })
  const exited = new Promise<void>((resolve) => holder.on('exit', () => resolve()))
  let out = ''
  holder.stdout.on('data', (c: Buffer) => { out += c.toString() })
  const deadline = Date.now() + 5_000
  while (!out.includes('HELD') && Date.now() < deadline) await sleep(20)
  assert.ok(out.includes('HELD'), '外部持锁进程应已监听管道')
  return { exited }
}

test('仲裁超时：外部进程占锁 → DESKTOP_RESOURCE_BUSY（effect none、retryable、非旧 BUSY 码）', async () => {
  const resourceKey = deriveResourceKey(randomUUID())
  const lockName = desktopLockName(resourceKey)
  const { exited } = await holdLockExternally(lockName, 1_500)
  const stack = await startTestStack({ desktopResourceKey: resourceKey, desktopLockTimeoutMs: 300 })
  try {
    const id = stack.cloud.enqueueInvocation('boss_greet', { durationMs: 100 }, { provider: 'boss-recruiting' })
    await stack.cloud.waitFor(
      () => stack.cloud.callsFor(id).some((c) => c.type === 'result'),
      15_000,
      'DESKTOP_RESOURCE_BUSY result',
    )
    const calls = stack.cloud.callsFor(id)
    // 锁等待发生在 started 之后：started 应存在；等待期仅有租约续期心跳（空 progress），无业务执行
    assert.ok(calls.some((c) => c.type === 'started'), '仲裁等待在 started 之后，started 应存在')
    const result = calls.find((c) => c.type === 'result')!
    assert.equal(result.payload['success'], false)
    assert.equal(result.payload['code'], 'DESKTOP_RESOURCE_BUSY')
    assert.notEqual(result.payload['code'], 'BUSY', '绝不复用旧 BUSY 错误码')
    assert.equal(result.payload['effect'], 'none')
    assert.equal(result.payload['retryable'], true)
    assert.equal(stack.cloud.getInvocation(id)!.state, 'failed')

    // 外部释放后：下一个 invocation 正常获得锁执行成功
    await exited
    const okId = stack.cloud.enqueueInvocation('boss_goto', { durationMs: 100 }, { provider: 'boss-recruiting' })
    await stack.cloud.waitFor(() => stack.cloud.getInvocation(okId)?.state === 'succeeded', 15_000, 'lock released then success')
  } finally {
    await stack.stop()
  }
})

test('仲裁互斥：两个 stack 同 resource_key 串行执行（一个持锁时另一个排队等待获得）', async () => {
  const resourceKey = deriveResourceKey(randomUUID())
  const stackA = await startTestStack({ desktopResourceKey: resourceKey })
  const stackB = await startTestStack({ desktopResourceKey: resourceKey, providerEntries: { weixin: fakeProviderEntry() } })
  try {
    // A 先注入长任务（boss 链路）
    const idA = stackA.cloud.enqueueInvocation('boss_greet', { durationMs: 1_200, stepMs: 100 }, { provider: 'boss-recruiting' })
    // 等 A 的业务进度（fakeProvider 工具内通知，带 message）出现——证明 A 已持有桌面锁执行中
    await stackA.cloud.waitFor(
      () => stackA.cloud.callsFor(idA).some(
        (c) => c.type === 'progress' && String(c.payload['message'] ?? '').includes('working'),
      ),
      15_000,
      'A 业务进度（已持锁）',
    )
    // 此时 B（weixin 实例路由）注入短任务：必须排队等 A 释放锁后才能执行
    const tEnqueueB = Date.now()
    const idB = stackB.cloud.enqueueInvocation('weixin_probe', { durationMs: 100 }, { provider: 'weixin' })
    await stackB.cloud.waitFor(() => stackB.cloud.getInvocation(idB)?.state === 'succeeded', 20_000, 'B succeeded')
    await stackA.cloud.waitFor(() => stackA.cloud.getInvocation(idA)?.state === 'succeeded', 20_000, 'A succeeded')
    // 排队证据：B 自身工作仅 100ms，总耗时却须覆盖 A 的剩余持锁时间（≥ 数百 ms）
    const bTotal = Date.now() - tEnqueueB
    assert.ok(bTotal >= 700, `B 应排队等待 A 持锁执行完（串行证据），实际总耗时 ${bTotal}ms`)
  } finally {
    await Promise.all([stackA.stop(), stackB.stop()])
  }
})

test('R24 机器作用域锁：同机两个不同 device_id 的栈共享同一把锁（resource_key 去 device_id）串行执行', async () => {
  // 机器作用域 key：deriveResourceKey() 不再接收 device_id——同机同 Windows 用户+会话恒同锁
  const resourceKey = deriveResourceKey()
  const stackA = await startTestStack({ desktopResourceKey: resourceKey })
  const stackB = await startTestStack({ desktopResourceKey: resourceKey, providerEntries: { weixin: fakeProviderEntry() } })
  try {
    assert.notEqual(stackA.deviceId, stackB.deviceId, '前提：两栈是不同 device_id（云端配对各异）')
    const idA = stackA.cloud.enqueueInvocation('boss_greet', { durationMs: 1_200, stepMs: 100 }, { provider: 'boss-recruiting' })
    await stackA.cloud.waitFor(
      () => stackA.cloud.callsFor(idA).some(
        (c) => c.type === 'progress' && String(c.payload['message'] ?? '').includes('working'),
      ),
      15_000,
      'A 业务进度（已持锁）',
    )
    const tEnqueueB = Date.now()
    const idB = stackB.cloud.enqueueInvocation('weixin_probe', { durationMs: 100 }, { provider: 'weixin' })
    await stackB.cloud.waitFor(() => stackB.cloud.getInvocation(idB)?.state === 'succeeded', 20_000, 'B succeeded')
    await stackA.cloud.waitFor(() => stackA.cloud.getInvocation(idA)?.state === 'succeeded', 20_000, 'A succeeded')
    const bTotal = Date.now() - tEnqueueB
    assert.ok(bTotal >= 700, `不同 device_id 仍须共享桌面锁串行执行（R24），实际总耗时 ${bTotal}ms`)
  } finally {
    await Promise.all([stackA.stop(), stackB.stop()])
  }
})

test('R24 跨进程互斥：外部进程持机器作用域锁（无 device 概念）→ 不同 device_id 的栈等待，释放后执行', async () => {
  const resourceKey = deriveResourceKey()
  const lockName = desktopLockName(resourceKey)
  const { exited } = await holdLockExternally(lockName, 1_500)
  const stack = await startTestStack({ desktopResourceKey: resourceKey })
  try {
    const id = stack.cloud.enqueueInvocation('boss_greet', { durationMs: 100 }, { provider: 'boss-recruiting' })
    // 锁等待发生在 started 之后：started 出现，但外部持锁期间不得有执行结果
    await stack.cloud.waitFor(() => stack.cloud.callsFor(id).some((c) => c.type === 'started'), 15_000, 'started during hold')
    await sleep(300)
    assert.ok(!stack.cloud.callsFor(id).some((c) => c.type === 'result'), '外部持锁期间不得执行')
    await exited
    const exitAt = Date.now()
    await stack.cloud.waitFor(
      () => stack.cloud.callsFor(id).some((c) => c.type === 'result'),
      15_000,
      'result after external release',
    )
    const result = stack.cloud.callsFor(id).find((c) => c.type === 'result')!
    assert.ok(result.at >= exitAt - 100, `执行必须发生在外部持锁进程退出后（result@${result.at} exit@${exitAt}）`)
    assert.equal(result.payload['success'], true)
  } finally {
    await stack.stop()
  }
})
