import assert from 'node:assert/strict'
import { randomUUID, createHash } from 'node:crypto'
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { setTimeout as delay } from 'node:timers/promises'

// Run with the product's fixed Node. Uses an isolated home and never pairs or starts business work.
const directory = resolve(process.argv[2] ?? '')
const hostModule = resolve(process.argv[3] ?? 'dist/src/runtimeHost.js')
assert.equal(process.versions.node, '22.23.3')
assert.equal(process.versions.modules, '127')
const { openRuntimeHost } = await import(pathToFileURL(hostModule).href)
const trust = JSON.parse(readFileSync(join(directory, 'ISOLATED-TEST-TRUST-ROOT.json'), 'utf8')).roots
const home = mkdtempSync(join(tmpdir(), 'aid-final-host-smoke-'))
const selected = new Map()
let consumed = 0
const platform = { takeSelectedPackage: async input => {
  const selection = selected.get(input.selection_ref)
  assert.ok(selection && selection.request_key === input.request_key && selection.instance_id === input.instance_id)
  selected.delete(input.selection_ref); consumed++
  return selection.snapshot
} }
let host
async function query(method, params = {}) {
  const response = await host.request({ request_id: randomUUID(), method, params })
  assert.equal(response.code, 0, JSON.stringify(response))
  return response.result
}
async function finish(accepted) {
  const deadline = Date.now() + 120_000
  let operation = accepted
  while (operation.status === 'running') {
    assert.ok(Date.now() < deadline, 'Plugin operation exceeded smoke budget')
    await delay(25)
    operation = await query('operations.get', { operation_id: accepted.operation_id })
  }
  return operation
}
async function mutate(method, fields) {
  const started = performance.now()
  const operation = await finish(await query(method, { request_key: randomUUID(), ...fields }))
  assert.equal(operation.status, 'succeeded', JSON.stringify(operation))
  console.log(JSON.stringify({ method, ms: Math.round(performance.now() - started) }))
}
async function list() {
  const started = performance.now()
  const result = await query('plugins.list')
  console.log(JSON.stringify({ method: 'plugins.list', ms: Math.round(performance.now() - started), plugins: result.plugins }))
  return result.plugins
}
try {
  host = await openRuntimeHost({ home, trust, platform, supervisor: 'runtime_app' })
  assert.ok((await query('describe')).features.includes('first_party_plugins'))
  assert.deepEqual(await list(), [])
  for (const file of ['boss-0.3.0-dev.aidplugin.zip', 'weixin-0.1.0-dev.aidplugin.zip', 'wecom-0.1.0-dev.aidplugin.zip']) {
    const bytes = readFileSync(join(directory, file))
    const staged_path = join(home, `${randomUUID()}.zip`); writeFileSync(staged_path, bytes)
    const request_key = randomUUID(), selection_ref = randomUUID()
    selected.set(selection_ref, { request_key, instance_id: host.instanceId, snapshot: { staged_path, size: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex') } })
    const started = performance.now()
    const accepted = await query('plugins.import', { request_key, selection_ref })
    // Main's transient selected copy can change after acceptance; Host must use its durable frozen copy.
    writeFileSync(staged_path, 'changed after durable acceptance')
    const operation = await finish(accepted)
    assert.equal(operation.status, 'succeeded', JSON.stringify(operation))
    const before = consumed
    const repeated = await query('plugins.import', { request_key, selection_ref })
    assert.equal(repeated.operation_id, operation.operation_id)
    assert.equal(consumed, before, 'Retry must not consume the expired Main reference again')
    console.log(JSON.stringify({ method: 'plugins.import', file, ms: Math.round(performance.now() - started) }))
  }
  const installed = await list()
  assert.equal(installed.length, 3); assert.ok(installed.every(plugin => plugin.enabled))
  const id = installed.find(plugin => plugin.plugin_id === 'ai.aidwork.weixin').installation_id
  await mutate('plugins.disable', { installation_id: id })
  assert.equal((await list()).find(plugin => plugin.installation_id === id).enabled, false)
  await host.dispose()
  host = await openRuntimeHost({ home, trust, platform, supervisor: 'runtime_app' })
  assert.equal((await list()).find(plugin => plugin.installation_id === id).enabled, false)
  await mutate('plugins.enable', { installation_id: id })
  assert.equal((await list()).find(plugin => plugin.installation_id === id).enabled, true)
  for (const plugin of installed) await mutate('plugins.uninstall', { installation_id: plugin.installation_id })
  assert.deepEqual(await list(), [])
  await host.dispose()
  host = await openRuntimeHost({ home, trust, platform, supervisor: 'runtime_app' })
  assert.deepEqual(await list(), [])
  console.log(JSON.stringify({ passed: true, home, fixed_node: process.versions.node, imports: consumed }))
} finally { await host?.dispose() }
