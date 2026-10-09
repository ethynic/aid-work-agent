import test from 'node:test'
import assert from 'node:assert/strict'
import { runDoctorChecks } from '../src/cli/commands/doctor.js'
import { mkdtempSync, rmSync } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'

test('doctor keeps legacy environment success while absent business app is not ready', async () => {
  const home = mkdtempSync(join(tmpdir(), 'wc-doctor-readiness-'))
  try {
    const report = await runDoctorChecks({ localAppData: home, env: { platform: 'win32', sessionName: 'Console', execFileFn: async (file) => ({ stdout: file === 'where.exe' ? 'C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe' : '' }) } })
    assert.equal(report.success, true)
    assert.equal(report.runtime_readiness.ready, false)
    assert.match(report.runtime_readiness.reason!, /启动企业微信/)
  } finally { rmSync(home, { recursive: true, force: true }) }
})

test('a running app never overrides a noninteractive desktop gate', async () => {
  const home = mkdtempSync(join(tmpdir(), 'wc-doctor-services-'))
  try {
    const report = await runDoctorChecks({ localAppData: home, env: { platform: 'win32', sessionName: 'Services', execFileFn: async (file) => ({ stdout: file === 'where.exe' ? 'powershell.exe' : '"WXWork.exe","123"' }) } })
    assert.equal(report.success, false)
    assert.equal(report.runtime_readiness.ready, false)
    assert.match(report.runtime_readiness.reason!, /桌面运行条件/)
  } finally { rmSync(home, { recursive: true, force: true }) }
})
