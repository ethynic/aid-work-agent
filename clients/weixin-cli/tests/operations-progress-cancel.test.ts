/**
 * progress 回调、AbortSignal 协作取消、单飞 BUSY 与取消后锁释放（设计 §6.1/§6.2）。
 *
 * 悬挂用测试 hook AID_WEIXIN_TEST_HANG=1（子进程维度由 mcp-server/conformance 覆盖，
 * 本文件验证 operation 与 runExclusive 层语义）。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { createWeixinProbeOperation } from '../src/operations/probe.js'
import { runExclusive } from '../src/mcp/server.js'
import { NamedMutex } from '../src/platform/namedMutex.js'
import { hangUntilAbort, runWeixinOperation } from '../src/operations/context.js'
import { probeEnvironment } from '../src/platform/environment.js'
import type { OpContext, OperationResult, ProgressEvent } from '../src/operations/types.js'

/** 悬挂 operation：走真实 runWeixinOperation 骨架（取消映射为 CANCELLED 结构化结果） */
function hangOperation(signal: AbortSignal): Promise<OperationResult> {
  return runWeixinOperation('readonly', { signal, progress: () => {} }, () => null, async () => {
    await hangUntilAbort(signal)
    return { message: '不会到达' }
  })
}

/** 正常环境的 fake 探测（不触达系统命令） */
function fakeProbeEnv() {
  return (opts: Parameters<typeof probeEnvironment>[0]) =>
    probeEnvironment({
      ...opts,
      sessionName: 'Console',
      execFileFn: async (file) => ({
        stdout: file.includes('where') ? 'C:\\powershell.exe\r\n' : '"Weixin.exe","1","Console","1","1,000 K"\r\n',
      }),
    })
}

test('probe：progress 按检查步骤推进并以 done 收尾', async () => {
  const events: ProgressEvent[] = []
  const ctx: OpContext = { signal: new AbortController().signal, progress: (p) => events.push(p) }
  const op = createWeixinProbeOperation({ probeEnvironmentFn: fakeProbeEnv() })
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
  const op = createWeixinProbeOperation({
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
  const mutex = new NamedMutex(`test-runexclusive-busy-${process.pid}`)
  const ac = new AbortController()
  const first = runExclusive(running, mutex, 'weixin_probe', () => hangOperation(ac.signal))
  first.catch(() => {})
  await new Promise((r) => setTimeout(r, 100))

  const started = Date.now()
  const second = await runExclusive(running, mutex, 'weixin_probe', async () => {
    throw new Error('单飞内不应执行')
  })
  const elapsed = Date.now() - started
  assert.equal(second.success, false)
  assert.equal(second.code, 'BUSY')
  assert.equal(second.effect, 'none')
  assert.equal(second.retryable, true)
  assert.ok(elapsed < 5000, `BUSY 应快速返回（耗时 ${elapsed}ms）`)

  ac.abort()
  await first.catch(() => {})
  await mutex.release()
})

test('runExclusive：互斥占用抛系统错误时映射为 INTERNAL_ERROR 结构化结果（不向 handler 穿透）', async () => {
  // 意图：acquire 的非 EADDRINUSE 失败（权限/系统错误）必须变成结构化结果，
  // 否则原始异常穿透 MCP handler，Host 拿到的是无法按 code 决策的自由文本 isError
  const running: { current: string | null } = { current: null }
  const brokenMutex = {
    scope: 'test-broken',
    held: false,
    acquire: async () => {
      throw new Error('mock 系统错误：拒绝访问')
    },
    release: async () => {},
  } as unknown as NamedMutex
  const r = await runExclusive(running, brokenMutex, 'weixin_probe', async () => {
    throw new Error('互斥失败后不应执行')
  })
  assert.equal(r.success, false)
  assert.equal(r.code, 'INTERNAL_ERROR')
  assert.equal(r.effect, 'none')
  assert.equal(r.retryable, false)
  assert.equal(running.current, null, '失败后单飞位必须复位')
})

test('runExclusive：跨进程互斥——命名管道被占用时返回 BUSY', async () => {
  const holder = new NamedMutex(`test-runexclusive-xproc-${process.pid}`)
  assert.equal(await holder.acquire(), true)
  const running: { current: string | null } = { current: null }
  const contender = new NamedMutex(`test-runexclusive-xproc-${process.pid}`)
  const r = await runExclusive(running, contender, 'weixin_probe', async () => {
    throw new Error('互斥内不应执行')
  })
  assert.equal(r.code, 'BUSY')
  assert.ok(r.message.includes('跨进程'))
  assert.equal(contender.held, false)
  await holder.release()
})

test('runExclusive：取消后锁释放（命名管道与单飞位都可复用）', async () => {
  const running: { current: string | null } = { current: null }
  const mutex = new NamedMutex(`test-runexclusive-cancel-${process.pid}`)
  const ac = new AbortController()
  const first = runExclusive(running, mutex, 'weixin_probe', () => hangOperation(ac.signal))
  await new Promise((r) => setTimeout(r, 100))
  assert.equal(mutex.held, true)

  ac.abort()
  const r1 = await first
  assert.equal(r1.code, 'CANCELLED')
  assert.equal(mutex.held, false)
  assert.equal(running.current, null)

  // 锁已释放：同 scope 新互斥可立即占用；新一轮调用正常执行
  const probe = new NamedMutex(`test-runexclusive-cancel-${process.pid}`)
  assert.equal(await probe.acquire(), true)
  await probe.release()
  const r2 = await runExclusive(running, mutex, 'weixin_probe', async (): Promise<OperationResult> => {
    const op = createWeixinProbeOperation({ probeEnvironmentFn: fakeProbeEnv() })
    return op.execute({}, { signal: new AbortController().signal, progress: () => {} })
  })
  assert.equal(r2.success, true)
})
