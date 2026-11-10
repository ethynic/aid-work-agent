import assert from 'node:assert/strict'
import { mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { randomUUID } from 'node:crypto'
import test from 'node:test'
import { collectRuntimeLogs, projectRuntimeLog, recordDesktopDiagnostic } from '../dist/legacy/logDiagnostics.js'
import { resolveRuntimeHome } from '../dist/legacy/diagnostics.js'

test('exports fixed startup failure stages alongside historical generic failures without adding details', () => {
  const root = mkdtempSync(path.join(tmpdir(), 'aid-log-startup-stages-'))
  try {
    const now = Date.now()
    const events = ['runtime-login-resume-failed', 'runtime-login-handshake-failed', 'runtime-login-state-read-failed',
      'runtime-login-stop-proof-pending', 'runtime-login-host-blocked', 'runtime-login-device-revoked',
      'runtime-login-start-request-failed', 'runtime-login-start-confirmation-failed', 'runtime-login-start-unconfirmed']
    events.forEach((event, index) => assert.equal(recordDesktopDiagnostic(root, event, now - events.length + index), true, event))
    assert.equal(recordDesktopDiagnostic(root, 'runtime-login-state-read-failed:fixture-private-value', now), false)
    const snapshot = collectRuntimeLogs({ desktopDirectory: root, now })
    assert.deepEqual(snapshot.entries.map(entry => entry.event), events)
    assert.ok(snapshot.entries.every(entry => entry.level === 'error'))
    assert.doesNotMatch(JSON.stringify(snapshot), /fixture-private-value/)
  } finally { rmSync(root, { recursive: true, force: true }) }
})
const now = Date.parse('2026-11-02T08:00:00.000Z'), at = '2026-11-02T07:00:00.000Z', invocation = '02ecfd46-75ae-452d-b239-c6b69f9ee7eb'
const fixture = () => mkdtempSync(path.join(tmpdir(), 'aid-log-view-'))
const profile = { profile: 'acceptance', userDataDir: 'runtime-app', legacyHomeDir: 'legacy-runtime' }
const line = message => `${at} [runtime][INFO] ${message}\n`

test('an empty active home preserves coverage and safe connection events from the trusted prior home', () => {
  const root = fixture(), home = path.join(root, 'active'), priorHome = path.join(root, 'prior')
  try {
    mkdirSync(path.join(priorHome, 'logs'), { recursive: true })
    writeFileSync(path.join(priorHome, 'logs/runtime.log'), line('[runtime-connection] heartbeat-disconnected')
      + line('心跳失败（fixture-private-connection-error），1000ms 后重试') + line('[runtime-connection] heartbeat-recovered'))
    const previous = '2026-11-01T06:00:00.000Z'
    writeFileSync(path.join(priorHome, 'logs/runtime.log.old.log'), `${previous} [runtime][INFO] [runtime-connection] claim-ready\n`)
    const snapshot = collectRuntimeLogs({ home, priorHome, desktopDirectory: root, now, hours: 24 })
    assert.deepEqual(snapshot.entries.map(event => [event.location, event.event]), [
      ['prior', 'heartbeat-disconnected'], ['prior', 'heartbeat-recovered'],
    ])
    assert.equal(snapshot.sources.find(source => source.location === 'active' && source.name === 'runtime.log').status, 'missing')
    const rolled = snapshot.sources.find(source => source.location === 'prior' && source.name === 'runtime.log.old.log')
    assert.equal(rolled.status, 'read'); assert.equal(rolled.observedTo, previous); assert.equal(rolled.stale, true)
    assert.doesNotMatch(JSON.stringify(snapshot), /fixture-private-connection-error/)
  } finally { rmSync(root, { recursive: true, force: true }) }
})

test('legacy log projection preserves correlation but never exports arbitrary stderr, business text, paths or credentials', () => {
  const root = fixture(), home = path.join(root, 'home'), desktop = path.join(root, 'desktop')
  try {
    mkdirSync(path.join(home, 'logs'), { recursive: true })
    const secret = 'fixture-private-chat-content', token = 'fixture-short-password'
    writeFileSync(path.join(home, 'logs/runtime.log'), line(`invocation ${invocation} 已开始执行 tool=wecom_read_session`) + line(`Provider 已启动 pid=1 entry=C:/personal/${token}`) + `${at} [wecom] ${secret}\n` + line(`error ${token}`))
    writeFileSync(path.join(home, 'logs/log-health.json'), JSON.stringify({ fileWriteFailures: 2, lastFileWriteErrorAt: at, lastFileWriteError: `C:/private/${secret}`, degraded: false }))
    recordDesktopDiagnostic(desktop, 'startup-failed', now)
    const snapshot = collectRuntimeLogs({ home, desktopDirectory: desktop, now })
    assert.equal(snapshot.entries.length, 2)
    assert.equal(snapshot.entries[0].invocationId, invocation, 'Retain the original safe opaque invocation ID for support correlation')
    assert.equal(snapshot.sources.find(source => source.name === 'runtime.log').skippedLines, 3)
    assert.equal(snapshot.health.fileWriteFailures, 2)
    const exported = JSON.stringify(snapshot)
    for (const excluded of [secret, token, home, 'C:/private', '"lastFileWriteError":']) assert.equal(exported.includes(excluded), false)
    assert.equal(snapshot.sources.find(source => source.name === 'runtime.log.old.log').status, 'missing')
    assert.equal(recordDesktopDiagnostic(desktop, secret, now), false, 'New shell records must use a fixed event vocabulary')
    const failure = projectRuntimeLog(line(`invocation ${invocation} 执行器异常: ${secret} ${token}`).trim())
    assert.equal(failure.event, 'invocation-failed')
    assert.equal(JSON.stringify(failure).includes(secret), false, 'Support can see the failure category while arbitrary exception text remains private')
  } finally { rmSync(root, { recursive: true, force: true }) }
})

test('time filtering, live partial tails and bounded snapshots report their actual limits', () => {
  const root = fixture(), home = path.join(root, 'home')
  try {
    mkdirSync(path.join(home, 'logs'), { recursive: true })
    const event = line(`invocation ${invocation} 已开始执行 tool=wecom_read_session`)
    writeFileSync(path.join(home, 'logs/runtime.log'), 'untrusted free text\n'.repeat(40_000) + event.repeat(510) + 'unfinished live line')
    const snapshot = collectRuntimeLogs({ home, desktopDirectory: path.join(root, 'desktop'), now, hours: 24 })
    assert.equal(snapshot.entries.length, 500)
    const source = snapshot.sources.find(source => source.name === 'runtime.log')
    assert.equal(source.truncated, true); assert.equal(source.bytesRead, 512 * 1024); assert.equal(source.partialLines, 2)
    assert.equal(snapshot.truncated, true)
    assert.equal(collectRuntimeLogs({ home, desktopDirectory: root, now: now + 2 * 3600_000, hours: 1 }).entries.length, 0)
    assert.equal(projectRuntimeLog(line(`[runtime-perf] invocation=${invocation} tool=wecom_read_session provider=wecom started_ms=1 lock_wait_ms=2 lock_prep_ms=3 provider_call_ms=45`).trim()).durationMs, 45)
  } finally { rmSync(root, { recursive: true, force: true }) }
})

test('current rt and connections homes plus old generation pointers resolve read-only without mixing another connection', () => {
  const root = fixture(), userData = path.join(root, profile.userDataDir), pointer = path.join(userData, 'installation-v2/current.json')
  try {
    mkdirSync(path.dirname(pointer), { recursive: true })
    for (const home of [path.join(userData, 'rt'), path.join(userData, 'connections', randomUUID()), path.join(userData, 'installation-v2/generations', randomUUID(), 'connections', randomUUID())]) {
      mkdirSync(home, { recursive: true })
      const original = JSON.stringify({ version: 2, home })
      writeFileSync(pointer, original)
      assert.equal(resolveRuntimeHome(root, profile).home, home)
      collectRuntimeLogs({ desktopDirectory: path.join(root, 'desktop'), appDataPath: root, profile, now })
      assert.equal(readFileSync(pointer, 'utf8'), original, 'Viewing logs must not initialize an installation or rewrite its pointer')
    }
    const active = path.join(root, 'known-active'); mkdirSync(path.join(active, 'logs'), { recursive: true })
    writeFileSync(path.join(active, 'logs/runtime.log'), line(`invocation ${invocation} 已开始执行 tool=boss_filter`))
    assert.equal(collectRuntimeLogs({ home: active, desktopDirectory: root, appDataPath: root, profile, now }).entries[0].tool, 'boss_filter')
  } finally { rmSync(root, { recursive: true, force: true }) }
})

test('a linked source is skipped, other available diagnostics still render, and shell retention rotates only its own files', () => {
  const root = fixture(), home = path.join(root, 'home'), outside = path.join(root, 'outside'), desktop = path.join(root, 'desktop')
  try {
    mkdirSync(home); mkdirSync(outside)
    writeFileSync(path.join(outside, 'runtime.log'), line(`invocation ${invocation} 已开始执行 tool=boss_filter`))
    symlinkSync(outside, path.join(home, 'logs'), 'junction')
    recordDesktopDiagnostic(desktop, 'startup-failed', now)
    const snapshot = collectRuntimeLogs({ home, desktopDirectory: desktop, now })
    assert.equal(snapshot.sources.find(source => source.name === 'runtime.log').status, 'linked')
    assert.equal(snapshot.entries.length, 1)
    writeFileSync(path.join(desktop, 'desktop-diagnostics.jsonl'), 'x'.repeat(1024 * 1024))
    recordDesktopDiagnostic(desktop, 'api-configuration-loaded', now)
    assert.equal(readFileSync(path.join(desktop, 'desktop-diagnostics.old.jsonl')).length, 1024 * 1024)
    assert.ok(readFileSync(path.join(desktop, 'desktop-diagnostics.jsonl'), 'utf8').includes('api-configuration-loaded'))
    assert.equal(readFileSync(path.join(outside, 'runtime.log'), 'utf8').includes('boss_filter'), true, 'Retention must not delete business or external source files')
  } finally { rmSync(root, { recursive: true, force: true }) }
})
