// Original Runtime result/outbox path; only external Device HTTP is represented by a receiver.
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { randomUUID } from 'node:crypto'
import { ProviderSet } from '../../dist/legacy/providerManager.js'
import { runInvocation } from '../../dist/legacy/invocationRunner.js'
import { configureRuntimeHome } from '../../dist/legacy/config.js'
import { ApiError } from '../../dist/legacy/apiClient.js'

const base = process.env.WECOM_TEST_HOME
if (!base || resolve(base) !== base) throw new Error('absolute synthetic home required')
const request = JSON.parse(readFileSync(0, 'utf8'))
const home = join(base, 'runtime')
configureRuntimeHome(home)
const providers = new ProviderSet({ wecom: fileURLToPath(new URL('./attachmentProvider.mjs', import.meta.url)) }, {
  nodeExecutable: process.execPath, baseEnv: { SystemRoot: process.env.SystemRoot ?? '', PATH: process.env.PATH ?? '',
    TEMP: base, TMP: base, APPDATA: base, LOCALAPPDATA: base, WECOM_TEST_HOME: base,
    AID_WECOM_LOCALDB_PYTHON: process.env.AID_WECOM_LOCALDB_PYTHON ?? '' }, shutdownTimeoutMs: 3000,
})
let result, attempts = 0, firstPayload
try {
  await runInvocation({ invocation_id: randomUUID(), provider: 'wecom', tool_name: 'wecom_read_session',
    arguments: request, claim_token: 'synthetic', timeout_seconds: 60,
    lease_expires_at: new Date(Date.now() + 120000).toISOString() }, {
    providers, api: { started: async () => {}, progress: async () => ({ seq: 0, cancel: false }),
      result: async (_id, payload) => {
        attempts++
        if (attempts === 1) { firstPayload = JSON.stringify(payload); throw new ApiError(503, 'synthetic lost ACK') }
        assert.equal(JSON.stringify(payload), firstPayload, 'retry preserves the original image/result, never re-reads the attachment')
        result = payload
      } },
    desktopCheck: async () => { throw new Error('read-only attachment must not require desktop input') },
    desktopResourceKey: 'fixture-' + base.replace(/[^a-z0-9]/gi, ''), runtimeDataDir: home,
    retryBaseMs: 10, retryMaxMs: 20,
  })
  assert.ok(result, 'missing Runtime result')
} finally { await providers.shutdownAll(); configureRuntimeHome(undefined) }
process.stdout.write(JSON.stringify({ payload: result, result_attempts: attempts }) + '\n')
