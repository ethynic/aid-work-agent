/**
 * timing 工具单测（2026-11-10 读简历提速 Phase 1）：jitter 区间随机 + 等待常量语义锁定。
 *
 * jitter 用注入随机源做确定性断言（边界映射、整数性、min===max 恒等、区间全覆盖），
 * 默认参数 rand=Math.random 的行为用大量采样断言边界与整数性；
 * 常量语义锁定防误改：区间/定值与设计文档 §3.1/§3.2 定稿值一一对应，
 * openAttempts = ceil(OPEN_POLL_TIMEOUT / OPEN_POLL_INTERVAL) 随 400ms 间隔自动变 20。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { jitter, type DelayRange } from '../src/main/boss/timing.js'
import {
  SCROLL_TOP_SETTLE_DELAY,
  SEGMENT_REPAINT_DELAY,
  BOTTOM_CONFIRM_DELAY,
} from '../src/main/boss/ResumeReader.js'
import {
  OPEN_POLL_TIMEOUT,
  OPEN_POLL_INTERVAL,
  CLOSE_POLL_INTERVAL,
  CARD_PACE_DELAY,
} from '../src/main/boss/ResumeBatchReader.js'

// ---------- jitter：注入随机源（确定性） ----------

test('jitter：注入随机源确定性输出（0→min，1⁻→max，中间值按 floor 映射到 [500,600]）', () => {
  assert.equal(jitter(500, 600, () => 0), 500) // floor(0×101)=0
  assert.equal(jitter(500, 600, () => 0.999), 600) // floor(0.999×101)=100
  assert.equal(jitter(500, 600, () => 0.1), 510) // floor(10.1)=10
  assert.equal(jitter(500, 600, () => 0.25), 525) // floor(25.25)=25
  assert.equal(jitter(500, 600, () => 0.5), 550) // floor(50.5)=50
})

test('jitter：随机源序列逐次消费（同一调用序列映射稳定）', () => {
  const seq = [0, 0.25, 0.75, 0.999]
  const rands = [...seq]
  assert.deepEqual(
    seq.map(() => jitter(500, 600, () => rands.shift()!)),
    [500, 525, 575, 600],
  )
})

test('jitter：min===max 时恒等，任意随机源不越界', () => {
  for (const r of [0, 0.3, 0.7, 0.999999]) {
    assert.equal(jitter(500, 500, () => r), 500)
    assert.equal(jitter(0, 0, () => r), 0)
  }
})

test('jitter：均匀源全区间覆盖——101 个原子区间逐一命中（rand=i/1001 单调步进）', () => {
  const seen = new Set<number>()
  for (let i = 0; i <= 1000; i++) {
    const ms = jitter(500, 600, () => i / 1001)
    seen.add(ms)
    assert.ok(Number.isInteger(ms), `非整数 ${ms}`)
    assert.ok(ms >= 500 && ms <= 600, `越界 ${ms}`)
  }
  // 每个原子区间宽 1001/101≈9.91 个采样步，必然被命中 → 全 101 个值都出现
  assert.equal(seen.size, 101)
})

// ---------- jitter：默认随机源（Math.random，统计边界） ----------

test('jitter：默认随机源大量采样恒为整数、恒在闭区间内', () => {
  for (let i = 0; i < 2000; i++) {
    const ms = jitter(500, 600)
    assert.equal(Number.isInteger(ms), true, `非整数 ${ms}`)
    assert.ok(ms >= 500 && ms <= 600, `${ms} 越界`)
  }
})

// ---------- 常量语义锁定（设计 §3.1/§3.2 定稿值） ----------

test('Phase 1 常量语义：抖动区间 + 定值确认 + 轮询间隔与 openAttempts 换算', () => {
  assert.deepEqual<DelayRange>(SCROLL_TOP_SETTLE_DELAY, { min: 500, max: 600 })
  assert.deepEqual<DelayRange>(SEGMENT_REPAINT_DELAY, { min: 500, max: 600 })
  assert.equal(BOTTOM_CONFIRM_DELAY, 1000) // 到底确认路径保持定值，不做随机化
  assert.deepEqual<DelayRange>(CARD_PACE_DELAY, { min: 500, max: 600 }) // 风控权衡项，可一键调回 2000
  assert.equal(OPEN_POLL_INTERVAL, 400)
  assert.equal(CLOSE_POLL_INTERVAL, 250)
  // 打开轮询总超时 8000 不变 → 次数随间隔自动收紧为 20（与执行处公式一致，循环逻辑不动）
  assert.equal(Math.max(1, Math.ceil(OPEN_POLL_TIMEOUT / OPEN_POLL_INTERVAL)), 20)
})
