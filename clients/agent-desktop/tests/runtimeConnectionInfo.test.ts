import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { readRuntimeConnectionInfo } from '../electron/runtimeCleanConnection.js'

test('connection details expose only the active public origin and never modify configuration', () => {
  const home = mkdtempSync(path.join(tmpdir(), 'aid-connection-info-'))
  try {
    const file = path.join(home, 'config.json')
    const content = JSON.stringify({ server: 'https://user:password@test.example:8443/api?token=private#secret', device_id: 'private-device', token: 'private-token', providers: { secret: 'private-entry' } })
    writeFileSync(file, content)
    assert.deepEqual(readRuntimeConnectionInfo(home), { server: 'https://test.example:8443' })
    assert.equal(readFileSync(file, 'utf8'), content)
    writeFileSync(file, JSON.stringify({ server: 'http://127.0.0.1:8000/api' }))
    assert.deepEqual(readRuntimeConnectionInfo(home), { server: 'http://127.0.0.1:8000' }, 'A new connection must not keep a cached old server')
  } finally { rmSync(home, { recursive: true, force: true }) }
})

test('unreadable connection metadata does not require registration, network or Host recovery', () => {
  const home = mkdtempSync(path.join(tmpdir(), 'aid-connection-info-'))
  try {
    assert.deepEqual(readRuntimeConnectionInfo(null), { server: null })
    assert.deepEqual(readRuntimeConnectionInfo(home), { server: null })
    for (const content of ['invalid JSON', '{"server":"file:///private/path"}', '{"server":123}']) {
      writeFileSync(path.join(home, 'config.json'), content)
      assert.deepEqual(readRuntimeConnectionInfo(home), { server: null })
    }
  } finally { rmSync(home, { recursive: true, force: true }) }
})
