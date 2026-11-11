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
import { ProcessSupervision } from '../src/processSupervision.js'
import { dpapiProtect } from '../src/dpapi.js'

const windows = { skip: process.platform !== 'win32' }
const seal = (text: string) => Buffer.from(`fixture-seal:${text}`).toString('base64')
const OLD_SESSION = 99

/** Same shape as connectionRecoveryResources.test.ts: mocked native/DPAPI/CIM
 * around a real temp home, exercising ProcessSupervision.recover evidence. */
function fixture(t: TestContext) {
  const home = mkdtempSync(join(tmpdir(), 'recover-evidence-'))
  const state = { boot: randomUUID(), session: 7, supervisor: true, protection: true, cimAvailable: true,
    cimCount: 0, cimChecks: 0, commands: [] as string[][], jobs: new Map<string, number>() }
  t.mock.method(childProcess, 'execFile', ((file: string, args: string[], _options: unknown,
    callback: (error: Error | null, stdout: string, stderr: string) => void) => {
    const child = new EventEmitter() as ChildProcess
    child.stdin = new PassThrough(); child.kill = () => true
    if (file.endsWith('RuntimeProcessSupervisor.exe')) {
      state.commands.push([...args])
      queueMicrotask(() => {
        if (!state.supervisor) { callback(new Error('fixture native failure'), '', ''); return }
        if (args[0] === 'boot-id') callback(null, JSON.stringify({ bootId: state.boot }), '')
        else if (args[0] === 'session-id') callback(null, JSON.stringify({ sessionId: state.session }), '')
        else {
          const name = args[2]!
          if (args[0] === 'terminate') state.jobs.set(name, 0)
          callback(null, JSON.stringify({ exists: state.jobs.has(name), activeProcesses: state.jobs.get(name) ?? 0 }), '')
        }
      })
    } else {
      const script = args.at(-1)!
      // CIM queries carry no stdin; only DPAPI pipes its payload through stdin.
      if (script.includes('Win32_Process')) {
        state.cimChecks++
        queueMicrotask(() => state.cimAvailable ? callback(null, String(state.cimCount), '')
          : callback(new Error('fixture CIM failure'), '', ''))
      } else if (script.includes(']::Protect(') || script.includes(']::Unprotect(')) {
        let input = ''; child.stdin.on('data', chunk => { input += chunk.toString() })
        child.stdin.on('finish', () => queueMicrotask(() => {
          if (!state.protection) callback(new Error('fixture DPAPI failure'), '', '')
          else if (script.includes(']::Protect(')) callback(null, seal(Buffer.from(input, 'base64').toString('utf8')), '')
          else {
            const decoded = Buffer.from(input, 'base64').toString('utf8')
            if (!decoded.startsWith('fixture-seal:')) callback(new Error('fixture protected blob invalid'), '', '')
            else callback(null, Buffer.from(decoded.slice(13)).toString('base64'), '')
          }
        }))
      } else queueMicrotask(() => callback(new Error('fixture unexpected powershell script'), '', ''))
    }
    return child
  }) as typeof childProcess.execFile)
  syncBuiltinESMExports()
  t.after(() => { t.mock.restoreAll(); syncBuiltinESMExports(); rmSync(home, { recursive: true, force: true }) })
  const supervisor = new ProcessSupervision(home, process.execPath)
  const setup = (options: { python?: boolean; session?: number; corrupt?: boolean; jobs?: string[] } = {}) => {
    const generation = randomUUID(); const job = `AidWorkRuntime.${randomUUID()}`
    const bootId = state.boot; const sessionId = options.session ?? state.session
    mkdirSync(join(home, 'process-supervision'), { recursive: true })
    writeFileSync(join(home, 'process-supervision', `${generation}.bin`),
      options.corrupt ? 'AAAA' : seal(JSON.stringify({ schemaVersion: 1, home, generation, bootId, sessionId, ownerPid: 4321, jobs: options.jobs ?? [job] })))
    writeFileSync(join(home, 'host-running.json'), JSON.stringify({ instance_id: generation, schemaVersion: 2,
      supervision: seal(JSON.stringify({ generation, home, bootId, sessionId, rebootOnly: options.python ?? false })) }))
    const receipt = async (valid = true) => writeFileSync(join(home, 'process-supervision', `${job}.receipt.json`), JSON.stringify({ schemaVersion: 1,
      protectedData: await dpapiProtect(JSON.stringify({ schemaVersion: 1, jobName: job, ownerPid: 4321, processStopped: true,
        remainingBusinessApps: valid ? 0 : 1, nodePid: 99 })) }))
    return { generation, job, receipt }
  }
  const recover = async () => supervisor.recover(readFileSync(join(home, 'host-running.json'), 'utf8'))
  return { home, state, setup, recover, ledger: (generation: string) => join(home, 'process-supervision', `${generation}.bin`) }
}

