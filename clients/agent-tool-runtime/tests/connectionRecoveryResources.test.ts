import assert from 'node:assert/strict'
import childProcess, { type ChildProcess } from 'node:child_process'
import { EventEmitter } from 'node:events'
import fs, { mkdtempSync, readFileSync, writeFileSync, mkdirSync, rmSync } from 'node:fs'
import { syncBuiltinESMExports } from 'node:module'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { PassThrough } from 'node:stream'
import { randomUUID } from 'node:crypto'
import test, { type TestContext } from 'node:test'
import type { RuntimeHost } from '@aid/local-tool-host-core'
import { inspectConnectionRecovery } from '../src/connectionRecoveryInspection.js'
import { checkConnectionRecoveryResources as check, registerConnectionRecoveryReboot as register,
  recordFreshConnectionEpoch as fresh } from '../src/connectionRecoveryResources.js'

const windows = { skip: process.platform !== 'win32' }
const seal = (text: string) => Buffer.from(`fixture-seal:${text}`).toString('base64')
function fixture(t: TestContext) {
  const home = mkdtempSync(join(tmpdir(), 'connection-resources-'))
  const originalStat = fs.lstatSync
  const state = { boot: randomUUID(), session: 7, legacy: 0, protection: true, supervisor: true, cliAvailable: true,
    commands: [] as string[][], jobs: new Map<string, number>(), failStop: false, cimChecks: 0 }
  t.mock.method(fs, 'lstatSync', ((path: fs.PathLike, ...args: unknown[]) => {
    if (String(path).endsWith('native\\RuntimeProcessSupervisor.exe') || String(path).endsWith('native/RuntimeProcessSupervisor.exe')) {
      if (state.supervisor) return originalStat(process.execPath)
      const failure = Object.assign(new Error('fixture missing'), { code: 'ENOENT' }); throw failure
    }
    return Reflect.apply(originalStat, fs, [path, ...args])
  }) as typeof fs.lstatSync)
  t.mock.method(childProcess, 'execFile', ((file: string, args: string[], _options: unknown,
    callback: (error: Error | null, stdout: string, stderr: string) => void) => {
    const child = new EventEmitter() as ChildProcess
    child.stdin = new PassThrough(); child.kill = () => true
    if (file.endsWith('RuntimeProcessSupervisor.exe')) {
      state.commands.push([...args])
      queueMicrotask(() => {
        if (args[0] === 'boot-id') callback(null, JSON.stringify({ bootId: state.boot }), '')
        else if (args[0] === 'session-id') callback(null, JSON.stringify({ sessionId: state.session }), '')
        else {
          const name = args[2]!
          if (args[0] === 'terminate' && state.failStop) { callback(new Error('fixture native failure'), '', ''); return }
          if (args[0] === 'terminate') state.jobs.set(name, 0)
          callback(null, JSON.stringify({ exists: state.jobs.has(name), activeProcesses: state.jobs.get(name) ?? 0 }), '')
        }
      })
    } else if (args.includes('-EncodedCommand')) {
      state.cimChecks++
      queueMicrotask(() => state.cliAvailable ? callback(null, String(state.legacy), '') : callback(new Error('fixture CIM failure'), '', ''))
    } else {
      let input = ''; child.stdin.on('data', chunk => { input += chunk.toString() })
      child.stdin.on('finish', () => {
        if (!state.protection) { callback(new Error('fixture DPAPI failure'), '', ''); return }
        const script = args.at(-1)!
        if (script.includes(']::Protect(')) callback(null, seal(Buffer.from(input, 'base64').toString('utf8')), '')
        else {
          const decoded = Buffer.from(input, 'base64').toString('utf8')
          if (!decoded.startsWith('fixture-seal:')) callback(new Error('fixture protected blob invalid'), '', '')
          else callback(null, Buffer.from(decoded.slice(13)).toString('base64'), '')
        }
      })
    }
    return child
  }) as typeof childProcess.execFile)
  syncBuiltinESMExports()
  t.after(() => { t.mock.restoreAll(); syncBuiltinESMExports(); rmSync(home, { recursive: true, force: true }) })
  const file = (name: string, value: string) => writeFileSync(join(home, name), value)
  const metadata = () => { file('config.json', '{"device_id":"fixture"}'); file('credentials.bin', 'unchanged-fixture-credential') }
  const ledger = (options: { boot?: string; session?: number; generation?: string; job?: string; marker?: boolean; python?: boolean } = {}) => {
    const generation = options.generation ?? randomUUID(); const job = options.job ?? `AidWorkRuntime.${randomUUID()}`
    const bootId = options.boot ?? state.boot; const sessionId = options.session ?? state.session
    mkdirSync(join(home, 'process-supervision'), { recursive: true })
    file(`process-supervision/${generation}.bin`, seal(JSON.stringify({ schemaVersion: 1, home, generation, bootId, sessionId, ownerPid: 12345, jobs: [job] })))
    if (options.marker !== false) file('host-running.json', JSON.stringify({ instance_id: generation, schemaVersion: 2,
      supervision: seal(JSON.stringify({ generation, home, bootId, sessionId, rebootOnly: options.python ?? false })) }))
    return { job, generation }
  }
  const run = (prepareReboot = false) => check(home, process.execPath, { prepareReboot })
  return { home, state, file, metadata, ledger, run }
}

