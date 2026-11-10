import assert from 'node:assert/strict'
import { mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, symlinkSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawn } from 'node:child_process'
import { once } from 'node:events'
import test from 'node:test'
import { RuntimeLogs, readLogHours, writeLogArchive } from '../electron/runtimeLogs.js'
import { RuntimeStartupFailure, readRuntimeStartupState, resumeRuntimeAtLogin } from '../electron/runtimeStartup.js'
import { RuntimeSupervisor } from '../electron/runtimeSupervisor.js'
import { collectRuntimeLogs, recordDesktopDiagnostic } from '@aid/local-tool-host-core/legacy/logDiagnostics'
import { readSafeZip } from '@aid/local-tool-host-core/plugins/package'

const rootFixture = () => mkdtempSync(path.join(tmpdir(), 'aid-log-export-'))
const collector = { collectRuntimeLogs, recordDesktopDiagnostic }
function options(root: string) {
  return { appVersion: '0.0.33', hostEntry: null, desktopDirectory: path.join(root, 'desktop'), home: () => path.join(root, 'home'), appDataPath: root,
    profile: { profile: 'acceptance' as const, userDataDir: 'app', legacyHomeDir: 'legacy' },
    chooseSave: async (_defaultPath: string): Promise<string | null> => null, loadCollector: async () => collector }
}

test('logs read/export have no Host, pairing or network dependency, and cancel never creates an export', async () => {
  const root = rootFixture()
  try {
    const log = new RuntimeLogs(options(root))
    const snapshot = await log.read()
    assert.equal(snapshot.hours, 24)
    assert.equal(snapshot.sources.find(source => source.name === 'runtime.log')?.status, 'missing')
    assert.deepEqual(await log.export({ hours: 1 }), { canceled: true })
    assert.throws(() => readLogHours({ hours: 24, path: 'arbitrary' }))
    assert.throws(() => readLogHours({ hours: 12 }))
    assert.equal(readLogHours({ hours: 168 }), 168)
    const broken = new RuntimeLogs({ ...options(root), loadCollector: async () => { throw new Error('fixture missing resources') } })
    assert.equal((await broken.read()).sources[0]?.status, 'unreadable', 'Missing packaged collector is a partial diagnostic result rather than a startup gate')
  } finally { rmSync(root, { recursive: true, force: true }) }
})

test('saving returns only a filename, writes the bounded projected snapshot, and excludes raw logs', async () => {
  const root = rootFixture(), selected = path.join(root, '诊断.zip'), home = path.join(root, 'home')
  try {
    mkdirSync(path.join(home, 'logs'), { recursive: true })
    const secret = 'fixture-business-message-and-password'
    writeFileSync(path.join(home, 'logs/runtime.log'), `${new Date().toISOString()} [runtime][ERROR] ${secret}\n`)
    const log = new RuntimeLogs({ ...options(root), chooseSave: async () => selected })
    log.record('startup-failed')
    await new Promise<void>(resolve => setImmediate(resolve))
    assert.deepEqual(await log.export(), { canceled: false, fileName: '诊断.zip' })
    const bytes = readFileSync(selected)
    assert.equal(bytes.readUInt32LE(0), 0x04034b50)
    const contents = await readSafeZip(bytes)
    assert.deepEqual([...contents.keys()].sort(), ['events.jsonl', 'manifest.json', 'summary.txt'])
    const exported = [...contents.values()].map(value => value.toString('utf8')).join('\n')
    assert.equal(exported.includes(secret), false)
    assert.equal(exported.includes(home), false)
    const manifest = JSON.parse(contents.get('manifest.json')!.toString())
    assert.deepEqual(manifest.versions, { app: '0.0.33', host: null, core: null }, 'The exported support bundle includes installed version facts and explicit unknowns')
    assert.equal(manifest.sources.find((source: { name: string }) => source.name === 'runtime.log').skippedLines, 1)
    assert.equal(JSON.parse(contents.get('events.jsonl')!.toString().trim()).event, 'startup-failed')
    await assert.rejects(writeLogArchive(path.join(root, 'missing/export.zip'), await log.read()), 'Saving to an unavailable destination must fail visibly')
  } finally { rmSync(root, { recursive: true, force: true }) }
})