test('cross-session logout alone proves the old execution stopped without cross-session probes', windows, async t => {
  const f = fixture(t); f.setup({ session: OLD_SESSION })
  f.state.cimCount = 0
  assert.equal(await f.recover(), true)
  assert.equal(f.state.commands.some(args => args[0] === 'probe' || args[0] === 'terminate'), false)
  assert.equal(fs.existsSync(join(f.home, 'identity-reconciliation.bin')), true)
})

test('a living old session with no receipts keeps the marker unconfirmed', windows, async t => {
  const f = fixture(t); f.setup({ session: OLD_SESSION })
  f.state.cimCount = 3
  assert.equal(await f.recover(), false)
  assert.equal(f.state.commands.some(args => args[0] === 'probe' || args[0] === 'terminate'), false)
})

test('complete native receipts close a cross-session managed execution without Python coverage claims', windows, async t => {
  const f = fixture(t); const { receipt } = f.setup({ session: OLD_SESSION })
  await receipt()
  f.state.cimCount = 3
  assert.equal(await f.recover(), true)
  assert.equal(f.state.cimChecks, 0, 'receipt evidence short-circuits session queries')
})

test('receipts never cover the Python boundary: rebootOnly stays unconfirmed while the session lives', windows, async t => {
  const f = fixture(t); const { receipt } = f.setup({ session: OLD_SESSION, python: true })
  await receipt()
  f.state.cimCount = 3
  assert.equal(await f.recover(), false)
})

test('an invalid receipt is not stop evidence and cannot skip the session check', windows, async t => {
  const f = fixture(t); const { receipt } = f.setup({ session: OLD_SESSION })
  await receipt(false)
  f.state.cimCount = 3
  assert.equal(await f.recover(), false)
})

test('temporarily unavailable session evidence stays unconfirmed instead of throwing', windows, async t => {
  const f = fixture(t); f.setup({ session: OLD_SESSION, corrupt: true })
  f.state.cimAvailable = false
  assert.equal(await f.recover(), false)
  assert.equal(fs.existsSync(join(f.home, 'identity-reconciliation.bin')), false)
})

test('same-session recovery terminates registered jobs and confirms the stop', windows, async t => {
  const f = fixture(t); const { job } = f.setup()
  assert.equal(await f.recover(), true)
  assert.deepEqual(f.state.commands.filter(args => args[0] === 'terminate').map(args => args[2]), [job])
})

test('an unreadable marker or a decrypted-but-garbage proof retires the old execution', windows, async t => {
  const f = fixture(t); const { generation } = f.setup({ session: OLD_SESSION })
  writeFileSync(join(f.home, 'host-running.json'), '{oops')
  assert.equal(await f.recover(), true)
  assert.equal(fs.existsSync(join(f.home, 'identity-reconciliation.bin')), true)
  // 解密成功但明文不是 JSON：记录本体损坏（原子写入被外部改坏），归档放行。
  writeFileSync(join(f.home, 'host-running.json'), JSON.stringify({ instance_id: generation, schemaVersion: 2,
    supervision: seal('not even json') }))
  assert.equal(await f.recover(), true)
})

test('a failing proof decryption waits for the recheck instead of retiring the record', windows, async t => {
  const f = fixture(t); const { generation } = f.setup({ session: OLD_SESSION })
  writeFileSync(join(f.home, 'host-running.json'), JSON.stringify({ instance_id: generation, schemaVersion: 2,
    supervision: Buffer.from('externally damaged blob').toString('base64') }))
  assert.equal(await f.recover(), false)
  assert.equal(fs.existsSync(join(f.home, 'identity-reconciliation.bin')), false)
})

test('a corrupt ledger with the old session gone still recovers on session evidence', windows, async t => {
  const f = fixture(t); f.setup({ session: OLD_SESSION, corrupt: true })
  f.state.cimCount = 0
  assert.equal(await f.recover(), true)
  assert.equal(fs.existsSync(join(f.home, 'identity-reconciliation.bin')), true)
})

test('a corrupt ledger with a living old session stays unconfirmed', windows, async t => {
  const f = fixture(t); f.setup({ session: OLD_SESSION, corrupt: true })
  f.state.cimCount = 3
  assert.equal(await f.recover(), false)
})