function readonlyHost(ownsExecution = false): RuntimeHost {
  return { getState: () => ({ state: 'blocked', blocked_reason: '管理密钥缺失，当前只读管理' }),
    inspectConnectionRecovery: (inspect: (owns: boolean) => Promise<unknown>) => inspect(ownsExecution) } as unknown as RuntimeHost
}

test('connection instructions do not scan old execution resources even when native inspection is unavailable', windows, async t => {
  const f = fixture(t); f.metadata(); f.state.supervisor = false
  for (let i = 0; i < 32; i++) f.ledger({ marker: false })
  const checked = await inspectConnectionRecovery(readonlyHost(), f.home, process.execPath, 'diagnose')
  assert.equal(checked.diagnosis.category, 'CONFIG_INVALID')
  assert.equal(checked.diagnosis.can_reconfigure, true)
  assert.equal(checked.resources_safe, false)
  assert.equal(checked.identity_digest, null)
  assert.equal(f.state.commands.length, 0); assert.equal(f.state.cimChecks, 0)
  assert.equal(fs.existsSync(join(f.home, 'connection-recovery-witness.bin')), false)
})

test('explicit new-device preparation still proves resources and refuses unknown execution history', windows, async t => {
  const f = fixture(t); f.metadata(); f.ledger({ marker: false })
  const original = readFileSync(join(f.home, 'credentials.bin'))
  const checked = await inspectConnectionRecovery(readonlyHost(), f.home, process.execPath, 'prepare_new')
  assert.equal(checked.resources_safe, false)
  assert.equal(checked.diagnosis.requires_reboot, true)
  assert.equal(checked.diagnosis.category, 'CONNECTION_RESOURCE_REBOOT_PREPARED')
  assert.ok(f.state.commands.length > 0); assert.ok(f.state.cimChecks > 0)
  assert.deepEqual(readFileSync(join(f.home, 'credentials.bin')), original)
})

test('fresh empty home is safe and diagnostics write no recovery control files', windows, async t => {
  const f = fixture(t)
  assert.deepEqual(await f.run(), { safe: true, needs_reboot: false, category: 'CONNECTION_RESOURCE_NEW_HOME' })
  assert.deepEqual(fs.readdirSync(f.home), [])
})

test('identity and empty journal without a marker require a registered genuine reboot', windows, async t => {
  const f = fixture(t); f.metadata(); mkdirSync(join(f.home, 'journal'))
  const credential = readFileSync(join(f.home, 'credentials.bin'))
  assert.equal((await f.run()).safe, false)
  assert.equal(fs.existsSync(join(f.home, 'connection-recovery-witness.bin')), false)
  assert.equal((await f.run(true)).category, 'CONNECTION_RESOURCE_REBOOT_PREPARED')
  assert.equal((await f.run()).safe, false)
  f.state.boot = randomUUID()
  assert.deepEqual(await f.run(), { safe: true, needs_reboot: false, category: 'CONNECTION_RESOURCE_REBOOT_CONFIRMED' })
  assert.deepEqual(readFileSync(join(f.home, 'credentials.bin')), credential)
})

