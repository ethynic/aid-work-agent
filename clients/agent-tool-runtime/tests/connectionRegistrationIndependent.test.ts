import test from 'node:test'
import assert from 'node:assert/strict'
import http from 'node:http'
import { execFileSync, spawn } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import { existsSync, mkdtempSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { acquireHostLease } from '@aid/local-tool-host-core'
import { dpapiProtect, dpapiUnprotect } from '../src/dpapi.js'
import { registerConnection, registrationStatus, registrationBlocksExecution, verifyRegisteredConnection,
  rememberRegistrationAction, withConnectionRegistration } from '../src/connectionRegistration.js'
import { diagnosis } from '../src/connectionDiagnosis.js'

const windows = { skip: process.platform !== 'win32', timeout: 90000 }
async function fixture(t: test.TestContext) {
  const root = mkdtempSync(join(tmpdir(), 'aid-registration-independent-'))
  const home = join(root, 'runtime'); mkdirSync(home)
  const state = join(home + '.connection-registration', 'state.bin')
  const tokens = new Map<string, string>(); const calls: string[] = []
  let revoked = false, losePair = false, readonlyOnResponse = false
  const server = http.createServer(async (req, res) => {
    calls.push(req.url!)
    let body = ''; for await (const part of req) body += String(part)
    const input = JSON.parse(body || '{}')
    if (req.url!.endsWith('/pair')) {
      assert.equal(input.pairing_protocol, 2)
      const id = randomUUID(), token = randomUUID(); tokens.set(token, id)
      if (losePair) { res.destroy(); return }
      if (readonlyOnResponse) execFileSync('attrib.exe', ['+R', state])
      revoked = false
      res.setHeader('Content-Type', 'application/json')
      res.end(JSON.stringify({ success: true, device_id: id, device_token: token, replacement_pending: true })); return
    }
    assert.ok(req.url!.endsWith('/heartbeat'))
    assert.deepEqual(input, {})
    const token = String(req.headers.authorization || '').replace('Bearer ', '')
    assert.ok(tokens.has(token))
    res.writeHead(revoked ? 401 : 200, { 'Content-Type': 'application/json' })
    res.end(JSON.stringify(revoked ? {} : { success: true, selected: false }))
  })
  await new Promise<void>(r => server.listen(0, '127.0.0.1', r))
  const serverURL = 'http://127.0.0.1:' + (server.address() as {port: number}).port
  const input = {server: serverURL, device_name: 'independent isolated device', pairing_code: 'isolated-code-' + randomUUID()}
  t.after(async () => {
    if (existsSync(state)) execFileSync('attrib.exe', ['-R', state])
    server.closeAllConnections(); await new Promise<void>(r => server.close(() => r()))
    assert.equal(resolve(root).startsWith(resolve(tmpdir()) + '\\aid-registration-independent-'), true)
    rmSync(root, {recursive: true, force: true})
  })
  const register = () => withConnectionRegistration(home, () => registerConnection(home, input))
  const decode = async () => JSON.parse(await dpapiUnprotect(readFileSync(state, 'utf8')))
  return {home, root, state, input, calls, tokens, register, decode,
    revoked: (v: boolean) => { revoked = v }, lost: (v: boolean) => { losePair = v },
    readonly: (v: boolean) => {readonlyOnResponse = v},
    pairCount: () => calls.filter(p => p.endsWith('/pair')).length}
}
function customerHistory() {
  const entries: Record<string, unknown> = {}
  const names = [...Array(13).fill(['stop', 'reconciling']), ['stop', 'succeeded'], ['start', 'succeeded'], ['start', 'succeeded'], ['plugins.import', 'succeeded'], ['pair', 'succeeded']]
  for (const [method, status] of names) {
    const operation_id = randomUUID()
    entries[operation_id] = {digest: 'a'.repeat(64), operation: {operation_id, method, status, code: 0, created_at: 1, updated_at: 1}}
  }
  return JSON.stringify(entries)
}

test('independent HTTP: mixed 18-operation home pairs once without archive; protected reboot action survives passive status and repeat input', windows, async t => {
  const f = await fixture(t); const old = customerHistory()
  writeFileSync(join(f.home, 'management-operations.json'), old)
  assert.equal((await f.register()).category, 'REGISTRATION_PENDING')
  assert.equal(f.pairCount(), 1); assert.equal(readdirSync(f.home).length, 1)
  assert.equal(existsSync(f.home + '.connection-history'), false)
  assert.equal(readFileSync(join(f.home, 'management-operations.json'), 'utf8'), old)
  const encrypted = readFileSync(f.state, 'utf8'), r = await f.decode()
  assert.equal(r.phase, 'paired'); assert.equal(f.tokens.get(r.token), r.config.device_id)
  assert.ok(!encrypted.includes(r.token)); assert.ok(!encrypted.includes(f.input.pairing_code))
  assert.equal(await registrationBlocksExecution(f.home), true)
  await withConnectionRegistration(f.home, () => rememberRegistrationAction(f.home,
    diagnosis('CONNECTION_RESOURCE_REBOOT_PREPARED', 'isolated reboot required', ['Restart Windows normally.'], {requires_reboot: true})))
  const before = f.calls.length
  assert.equal((await registrationStatus(f.home))?.requires_reboot, true)
  assert.equal(f.calls.length, before)
  assert.equal((await verifyRegisteredConnection(f.home)).requires_reboot, true)
  await f.register(); assert.equal(f.pairCount(), 1)
  f.revoked(true)
  const offline = await verifyRegisteredConnection(f.home)
  assert.equal(offline.category, 'CREDENTIALS_REVOKED'); assert.equal(offline.requires_reboot, true)
  assert.ok(offline.steps.includes('Restart Windows normally.'))
  assert.equal(readFileSync(join(f.home, 'management-operations.json'), 'utf8'), old)
})

test('independent real disk denial: replacing revoked staged identity cannot drop old protected gate before new requested publication', windows, async t => {
  const f = await fixture(t); await f.register(); f.revoked(true)
  const original = readFileSync(f.state, 'utf8'); execFileSync('attrib.exe', ['+R', f.state])
  await assert.rejects(f.register())
  assert.equal(readFileSync(f.state, 'utf8'), original)
  assert.equal(await registrationBlocksExecution(f.home), true)
  assert.equal(f.pairCount(), 1)
  const files = readdirSync(f.home + '.connection-registration')
  assert.ok(files.some(n => n.endsWith('.unconfirmed.bin')))
})

test('independent real HTTP: token protection publication failure after accepted pair does not retry or complete replacement', windows, async t => {
  const f = await fixture(t); const old = customerHistory()
  writeFileSync(join(f.home, 'management-operations.json'), old); f.readonly(true)
  assert.equal((await f.register()).category, 'REGISTRATION_UNCONFIRMED')
  const r = await f.decode(); assert.equal(r.phase, 'requested'); assert.equal(r.token, null)
  assert.equal(f.pairCount(), 1); assert.equal(f.tokens.size, 1)
  await registrationStatus(f.home); await verifyRegisteredConnection(f.home)
  assert.equal(f.pairCount(), 1)
  assert.equal(f.calls.some(p => p.endsWith('/complete')), false)
  assert.equal(readFileSync(join(f.home, 'management-operations.json'), 'utf8'), old)
  assert.equal(existsSync(f.home + '.connection-history'), false)
})

test('independent lost actual HTTP response: protected requested transaction remains; passive reopen/status never consumes another code', windows, async t => {
  const f = await fixture(t); f.lost(true); await f.register()
  assert.equal(f.tokens.size, 1); assert.equal(f.pairCount(), 1)
  const old = readFileSync(f.state, 'utf8'), requested = await f.decode()
  assert.equal(requested.phase, 'requested')
  await registrationStatus(f.home); await verifyRegisteredConnection(f.home); await registrationBlocksExecution(f.home)
  assert.equal(f.calls.length, 1); assert.equal(readFileSync(f.state, 'utf8'), old)
  f.lost(false); f.input.pairing_code = 'explicit-new-' + randomUUID(); await f.register()
  assert.equal(f.pairCount(), 2)
  assert.equal(readFileSync(join(f.home + '.connection-registration', requested.id + '.unconfirmed.bin'), 'utf8'), old)
})

test('independent HTTP 401: no old archive; interrupted activating lineage persists across requested response loss and twice superseded credentials', windows, async t => {
  const f = await fixture(t); writeFileSync(join(f.home, 'management-operations.json'), customerHistory())
  await f.register(); const original = await f.decode(); original.phase = 'activating'
  writeFileSync(f.state, await dpapiProtect(JSON.stringify(original)))
  f.revoked(true)
  assert.equal((await verifyRegisteredConnection(f.home)).category, 'CREDENTIALS_REVOKED')
  assert.equal(existsSync(f.home + '.connection-history'), false)
  f.lost(true); await f.register()
  assert.equal((await f.decode()).archive_id, original.archive_id)
  f.lost(false); await f.register(); const paired = await f.decode()
  assert.equal(paired.archive_id, original.archive_id)
  f.revoked(true); await f.register()
  const next = await f.decode(); assert.equal(next.archive_id, original.archive_id); assert.notEqual(next.id, paired.id)
  assert.equal(next.phase, 'paired'); assert.equal(await registrationBlocksExecution(f.home), true)
  assert.equal(existsSync(f.home + '.connection-history'), false)
  assert.equal(f.calls.some(p => p.endsWith('/complete')), false)
})

test('independent actual OS directory lease excludes another Node process before any pairing HTTP', windows, async t => {
  const f = await fixture(t); mkdirSync(f.home + '.connection-registration')
  const coreModule = pathToFileURL(join(import.meta.dirname, '..', '..', 'node_modules', '@aid', 'local-tool-host-core', 'dist', 'instanceLease.js')).href
  const script = 'import{acquireHostLease}from' + JSON.stringify(coreModule) + ';const l=await acquireHostLease(' + JSON.stringify(f.home + '.connection-registration') + ');if(!l)process.exit(9);console.log("OWN_LEASE_READY");process.stdin.once("data",async()=>{await l.release();process.exit(0)});setTimeout(()=>process.exit(8),20000)'
  const child = spawn(process.execPath, ['--input-type=module', '-e', script], {stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true})
  const done = new Promise<number | null>(r => child.once('exit', r)); child.stderr.resume()
  try {
    await new Promise<void>((r, j) => {const timer = setTimeout(() => j(Error('owned child startup timeout')), 10000); child.stdout.once('data', () => {clearTimeout(timer); r()}); child.once('error', j)})
    await assert.rejects(f.register(), /另一客户端/); assert.equal(f.calls.length, 0)
  } finally {
    child.stdin.end('release')
    const exit = await Promise.race([done, new Promise<'timeout'>(r => setTimeout(() => r('timeout'), 5000))])
    if (exit === 'timeout') {child.kill(); await done; throw Error('owned lease fixture needed bounded cleanup')}
    assert.equal(exit, 0)
  }
  const lease = await acquireHostLease(f.home + '.connection-registration'); assert.ok(lease); await lease.release()
})