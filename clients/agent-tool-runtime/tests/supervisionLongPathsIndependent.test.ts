import assert from 'node:assert/strict'
import { spawn, spawnSync, type ChildProcessWithoutNullStreams } from 'node:child_process'
import { createHash, randomUUID } from 'node:crypto'
import { appendFileSync, copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmdirSync, symlinkSync, unlinkSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { basename, dirname, join, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { test, after, type TestContext } from 'node:test'

// Consume the parent's already built bytes. This test never rebuilds or writes dist.
const testDirectory = dirname(fileURLToPath(import.meta.url))
const runtime = resolve(testDirectory, basename(dirname(testDirectory)) === 'dist' ? '../..' : '..')
const repo = resolve(runtime, '../..')
const built = (name: string) => pathToFileURL(join(runtime, 'dist/src', name + '.js')).href
const { dpapiUnprotect, trustedSystemPowerShell } = await import(built('dpapi'))
const { configureRuntimeHome } = await import(built('config'))
const { ProviderManager } = await import(built('providerManager'))
const helper = join(runtime, 'dist/src/native/RuntimeProcessSupervisor.exe')
const installedNode = resolve(process.env.AID_SUPERVISION_TEST_NODE ?? process.execPath)
const bossPayload = process.env.AID_SUPERVISION_TEST_BOSS_PAYLOAD
const output = join(repo, 'clients/agent-desktop/build/supervision-longpaths-independent')
mkdirSync(output, { recursive: true })
const results: Record<string, unknown>[] = []
const audit = (phase: string, fields: Record<string, unknown> = {}) => appendFileSync(join(output, 'driver-checkpoints.jsonl'), JSON.stringify({ phase, pid: process.pid, ...fields }) + '\n')
const suiteKeepAlive = setInterval(() => {}, 250)
const digest = (file: string) => createHash('sha256').update(readFileSync(file)).digest('hex')
const originalBindings = {
  nativeSource: digest(join(runtime, 'native/RuntimeProcessSupervisor.cs')),
  nativeExecutable: digest(helper),
  providerSource: digest(join(repo, 'clients/shared/local-tool-host-core/src/legacy/providerManager.ts')),
  providerCompiled: digest(join(repo, 'clients/shared/local-tool-host-core/dist/legacy/providerManager.js')),
  nodeExecutable: digest(installedNode),
}
const delay = (ms: number) => new Promise(resolve => setTimeout(resolve, ms))
const alive = (pid: number) => { try { process.kill(pid, 0); return true } catch { return false } }
const tickScript = (pid: number) => `(Get-Process -Id ${pid}).StartTime.ToUniversalTime().Ticks`
function ps(script: string) {
  const trusted = trustedSystemPowerShell()
  const result = spawnSync(trusted.executable, ['-NoProfile', '-NonInteractive', '-Command', script], {
    cwd: trusted.cwd, env: trusted.env, windowsHide: true, encoding: 'utf8', timeout: 15000,
  })
  assert.equal(result.status, 0, 'owned read-only PowerShell probe must succeed')
  assert.equal(typeof result.stdout, 'string')
  return String(result.stdout).trim()
}
function args(entry: string, receipt: string, owner = process.pid, job = 'AidWorkRuntime.' + randomUUID()) {
  return ['run', '--job-name', job, '--node', installedNode, '--entry', entry,
    '--owner-pid', String(owner), '--owner-start-time', ps(tickScript(owner)), '--receipt', receipt]
}
function probe(job: string) {
  const result = spawnSync(helper, ['probe', '--job-name', job], { encoding: 'utf8', windowsHide: true, timeout: 10000 })
  assert.equal(result.status, 0)
  return JSON.parse(result.stdout)
}
function exited(child: ChildProcessWithoutNullStreams) {
  return new Promise<number | null>((resolve, reject) => { child.once('exit', resolve); child.once('error', reject) })
}
async function until(check: () => boolean, message: string) {
  for (let i = 0; i < 160; i++) { if (check()) return; await delay(50) }
  assert.fail(message)
}
async function firstLine(child: ChildProcessWithoutNullStreams): Promise<{ node: number; child: number; cwd: string; args: string[]; exec?: string[]; main?: boolean; fork?: string[]; worker?: string[] }> {
  return new Promise((accept, reject) => {
    let text = ''
    let category = 'NATIVE_OR_NODE_EXIT'
    const timer = setTimeout(() => { cleanup(); reject(new Error('owned fixture ready timed out')) }, 8000)
    const consume = (chunk: Buffer) => { text += chunk; if (text.includes('\n')) { cleanup(); accept(JSON.parse(text.split('\n')[0]!)) } }
    const diagnostic = (chunk: Buffer) => { const match = chunk.toString().match(/\{"error":"([A-Z_]+)"\}/); if (match) category = match[1]! }
    const ended = (code: number | null) => { cleanup(); reject(new Error(`owned fixture exited before ready (${code}, ${category})`)) }
    const cleanup = () => { clearTimeout(timer); child.stdout.off('data', consume); child.stderr.off('data', diagnostic); child.off('exit', ended) }
    child.stdout.on('data', consume)
    child.stderr.on('data', diagnostic)
    child.once('exit', ended)
  })
}
function fixture(t: TestContext) {
  const root = mkdtempSync(join(tmpdir(), 'aid-supervision-independent-'))
  const owned: ChildProcessWithoutNullStreams[] = []
  const jobs: string[] = []
  let directory = join(root, '客户 目录 独立测试')
  while (directory.length < 510) directory = join(directory, '长路径 空格 监管边界验证'.repeat(3))
  mkdirSync(directory, { recursive: true })
  const entry = join(directory, '独立 入口.cjs')
  // A long filename exercises the fixed-size temporary sibling change too.
  const receipt = join(directory, '受保护 回执'.repeat(20) + '.json')
  t.after(async () => {
    audit('cleanup_begin', { root, ownedPids: owned.map(child => child.pid), jobs })
    for (const job of jobs) spawnSync(helper, ['terminate', '--job-name', job], { windowsHide: true, timeout: 20000 })
    audit('cleanup_jobs_terminated', { root })
    for (const child of owned) if (child.exitCode === null) child.kill()
    audit('cleanup_owned_killed', { root })
    await delay(100)
    const canonical = resolve(root)
    assert.ok(canonical.startsWith(resolve(tmpdir()) + '\\aid-supervision-independent-'))
    // Avoid Node 22's native recursive-rm path on deep Unicode Windows trees.
    // Enumerate only owned entries; a dependency junction is unlinked, never traversed.
    const remove = (directory: string) => {
      for (const entry of readdirSync(directory, { withFileTypes: true })) {
        const child = join(directory, entry.name)
        if (entry.isDirectory() && !entry.isSymbolicLink()) remove(child)
        else unlinkSync(child)
      }
      rmdirSync(directory)
    }
    audit('cleanup_remove_begin', { root })
    remove('\\\\?\\' + canonical)
    audit('cleanup_complete', { root })
  })
  return { root, directory, entry, receipt, owned, jobs }
}
async function receiptProof(receipt: string, job: string, owner: number, reason: string) {
  await until(() => existsSync(receipt), 'supervisor did not publish its scoped receipt')
  const envelope = JSON.parse(readFileSync(receipt, 'utf8'))
  assert.deepEqual(Object.keys(envelope).sort(), ['protectedData', 'schemaVersion'])
  assert.equal(envelope.schemaVersion, 1)
  const proof = JSON.parse(await dpapiUnprotect(envelope.protectedData))
  assert.equal(proof.jobName, job)
  assert.equal(proof.ownerPid, owner)
  assert.equal(proof.processStopped, true)
  assert.equal(proof.remainingBusinessApps, 0)
  assert.equal(proof.exitReason, reason)
  assert.ok(!readdirSync(dirname(receipt)).some(file => file.endsWith('.tmp')))
  return proof
}
function receiptAcl(receipt: string) {
  const io = '\\\\?\\' + receipt
  const script = `$ErrorActionPreference='Stop'; [AppContext]::SetSwitch('Switch.System.IO.UseLegacyPathHandling',$false); [AppContext]::SetSwitch('Switch.System.IO.BlockLongPaths',$false); $acl=[IO.File]::GetAccessControl('${io.replaceAll("'", "''")}'); $sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value; @{protected=$acl.AreAccessRulesProtected;currentSid=$sid;aces=@($acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]) | ForEach-Object { @{sid=$_.IdentityReference.Value;rights=[int]$_.FileSystemRights;type=[string]$_.AccessControlType;inherited=$_.IsInherited} })} | ConvertTo-Json -Depth 4 -Compress`
  const acl = JSON.parse(ps(script))
  assert.equal(acl.protected, true)
  assert.deepEqual(acl.aces.map((ace: { sid: string }) => ace.sid).sort(), ['S-1-5-18', acl.currentSid].sort())
  assert.ok(acl.aces.every((ace: { rights: number; type: string; inherited: boolean }) => ace.rights === 2032127 && ace.type === 'Allow' && !ace.inherited))
  return { protected: true, current_user_and_system_only: true, full_control: true }
}

test('independent native long Unicode entry and receipt publish DPAPI proof with private ACL and no temporary residue', { skip: process.platform !== 'win32', timeout: 45000 }, async t => {
  const f = fixture(t)
  writeFileSync(join(f.directory, '资源 文件.txt'), 'module-relative asset')
  writeFileSync(f.entry, `const {Worker,isMainThread,parentPort}=require('node:worker_threads');
if(!isMainThread) { parentPort.postMessage(process.execArgv); }
else if(process.argv[2]==='fork-fixture') { process.send(process.execArgv);process.exit(0); }
else {
  require('node:assert/strict').equal(require('node:fs').readFileSync(require('node:path').join(__dirname,'资源 文件.txt'),'utf8'),'module-relative asset');
  const c=require('node:child_process').fork(__filename,['fork-fixture'],{stdio:['ignore','ignore','ignore','ipc']});
  const w=new Worker(__filename);
  const result=(p)=>new Promise((resolve,reject)=>{let value;p.once('message',m=>value=m);p.once('error',reject);p.once('exit',code=>code===0?resolve(value):reject(Error('owned child exit')))});
  Promise.all([result(c),result(w)]).then(([fork,worker])=>process.stdout.write(JSON.stringify({cwd:process.cwd(),args:process.argv.slice(2),exec:process.execArgv,main:require.main===module,node:process.pid,child:0,fork,worker})+'\\n'));
  process.stdin.once('data',()=>process.exit(0));setInterval(()=>{},1000);
}`)
  const launch = args(f.entry, f.receipt)
  const job = launch[2]!
  f.jobs.push(job)
  const child = spawn(helper, launch, { windowsHide: true, stdio: 'pipe' })
  f.owned.push(child)
  const done = exited(child)
  const ready = await firstLine(child)
  assert.deepEqual(ready.args, ['mcp', '--stdio'])
  assert.deepEqual(ready.exec, [])
  assert.equal(ready.main, true)
  assert.deepEqual(ready.fork, [])
  assert.deepEqual(ready.worker, [])
  let safeCwd = f.directory
  while (safeCwd.length >= 260) safeCwd = dirname(safeCwd)
  assert.equal(ready.cwd.replace(/^\\\\\?\\/, ''), safeCwd)
  assert.ok(probe(job).activeProcesses >= 1)
  child.stdin.write('finish\n')
  assert.equal(await done, 0)
  const proof = await receiptProof(f.receipt, job, process.pid, 'node-exited')
  assert.equal(proof.nodePid, ready.node)
  const acl = receiptAcl(f.receipt)
  assert.equal(probe(job).activeProcesses, 0)
  assert.ok(!alive(ready.node))
  results.push({ name: 'long_unicode_normal_exit', entry_length: f.entry.length, receipt_length: f.receipt.length, receipt_filename_length: f.receipt.slice(f.directory.length + 1).length, native_exit: 0, safe_cwd_length: safeCwd.length, argv_main_module_fork_worker_preserved: true, dpapi_current_user: true, private_acl: acl, temporary_residue: false, job_active_after: 0 })
})

test('independent owner death at long paths clears the Job tree and preserves an unrelated owned canary', { skip: process.platform !== 'win32', timeout: 45000 }, async t => {
  const f = fixture(t)
  const owner = spawn(installedNode, ['-e', 'setInterval(()=>{},1000)'], { windowsHide: true, stdio: 'pipe' })
  const canary = spawn(installedNode, ['-e', 'setInterval(()=>{},1000)'], { windowsHide: true, stdio: 'pipe' })
  f.owned.push(owner, canary)
  assert.ok(owner.pid && canary.pid)
  writeFileSync(f.entry, `const {spawn}=require('node:child_process'); const c=spawn(process.execPath,['-e','setInterval(()=>{},1000)'],{stdio:'ignore',windowsHide:true});process.stdout.write(JSON.stringify({node:process.pid,child:c.pid,cwd:process.cwd(),args:process.argv.slice(2)})+'\\n');setInterval(()=>{},1000)`)
  const launch = args(f.entry, f.receipt, owner.pid)
  const job = launch[2]!
  f.jobs.push(job)
  const child = spawn(helper, launch, { windowsHide: true, stdio: 'pipe' })
  f.owned.push(child)
  const done = exited(child)
  const ready = await firstLine(child)
  audit('owner_case_ready', { owner: owner.pid, canary: canary.pid, helper: child.pid, fixtureNode: ready.node, descendant: ready.child })
  const activeBefore = probe(job).activeProcesses
  assert.ok(activeBefore >= 2)
  assert.ok(alive(ready.node) && alive(ready.child))
  audit('owner_kill_begin', { owner: owner.pid })
  owner.kill()
  audit('owner_kill_sent')
  assert.equal(await done, 0)
  audit('owner_helper_exited')
  const proof = await receiptProof(f.receipt, job, owner.pid, 'owner-exited')
  audit('owner_receipt_decrypted')
  assert.equal(proof.nodePid, ready.node)
  assert.ok(!alive(ready.node) && !alive(ready.child))
  assert.ok(alive(canary.pid), 'unrelated process must remain outside Job boundary')
  assert.equal(probe(job).activeProcesses, 0)
  results.push({ name: 'long_paths_owner_death', entry_length: f.entry.length, receipt_length: f.receipt.length, contained_tree_processes_minimum: 2, actual_job_members_before: activeBefore, native_exit: 0, node_and_descendant_stopped: true, unrelated_owned_canary_preserved: true, dpapi_receipt_reason: 'owner-exited', job_active_after: 0 })
  audit('owner_assertions_complete')
})

test('independent invalid relative UNC and extended external paths remain rejected before child creation', { skip: process.platform !== 'win32', timeout: 45000 }, async t => {
  const f = fixture(t)
  writeFileSync(f.entry, `process.exit(99)`)
  const valid = args(f.entry, f.receipt)
  const matrix: [string, string, string][] = [
    ['--node', 'node.exe', 'INVALID_NODE'],
    ['--entry', 'relative.cjs', 'INVALID_ENTRY'],
    ['--entry', '\\\\localhost\\C$\\entry.cjs', 'INVALID_ENTRY'],
    ['--entry', '\\\\?\\' + f.entry, 'INVALID_ENTRY'],
    ['--entry', join(f.root, 'sub') + '\\..\\fixture.cjs', 'INVALID_ENTRY'],
    ['--entry', 'C:\\bad|entry.cjs', 'SUPERVISOR_FAILED'],
    ['--receipt', 'relative.json', 'INVALID_RECEIPT'],
    ['--receipt', '\\\\localhost\\C$\\receipt.json', 'INVALID_RECEIPT'],
    ['--receipt', '\\\\?\\' + f.receipt, 'INVALID_RECEIPT'],
    ['--owner-start-time', '1', 'OWNER_IDENTITY_MISMATCH'],
  ]
  for (const [key, value, code] of matrix) {
    const options = [...valid]
    options[options.indexOf(key) + 1] = value
    const result = spawnSync(helper, options, { encoding: 'utf8', windowsHide: true, timeout: 10000 })
    assert.equal(result.status, 1, key)
    assert.deepEqual(JSON.parse(result.stderr), { error: code })
    assert.equal(probe(valid[2]!).activeProcesses, 0)
    assert.equal(existsSync(f.receipt), false)
  }
  results.push({ name: 'invalid_inputs_rejected', scenarios: matrix.length, native_exit: 1, fixed_categories_only: true, child_created: false })
})

test('independent actual pre-handshake native and Node stderr is persisted and redacted', { skip: process.platform !== 'win32', timeout: 45000 }, async t => {
  const keepAlive = setInterval(() => {}, 250)
  t.after(() => clearInterval(keepAlive))
  const f = fixture(t)
  const home = join(f.root, 'isolated-home')
  configureRuntimeHome(home)
  t.after(() => configureRuntimeHome(undefined))
  writeFileSync(f.entry, 'process.exit(98)')
  const native = new ProviderManager(f.entry, { prepareLaunch: async () => {
    const options = args(f.entry, f.receipt)
    options[options.indexOf('--receipt') + 1] = 'relative.json'
    return { command: helper, args: options }
  } })
  try { await assert.rejects(native.ensureStarted()) } finally { await native.shutdown() }
  let log = readFileSync(join(home, 'logs/runtime.log'), 'utf8')
  assert.ok(log.includes('INVALID_RECEIPT'))
  const synthetic = 'TESTONLY' + 'AB12'.repeat(16)
  writeFileSync(f.entry, `require('node:fs').writeSync(2, 'STARTUP_FS_DENIED '+${JSON.stringify(synthetic)}+'\\n');process.exit(17)`)
  const manager = new ProviderManager(f.entry, { nodeExecutable: installedNode })
  try {
    for (let i = 1; i <= 2; i++) {
      await assert.rejects(manager.ensureStarted())
      log = readFileSync(join(home, 'logs/runtime.log'), 'utf8')
      assert.equal(log.split('STARTUP_FS_DENIED').length - 1, i)
      assert.ok(!log.includes(synthetic))
      assert.ok(log.includes('TESTON***'))
      assert.equal(manager.isRunning, false)
    }
  } finally { await manager.shutdown() }
  results.push({ name: 'startup_stderr_persisted', actual_native_failure_category: 'INVALID_RECEIPT', synthetic_startup_attempts: 2, before_initialize_failure: true, file_contains_fixed_failure: true, synthetic_secret_absent: true })
})

test('independent BOSS initialize and listTools work through the actual helper at long Unicode paths', { skip: process.platform !== 'win32' || !bossPayload, timeout: 60000 }, async t => {
  const f = fixture(t)
  const home = join(f.root, 'isolated-runtime-home')
  configureRuntimeHome(home)
  t.after(() => configureRuntimeHome(undefined))
  const payload = join(f.directory, 'BOSS 只读 配套资源')
  mkdirSync(payload, { recursive: true })
  audit('boss_copy_begin', { root: f.root })
  const copy = (source: string, destination: string) => {
    mkdirSync(destination, { recursive: true })
    for (const child of readdirSync(source, { withFileTypes: true })) {
      assert.ok(!child.isSymbolicLink(), 'source code copy must not follow external links')
      if (child.isDirectory()) copy(join(source, child.name), join(destination, child.name))
      else copyFileSync(join(source, child.name), join(destination, child.name))
    }
  }
  const ioPayload = '\\\\?\\' + payload
  copy(join(bossPayload!, 'dist'), join(ioPayload, 'dist'))
  copyFileSync(join(bossPayload!, 'package.json'), join(ioPayload, 'package.json'))
  audit('boss_copy_complete', { root: f.root })
  symlinkSync(join(bossPayload!, 'node_modules'), join(ioPayload, 'node_modules'), 'junction')
  audit('boss_dependency_link_created', { root: f.root })
  const entry = join(payload, 'dist/src/cli/index.js')
  const launch = args(entry, f.receipt)
  f.jobs.push(launch[2]!)
  const manager = new ProviderManager(entry, { prepareLaunch: async () => ({ command: helper, args: launch }), nodeExecutable: installedNode })
  let count = 0
  try {
    await manager.ensureStarted()
    const tools = await manager.client.listTools({}, { timeout: 15000 })
    count = tools.tools.length
    assert.ok(count > 0)
    assert.ok(tools.tools.some((tool: { name: string }) => tool.name.startsWith('boss_')))
  } finally { await manager.shutdown() }
  await receiptProof(f.receipt, launch[2]!, process.pid, 'node-exited')
  assert.equal(probe(launch[2]!).activeProcesses, 0)
  results.push({ name: 'real_boss_initialize_listtools', boss_version: JSON.parse(readFileSync(join(payload, 'package.json'), 'utf8')).version, source_entry_sha256: digest(join(bossPayload!, 'dist/src/cli/index.js')), copied_entry_sha256: digest(entry), entry_length: entry.length, receipt_length: f.receipt.length, tool_count: count, methods: ['initialize', 'notifications/initialized', 'tools/list'], business_tool_calls: 0, protected_receipt_and_job_stop: true })
})

after(() => {
  clearInterval(suiteKeepAlive)
  assert.equal(digest(helper), originalBindings.nativeExecutable, 'test must bind stable built helper bytes')
  const report = { date: '2026-10-15', source_bindings: originalBindings, test_script_sha256: digest(fileURLToPath(import.meta.url)), passed_results: results,
    scope: 'Owned nonce temp paths/PIDs/Jobs only. Parent-built helper and installed fixed Node executed; read-only source BOSS payload copied to owned long path with dependency junction. No recruiting tools, customer state, server, registry, install, build or deployment.',
    untested: ['Customer machine and packaged installer containing this new helper', 'Windows policy variants and older .NET installs', 'Real BOSS recruitment or browser interaction', 'Forced I/O denial or disk full during receipt publication'] }
  writeFileSync(join(output, 'supervision-longpaths-report.json'), JSON.stringify(report, null, 2) + '\n')
})
