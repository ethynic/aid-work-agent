import test from 'node:test'
import assert from 'node:assert/strict'
import { runDoctorChecks } from '../src/cli/commands/doctor.js'
import { mkdtempSync, rmSync } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'

test('doctor keeps legacy environment success while absent business app is not ready', async () => {
  const home = mkdtempSync(join(tmpdir(), 'wc-doctor-readiness-'))
  try {
    const report = await runDoctorChecks({ localAppData: home, env: { platform: 'win32', desktopProbeFn: async () => 'interactive', execFileFn: async (file) => ({ stdout: file === 'where.exe' ? 'C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe' : '' }) } })
    assert.equal(report.success, true)
    assert.equal(report.runtime_readiness.ready, false)
    assert.match(report.runtime_readiness.reason!, /启动企业微信/)
  } finally { rmSync(home, { recursive: true, force: true }) }
})

test('a running app never overrides a noninteractive desktop gate', async () => {
  const home = mkdtempSync(join(tmpdir(), 'wc-doctor-services-'))
  try {
    const report = await runDoctorChecks({ localAppData: home, env: { platform: 'win32', desktopProbeFn: async () => 'session-inactive', execFileFn: async (file) => ({ stdout: file === 'where.exe' ? 'powershell.exe' : '"WXWork.exe","123"' }) } })
    assert.equal(report.success, false)
    assert.equal(report.runtime_readiness.ready, false)
    assert.match(report.runtime_readiness.reason!, /会话未连接到交互桌面/)
  } finally { rmSync(home, { recursive: true, force: true }) }
})

test('doctor uses the Windows input desktop when SESSIONNAME is absent or conflicts', async () => {
  const home = mkdtempSync(join(tmpdir(), 'wc-doctor-desktop-'))
  const original = process.env.SESSIONNAME
  try {
    for (const [sessionName, desktop, expected] of [
      [undefined, 'interactive', true],
      ['Console', 'input-unavailable', false],
    ] as const) {
      if (sessionName === undefined) delete process.env.SESSIONNAME
      else process.env.SESSIONNAME = sessionName
      let probeCalls = 0
      const report = await runDoctorChecks({
        localAppData: home,
        env: {
          platform: 'win32',
          execFileFn: async (file, args, timeoutMs) => {
            if (file === 'where.exe') return { stdout: 'powershell.exe' }
            if (file === 'tasklist') return { stdout: '"WXWork.exe","123"' }
            assert.equal(file, 'powershell.exe')
            assert.equal(args[2], '-EncodedCommand')
            assert.equal(timeoutMs, 2500, 'Runtime 等待整个只读诊断的上限为 5 秒')
            probeCalls++
            return { stdout: desktop }
          },
        },
      })
      assert.equal(probeCalls, 1)
      assert.equal(report.runtime_readiness.ready, expected)
      if (!expected) assert.match(report.runtime_readiness.reason!, /输入桌面不可用/)
    }
  } finally {
    if (original === undefined) delete process.env.SESSIONNAME
    else process.env.SESSIONNAME = original
    rmSync(home, { recursive: true, force: true })
  }
})

test('doctor gives a distinct reason when the Windows desktop probe fails to run', async () => {
  const home = mkdtempSync(join(tmpdir(), 'wc-doctor-desktop-failed-'))
  try {
    const report = await runDoctorChecks({
      localAppData: home,
      env: {
        platform: 'win32',
        execFileFn: async (file) => {
          if (file === 'where.exe') return { stdout: 'powershell.exe' }
          if (file === 'tasklist') return { stdout: '"WXWork.exe","123"' }
          throw new Error('mock timeout')
        },
      },
    })
    assert.equal(report.success, false)
    assert.equal(report.runtime_readiness.ready, false)
    assert.match(report.runtime_readiness.reason!, /无法检测 Windows 交互桌面/)
  } finally { rmSync(home, { recursive: true, force: true }) }
})
