/**
 * progress 回调、AbortSignal 协作取消、单飞 BUSY 与取消后锁释放。
 *
 * 悬挂用测试 hook AID_WECOM_TEST_HANG=1 的等价物：本文件直接用 hangUntilAbort
 * 走真实 runWecomOperation 骨架验证 operation 与 runExclusive 层语义（不启动驱动）。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { createWecomProbeOperation } from '../src/operations/probe.js'
import { runExclusive } from '../src/mcp/server.js'
import { NamedMutex } from '../src/platform/namedMutex.js'
import { hangUntilAbort, runWecomOperation } from '../src/operations/context.js'
import { probeEnvironment } from '../src/platform/environment.js'
import type { OpContext, OperationResult, ProgressEvent } from '../src/operations/types.js'

/** 悬挂 operation：走真实 runWecomOperation 骨架（取消映射为 CANCELLED 结构化结果） */
function hangOperation(signal: AbortSignal): Promise<OperationResult> {
  return runWecomOperation('readonly', { signal, progress: () => {} }, () => null, async () => {
    await hangUntilAbort(signal)
    return { message: '不会到达' }
  })
}

/** 正常环境的 fake 探测（不触达系统命令；企微未运行 → 不启动驱动） */
function fakeProbeEnv() {
  return (opts: Parameters<typeof probeEnvironment>[0]) =>
    probeEnvironment({
      ...opts,
      sessionName: 'Console',
      execFileFn: async (file) => ({
        stdout: file.includes('where') ? 'C:\\powershell.exe\r\n' : '信息: 没有运行的任务匹配指定标准。',
      }),
    })
}

test('probe：progress 按检查步骤推进并以 done 收尾', async () => {
  const events: ProgressEvent[] = []
  const ctx: OpContext = { signal: new AbortController().signal, progress: (p) => events.push(p) }
  const op = createWecomProbeOperation({ probeEnvironmentFn: fakeProbeEnv() })
  const r = await op.execute({}, ctx)
  assert.equal(r.success, true)
  const stages = events.map((e) => e.stage)
  assert.ok(stages.filter((s) => s === 'check').length >= 3, `应至少 3 个 check 步骤：${stages.join(',')}`)
  assert.equal(stages[stages.length - 1], 'done')
})

test('probe：入口前已 abort → CANCELLED + effect=none，不触达环境探测', async () => {
  const ac = new AbortController()
  ac.abort()
  let probeCalled = 0
  const op = createWecomProbeOperation({
    probeEnvironmentFn: (opts) => {
      probeCalled++
      return fakeProbeEnv()(opts)
    },
  })
  const r = await op.execute({}, { signal: ac.signal, progress: () => {} })
  assert.equal(r.code, 'CANCELLED')
  assert.equal(r.effect, 'none')
  assert.equal(probeCalled, 0)
})

test('hangUntilAbort：悬挂直到 abort，abort 后抛 CancelledError', async () => {
  const ac = new AbortController()
  const p = hangUntilAbort(ac.signal)
  const outcome = Promise.race([p.then(() => 'resolved', (e: Error) => e.name), new Promise((r) => setTimeout(() => r('pending'), 200))])
  assert.equal(await outcome, 'pending')
  ac.abort()
  await assert.rejects(p, (e: Error) => e.name === 'CancelledError')
})

test('runExclusive：并发第二个调用快速返回 BUSY 结构化结果', async () => {
  const running: { current: string | null } = { current: null }
  const mutex = new NamedMutex(`test-wecom-runexclusive-busy-${process.pid}`)
  const ac = new AbortController()
  const first = runExclusive(running, mutex, 'wecom_probe', () => hangOperation(ac.signal))
  first.catch(() => {})
  await new Promise((r) => setTimeout(r, 100))

  const started = Date.now()
  const second = await runExclusive(running, mutex, 'wecom_probe', () => hangOperation(ac.signal))
  assert.equal(second.success, false)
  assert.equal(second.code, 'BUSY')
  assert.ok(Date.now() - started < 1000, 'BUSY 应立即返回，不等待前一个操作')

  ac.abort()
  const firstResult = await first
  assert.equal(firstResult.code, 'CANCELLED')
  assert.equal(running.current, null, '取消后单飞锁应释放')
})

test('runExclusive：取消释放跨进程互斥，后续调用可再占用', async () => {
  const running: { current: string | null } = { current: null }
  const mutex = new NamedMutex(`test-wecom-runexclusive-release-${process.pid}`)
  const ac = new AbortController()
  const first = runExclusive(running, mutex, 'wecom_probe', () => hangOperation(ac.signal))
  first.catch(() => {})
  await new Promise((r) => setTimeout(r, 100))
  ac.abort()
  await first

  const r = await runExclusive(running, mutex, 'wecom_probe', async () => {
    return { success: true, code: 'OK', message: 'ok', effect: 'none', data: {}, retryable: false, run_id: 'x' } as OperationResult
  })
  assert.equal(r.success, true, '取消后互斥应已释放，后续调用可正常执行')
})
