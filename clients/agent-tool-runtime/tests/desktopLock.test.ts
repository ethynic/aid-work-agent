/**
 * 桌面资源锁：命名管道互斥——同锁名串行、超时抛 DesktopLockTimeoutError、释放后可获得、
 * 真实跨进程互斥（外部 node 进程占锁，非 mock）。
 */
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { createHash, randomUUID } from 'node:crypto'
import os from 'node:os'
import { test } from 'node:test'
import {
  DesktopLockTimeoutError,
  deriveResourceKey,
  desktopLockName,
  withDesktopLock,
} from '../src/desktopLock.js'

function uniqueLockName(): string {
  return desktopLockName(`t${randomUUID().replace(/-/g, '')}`)
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

test('desktopLock：同锁名并发调用串行执行（进程级并发安全）', async () => {
  const name = uniqueLockName()
  const order: string[] = []
  const run = async (tag: string, holdMs: number) => {
    await withDesktopLock(name, async () => {
      order.push(`start-${tag}`)
      await sleep(holdMs)
      order.push(`end-${tag}`)
    }, { timeoutMs: 5_000 })
  }
  await Promise.all([run('a', 150), run('b', 150), run('c', 10)])
  // 任意执行序都必须是一个接一个：不存在 start-x 与 start-y 之间没有对应 end-x
  let held = false
  for (const entry of order) {
    if (entry.startsWith('start-')) {
      assert.ok(!held, `必须串行执行，实际顺序: ${order.join(',')}`)
      held = true
    } else {
      held = false
    }
  }
})

test('desktopLock：持锁期间第二个 acquire 超时 → DesktopLockTimeoutError；释放后可获得', async () => {
  const name = uniqueLockName()
  let releaseFirst: (() => void) | null = null
  const firstGate = new Promise<void>((resolve) => {
    void withDesktopLock(name, () => new Promise<void>((r) => {
      releaseFirst = r
      resolve()
    }), { timeoutMs: 5_000 }).catch(() => {})
  })
  await firstGate // 第一个已持锁
  await assert.rejects(
    () => withDesktopLock(name, async () => 'never', { timeoutMs: 250 }),
    (err: unknown) => err instanceof DesktopLockTimeoutError,
  )
  releaseFirst!()
  await sleep(50)
  // 释放后可获得
  const got = await withDesktopLock(name, async () => 'acquired', { timeoutMs: 5_000 })
  assert.equal(got, 'acquired')
})

test('desktopLock：fn 抛异常/被中止后锁必释放（异常路径不泄漏句柄）', async () => {
  const name = uniqueLockName()
  await assert.rejects(
    () => withDesktopLock(name, async () => {
      throw new Error('boom')
    }, { timeoutMs: 1_000 }),
    /boom/,
  )
  // 前一次异常退出后锁应可立即再获得
  const got = await withDesktopLock(name, async () => 'ok', { timeoutMs: 1_000 })
  assert.equal(got, 'ok')

  // 等待期被 abort：持锁被他人占用时等待方 abort → 抛中止错误且未获得锁
  let releaseHolder: (() => void) | null = null
  const holderGate = new Promise<void>((resolve) => {
    void withDesktopLock(name, () => new Promise<void>((r) => {
      releaseHolder = r
      resolve()
    }), { timeoutMs: 5_000 }).catch(() => {})
  })
  await holderGate
  const controller = new AbortController()
  const pending = withDesktopLock(name, async () => 'never', { timeoutMs: 10_000, signal: controller.signal })
  await sleep(50)
  controller.abort()
  await assert.rejects(pending, /中止/)
  releaseHolder!()
  const after = await withDesktopLock(name, async () => 'ok2', { timeoutMs: 1_000 })
  assert.equal(after, 'ok2')
})

test('desktopLock：真实跨进程互斥——外部 node 进程持锁时本进程 acquire 超时，外部退出后可获得', async () => {
  const name = uniqueLockName()
  // 外部进程直接 net.createServer 监听同一管道名（不经被测代码路径，杜绝 mock 掩盖）
  const holder = spawn(process.execPath, ['--input-type=module', '-e', `
    import net from 'node:net'
    const server = net.createServer((s) => s.destroy())
    server.listen(${JSON.stringify(name)}, () => {
      process.stdout.write('HELD\\n')
      setTimeout(() => process.exit(0), 1500)
    })
    server.on('error', (err) => { process.stderr.write(String(err)); process.exit(1) })
  `], { stdio: ['ignore', 'pipe', 'pipe'] })
  let heldOutput = ''
  holder.stdout.on('data', (c: Buffer) => { heldOutput += c.toString() })
  const holderExit = new Promise<void>((resolve) => holder.on('exit', () => resolve()))
  const deadline = Date.now() + 5_000
  while (!heldOutput.includes('HELD') && Date.now() < deadline) await sleep(20)
  assert.ok(heldOutput.includes('HELD'), '外部持锁进程应已监听管道')

  // 外部进程持锁 → 本进程短超时 acquire 失败
  await assert.rejects(
    () => withDesktopLock(name, async () => 'never', { timeoutMs: 300 }),
    (err: unknown) => err instanceof DesktopLockTimeoutError,
  )

  // 外部进程退出（OS 回收管道句柄）→ 本进程可获得
  await holderExit
  const got = await withDesktopLock(name, async () => 'recovered', { timeoutMs: 5_000 })
  assert.equal(got, 'recovered')
})

test('desktopLock：锁名与 resource_key 派生规范（R24：机器作用域，不含 device_id）', () => {
  const key = deriveResourceKey()
  assert.match(key, /^[0-9a-f]{64}$/, 'resource_key 应为 sha256 hex')
  assert.equal(desktopLockName(key, 'win32'), `\\\\.\\pipe\\AidWorkAgent.DesktopResource.${key}`)
  // 派生公式：sha256(hostname|username|sessionId)，同机同会话恒同 key（与云端配对 device 无关）
  const expected = createHash('sha256')
    .update(`${os.hostname()}|${os.userInfo().username}|`)
    .digest('hex')
  assert.equal(key, expected)
  assert.equal(deriveResourceKey(), key, '无参调用确定性（同机同会话恒同锁）')
  // 会话隔离维度仍在：不同 sessionId 派生不同 key（测试栈隔离依赖）
  assert.notEqual(deriveResourceKey('session-1'), deriveResourceKey('session-2'))
  assert.notEqual(deriveResourceKey('session-1'), key)
  // 非法字符集拒绝
  assert.throws(() => desktopLockName('bad key with spaces'))
  assert.throws(() => desktopLockName('bad/key'))
})
