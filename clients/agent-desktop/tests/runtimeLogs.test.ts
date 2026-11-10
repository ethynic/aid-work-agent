import assert from 'node:assert/strict'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, symlinkSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { execFileSync, spawn } from 'node:child_process'
import { once } from 'node:events'
import { randomUUID } from 'node:crypto'
import test from 'node:test'
import { RuntimeLogs, readLogHours, writeLogArchive } from '../electron/runtimeLogs.js'
import { RuntimeStartupFailure, readRuntimeStartupState, resumeRuntimeAtLogin } from '../electron/runtimeStartup.js'
import { RuntimeSupervisor } from '../electron/runtimeSupervisor.js'
import { parseRuntimePowerEvidence, readRuntimePowerEvidence, runtimePowerScript } from '../electron/runtimeDiagnosticEvidence.js'
import { collectRuntimeLogs, recordDesktopDiagnostic } from '@aid/local-tool-host-core/legacy/logDiagnostics'
import { trustedSystemPowerShell } from '@aid/local-tool-host-core/legacy/dpapi'
import { readSafeZip } from '@aid/local-tool-host-core/plugins/package'

const rootFixture = () => mkdtempSync(path.join(tmpdir(), 'aid-log-export-'))
const collector = { collectRuntimeLogs, recordDesktopDiagnostic }
const runtimeHostEntry = fileURLToPath(new URL('../../../agent-tool-runtime/dist/src/managed-entry.js', import.meta.url))
const windowsRuntime = { skip: process.platform !== 'win32' || !existsSync(runtimeHostEntry) ? 'requires Windows and a built agent-tool-runtime' : false }
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

test('a real ZIP samples only the trusted prior home and protected state even when active has no identity', windowsRuntime, async () => {
  const root = rootFixture(), userDataPath = path.join(root, 'app'), prior = path.join(userDataPath, 'rt')
  const metadata = path.join(userDataPath, 'installation-v2'), active = path.join(userDataPath, 'connections', randomUUID())
  const pointer = path.join(metadata, 'current.json'), selected = path.join(root, 'diagnostics.zip')
  const hostEntry = runtimeHostEntry
  const hostModule = (name: string) => import(pathToFileURL(path.join(path.dirname(hostEntry), name)).href)
  try {
    for (const directory of [active, metadata, path.join(prior, 'logs'), path.join(root, 'outside', 'logs')]) mkdirSync(directory, { recursive: true })
    const id = randomUUID(), oldDevice = randomUUID(), token = 'a'.repeat(64), server = 'https://fixture-private-origin.example'
    const originalPointer = JSON.stringify({ version: 2, id, home: active, priorHome: prior, initialized: true })
    writeFileSync(pointer, originalPointer)
    writeFileSync(path.join(prior, 'config.json'), JSON.stringify({ server, device_id: oldDevice }))
    const dpapi = await hostModule('dpapi.js'), registration = await hostModule('cleanRegistration.js')
    writeFileSync(path.join(prior, 'credentials.bin'), await dpapi.dpapiProtect(token))
    const pending = await registration.beginCleanRegistration(metadata, { server: 'http://fixture-private-origin.example', device_name: 'fixture-private-name' }, [])
    await registration.saveCleanRegistration(metadata, { ...pending, home: active })
    const native = path.join(path.dirname(hostEntry), 'native/RuntimeProcessSupervisor.exe')
    const getNative = (operation: string) => JSON.parse(execFileSync(native, [operation], { encoding: 'utf8', timeout: 5000, windowsHide: true }))
    const { bootId } = getNative('boot-id'), { sessionId } = getNative('session-id'), generation = randomUUID()
    const proof = await dpapi.dpapiProtect(JSON.stringify({ generation, home: prior, bootId, sessionId: sessionId + 1, rebootOnly: false }))
    const marker = path.join(prior, 'host-running.json')
    writeFileSync(marker, JSON.stringify({ instance_id: generation, schemaVersion: 2, supervision: proof }))
    writeFileSync(path.join(prior, 'management-operations.json'), JSON.stringify({ stop: { operation: { operation: 'stop', status: 'reconciling',
      message: '旧执行尚未确认停止，请停止原Runtime后重试；不会重发旧任务（代码 11）', error: 'fixture-private-stderr' } } }))
    const now = new Date().toISOString()
    writeFileSync(path.join(prior, 'logs/runtime.old.log'), `${now} [runtime][INFO] [runtime-connection] heartbeat-recovered\n`)
    writeFileSync(path.join(root, 'outside/logs/runtime.log'), `${now} [runtime][INFO] [runtime-connection] claim-ready\n`)
    symlinkSync(path.join(root, 'outside/logs'), path.join(active, 'logs'), 'junction')
    const log = new RuntimeLogs({ ...options(root), installation: { userDataPath, legacyHome: path.join(root, 'legacy'), hostEntry }, chooseSave: async () => selected })
    log.record('runtime-pair-prepare-failed', { protocol: 'http', reason: 'redirect', httpStatus: 301 })
    log.record('runtime-diagnostic-heartbeat-succeeded', { protocol: 'https' })
    await new Promise<void>(resolve => setImmediate(resolve))
    assert.equal((await log.export()).canceled, false)
    const archive = await readSafeZip(readFileSync(selected)), manifest = JSON.parse(archive.get('manifest.json')!.toString())
    const events = archive.get('events.jsonl')!.toString().trim().split('\n').map(line => JSON.parse(line))
    assert.equal(manifest.version, 1)
    assert.equal(manifest.sources.find((source: { location: string; name: string }) => source.location === 'active' && source.name === 'runtime.log').status, 'linked')
    assert.ok(events.some(event => event.location === 'prior' && event.event === 'heartbeat-recovered'))
    assert.ok(!events.some(event => event.event === 'claim-ready'), 'A linked active logs directory must never be read')
    assert.ok(events.some(event => event.event === 'runtime-pair-prepare-failed' && event.httpStatus === 301 && event.protocol === 'http'))
    assert.ok(events.some(event => event.event === 'runtime-diagnostic-heartbeat-succeeded' && event.location === 'desktop'))
    assert.equal(manifest.evidence.pointer, 'read')
    assert.equal(manifest.evidence.active.identityReadable, false)
    assert.equal(manifest.evidence.prior.identityReadable, true)
    assert.equal(manifest.evidence.prior.marker.schemaVersion, 2)
    assert.deepEqual(manifest.evidence.prior.supervision, { status: 'read', bootMatchesMarker: true, sessionMatchesHost: false, rebootOnly: false })
    assert.equal(manifest.evidence.prior.lastStop.reason, 'previous-execution-not-confirmed-stopped')
    assert.equal(manifest.evidence.registration.phase, 'requested')
    assert.equal(manifest.evidence.registration.referencesActiveHome, true)
    assert.ok(Number.isFinite(Date.parse(manifest.evidence.sampledAt)))
    const exported = [...archive.values()].map(file => file.toString()).join('\n')
    for (const secret of ['fixture-private-', root, token, oldDevice, pending.request.registration_id, pending.request.registration_token, proof, bootId, generation]) assert.equal(exported.includes(secret), false)
    assert.equal(readFileSync(pointer, 'utf8'), originalPointer)
    assert.equal((await registration.readCleanRegistration(metadata)).phase, 'requested')
    writeFileSync(marker, JSON.stringify({ instance_id: randomUUID(), schemaVersion: 2, supervision: proof }))
    assert.deepEqual(await (await hostModule('connectionRecoveryResources.js')).readRuntimeMarkerMatch(prior), { status: 'unreadable' })

    writeFileSync(pointer, JSON.stringify({ version: 2, id, home: active, priorHome: path.join(root, 'outside'), initialized: true }))
    assert.ok((await log.read()).sources.filter(source => source.location === 'prior').every(source => source.status === 'unreadable'))
    assert.ok(!(await log.read()).entries.some(event => event.location === 'prior'))
  } finally { rmSync(root, { recursive: true, force: true }) }
})

