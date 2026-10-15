/**
 * 1.10 轻量登录探测原语单测（全 mock runner，零 PowerShell/企微依赖）。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { probeLoginState, type LocaldbLoginState } from '../src/platform/localdb/login.js'
import type { PowerShellScriptOptions } from '../src/platform/powershell.js'

type Runner = (opts: PowerShellScriptOptions) => Promise<Record<string, unknown>>

/** 固定返回 payload 的 runner 替身 */
function runnerReturning(payload: Record<string, unknown>): Runner {
  return async () => payload
}

test('probeLoginState：三态驱动返回逐一归一（online / need_login / offline）', async () => {
  for (const expected of ['online', 'need_login', 'offline'] as const) {
    const result = await probeLoginState({ runDriverFn: runnerReturning({ login_state: expected }) })
    assert.equal(result.login_state, expected)
    assert.equal(result.source, 'localdb-login-driver')
  }
})

test('probeLoginState：驱动抛错 → unknown，不抛出', async () => {
  const runner: Runner = async () => {
    throw new Error('driver boom')
  }
  const result = await probeLoginState({ runDriverFn: runner })
  assert.equal(result.login_state, 'unknown')
  assert.equal(result.source, 'localdb-login-driver')
})

test('probeLoginState：驱动返回非法 login_state → unknown（防御性归一）', async () => {
  const result = await probeLoginState({ runDriverFn: runnerReturning({ login_state: 'hacked' }) })
  assert.equal(result.login_state, 'unknown')
})

test('probeLoginState：驱动返回空对象 / 非字符串 → unknown', async () => {
  assert.equal((await probeLoginState({ runDriverFn: runnerReturning({}) })).login_state, 'unknown')
  assert.equal((await probeLoginState({ runDriverFn: runnerReturning({ login_state: 123 }) })).login_state, 'unknown')
})

test('probeLoginState：嵌套 data.login_state 形态也容忍（runner 直返完整 DRIVER_JSON 时不断链）', async () => {
  const result = await probeLoginState({
    runDriverFn: runnerReturning({ ok: true, data: { login_state: 'need_login' } }),
  })
  assert.equal(result.login_state, 'need_login')
})

test('probeLoginState：默认 driver 路径指向包根 drivers/ps1/localdb-login.ps1，且不透传 args', async () => {
  const seen: PowerShellScriptOptions[] = []
  const runner: Runner = async (opts) => {
    seen.push(opts)
    return { login_state: 'online' as LocaldbLoginState }
  }
  await probeLoginState({ runDriverFn: runner })
  assert.equal(seen.length, 1)
  // Windows 分隔符归一后比对（dist 运行态路径为反斜杠）
  const normalized = seen[0]!.script.replace(/\\/g, '/')
  assert.ok(normalized.endsWith('drivers/ps1/localdb-login.ps1'), `实际 script=${seen[0]!.script}`)
})

test('probeLoginState：仅在线且无窗口歧义的有效 PID 可用于直接账号查询', async () => {
  const good = await probeLoginState({ runDriverFn: runnerReturning({ login_state: 'online', window_pid: 42 }) })
  assert.equal(good.window_pid, 42)
  for (const payload of [
    { login_state: 'online', window_pid: '42' },
    { login_state: 'offline', window_pid: 42 },
    { login_state: 'online', window_pid: 42, window_error: 'WINDOW_AMBIGUOUS' },
  ]) {
    assert.equal((await probeLoginState({ runDriverFn: runnerReturning(payload) })).window_pid, null)
  }
})
