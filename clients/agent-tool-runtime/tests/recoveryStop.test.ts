/** Actual DPAPI/Windows Jobs, always task-owned dummy processes and temp home. */
import assert from 'node:assert/strict'
import test, { type TestContext } from 'node:test'
import { execFile, spawn } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import { mkdtempSync, readFileSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { promisify } from 'node:util'
import { ProcessSupervision } from '../src/processSupervision.js'
import { dpapiProtect, dpapiUnprotect } from '../src/dpapi.js'
import { ProviderSet } from '../src/providerManager.js'

const windows = { skip: process.platform !== 'win32', timeout: 120000 }
async function fixture(t: TestContext, business: boolean, legacy = false) {
  const home = mkdtempSync(join(tmpdir(), 'aid-stop-managed-'))
  let child: ReturnType<typeof spawn> | undefined
  let terminate: (() => Promise<unknown>) | undefined
  t.after(async () => {
    await terminate?.().catch(() => {})
    child?.kill()
    if (child && child.exitCode === null) await new Promise(resolve => { child!.once('exit', resolve); setTimeout(resolve, 2000).unref() })
    rmSync(home, { recursive: true, force: true })
  })
  writeFileSync(join(home, 'config.json'), JSON.stringify({ server: 'https://isolated.invalid', skills: { python: 'configured-but-not-running' }, sessionTasks: true }))
  const supervisor = new ProcessSupervision(home, process.execPath), generation = randomUUID()
  await supervisor.marker(generation)
  if (business) await supervisor.noteBusinessExecution()
  const entry = join(home, 'dummy.mjs')
  writeFileSync(entry, `import {spawn} from 'node:child_process'; spawn(process.execPath,['-e','setInterval(()=>{},1000)'],{stdio:'ignore',windowsHide:true}); setInterval(()=>{},1000)`)
  const launch = await supervisor.prepareLaunch(entry)
  child = spawn(launch.command, launch.args, { stdio: 'ignore', windowsHide: true })
  const path = join(home, 'process-supervision', `${generation}.bin`)
  const ledger = JSON.parse(await dpapiUnprotect(readFileSync(path, 'utf8')))
  const job = ledger.jobs[0]
  const command = async (action: string) => JSON.parse((await promisify(execFile)(supervisor.executable, [action, '--job-name', job], { windowsHide: true })).stdout)
  terminate = () => command('terminate')
  let active = 0
  for (let n = 0; n < 80 && active < 2; n++) {
    active = (await command('probe')).activeProcesses
    if (active < 2) await new Promise(resolve => setTimeout(resolve, 50))
  }
  assert.ok(active >= 2, 'a real task-owned dummy plus descendant must be in the Job')
  if (legacy) { delete ledger.business_started; writeFileSync(path, await dpapiProtect(JSON.stringify(ledger))) }
  return { home, supervisor, command, path, ledger }
}

test('same-generation ordinary business boundary is stopped without reboot or per-invocation generation', windows, async t => {
  const { supervisor, command } = await fixture(t, true)
  const proof = await supervisor.stopManagedResources()
  assert.equal(proof.stop_evidence, 'supervised_device_boundary')
  assert.equal(proof.resource_scope, 'managed_provider_pool')
  assert.equal((await command('probe')).activeProcesses, 0)
})

test('existing legacy protected Job ledger covers a result-lost call without adding invocation metadata', windows, async t => {
  const { supervisor, command } = await fixture(t, false, true)
  assert.equal((await supervisor.stopManagedResources()).stop_evidence, 'supervised_device_boundary')
  assert.equal((await command('probe')).activeProcesses, 0)
})

test('new-home pure probe Jobs cannot manufacture historical coverage, even after reopening Runtime', windows, async t => {
  const { supervisor, home, command } = await fixture(t, false)
  await assert.rejects(supervisor.stopManagedResources(), /NO_EXECUTION_EVIDENCE/)
  const reopened = new ProcessSupervision(home, process.execPath)
  await reopened.marker(randomUUID())
  await assert.rejects(reopened.stopManagedResources(), /NO_EXECUTION_EVIDENCE/)
  assert.ok((await command('probe')).activeProcesses >= 2)
})

test('business flag without any actual managed Job cannot sign stop', windows, async t => {
  const home = mkdtempSync(join(tmpdir(), 'aid-stop-empty-'))
  t.after(() => rmSync(home, { recursive: true, force: true }))
  writeFileSync(join(home, 'config.json'), '{}')
  const supervisor = new ProcessSupervision(home, process.execPath)
  await supervisor.marker(randomUUID()); await supervisor.noteBusinessExecution()
  await assert.rejects(supervisor.stopManagedResources(), /NO_EXECUTION_EVIDENCE/)
})

for (const [field, value, code] of [['business_started', 'true', 'EVIDENCE_INVALID'], ['sessionId', 999999, 'OTHER_SESSION']] as const) {
  test(`protected malformed or other-session ledger is not treated as stopped: ${field}`, windows, async t => {
    const { supervisor, command, ledger, path } = await fixture(t, true)
    writeFileSync(path, await dpapiProtect(JSON.stringify({ ...ledger, [field]: value })))
    await assert.rejects(supervisor.stopManagedResources(), new RegExp(code))
    assert.ok((await command('probe')).activeProcesses >= 2)
  })
}

test('actual busy call rejects idle-pool recovery; an already obtained manager cannot spawn after shutdown', async () => {
  let release!: () => void, entered!: () => void
  const gate = new Promise<void>(resolve => { release = resolve })
  const ready = new Promise<void>(resolve => { entered = resolve })
  const providers = new ProviderSet({ 'boss-recruiting': 'never-launched.mjs' }, { prepareLaunch: async () => {
    entered(); await gate; return { command: process.execPath, args: ['never-launched.mjs'] }
  } })
  const old = providers.get('boss-recruiting')
  const running = old.callTool('boss_goto', {}).catch(error => error as Error)
  await ready
  let stopped = false
  await assert.rejects(providers.recoverIdlePool(async () => { stopped = true }), /BUSY/)
  assert.equal(stopped, false)
  await old.shutdown(); release()
  assert.ok((await running) instanceof Error)
  assert.equal(old.childPid, null)
  await providers.recoverIdlePool(async () => true)
  assert.notEqual(providers.get('boss-recruiting'), old)
  await assert.rejects(old.ensureStarted(), /关闭|shutting/)
  await providers.shutdownAll()
})


test('temporary recovery fence never reopens permanent Runtime shutdown admission', async () => {
  const providers = new ProviderSet({ 'boss-recruiting': 'not-started.mjs' })
  await providers.recoverIdlePool(async () => {
    providers.stopAdmission()
    assert.throws(() => providers.get('boss-recruiting'))
  })
  assert.throws(() => providers.get('boss-recruiting'))
})

test('failed recovery restores only temporary admission, allowing a fresh idle manager', async () => {
  const providers = new ProviderSet({ 'boss-recruiting': 'not-started.mjs' })
  const original = providers.get('boss-recruiting')
  await assert.rejects(providers.recoverIdlePool(async () => { throw new Error('unproved') }), /unproved/)
  assert.notEqual(providers.get('boss-recruiting'), original)
  await providers.shutdownAll()
})


test('business coverage is persisted once per generation and failed writes never leave a false in-memory success', windows, async t => {
  const home = mkdtempSync(join(tmpdir(), 'aid-business-bit-'))
  t.after(() => rmSync(home, { recursive: true, force: true }))
  writeFileSync(join(home, 'config.json'), '{}')
  const supervisor = new ProcessSupervision(home, process.execPath), generation = randomUUID()
  await supervisor.marker(generation)
  const boundary = supervisor as unknown as { write(value: unknown): Promise<void> }
  const persist = boundary.write.bind(boundary)
  let calls = 0
  t.mock.method(boundary, 'write', async (value: unknown) => {
    if (++calls === 1) throw new Error('fixture persistence unavailable')
    await persist(value)
  })
  await assert.rejects(supervisor.noteBusinessExecution(), /persistence/)
  await supervisor.noteBusinessExecution()
  await supervisor.noteBusinessExecution()
  await supervisor.noteBusinessExecution()
  assert.equal(calls, 2, 'only failed first attempt and successful retry write; later calls do not invoke DPAPI')
  const persisted = JSON.parse(await dpapiUnprotect(readFileSync(join(home, 'process-supervision', `${generation}.bin`), 'utf8')))
  assert.equal(persisted.business_started, true)
})


test('shutdown during an actual stdio MCP connect cannot resurrect the old manager', { timeout: 15000 }, async t => {
  const home = mkdtempSync(join(tmpdir(), 'aid-provider-connect-stop-'))
  const entry = join(home, 'wait-initialize.mjs')
  writeFileSync(entry, 'process.stdin.resume(); setInterval(()=>{},1000)')
  const providers = new ProviderSet({ 'boss-recruiting': entry }, { nodeExecutable: process.execPath })
  t.after(async () => { await providers.shutdownAll(); rmSync(home, { recursive: true, force: true }) })
  const manager = providers.get('boss-recruiting')
  const starting = manager.ensureStarted().catch(error => error as Error)
  for (let n = 0; n < 100 && manager.childPid === null; n++) await new Promise(resolve => setTimeout(resolve, 10))
  assert.ok(manager.childPid, 'real dummy stdio child started, MCP initialize still pending')
  await manager.shutdown()
  assert.ok((await starting) instanceof Error)
  assert.equal(manager.childPid, null)
  assert.equal(manager.isRunning, false)
  await providers.recoverIdlePool(async () => true)
  assert.notEqual(providers.get('boss-recruiting'), manager)
  await assert.rejects(manager.ensureStarted(), /关闭|shutting/)
  await providers.shutdownAll()
})


test('cross-session empty metadata does not block stop; protected native receipts close a nonempty old session ledger', windows, async t => {
  const { supervisor, command, ledger, path, home } = await fixture(t, true)
  const emptyId = randomUUID()
  writeFileSync(join(home, 'process-supervision', `${emptyId}.bin`), await dpapiProtect(JSON.stringify({ ...ledger, generation: emptyId, jobs: [], sessionId: 999999 })))
  writeFileSync(path, await dpapiProtect(JSON.stringify({ ...ledger, sessionId: 999999 })))
  await assert.rejects(supervisor.stopManagedResources(), /OTHER_SESSION/)
  await command('terminate')
  for (let n = 0; n < 100; n++) {
    try { readFileSync(join(home, 'process-supervision', `${ledger.jobs[0]}.receipt.json`)); break } catch { await new Promise(resolve => setTimeout(resolve, 20)) }
  }
  const stopped = await supervisor.stopManagedResources()
  assert.equal(stopped.stop_evidence, 'supervised_device_boundary')
})
