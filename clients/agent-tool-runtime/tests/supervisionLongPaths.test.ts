import assert from 'node:assert/strict'
import { randomUUID } from 'node:crypto'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { test } from 'node:test'
import { configureRuntimeHome } from '../src/config.js'
import { dpapiUnprotect } from '../src/dpapi.js'
import { ProcessSupervision } from '../src/processSupervision.js'
import { ProviderManager } from '../src/providerManager.js'

const provider = `
const assert = require('node:assert/strict');
assert.equal(require.main, module);
if (__dirname.length < 260) assert.equal(process.cwd(), __dirname);
else assert.ok(process.cwd().length < 260);
assert.deepEqual(process.argv.slice(2), ['mcp','--stdio']);
assert.ok(!process.execArgv.includes('--import'));
assert.equal(require('node:fs').readFileSync(require('node:path').join(__dirname,'relative-resource.txt'),'utf8'), 'relative path preserved');
if (__dirname.length < 260) assert.equal(require('node:fs').readFileSync('relative-resource.txt','utf8'), 'relative path preserved');
const readline = require('node:readline');
const input = readline.createInterface({input:process.stdin});
input.on('line', line => {
  const request = JSON.parse(line);
  if (request.id === undefined) return;
  const result = request.method === 'initialize'
    ? {protocolVersion:request.params.protocolVersion,capabilities:{tools:{}},serverInfo:{name:'long-path-probe',version:'1.0.0'}}
    : {tools:[]};
  process.stdout.write(JSON.stringify({jsonrpc:'2.0',id:request.id,result})+'\\n');
});
input.on('close',()=>process.exit(0));
`

for (const length of [259, 360, 600]) {
  test(`Windows supervised MCP starts and publishes protected receipt with ${length}-character entry`, { skip: process.platform !== 'win32', timeout: 30_000 }, async t => {
    const root = mkdtempSync(join(tmpdir(), 'aid-long-path-'))
    t.after(() => { configureRuntimeHome(undefined); rmSync(root, { recursive: true, force: true }) })
    let home = join(root, '客户 目录')
    const relativeEntry = join('plugins', 'releases', randomUUID(), 'payload', 'dist', 'src', 'cli', 'index.js')
    while (join(home, relativeEntry).length < length) {
      const remaining = length - join(home, relativeEntry).length
      if (remaining === 1) { home += 'x'; continue }
      home = join(home, 'x'.repeat(Math.min(48, remaining - 1)))
    }
    const entry = join(home, relativeEntry)
    assert.equal(entry.length, length)
    mkdirSync(dirname(entry), { recursive: true })
    writeFileSync(entry, provider)
    writeFileSync(join(dirname(entry), 'relative-resource.txt'), 'relative path preserved')
    writeFileSync(join(home, 'config.json'), '{}')
    configureRuntimeHome(home)
    const supervision = new ProcessSupervision(home, process.execPath)
    await supervision.marker(randomUUID())
    const launch = await supervision.prepareLaunch(entry)
    const receipt = launch.args[launch.args.indexOf('--receipt') + 1]!
    assert.ok(receipt.length >= 260, 'must exercise the original MAX_PATH failure')
    const manager = new ProviderManager(entry, { prepareLaunch: async () => launch })
    try {
      await manager.ensureStarted()
      assert.equal(manager.isRunning, true)
    } finally { await manager.shutdown() }
    for (let i = 0; !existsSync(receipt) && i < 100; i++) await new Promise(resolve => setTimeout(resolve, 20))
    assert.ok(existsSync(receipt), 'successful startup alone is insufficient; exit must publish receipt')
    const encrypted = JSON.parse(readFileSync(receipt, 'utf8'))
    const proof = JSON.parse(await dpapiUnprotect(encrypted.protectedData))
    assert.equal(proof.schemaVersion, 1)
    assert.equal(proof.processStopped, true)
    assert.equal(proof.remainingBusinessApps, 0)
    assert.equal(proof.exitReason, 'node-exited')
    assert.equal(proof.jobName, launch.args[launch.args.indexOf('--job-name') + 1])
    assert.equal(proof.ownerPid, process.pid)
    assert.ok(!readdirSync(dirname(receipt)).some(file => file.endsWith('.tmp')))
  })
}
