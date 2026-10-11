import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync, rmSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { after, test } from 'node:test'
import { configureRuntimeHome } from '../src/config.js'
import { MCP_STDIO_MAX_BUFFER_BYTES, ProviderCrashError, ProviderManager, ProviderSet } from '../src/providerManager.js'
import { startTestStack } from './helpers/runtimeStack.js'

const entry = fileURLToPath(new URL('./helpers/largeResultProvider.js', import.meta.url))
const mib = 1024 * 1024
const sharedHome = mkdtempSync(path.join(os.tmpdir(), 'aidwork-stdio-tests-'))
configureRuntimeHome(sharedHome)
after(() => rmSync(sharedHome, { recursive: true, force: true }))

test('default ProviderSet preserves a duplicated response above SDK 10MiB', async () => {
  const providers = new ProviderSet({ synthetic: entry })
  try {
    const result = await providers.get('synthetic').callTool('synthetic_read', { base64_bytes: 6 * mib })
    const data = result.data as { images: Array<{ base64: string }> }
    assert.equal(result.success, true)
    assert.equal(result.effect, 'none')
    assert.equal(data.images[0]!.base64.length, 6 * mib)
    assert.equal(data.images[0]!.base64, 'A'.repeat(6 * mib))
  } finally { await providers.shutdownAll() }
})

test('trusted receive budget rejects invalid values before spawning', () => {
  for (const value of [0, -1, mib - 1, 1.5, NaN, Infinity, MCP_STDIO_MAX_BUFFER_BYTES + 1, '1048576']) {
    assert.throws(() => new ProviderManager(entry, { stdioMaxBufferBytes: value as number }), RangeError)
  }
})

test('exact unread-budget boundary passes; overflow keeps first cause and respawn clears it', async () => {
  const home = mkdtempSync(path.join(os.tmpdir(), 'aidwork-stdio-size-'))
  configureRuntimeHome(home)
  const manager = new ProviderManager(entry, { stdioMaxBufferBytes: mib })
  const counter = path.join(home, 'calls.txt')
  try {
    const atLimit = await manager.callTool('synthetic_read', { wire_bytes: mib, counter_file: counter })
    assert.equal(atLimit.success, true)
    await assert.rejects(manager.callTool('synthetic_read', { wire_bytes: mib + 1, counter_file: counter }),
      (error: unknown) => error instanceof ProviderCrashError && error.message.includes(`接收超出预算（${mib} bytes）`))
    assert.equal(readFileSync(counter, 'utf8').trim().split('\n').length, 2, 'overflow invocation must not replay')
    const recovered = await manager.callTool('synthetic_read', { base64_bytes: 16, counter_file: counter })
    assert.equal(recovered.success, true)
    await assert.rejects(manager.callTool('synthetic_read', { malformed: true, counter_file: counter }),
      (error: unknown) => error instanceof ProviderCrashError && error.message.endsWith('MCP transport error'))
    await assert.rejects(manager.callTool('synthetic_read', { crash: true, counter_file: counter }),
      (error: unknown) => error instanceof ProviderCrashError && !error.message.includes('接收超出预算')
        && !error.message.includes('PRIVATE_') && error.message.includes('Connection closed'))
    const log = readFileSync(path.join(home, 'logs', 'runtime.log'), 'utf8')
    assert.match(log, /接收超出预算/)
    assert.match(log, /MCP transport error/)
    assert.doesNotMatch(log, /PRIVATE_|not-json/)
  } finally { await manager.shutdown(); configureRuntimeHome(sharedHome); rmSync(home, { recursive: true, force: true }) }
})

test('transport overflow keeps Runtime unknown without replay and next invocation respawns', async () => {
  const stack = await startTestStack({ providerEntries: { 'boss-recruiting': entry },
    providerOptions: { stdioMaxBufferBytes: mib } })
  const counter = path.join(stack.dataDir, 'large-calls.txt')
  configureRuntimeHome(stack.dataDir)
  try {
    const id = stack.cloud.enqueueInvocation('boss_greet', { wire_bytes: mib + 1, counter_file: counter })
    await stack.cloud.waitFor(() => Boolean(stack.cloud.getInvocation(id)?.result), 10_000)
    const result = stack.cloud.getInvocation(id)!.result!
    assert.equal(result.code, 'EXECUTION_UNKNOWN')
    assert.equal(result.effect, 'unknown')
    assert.equal(result.retryable, false)
    assert.equal(stack.cloud.getInvocation(id)!.state, 'unknown')
    const next = stack.cloud.enqueueInvocation('boss_goto', { base64_bytes: 16, counter_file: counter })
    await stack.cloud.waitFor(() => stack.cloud.getInvocation(next)?.state === 'succeeded', 10_000)
    assert.equal(readFileSync(counter, 'utf8').trim().split('\n').length, 2)
  } finally { await stack.stop(); configureRuntimeHome(sharedHome); rmSync(stack.dataDir, { recursive: true, force: true }) }
})
