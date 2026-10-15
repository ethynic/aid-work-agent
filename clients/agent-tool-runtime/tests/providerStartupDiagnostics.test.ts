import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import { configureRuntimeHome } from '../src/config.js'
import { ProviderCrashError, ProviderManager } from '../src/providerManager.js'

test('stderr before a failed MCP handshake is persisted and redacted on every launch', async t => {
  const home = mkdtempSync(join(tmpdir(), 'aid-startup-diagnostic-'))
  configureRuntimeHome(home)
  const entry = join(home, 'broken-provider.cjs')
  const secret = 'SYNTHETIC' + 'a'.repeat(40)
  writeFileSync(entry, `require('node:fs').writeSync(2, 'STARTUP_DEPENDENCY_MISSING ${secret}\\n'); process.exit(1)`)
  const manager = new ProviderManager(entry)
  t.after(async () => { await manager.shutdown(); configureRuntimeHome(undefined); rmSync(home, { recursive: true, force: true }) })
  for (let attempt = 1; attempt <= 2; attempt++) {
    await assert.rejects(manager.ensureStarted(), (err: unknown) => err instanceof ProviderCrashError && /Connection closed/.test(err.message))
    const log = readFileSync(join(home, 'logs', 'runtime.log'), 'utf8')
    assert.equal(log.split('STARTUP_DEPENDENCY_MISSING').length - 1, attempt)
    assert.ok(!log.includes(secret), 'startup diagnostics must use the existing redaction boundary')
    assert.ok(log.includes('SYNTHE***'))
    assert.equal(manager.isRunning, false)
  }
})
