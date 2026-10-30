import assert from 'node:assert/strict'
import test from 'node:test'
import { randomUUID } from 'node:crypto'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { ProviderManager, ProviderSet } from '../dist/legacy/providerManager.js'
import { getProviderManifest, isWriteToolFor } from '../dist/legacy/providers.js'
import { runInvocation } from '../dist/legacy/invocationRunner.js'
import { configureRuntimeHome } from '../dist/legacy/config.js'

const id = 'send-2026-10-26T00-00-00-000Z'
const definition = { name: 'wecom_send_status', inputSchema: { type: 'object', required: ['receipt_id'],
  properties: { receipt_id: { type: 'string' } } }, annotations: { readOnlyHint: true } }

function stub(tools, status = 'unknown') {
  const calls = []
  let lists = 0
  return { client: {
    listTools: async () => { lists++; return { tools } },
    callTool: async request => {
      calls.push(request)
      return { structuredContent: { success: true, code: 'OK', effect: 'none', message: 'read only',
        data: { receipt_id: id, send_status: status }, run_id: 'anonymous-run', retryable: false } }
    }, close: async () => {},
  }, calls, lists: () => lists }
}

test('old or malformed WeCom CLI cannot receive the new call; legacy tools remain allowed', async () => {
  for (const tools of [[], [{ ...definition, annotations: { readOnlyHint: false } }],
    [{ ...definition, inputSchema: { type: 'object', properties: {} } }],
    [{ ...definition, inputSchema: { ...definition.inputSchema, required: ['receipt_id', 'new_required'] } }]]) {
    const manager = new ProviderManager('/never-spawn')
    const fake = stub(tools)
    manager.client = fake.client
    try {
      const result = await manager.callTool('wecom_send_status', { receipt_id: id })
      assert.equal(result.code, 'TOOL_NOT_ALLOWED')
      assert.equal(result.effect, 'none')
      assert.equal(result.data.send_status, 'unknown')
      assert.equal(fake.calls.length, 0)
      await manager.callTool('wecom_message_send', { text: 'synthetic' })
      assert.equal(fake.lists(), 1, 'legacy calls must not acquire a new capability dependency')
      assert.equal(fake.calls.length, 1)
    } finally { await manager.shutdown() }
  }
})

test('capability belongs to one Provider generation; query success never rewrites its send_status', async () => {
  const manager = new ProviderManager('/never-spawn')
  const fake = stub([definition])
  manager.client = fake.client
  try {
    for (let i = 0; i < 2; i++) {
      const result = await manager.callTool('wecom_send_status', { receipt_id: id })
      assert.equal(result.success, true)
      assert.equal(result.effect, 'none')
      assert.equal(result.data.send_status, 'unknown')
    }
    assert.equal(fake.lists(), 1)
    assert.ok(fake.calls.every(call => call.name === 'wecom_send_status'))
    await manager.disposeDead()
    const old = stub([])
    manager.client = old.client
    assert.equal((await manager.callTool('wecom_send_status', { receipt_id: id })).code, 'TOOL_NOT_ALLOWED')
    assert.equal(old.lists(), 1)
    assert.equal(old.calls.length, 0)
    const manifest = getProviderManifest('wecom')
    assert.ok(manifest.tools.includes('wecom_send_status'))
    assert.equal(isWriteToolFor(manifest, 'wecom_send_status'), false)
    assert.equal(manifest.protocol_version, 1)
    assert.equal(manifest.shared_lock_capable, false)
  } finally { await manager.shutdown() }
})

test('confirmed remains an independent read-only observation, without any send call', async () => {
  const manager = new ProviderManager('/never-spawn')
  const fake = stub([definition], 'confirmed')
  manager.client = fake.client
  try {
    const result = await manager.callTool('wecom_send_status', { receipt_id: id })
    assert.equal(result.data.send_status, 'confirmed')
    assert.equal(result.effect, 'none')
    assert.deepEqual(fake.calls.map(call => call.name), ['wecom_send_status'])
  } finally { await manager.shutdown() }
})

test('real WeCom stdio -> Runtime invocation -> original result outbox preserves missing-receipt unknown', async () => {
  const home = mkdtempSync(join(tmpdir(), 'wecom-mcp-runtime-'))
  configureRuntimeHome(home)
  const entry = fileURLToPath(new URL('../../../wecom-cli/dist/src/cli/index.js', import.meta.url))
  const providers = new ProviderSet({ wecom: entry }, { nodeExecutable: process.execPath,
    baseEnv: { SystemRoot: process.env.SystemRoot ?? '', PATH: process.env.PATH ?? '',
      TEMP: home, TMP: home, LOCALAPPDATA: home, APPDATA: home }, shutdownTimeoutMs: 3000 })
  const results = []
  let desktopChecks = 0
  const api = { started: async () => {}, progress: async () => ({ seq: 0, cancel: false }),
    result: async (invocation, payload) => { results.push({ invocation, payload }) } }
  try {
    const invocation_id = randomUUID()
    await runInvocation({ invocation_id, provider: 'wecom', tool_name: 'wecom_send_status',
      arguments: { receipt_id: id }, claim_token: 'synthetic-claim', lease_expires_at: new Date(Date.now() + 60000).toISOString(),
      timeout_seconds: 30 }, { api, providers, desktopCheck: async () => { desktopChecks++; return false },
      desktopResourceKey: 'isolated-' + randomUUID(), runtimeDataDir: home, retryBaseMs: 10, retryMaxMs: 20 })
    assert.equal(results.length, 1)
    assert.equal(results[0].invocation, invocation_id)
    const result = results[0].payload
    assert.equal(result.success, false)
    assert.equal(result.code, 'CONTENT_UNAVAILABLE')
    assert.equal(result.effect, 'none')
    assert.equal(result.data.receipt_id, id)
    assert.equal(result.data.send_status, 'unknown')
    assert.equal(typeof result.data.run_id, 'string')
    assert.equal(desktopChecks, 0, 'a strictly read-only query must not enter write desktop admission')
    const instructions = providers.get('wecom').client.getInstructions().slice(0, 512)
    for (const prerequisite of ['仅 Windows', 'UI操作', '已解锁', '每次单目标', '禁止重发', '对账只读', '查询成功不等于发送confirmed', '真实触达', '覆盖剪贴板且不恢复']) {
      assert.ok(instructions.includes(prerequisite), 'Agent must see prerequisites and effect limits before the tool catalog is truncated')
    }
  } finally {
    await providers.shutdownAll()
    configureRuntimeHome(undefined)
    rmSync(home, { recursive: true, force: true })
  }
})
