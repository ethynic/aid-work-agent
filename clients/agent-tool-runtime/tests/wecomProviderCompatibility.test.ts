import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { ProviderManager } from '../src/providerManager.js'
import { deviceCapabilities, type RuntimeConfig } from '../src/config.js'
import { fakeProviderEntry } from './helpers/runtimeStack.js'

for (const legacy of [true, false]) test(`WeCom ${legacy ? 'old' : 'new'} plugin keeps legacy calls and truthfully handles the three new interfaces`, async t => {
  const home = mkdtempSync(join(tmpdir(), 'aid-wecom-compat-'))
  t.after(() => rmSync(home, { recursive: true, force: true }))
  const callLog = join(home, 'calls.jsonl')
  const manager = new ProviderManager(fakeProviderEntry(), { env: { FAKE_WECOM_LEGACY: legacy ? '1' : '0' } })
  t.after(() => manager.shutdown())
  const config: RuntimeConfig = { server: 'https://fixture.invalid', device_id: 'fixture', providers: { wecom: { entry: fakeProviderEntry() } } }
  assert.ok(!(deviceCapabilities(config).capabilities as string[]).includes('wecom_read_sessions'), 'an entry alone does not prove batch support')
  assert.ok(!(deviceCapabilities(config).capabilities as string[]).includes('wecom_session_directory'), 'an entry alone does not prove picker support')
  await Promise.all([manager.inspectWecomBusinessTools(), manager.ensureStarted()])
  const capabilities = deviceCapabilities(config, manager.wecomBusinessReady, manager.wecomDirectoryReady)
  assert.equal((capabilities.capabilities as string[]).includes('wecom_read_sessions'), !legacy)
  assert.equal((capabilities.capabilities as string[]).includes('wecom_session_directory'), false)
  const directory = await manager.callTool('wecom_session_directory', { mode: 'list' })
  assert.equal(directory.code, 'TOOL_NOT_SUPPORTED', 'older CLI keeps old tools while picker is unavailable')
  for (const name of ['wecom_read_session', 'wecom_message_send']) {
    const result = await manager.callTool(name, { target_name: 'fixture', text: 'fixture', durationMs: 1, call_log_file: callLog })
    assert.equal(result.success, true, 'legacy arguments must remain usable with both releases')
  }
  for (const name of ['wecom_read_session', 'wecom_message_send', 'wecom_read_sessions']) {
    const result = await manager.callTool(name, { session: 'fixture', sessions: ['fixture'], text: 'fixture', durationMs: 1, call_log_file: callLog })
    assert.equal(result.success, !legacy)
    if (legacy) {
      assert.equal(result.code, 'TOOL_NOT_SUPPORTED')
      assert.equal(result.effect, 'none')
      assert.match(String(result.message), /升级/)
    }
  }
  const calls = readFileSync(callLog, 'utf8').trim().split('\n').map(line => JSON.parse(line))
  assert.equal(calls.length, legacy ? 2 : 5, 'unsupported interfaces never reach a business handler')
  assert.deepEqual(calls.slice(0, 2).map(call => call.target_name), ['fixture', 'fixture'])
  await manager.shutdown()
  assert.equal(manager.wecomBusinessReady, false, 'closed provider evidence cannot keep advertising new tools')
})

test('a failed capability connection is a safe pre-send failure and the next call can recover', async t => {
  let launches = 0
  const manager = new ProviderManager(fakeProviderEntry(), { prepareLaunch: async entry => {
    if (++launches === 1) throw new Error('fixture transient launch failure')
    return { command: process.execPath, args: [entry, 'mcp', '--stdio'] }
  } })
  t.after(() => manager.shutdown())
  const first = await manager.callTool('wecom_message_send', { session: 'fixture', text: 'fixture', durationMs: 1 })
  assert.equal(first.code, 'PROVIDER_NOT_AVAILABLE')
  assert.equal(first.effect, 'none', 'capability discovery has not entered a business operation')
  assert.equal(first.retryable, true)
  const second = await manager.callTool('wecom_message_send', { session: 'fixture', text: 'fixture', durationMs: 1 })
  assert.equal(second.success, true)
  assert.equal(launches, 2, 'a transient startup failure is not cached as missing capability')
})

