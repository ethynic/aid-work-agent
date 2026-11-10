import assert from 'node:assert/strict'
import { mkdtempSync, rmSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { RuntimeStartup, runtimeLoginLaunch, resumeRuntimeAtLogin, RUNTIME_LOGIN_ARGUMENT } from '../electron/runtimeStartup.js'

test('login launch only applies to installed Windows Runtime, excluding Desktop, dev and smoke', () => {
  const input = { kind: 'runtime', packaged: true, platform: 'win32', smoke: false, argv: [RUNTIME_LOGIN_ARGUMENT] }
  assert.equal(runtimeLoginLaunch(input), true)
  for (const change of [{ kind: 'desktop' }, { packaged: false }, { platform: 'darwin' }, { smoke: true }, { argv: [] }]) assert.equal(runtimeLoginLaunch({ ...input, ...change }), false)
})

test('first launch enables login once; subsequent launches preserve Windows/user disable choice', () => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'aid-startup-test-'))
  try {
    const calls: unknown[] = []
    let enabled = false
    const login = {
      getLoginItemSettings(options: unknown) { calls.push(options); return { openAtLogin: enabled, executableWillLaunchAtLogin: enabled } },
      setLoginItemSettings(options: { openAtLogin: boolean }) { calls.push(options); enabled = options.openAtLogin },
    }
    const startup = new RuntimeStartup(login, root, 'C:\\Program Files\\AID Work Runtime\\runtime.exe', 'test-runtime')
    startup.initialize()
    assert.equal(startup.enabled(), true)
    startup.setEnabled(false)
    startup.initialize()
    assert.equal(startup.enabled(), false)
    assert.equal(calls.length, 4)
    assert.deepEqual(calls[0], { path: 'C:\\Program Files\\AID Work Runtime\\runtime.exe', args: [RUNTIME_LOGIN_ARGUMENT], name: 'test-runtime', openAtLogin: true, enabled: true })
  } finally { rmSync(root, { recursive: true, force: true }) }
})

test('OS disabled startup is reported accurately', () => {
  const startup = new RuntimeStartup({ getLoginItemSettings: () => ({ openAtLogin: true, executableWillLaunchAtLogin: false }), setLoginItemSettings() {} }, '', 'C:\\runtime.exe', 'runtime')
  assert.equal(startup.enabled(), false)
})

test('Windows launchItems uses the exact product name when Electron omits argument matching', () => {
  let items = [{ name: 'runtime', enabled: true }, { name: 'other-product', enabled: true }]
  const startup = new RuntimeStartup({ getLoginItemSettings: () => ({ openAtLogin: false, executableWillLaunchAtLogin: true, launchItems: items }), setLoginItemSettings() {} }, '', 'C:\\runtime.exe', 'runtime')
  assert.equal(startup.enabled(), true)
  items[0]!.enabled = false
  assert.equal(startup.enabled(), false)
  items = [{ name: 'other-product', enabled: true }]
  assert.equal(startup.enabled(), false)
})

test('paired stopped host is started once through the management port, with fresh dedup key', async () => {
  const calls: any[] = []
  await resumeRuntimeAtLogin(async request => { calls.push(request); return { code: 0, result: calls.length === 1 ? { state: 'stopped', connection: 'offline', device: { device_id: 'device' } } : { operation_id: 'op', status: calls.length === 2 ? 'running' : 'succeeded', code: 0 } } })
  assert.deepEqual(calls.map(call => call.method), ['getState', 'start', 'operations.get'])
  assert.ok(calls[1].params.request_key)
  assert.notEqual(calls[0].request_id, calls[1].request_id)
})

test('unpaired or already running hosts never start or perform business actions', async () => {
  for (const result of [{ state: 'stopped', connection: 'unpaired' }, { state: 'running', device: { device_id: 'device' } }, { state: 'draining', device: { device_id: 'device' } }]) {
    const calls: any[] = []
    await resumeRuntimeAtLogin(async request => { calls.push(request); return { code: 0, result } })
    assert.deepEqual(calls.map(call => call.method), ['getState'])
  }
})

test('recovery and revoked states surface attention without starting', async () => {
  for (const result of [{ state: 'reconciling' }, { state: 'blocked' }, { state: 'stopped', connection: 'revoked' }]) await assert.rejects(resumeRuntimeAtLogin(async () => ({ code: 0, result })), /NeedsAttention/)
})

test('conflict or uncertain start is not retried', async () => {
  let calls = 0
  await assert.rejects(resumeRuntimeAtLogin(async () => { calls++; return calls === 1 ? { code: 0, result: { state: 'stopped', device: { device_id: 'device' } } } : { code: 11, result: null } }), /StartRejected/)
  assert.equal(calls, 2)
  await assert.rejects(resumeRuntimeAtLogin(async () => ({ code: 7, result: null })), /StateRejected/)
})

test('accepted start failure or timeout surfaces without resubmitting start', async () => {
  for (const status of ['failed', 'running']) {
    const methods: string[] = []
    await assert.rejects(resumeRuntimeAtLogin(async value => {
      const request = value as { method: string }
      methods.push(request.method)
      return { code: 0, result: request.method === 'getState' ? { state: 'stopped', device: { device_id: 'device' } } : { operation_id: 'op', status: request.method === 'start' ? 'running' : status, code: status === 'failed' ? 11 : 0 } }
    }, async () => {}), /StartFailed|StartUnconfirmed/)
    assert.equal(methods.filter(method => method === 'start').length, 1)
  }
})

test('startup diagnostics distinguish state read, old execution and accepted start failures without keeping the request error', async () => {
  const stopped = { state: 'stopped', device: { device_id: 'diagnostic-test-device' } }
  const scenarios = [
    { replies: [new Error('fixture-sensitive-transport-detail')], expected: 'runtime-login-state-read-failed', methods: ['getState'] },
    { replies: [{ code: 4, result: null }], expected: 'runtime-login-state-read-failed', methods: ['getState'] },
    { replies: [{ code: 0, result: { state: 'reconciling' } }], expected: 'runtime-login-stop-proof-pending', methods: ['getState'] },
    { replies: [{ code: 0, result: { state: 'blocked' } }], expected: 'runtime-login-host-blocked', methods: ['getState'] },
    { replies: [{ code: 0, result: { state: 'stopped', connection: 'revoked' } }], expected: 'runtime-login-device-revoked', methods: ['getState'] },
    { replies: [{ code: 0, result: stopped }, { code: 11, result: null }], expected: 'runtime-login-start-request-failed', methods: ['getState', 'start'] },
    { replies: [{ code: 0, result: stopped }, { code: 0, result: { operation_id: 'op' } }, { code: 0, result: { status: 'reconciling' } }], expected: 'runtime-login-start-confirmation-failed', methods: ['getState', 'start', 'operations.get'] },
  ]
  for (const scenario of scenarios) {
    const calls: string[] = []
    await assert.rejects(resumeRuntimeAtLogin(async value => {
      const reply = scenario.replies[calls.length]!
      calls.push((value as { method: string }).method)
      if (reply instanceof Error) throw reply
      return reply
    }), (error: Error & { diagnosticEvent?: string }) => {
      assert.equal(error.diagnosticEvent, scenario.expected)
      assert.doesNotMatch(String(error), /fixture-sensitive/)
      return true
    })
    assert.deepEqual(calls, scenario.methods)
  }
})
