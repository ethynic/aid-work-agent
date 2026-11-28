import assert from 'node:assert/strict'
import { test } from 'node:test'
import { busyPercentBetween, createCpuBusyReader } from '../dist/screen/cpuUsage.js'

// 整台电脑的 CPU 占用：os.cpus() 前后两次读数相减，1 - Δidle / Δtotal。设计 4.5 用它判断「资源不足」。
// 读数不可用时必须给出 null，让资源策略只去看编码器的信号，而不是当成 0% 或 100%。

const core = (user, sys, idle) => ({ user, nice: 0, sys, idle, irq: 0 })

test('两次读数相减算出整机忙碌百分比', () => {
  const before = [core(100, 50, 850), core(0, 0, 0)]
  // 第一核 Δtotal 200（1000 → 1200）、Δidle 150；第二核 Δtotal 200、Δidle 50 → Δidle/Δtotal = 200/400
  const after = [core(130, 70, 1000), core(100, 50, 50)]
  assert.equal(busyPercentBetween(before, after), 50)
})

test('边界：完全空闲是 0，完全忙碌是 100', () => {
  const before = [core(100, 100, 100)]
  assert.equal(busyPercentBetween(before, [core(100, 100, 300)]), 0)
  assert.equal(busyPercentBetween(before, [core(300, 100, 100)]), 100)
  // nice 与 irq 的时间也算进总数：Δtotal 400、Δidle 300
  assert.equal(busyPercentBetween([{ user: 0, nice: 0, sys: 0, idle: 100, irq: 0 }], [{ user: 0, nice: 0, sys: 0, idle: 400, irq: 100 }]), 25)
})

test('读数不可用或两次读数没有间隔时返回 null', () => {
  assert.equal(busyPercentBetween(null, [core(1, 1, 1)]), null)
  assert.equal(busyPercentBetween([core(1, 1, 1)], undefined), null)
  assert.equal(busyPercentBetween([], [core(1, 1, 1)]), null)
  assert.equal(busyPercentBetween([core(1, 1, 1)], []), null)
  assert.equal(busyPercentBetween([core(1, 1, 1)], [core(1, 1, 1)]), null, 'Δtotal 为 0')
})

test('读数器：第一次只记基线，之后每次给出与上一次相比的占用', () => {
  // 注入的是 os.cpus() 的返回值
  const cpu = (user, sys, idle) => ({ model: 'test', speed: 0, times: core(user, sys, idle) })
  let reading = [cpu(0, 0, 0)]
  const read = createCpuBusyReader(() => reading)
  assert.equal(read(), null, '没有上一次读数')
  reading = [cpu(100, 0, 300)]
  assert.equal(read(), 25)
  reading = [cpu(100, 0, 900)]
  assert.equal(read(), 0, '和上一次相比')
  assert.equal(read(), null, '读数没变，Δtotal 为 0')
  reading = undefined
  assert.equal(read(), null, 'os.cpus() 拿不到读数')
  reading = []
  assert.equal(read(), null)
  reading = [cpu(400, 0, 900)]
  assert.equal(read(), null, '丢过一次读数之后没有可比的基线')
  reading = [cpu(400, 0, 1600)]
  assert.equal(read(), 0)
})

test('不注入时读真实的 os.cpus()', () => {
  const read = createCpuBusyReader()
  assert.equal(read(), null)
  const busy = read()
  if (busy !== null) assert.ok(busy >= 0 && busy <= 100)
})