test('page-only directory keeps page access but cannot advertise or execute Agent search', async t => {
  const manager = new ProviderManager(fakeProviderEntry(), { env: { FAKE_WECOM_DIRECTORY: '1' } })
  t.after(() => manager.shutdown())
  const config: RuntimeConfig = { server: 'https://fixture.invalid', device_id: 'fixture', providers: { wecom: { entry: fakeProviderEntry() } } }
  assert.equal(manager.wecomDirectoryReady, false)
  await manager.inspectWecomBusinessTools()
  assert.equal(manager.wecomDirectoryReady, true)
  assert.equal(manager.wecomDirectorySearchReady, false)
  const capabilities = deviceCapabilities(config, manager.wecomBusinessReady, manager.wecomDirectoryReady, manager.wecomDirectorySearchReady).capabilities as string[]
  assert.ok(capabilities.includes('wecom_session_directory'))
  assert.ok(!capabilities.includes('wecom_session_directory_search'))
  assert.equal((await manager.callTool('wecom_session_directory', { mode: 'list' })).success, true)
  const search = await manager.callTool('wecom_session_directory', { mode: 'search', query: '沟通' })
  assert.equal(search.code, 'TOOL_NOT_SUPPORTED')
  assert.equal((await manager.callTool('wecom_read_session', { session: 'fixture' })).success, true)
})

test('search directory capability requires actual MCP mode, type and query schema', async t => {
  const manager = new ProviderManager(fakeProviderEntry(), { env: {
    FAKE_WECOM_DIRECTORY: '1', FAKE_WECOM_DIRECTORY_SEARCH: '1' } })
  t.after(() => manager.shutdown())
  const config: RuntimeConfig = { server: 'https://fixture.invalid', device_id: 'fixture', providers: { wecom: { entry: fakeProviderEntry() } } }
  await manager.inspectWecomBusinessTools()
  assert.equal(manager.wecomDirectoryReady, true)
  assert.equal(manager.wecomDirectorySearchReady, true)
  const capabilities = deviceCapabilities(config, manager.wecomBusinessReady, manager.wecomDirectoryReady, manager.wecomDirectorySearchReady).capabilities as string[]
  assert.ok(capabilities.includes('wecom_session_directory'))
  assert.ok(capabilities.includes('wecom_session_directory_search'))
  assert.equal((await manager.callTool('wecom_session_directory', { mode: 'search', query: '沟通', type: 'group' })).success, true)
})

test('search mode without type filter remains a page capability only', async t => {
  const manager = new ProviderManager(fakeProviderEntry(), { env: {
    FAKE_WECOM_DIRECTORY: '1', FAKE_WECOM_DIRECTORY_SEARCH_NO_TYPE: '1' } })
  t.after(() => manager.shutdown())
  await manager.inspectWecomBusinessTools()
  assert.equal(manager.wecomDirectoryReady, true)
  assert.equal(manager.wecomDirectorySearchReady, false)
  assert.equal((await manager.callTool('wecom_session_directory', { mode: 'list' })).success, true)
  assert.equal((await manager.callTool('wecom_session_directory', { mode: 'search', query: '孙程' })).code, 'TOOL_NOT_SUPPORTED')
})

test('shutdown waits for an in-flight discovery launch to close before resource-stop can finish', async () => {
  let entered!: () => void, release!: () => void
  const preparing = new Promise<void>(resolve => { entered = resolve })
  const prepared = new Promise<void>(resolve => { release = resolve })
  const manager = new ProviderManager(fakeProviderEntry(), { prepareLaunch: async entry => {
    entered(); await prepared
    return { command: process.execPath, args: [entry, 'mcp', '--stdio'] }
  } })
  const discovery = manager.inspectWecomBusinessTools()
  const rejected = assert.rejects(discovery, /shutting down/)
  await preparing
  const stopped = manager.shutdown()
  release()
  await stopped
  await rejected
  assert.equal(manager.childPid, null)
  assert.equal(manager.wecomBusinessReady, false)
})
