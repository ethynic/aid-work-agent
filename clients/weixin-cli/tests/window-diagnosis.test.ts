import assert from 'node:assert/strict'
import test from 'node:test'
import { diagnoseWeixinRuntime } from '../src/platform/runtimeDiagnosis.js'
import { createWeixinProbeOperation } from '../src/operations/probe.js'
import { runDoctorChecks } from '../src/cli/commands/doctor.js'
import { CodedOperationError } from '../src/operations/types.js'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const environment = {
  platform_ok: true, platform: 'win32', interactive_session: true, session_name: 'Console',
  powershell_available: true, powershell_path: 'powershell.exe', weixin_running: true, weixin_processes: 1,
}

test('runtime diagnosis distinguishes successful environment checks from a usable native session', async () => {
  for (const state of ['foreground', 'background', 'minimized', 'hidden', 'ambiguous', 'no_main_window'] as const) {
    const result = await diagnoseWeixinRuntime({
      probeEnvironmentFn: async () => environment,
      runDriverFn: async (options) => {
        assert.equal(options.useVisionProxy, false)
        assert.equal(options.args, undefined)
        assert.ok(options.timeoutMs! <= 3000)
        return { window_state: state, login_state: state === 'foreground' || state === 'background' ? 'online' : 'unknown', restored: false }
      },
    })
    assert.equal(result.environment.weixin_running, true)
    assert.equal(result.window_state, state)
    assert.equal(result.runtime_readiness.ready, state === 'foreground' || state === 'background')
  }
})

test('probe and doctor share native readiness and explicit restore failures retain the concrete window state', async () => {
  const stateDir = await mkdtemp(join(tmpdir(), 'wx-window-doctor-'))
  const runDriver = async (options: { args?: string[] }) => ({
    window_state: 'hidden', login_state: 'unknown', restored: false,
    ...(options.args?.includes('-RestoreWindow') ? { error_code: 'CONTENT_UNAVAILABLE' } : {}),
  })
  try {
    const probe = await createWeixinProbeOperation({ probeEnvironmentFn: async () => environment, runDriverFn: runDriver })
      .execute({}, { signal: new AbortController().signal, progress() {} })
    const env = { platform: 'win32' as const, sessionName: 'Console',
      execFileFn: async (file: string) => ({ stdout: file === 'where.exe' ? 'powershell.exe' : '"Weixin.exe","123"' }) }
    const report = await runDoctorChecks({ env, localAppData: stateDir, runDriverFn: runDriver })
    assert.equal(probe.success, true)
    assert.equal(report.success, true)
    assert.equal(report.window_state, probe.data.window_state)
    assert.equal(report.login_state, probe.data.login_state)
    assert.deepEqual(report.runtime_readiness, probe.data.runtime_readiness)
    const restore = await runDoctorChecks({ env, localAppData: stateDir, runDriverFn: runDriver, restoreWindow: true })
    assert.equal(restore.success, false)
    assert.equal(restore.window_state, 'hidden')
    assert.deepEqual(restore.restore, { restored: false, error_code: 'CONTENT_UNAVAILABLE' })
  } finally { await rm(stateDir, { recursive: true, force: true }) }
})

test('a timeout stays an explicit unreadiness result while native cancellation retains CANCELLED semantics', async () => {
  const ac = new AbortController()
  const timedOut = await diagnoseWeixinRuntime({
    probeEnvironmentFn: async () => environment,
    runDriverFn: async () => { throw new CodedOperationError('RESULT_TIMEOUT', 'timeout') },
  })
  assert.equal(timedOut.runtime_readiness.ready, false)
  assert.match(timedOut.guidance, /超时/)
  const cancelled = await createWeixinProbeOperation({
    probeEnvironmentFn: async () => environment,
    runDriverFn: async () => { ac.abort(); throw Error('cancelled child') },
  }).execute({}, { signal: ac.signal, progress() {} })
  assert.equal(cancelled.code, 'CANCELLED')
  assert.equal(cancelled.effect, 'none')
})

test('unsupported or offline prerequisites never start a native UI driver and failed diagnostics stay explicit', async () => {
  for (const fields of [{ platform_ok: false }, { interactive_session: false }, { powershell_available: false }, { weixin_running: false }]) {
    const result = await diagnoseWeixinRuntime({
      probeEnvironmentFn: async () => ({ ...environment, ...fields }),
      runDriverFn: async () => assert.fail('a native probe cannot run without its prerequisites'),
    })
    assert.equal(result.runtime_readiness.ready, false)
  }
  const result = await diagnoseWeixinRuntime({
    probeEnvironmentFn: async () => environment,
    runDriverFn: async () => { throw Error('private diagnostic details') },
  })
  assert.equal(result.window_state, 'unavailable')
  assert.equal(result.login_state, 'unknown')
  assert.doesNotMatch(JSON.stringify(result), /private diagnostic/)
})
