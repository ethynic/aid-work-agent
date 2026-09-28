/**
 * PerfCollector 聚合正确性（Phase 1 性能埋点，2026-09-28）：
 * count/totalMs/maxMs 聚合、异常透传仍记时、summaryParts 排序与格式、空 collector、快照深拷贝。
 * 计时断言用宽松下界（真实定时器只保证不小于设定值，慢环境只会更大）。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { PerfCollector } from '../src/main/perf.js'

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

test('time 聚合 count/totalMs/maxMs', async () => {
  const c = new PerfCollector()
  await c.time('a', () => sleep(15))
  await c.time('a', () => sleep(40))
  await c.time('a', async () => 7) // 快路径（同步返回值）
  const e = c.snapshot().get('a')
  assert.ok(e, 'a 应有聚合条目')
  assert.equal(e.count, 3)
  assert.ok(e.totalMs >= 55, `totalMs=${e.totalMs} 应 ≥ 15+40`)
  assert.ok(e.maxMs >= 40, `maxMs=${e.maxMs} 应 ≥ 40`)
  assert.ok(e.maxMs <= e.totalMs)
})

test('fn 抛错：照常记时并原样重新抛出', async () => {
  const c = new PerfCollector()
  await assert.rejects(
    c.time('boom', async () => {
      await sleep(12)
      throw new Error('炸了')
    }),
    /炸了/,
  )
  const e = c.snapshot().get('boom')
  assert.ok(e, '异常路径也应记时')
  assert.equal(e.count, 1)
  assert.ok(e.totalMs >= 12, `totalMs=${e.totalMs} 应 ≥ 12`)
  // 记时不影响后续聚合
  await c.time('boom', () => sleep(1))
  assert.equal(c.snapshot().get('boom')!.count, 2)
})

test('summaryParts：按 total_ms 降序、固定片段格式', async () => {
  const c = new PerfCollector()
  await c.time('fast', () => sleep(1))
  await c.time('slow', () => sleep(50))
  await c.time('fast', () => sleep(2))
  const [first, second] = c.summaryParts().split(' ')
  assert.match(first!, /^slow\{n=1,total_ms=\d+,max_ms=\d+\}$/)
  assert.match(second!, /^fast\{n=2,total_ms=\d+,max_ms=\d+\}$/)
})

test('空 collector：summaryParts 返回空串、snapshot 为空 Map', () => {
  const c = new PerfCollector()
  assert.equal(c.summaryParts(), '')
  assert.equal(c.snapshot().size, 0)
  assert.equal(c.totalMsOf('never'), 0)
})

test('snapshot 深拷贝：外部修改不影响内部聚合', async () => {
  const c = new PerfCollector()
  await c.time('x', async () => 1)
  const snap = c.snapshot()
  snap.get('x')!.count = 99
  snap.set('y', { count: 1, totalMs: 1, maxMs: 1 })
  assert.equal(c.snapshot().get('x')!.count, 1)
  assert.ok(!c.snapshot().has('y'))
})