test('same-boot live job diagnostics only query; explicit action terminates only registered jobs', windows, async t => {
  const f = fixture(t); f.metadata(); const own = f.ledger(); const outside = `AidWorkRuntime.${randomUUID()}`
  f.state.jobs.set(own.job, 2); f.state.jobs.set(outside, 3)
  f.file('identity-reconciliation.bin', 'opaque-fence-fixture'); const before = readFileSync(join(f.home, 'host-running.json'))
  assert.equal((await f.run()).category, 'CONNECTION_RESOURCE_ACTIVE')
  assert.equal(f.state.commands.some(args => args[0] === 'terminate'), false)
  assert.equal((await f.run(true)).safe, true)
  assert.deepEqual(f.state.commands.filter(args => args[0] === 'terminate').map(args => args[2]), [own.job])
  assert.equal(f.state.jobs.get(outside), 3)
  assert.deepEqual(readFileSync(join(f.home, 'host-running.json')), before)
  assert.equal(readFileSync(join(f.home, 'identity-reconciliation.bin'), 'utf8'), 'opaque-fence-fixture')
})

test('all protected ledgers are examined, including an unmarked generation', windows, async t => {
  const f = fixture(t); const first = f.ledger(); const second = f.ledger({ marker: false })
  f.state.jobs.set(first.job, 0); f.state.jobs.set(second.job, 1)
  assert.equal((await f.run()).safe, false)
  assert.ok(f.state.commands.some(args => args[0] === 'probe' && args[2] === second.job))
  assert.equal(f.state.commands.some(args => args[0] === 'terminate'), false)
})

test('missing marker is not proved by an otherwise valid ledger', windows, async t => {
  const f = fixture(t); f.metadata(); f.ledger({ marker: false })
  assert.equal((await f.run()).category, 'CONNECTION_RESOURCE_REBOOT_REQUIRED')
})

test('foreign-session current-boot evidence never uses a current-session absent job as proof', windows, async t => {
  const f = fixture(t); const scoped = f.ledger({ session: 99 })
  assert.equal((await f.run()).safe, false)
  assert.equal(f.state.commands.some(args => args[2] === scoped.job), false)
})

test('kernel boot GUID casing is not a reboot; reboot-only Python requires a real reboot', windows, async t => {
  const f = fixture(t); f.ledger({ boot: f.state.boot.toUpperCase(), python: true })
  assert.equal((await f.run()).safe, false)
  await f.run(true); f.state.boot = randomUUID()
  assert.equal((await f.run()).safe, true)
})

test('corrupt/foreign-user ledger remains unchanged and has a protected reboot exit', windows, async t => {
  const f = fixture(t); f.metadata(); const named = f.ledger(); const file = join(f.home, 'process-supervision', `${named.generation}.bin`)
  writeFileSync(file, 'AAAA'); const before = readFileSync(file)
  assert.equal((await f.run()).safe, false)
  assert.equal((await f.run(true)).needs_reboot, true)
  f.state.boot = randomUUID()
  assert.equal((await f.run()).safe, true)
  assert.deepEqual(readFileSync(file), before)
})

test('ledger home/generation mismatch cannot grant job cleanup authority', windows, async t => {
  const f = fixture(t); const named = f.ledger()
  f.file(`process-supervision/${named.generation}.bin`, seal(JSON.stringify({ schemaVersion: 1, home: join(f.home, 'other'),
    generation: named.generation, bootId: f.state.boot, sessionId: f.state.session, ownerPid: 9, jobs: [named.job] })))
  assert.equal((await f.run()).safe, false)
  assert.equal(f.state.commands.some(args => args[2] === named.job), false)
})