test('the production adapter reads its packaged Core collector without starting the Host entry', async () => {
  const root = rootFixture(), host = path.join(root, 'host'), coreParent = path.join(host, 'node_modules/@aid')
  try {
    mkdirSync(coreParent, { recursive: true })
    const core = path.resolve(path.dirname(realpathSync(fileURLToPath(import.meta.resolve('@aid/local-tool-host-core/legacy/logDiagnostics')))), '../..')
    symlinkSync(core, path.join(coreParent, 'local-tool-host-core'), 'junction')
    const entry = path.join(host, 'managed-entry.js')
    writeFileSync(entry, 'throw new Error("Host must not be started by log viewing")')
    writeFileSync(path.join(host, 'package.json'), JSON.stringify({ version: '0.2.25' }))
    const log = new RuntimeLogs({ ...options(root), hostEntry: entry, loadCollector: undefined })
    log.record('runtime-host-initialization-failed')
    const snapshot = await log.read()
    assert.equal(snapshot.entries[0]?.event, 'runtime-host-initialization-failed')
    assert.deepEqual(snapshot.versions, { app: '0.0.33', host: '0.2.25', core: JSON.parse(readFileSync(path.join(core, 'package.json'), 'utf8')).version })
    assert.ok(snapshot.sources.every(source => source.name !== 'diagnostic-collector'))
  } finally { rmSync(root, { recursive: true, force: true }) }
})

test('a single export distinguishes handshake, state read, pending stop and start failures', async () => {
  const root = rootFixture(), selected = path.join(root, 'startup-diagnostics.zip')
  try {
    const log = new RuntimeLogs({ ...options(root), chooseSave: async () => selected })
    const scenarios = [
      { failure: 'describe', event: 'runtime-login-handshake-failed' },
      { failure: 'getState', event: 'runtime-login-state-read-failed' },
      { failure: 'reconciling', event: 'runtime-login-stop-proof-pending' },
      { failure: 'start', event: 'runtime-login-start-request-failed' },
      { failure: 'operations.get', event: 'runtime-login-start-confirmation-failed' },
    ]
    for (const scenario of scenarios) {
      const entry = path.join(root, 'fixture.cjs')
      writeFileSync(entry, `
const failure = ${JSON.stringify(scenario.failure)};
process.on('message', request => {
  const response = { request_id: request.request_id, method: request.method };
  if (request.method === failure) {
    process.send({ ...response, code: 4, error: 'fixture-private-host-error', result: null });
    return;
  }
  const result = {
    describe: { api_major: 1, instance_id: 'fixture-private-instance' },
    getState: failure === 'reconciling' ? { state: 'reconciling' } : { state: 'stopped', device: { device_id: 'fixture-private-device' } },
    start: { operation_id: 'fixture-private-operation' },
  }[request.method];
  if (!result || (failure === 'reconciling' && request.method === 'start')) process.exit(2);
  process.send({ ...response, code: 0, error: '', result });
});
process.on('disconnect', () => process.exit(0));
`)
      let child: ReturnType<typeof spawn> | undefined
      let exited: Promise<unknown[]> | undefined
      const supervisor = new RuntimeSupervisor({
        nodeExecutable: process.execPath, hostEntry: entry, home: root, supervisor: 'runtime_app',
        requestTimeoutMs: 5_000, takeSelectedPackage: async () => { throw new Error('unused selection') },
        onEvent() {}, onDisconnect() {},
        spawnChild: (...args) => { child = spawn(...args); exited = once(child, 'exit'); return child },
      })
      const request = (value: unknown) => supervisor.request(value, 'startup')
      try {
        await assert.rejects(async () => {
          await readRuntimeStartupState(request)
          await resumeRuntimeAtLogin(request)
        }, (error: unknown) => {
          assert.ok(error instanceof RuntimeStartupFailure)
          log.record(error.diagnosticEvent)
          return true
        })
      } finally {
        if (child?.connected) child.disconnect()
        assert.deepEqual(await exited, [0, null], 'The Host must not attempt another operation after the startup failure')
      }
    }
    assert.equal((await log.export()).canceled, false)
    const contents = await readSafeZip(readFileSync(selected))
    const events = contents.get('events.jsonl')!.toString()
    assert.deepEqual(events.trim().split('\n').map(line => JSON.parse(line).event), scenarios.map(scenario => scenario.event))
    assert.doesNotMatch(events, /fixture-private/)
  } finally { rmSync(root, { recursive: true, force: true }) }
})
