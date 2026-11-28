import assert from 'node:assert/strict'
import { test } from 'node:test'
import { CPU_BUSY_THRESHOLD_PERCENT, RESOURCE_BUSY_STREAK, ResourcePolicy } from '../dist/screen/resourcePolicy.js'

// 任务优先于观看（设计 4.5）。每 5 秒一个采样：
//   有任务在跑，且连续两次资源不足（电脑 CPU 占用达到阈值，或编码器报因 CPU 受限）→ 降低画质，继续观看；
//   降低之后又连续两次资源不足 → 停止观看（resource_busy）；
//   任务结束后才恢复；任务还在跑但资源不再紧张时不恢复；没有任务在跑时不做任何动作。

const sample = (taskRunning, machineCpuBusy, encoderCpuLimited = false) => ({ taskRunning, machineCpuBusy, encoderCpuLimited })
const busy = () => sample(true, 95)
const calm = () => sample(true, 20)

test('阈值与连续次数是命名常量', () => {
  assert.equal(CPU_BUSY_THRESHOLD_PERCENT, 90)
  assert.equal(RESOURCE_BUSY_STREAK, 2)
})

test('连续两次资源不足才降低画质，且只降一次', () => {
  const policy = new ResourcePolicy()
  assert.equal(policy.sample(busy()), 'none')
  assert.equal(policy.sample(busy()), 'degrade')
  assert.equal(policy.sample(busy()), 'none', '已经降低过，不再重复发')
  assert.equal(policy.degraded, true)
})

test('中途出现一次资源不再不足，连续计数清零', () => {
  const policy = new ResourcePolicy()
  policy.sample(busy())
  assert.equal(policy.sample(calm()), 'none')
  assert.equal(policy.sample(busy()), 'none', '计数从上一次重新开始')
  assert.equal(policy.sample(busy()), 'degrade')
})

test('降低之后又连续两次资源不足才停止观看', () => {
  const policy = new ResourcePolicy()
  policy.sample(busy()); policy.sample(busy())
  assert.equal(policy.sample(busy()), 'none', '降低后的第一次只计数')
  assert.equal(policy.sample(busy()), 'stop')
})

test('降低之后中途一次资源不再不足，停止的计数也清零', () => {
  const policy = new ResourcePolicy()
  policy.sample(busy()); policy.sample(busy())
  policy.sample(busy()); policy.sample(calm())
  assert.equal(policy.sample(busy()), 'none')
  assert.equal(policy.sample(busy()), 'stop')
})

test('任务结束后恢复；任务还在跑但资源不再不足时不恢复', () => {
  const policy = new ResourcePolicy()
  policy.sample(busy()); policy.sample(busy())
  assert.equal(policy.sample(calm()), 'none', '任务还在跑，保持降低画质')
  assert.equal(policy.degraded, true)
  assert.equal(policy.sample(sample(false, 95)), 'restore')
  assert.equal(policy.degraded, false)
  assert.equal(policy.sample(sample(false, 95)), 'none', '恢复只发一次')
})

test('恢复之后任务再次开始，再按同样的步骤走一遍', () => {
  const policy = new ResourcePolicy()
  assert.deepEqual([policy.sample(busy()), policy.sample(busy()), policy.sample(sample(false, 10)), policy.sample(busy()), policy.sample(busy()), policy.sample(busy()), policy.sample(busy())],
    ['none', 'degrade', 'restore', 'none', 'degrade', 'none', 'stop'])
})

test('没有任务在跑时不产生任何动作，电脑再忙也一样', () => {
  const policy = new ResourcePolicy()
  for (let i = 0; i < 6; i++) assert.equal(policy.sample(sample(false, 100, true)), 'none')
  assert.equal(policy.degraded, false)
})

test('CPU 占用的阈值是 90%：达到算不足，差一点不算', () => {
  const policy = new ResourcePolicy()
  assert.equal(policy.sample(sample(true, 89.9)), 'none')
  assert.equal(policy.sample(sample(true, 90)), 'none')
  assert.equal(policy.sample(sample(true, 90)), 'degrade')
})

test('CPU 读数不可用时只看编码器的信号', () => {
  const policy = new ResourcePolicy()
  assert.equal(policy.sample(sample(true, null)), 'none', '读数缺失且编码器没报受限：不算资源不足')
  assert.equal(policy.sample(sample(true, null, true)), 'none')
  assert.equal(policy.sample(sample(true, null, true)), 'degrade')
  assert.equal(policy.sample(sample(true, null, true)), 'none')
  assert.equal(policy.sample(sample(true, null, true)), 'stop')
})