test('owned-stop-failure registration queries no CLI or jobs and never declares safe', windows, async t => {
  const f = fixture(t); const named = f.ledger(); f.state.jobs.set(named.job, 4)
  assert.deepEqual(await register(f.home, process.execPath), { safe: false, needs_reboot: true, category: 'CONNECTION_RESOURCE_REBOOT_PREPARED' })
  assert.ok(f.state.commands.every(args => ['boot-id', 'session-id'].includes(args[0]!)))
  const bytes = readFileSync(join(f.home, 'connection-recovery-witness.bin'))
  await register(f.home, process.execPath)
  assert.deepEqual(readFileSync(join(f.home, 'connection-recovery-witness.bin')), bytes)
  assert.equal(f.state.jobs.get(named.job), 4)
})

test('post-registration ledger changes require registering again, not pretending the reboot proved new data', windows, async t => {
  const f = fixture(t); f.metadata(); await register(f.home, process.execPath)
  f.ledger({ marker: false }); f.state.boot = randomUUID()
  assert.deepEqual(await f.run(), { safe: false, needs_reboot: true, category: 'CONNECTION_RESOURCE_EVIDENCE_CHANGED' })
  await f.run(true); assert.equal((await f.run()).safe, false)
  f.state.boot = randomUUID(); assert.equal((await f.run()).safe, true)
})

test('tampered witness is not proof and explicit registration repairs only that control file', windows, async t => {
  const f = fixture(t); f.metadata(); f.file('connection-recovery-witness.bin', 'AAAA')
  assert.equal((await f.run()).category, 'CONNECTION_RESOURCE_WITNESS_INVALID')
  assert.equal((await f.run(true)).category, 'CONNECTION_RESOURCE_REBOOT_PREPARED')
  f.state.boot = randomUUID(); assert.equal((await f.run()).safe, true)
})

test('old CLI blocks safety before and after a registered reboot', windows, async t => {
  const f = fixture(t); f.metadata(); f.state.legacy = 1
  assert.equal((await f.run()).category, 'CONNECTION_RESOURCE_LEGACY_ACTIVE')
  await f.run(true); f.state.boot = randomUUID(); assert.equal((await f.run()).safe, false)
  f.state.legacy = 0; assert.equal((await f.run()).safe, true)
})

test('DPAPI, kernel helper and CLI-check failures are fixed categories without fixture paths', windows, async t => {
  const f = fixture(t)
  f.state.protection = false
  assert.equal((await f.run()).category, 'CONNECTION_RESOURCE_PROTECTION_UNAVAILABLE')
  f.state.protection = true; f.state.supervisor = false
  assert.equal((await f.run()).category, 'CONNECTION_RESOURCE_SUPERVISOR_UNAVAILABLE')
  f.state.supervisor = true; f.state.cliAvailable = false
  const outcome = await f.run(); assert.equal(outcome.category, 'CONNECTION_RESOURCE_LEGACY_CHECK_UNAVAILABLE')
  assert.equal(JSON.stringify(outcome).includes(f.home), false)
})

test('failed job termination records a reboot exit without claiming resource stop', windows, async t => {
  const f = fixture(t); f.ledger(); f.state.failStop = true
  assert.deepEqual(await f.run(true), { safe: false, needs_reboot: true, category: 'CONNECTION_RESOURCE_REBOOT_PREPARED' })
})

test('a fresh epoch avoids repeated reboot after pairing but never absorbs execution history', windows, async t => {
  const f = fixture(t); await fresh(f.home, process.execPath)
  f.metadata(); f.file('management-operations.json', '{"pair":{"operation":{"operation":"pair"}}}')
  assert.equal((await f.run()).category, 'CONNECTION_RESOURCE_FRESH_EPOCH')
  mkdirSync(join(f.home, 'journal'))
  assert.equal((await f.run()).safe, false)
  await assert.rejects(fresh(f.home, process.execPath), { message: 'CONNECTION_RESOURCE_FRESH_EPOCH_NOT_EMPTY' })
})

