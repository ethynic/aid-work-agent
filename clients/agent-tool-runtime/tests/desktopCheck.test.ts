/**
 * 用例 9：desktopCheck——可注入 mock；写动作锁屏拒绝（DESKTOP_NOT_INTERACTIVE），只读放行。
 * 真实 Windows 下 doctor 路径手测留 M0.7。
 */
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { startTestStack } from './helpers/runtimeStack.js'
import { checkDesktopInteractive } from '../src/desktopCheck.js'

test('desktopCheck mock：写动作在锁屏时拒绝执行，回 DESKTOP_NOT_INTERACTIVE retryable=true', async () => {
  const stack = await startTestStack({ desktopCheck: async () => false })
  try {
    const id = stack.cloud.enqueueInvocation('boss_greet', { durationMs: 100 })
    await stack.cloud.waitFor(
      () => stack.cloud.callsFor(id).some((c) => c.type === 'result'),
      10_000,
      'desktop reject result',
    )
    const calls = stack.cloud.callsFor(id)
    const result = calls.find((c) => c.type === 'result')!
    assert.equal(result.payload['success'], false)
    assert.equal(result.payload['code'], 'DESKTOP_NOT_INTERACTIVE')
    assert.equal(result.payload['retryable'], true)
    assert.equal(result.payload['effect'], 'none')
    assert.ok(!calls.some((c) => c.type === 'progress'), '锁屏拒绝不得产生执行进度')
  } finally {
    await stack.stop()
  }
})

test('desktopCheck mock：只读工具不受锁屏检测拦截', async () => {
  const stack = await startTestStack({ desktopCheck: async () => false })
  try {
    const id = stack.cloud.enqueueInvocation('boss_goto', { durationMs: 100 })
    await stack.cloud.waitFor(() => stack.cloud.getInvocation(id)?.state === 'succeeded', 10_000, 'read-only success')
  } finally {
    await stack.stop()
  }
})

test('desktopCheck 真实实现：返回结构合法（win32 实际调用，不断言交互状态）', { skip: process.platform !== 'win32' }, async () => {
  const result = await checkDesktopInteractive()
  assert.equal(typeof result.interactive, 'boolean')
  if (!result.interactive) {
    assert.ok(result.error === undefined || typeof result.error === 'string')
  }
})
