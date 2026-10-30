/**
 * 任务 3.3 集成验证（合成）：CLI watch 跨进程互斥【按轮获取/释放】。
 *
 * 以子进程真实跑 dist 的 watch 循环（AID_WECOM_TEST_DRIVER_STUB=1 桩轮，不触达真实企微
 * 驱动；AID_WECOM_MUTEX_SCOPE 隔离命名管道），证明三件事：
 *   1. 轮结束（含失败轮）立即释放——首个 tick（轮 1 完成）后，测试进程能成功 acquire
 *      同 scope 互斥（轮询间隔不持锁，旧行为是全程持锁时这里必然 BUSY）；
 *   2. 占用中被跳过的轮不终止循环——测试进程持锁期间 watch 输出 BUSY 跳过告警且进程存活；
 *   3. 下一轮 watch 仍能自行 acquire——释放后 watch 继续产出 tick（轮内获取正常）。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { spawn, type ChildProcess } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { NamedMutex } from '../src/platform/namedMutex.js'

const DIST_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const CLI = path.join(DIST_ROOT, 'src', 'cli', 'index.js')

/** 带截止时间的轮询等待（避免测试挂死）；probe 支持异步（如 await acquire） */
async function waitFor(desc: string, deadlineMs: number, probe: () => boolean | Promise<boolean>): Promise<void> {
  const deadline = Date.now() + deadlineMs
  while (Date.now() < deadline) {
    if (await probe()) return
    await new Promise((resolve) => setTimeout(resolve, 25))
  }
  assert.fail(`等待超时（${deadlineMs}ms）：${desc}`)
}

test('watch：轮询间隔不持锁——间隔窗口内其他进程可 acquire；占用轮跳过；下一轮自行恢复', async () => {
  const scope = `watch-mutex-${process.pid}-${Date.now()}`
  const child: ChildProcess = spawn(process.execPath, [CLI, 'watch', '--interval', '1'], {
    env: { ...process.env, AID_WECOM_MUTEX_SCOPE: scope, AID_WECOM_TEST_DRIVER_STUB: '1' },
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  child.stdout?.setEncoding('utf8')
  child.stderr?.setEncoding('utf8')
  let stdout = ''
  let stderr = ''
  child.stdout?.on('data', (d: string) => (stdout += d))
  child.stderr?.on('data', (d: string) => (stderr += d))
  let exited = false
  child.on('close', () => (exited = true))

  const ticks = () => stdout.split('\n').filter((l) => l.includes('"type":"tick"')).length
  try {
    // 轮 1 完成（首个 tick）；桩轮瞬时完成，此后 watch 处于轮询间隔（不持锁）
    await waitFor('首个 tick（轮 1 完成）', 15000, () => ticks() >= 1)

    // 1) 间隔窗口内：另一进程成功 acquire 同 scope 互斥（证明锁已释放）
    const probe = new NamedMutex(scope)
    let acquired = false
    await waitFor('测试进程 acquire 成功（证明 watch 轮间不持锁）', 15000, async () => {
      if (!acquired) acquired = await probe.acquire().catch(() => false)
      return acquired
    })
    assert.ok(acquired, '轮询间隔内同 scope 互斥必须可被其他进程获取（旧行为全程持锁会失败）')
    try {
      // 2) 持锁期间：watch 的下一轮跳过（BUSY 告警）但循环不终止
      await waitFor('watch 输出 BUSY 跳过告警', 15000, () => stderr.includes('本轮跳过'))
      assert.ok(!exited, '被跳过的轮不得终止 watch 循环')
    } finally {
      await probe.release()
    }

    // 3) 释放后：下一轮 watch 仍能自行 acquire 并继续产出 tick
    const before = ticks()
    await waitFor('释放后 watch 继续产出 tick（下一轮自行获取成功）', 15000, () => ticks() >= before + 1)
    assert.ok(!exited, 'watch 循环在锁竞争后仍存活')
  } finally {
    child.kill()
    await new Promise<void>((resolve) => {
      if (exited) return resolve()
      child.once('close', () => resolve())
      setTimeout(resolve, 5000).unref()
    })
  }
  assert.ok(ticks() >= 2, `应观察到至少两轮 tick（实际 ${ticks()}）`)
})
