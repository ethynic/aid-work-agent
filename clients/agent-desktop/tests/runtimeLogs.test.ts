import assert from 'node:assert/strict'
import { mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, symlinkSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import { RuntimeLogs, readLogHours, writeLogArchive } from '../electron/runtimeLogs.js'
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