test('Windows PowerShell 5.1 query projects only known provider/id pairs and UTC timestamps', windowsRuntime, async () => {
  const to = Date.now(), from = to - 3600_000
  const mock = `
function Get-WinEvent($FilterHashtable, $MaxEvents, $ErrorAction) {
  if ($FilterHashtable.ProviderName -eq 'User32') { throw 'fixture-private-system-error' }
  if ($FilterHashtable.ProviderName -eq 'Microsoft-Windows-Kernel-Power') {
    @{ ProviderName=$FilterHashtable.ProviderName; Id=42; TimeCreated=$FilterHashtable.StartTime.AddMinutes(1); Message='fixture-private-original-event' }
    @{ ProviderName='Untrusted'; Id=42; TimeCreated=$FilterHashtable.StartTime.AddMinutes(2) }
    @{ ProviderName=$FilterHashtable.ProviderName; Id=19; TimeCreated=$FilterHashtable.StartTime.AddMinutes(2) }
  }
  if ($FilterHashtable.ProviderName -eq 'Microsoft-Windows-Kernel-Boot') {
    $boot = [pscustomobject]@{ ProviderName=$FilterHashtable.ProviderName; Id=27; TimeCreated=$FilterHashtable.StartTime.AddMinutes(2) }
    $boot | Add-Member ScriptMethod ToXml { '<Event><EventData><Data Name="BootType">0x0</Data><Data Name="Other">fixture-private-raw-xml</Data></EventData></Event>' }
    $boot
  }
}
`
  const encoded = Buffer.from(mock + runtimePowerScript(from, to), 'utf16le').toString('base64')
  const shell = trustedSystemPowerShell()
  const output = execFileSync(shell.executable, ['-NoProfile', '-NonInteractive', '-EncodedCommand', encoded], { cwd: shell.cwd, env: shell.env, encoding: 'utf8', windowsHide: true, timeout: 5000 })
  assert.doesNotMatch(output, /fixture-private-/)
  assert.deepEqual(parseRuntimePowerEvidence(output, from, to), { status: 'partial', truncated: false,
    events: [{ kind: 'kernel-boot', at: new Date(from + 120_000).toISOString(), bootType: 0 }, { kind: 'sleep-entered', at: new Date(from + 60_000).toISOString() }] })
  const real = await readRuntimePowerEvidence(runtimeHostEntry, from, to)
  assert.ok(real.status === 'read' || real.status === 'partial', 'The actual System event query must complete, including a successful empty result')
  assert.ok(real.events.every(event => ['at,kind', 'at,bootType,kind'].includes(Object.keys(event).sort().join(','))))
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
