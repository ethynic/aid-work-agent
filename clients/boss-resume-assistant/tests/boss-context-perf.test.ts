/**
 * runBossOperation 性能埋点（Phase 1，2026-09-28）：
 * - 结束时向 stderr 输出 [boss-perf] 单行（成功 ok=1 / 失败 ok=0 都输出）
 * - body 第三参收到 PerfCollector 实例
 * - session 原语被逐字段包装计时（测试注入的 fake session 同样被覆盖）
 *
 * stderr 断言：临时替换 process.stderr.write 收集再还原（不依赖子进程）。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { runBossOperation, type BossSession } from '../src/main/operations/bossContext.js'
import { PerfCollector } from '../src/main/perf.js'
import type { OpContext } from '../src/main/operations/types.js'
import type { DomSnapshot } from '../src/main/boss/domSnapshot.js'

function silentCtx(): OpContext {
  return { signal: new AbortController().signal, progress: () => {} }
}

const emptySnap: DomSnapshot = { strings: [], documents: [] }

function fakeSession(): BossSession {
  // 不带 clearInput/pageNavigate 可选成员——验证可选包装不要求成员存在
  return {
    snapshot: async () => emptySnap,
    click: async () => {},
    clickBrowse: async () => {},
    mouseWheel: async () => {},
    pressEscape: async () => {},
    clickAndType: async () => {},
    captureFullpage: async () => Buffer.alloc(0),
    getUrl: async () => 'https://www.zhipin.com/web/chat/recommend',
    close: async () => {},
  }
}

/** 临时接管 process.stderr.write 收集输出（finally 还原，不影响其他测试） */
async function captureStderr(fn: () => Promise<void>): Promise<string[]> {
  const chunks: string[] = []
  const orig = process.stderr.write.bind(process.stderr)
  process.stderr.write = ((chunk: Uint8Array | string) => {
    chunks.push(typeof chunk === 'string' ? chunk : Buffer.from(chunk).toString())
    return true
  }) as unknown as typeof process.stderr.write
  try {
    await fn()
  } finally {
    process.stderr.write = orig
  }
  return chunks
}

test('成功路径：[boss-perf] ok=1 行 + body 收到 PerfCollector + 原语包装计数', async () => {
  const session = fakeSession()
  let perfArg: PerfCollector | undefined
  let closeCalled = false
  session.close = async () => {
    closeCalled = true
  }
  const chunks = await captureStderr(async () => {
    const result = await runBossOperation(
      { kind: 'readonly', name: 'perf_probe' },
      silentCtx(),
      async () => session,
      () => null,
      async (s, _tracker, perf) => {
        perfArg = perf
        await s.snapshot()
        await s.click({ x: 1, y: 2 }, { width: 100, height: 100 })
        await s.mouseWheel(10, 20, 300)
        return { message: 'done' }
      },
    )
    assert.equal(result.success, true)
    assert.equal(closeCalled, true)
  })
  // body 第三参是 PerfCollector
  assert.ok(perfArg instanceof PerfCollector)
  // 原语被包装：调用经 instrumentSession 计时
  const agg = perfArg!.snapshot()
  assert.equal(agg.get('snapshot')?.count, 1)
  assert.equal(agg.get('win32:click')?.count, 1)
  assert.equal(agg.get('cdp:mouseWheel')?.count, 1)
  // stderr 出现 [boss-perf] 行，字段齐全
  const line = chunks.join('').split('\n').find((l) => l.startsWith('[boss-perf]'))
  assert.ok(line, `应输出 [boss-perf] 行，实际输出：${chunks.join('')}`)
  assert.match(
    line,
    /^\[boss-perf\] tool=perf_probe run_id=[0-9a-f-]{36} ok=1 total_ms=\d+ connect_ms=\d+ body_ms=\d+ close_ms=\d+/,
  )
  // 聚合片段含三段计时与原语计数
  assert.ok(line.includes('win32:click{n=1,'))
  assert.ok(line.includes('snapshot{n=1,'))
})

test('失败路径：body 抛错 → ok=0 行仍输出，异常映射不受埋点影响', async () => {
  const session = fakeSession()
  const chunks = await captureStderr(async () => {
    const result = await runBossOperation(
      { kind: 'write', name: 'perf_fail_probe' },
      silentCtx(),
      async () => session,
      () => null,
      async () => {
        throw new Error('boom: unexpected failure')
      },
    )
    assert.equal(result.success, false)
    assert.equal(result.code, 'INTERNAL_ERROR')
  })
  const lines = chunks.join('').split('\n')
  const perfLine = lines.find((l) => l.startsWith('[boss-perf]'))
  assert.ok(perfLine, '失败也要输出 [boss-perf] 行')
  assert.match(perfLine, /tool=perf_fail_probe .*ok=0 total_ms=\d+ connect_ms=\d+ body_ms=\d+ close_ms=\d+/)
  assert.ok(lines.some((l) => l.startsWith('[boss-op] 失败')), '[boss-op] 失败行保留不变')
})

test('connect 失败：ok=0 行输出，close_ms=0（session 未建立不计时）', async () => {
  const chunks = await captureStderr(async () => {
    const result = await runBossOperation(
      { kind: 'readonly', name: 'perf_connect_fail' },
      silentCtx(),
      async () => {
        throw new Error('connect ECONNREFUSED')
      },
      () => null,
      async () => ({ message: 'unreachable' }),
    )
    assert.equal(result.success, false)
    assert.equal(result.code, 'CHROME_UNAVAILABLE')
  })
  const perfLine = chunks.join('').split('\n').find((l) => l.startsWith('[boss-perf]'))
  assert.ok(perfLine)
  assert.match(perfLine, /ok=0 .*close_ms=0 /)
})

test('可选成员存在时被包装：clearInput/pageNavigate 计数', async () => {
  const session = fakeSession()
  let clearCalls = 0
  session.clearInput = async () => {
    clearCalls++
  }
  let perfArg: PerfCollector | undefined
  await captureStderr(async () => {
    await runBossOperation(
      { kind: 'readonly', name: 'perf_optional' },
      silentCtx(),
      async () => session,
      () => null,
      async (s, _t, perf) => {
        perfArg = perf
        await s.clearInput!()
        return { message: 'done' }
      },
    )
  })
  assert.equal(clearCalls, 1)
  assert.equal(perfArg!.snapshot().get('cdp:clearInput')?.count, 1)
})

test('参数校验失败：无任何 I/O，不输出 [boss-perf] 行', async () => {
  const chunks = await captureStderr(async () => {
    const result = await runBossOperation(
      { kind: 'write', name: 'perf_invalid' },
      silentCtx(),
      async () => fakeSession(),
      () => '参数不能为空',
      async () => ({ message: 'unreachable' }),
    )
    assert.equal(result.code, 'INVALID_ARGUMENT')
  })
  assert.ok(!chunks.join('').includes('[boss-perf]'), 'validate 失败不应有 [boss-perf] 行')
})