test('fresh epoch cannot bless a deleted execution ledger, foreign home or another session', windows, async t => {
  const f = fixture(t); await fresh(f.home, process.execPath); f.metadata()
  f.file('management-operations.json', '{"start":{"operation":{"operation":"start"}}}')
  assert.equal((await f.run()).safe, false)
  f.file('management-operations.json', '{}'); f.state.session++
  assert.equal((await f.run()).safe, false)
  f.file('connection-recovery-epoch.bin', seal(JSON.stringify({ schemaVersion: 1, home: join(f.home, 'foreign'), bootId: f.state.boot, sessionId: f.state.session })))
  assert.equal((await f.run()).category, 'CONNECTION_RESOURCE_FRESH_EPOCH_INVALID')
})

test('no registrar overwrites an existing identity to manufacture a fresh epoch', windows, async t => {
  const f = fixture(t); f.metadata()
  await assert.rejects(fresh(f.home, process.execPath), { message: 'CONNECTION_RESOURCE_FRESH_EPOCH_NOT_EMPTY' })
  assert.equal(fs.existsSync(join(f.home, 'connection-recovery-epoch.bin')), false)
})

test('transaction anchor and retired management keys do not invalidate resource proof or a fresh epoch', windows, async t => {
  const f = fixture(t)
  f.file('.connection-recovery-anchor.bin', 'isolated-anchor-control')
  f.file('management-retired-request-keys.bin', 'isolated-retired-control')
  await fresh(f.home, process.execPath); f.metadata()
  assert.equal((await f.run()).category, 'CONNECTION_RESOURCE_FRESH_EPOCH')
  await register(f.home, process.execPath)
  f.file('.connection-recovery-anchor.bin', 'changed-isolated-anchor-control')
  f.file('management-retired-request-keys.bin', 'changed-isolated-retired-control')
  f.state.boot = randomUUID()
  assert.equal((await f.run()).category, 'CONNECTION_RESOURCE_REBOOT_CONFIRMED')
})

test('business identity fence is never treated as fresh recovery metadata', windows, async t => {
  const f = fixture(t); await fresh(f.home, process.execPath)
  f.file('identity-reconciliation.bin', 'unchanged-opaque-business-fence')
  assert.equal((await f.run()).safe, false)
  assert.equal(readFileSync(join(f.home, 'identity-reconciliation.bin'), 'utf8'), 'unchanged-opaque-business-fence')
})

test('writing the business fence does not change a bound reboot digest, while marker bytes remain active', windows, async t => {
  const f = fixture(t); f.metadata(); f.ledger({ python: true })
  const marker = readFileSync(join(f.home, 'host-running.json'))
  await register(f.home, process.execPath)
  f.file('identity-reconciliation.bin', 'business-fence-written-after-preflight')
  f.state.boot = randomUUID()
  assert.equal((await f.run()).category, 'CONNECTION_RESOURCE_REBOOT_CONFIRMED')
  assert.deepEqual(readFileSync(join(f.home, 'host-running.json')), marker)
  assert.equal(readFileSync(join(f.home, 'identity-reconciliation.bin'), 'utf8'), 'business-fence-written-after-preflight')
})

test('an authenticated empty transaction epoch can resume after reboot or session change, never after identity creation', windows, async t => {
  const f = fixture(t); f.file('.connection-recovery-anchor.bin', 'verified-transaction-fixture')
  await fresh(f.home, process.execPath)
  const first = readFileSync(join(f.home, 'connection-recovery-epoch.bin'))
  f.state.boot = randomUUID(); f.state.session++
  await fresh(f.home, process.execPath)
  assert.equal(readFileSync(join(f.home, 'connection-recovery-epoch.bin')).equals(first), false)
  assert.equal((await f.run()).category, 'CONNECTION_RESOURCE_FRESH_EPOCH')
  f.metadata(); f.state.boot = randomUUID()
  await assert.rejects(fresh(f.home, process.execPath), { message: 'CONNECTION_RESOURCE_FRESH_EPOCH_NOT_EMPTY' })
})
