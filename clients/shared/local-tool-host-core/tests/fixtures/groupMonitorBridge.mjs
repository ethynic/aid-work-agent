// One isolated invocation using production Runtime -> real stdio MCP -> production CLI operations.
import { readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { randomUUID } from 'node:crypto'
import { ProviderSet } from '../../dist/legacy/providerManager.js'
import { runInvocation } from '../../dist/legacy/invocationRunner.js'
import { configureRuntimeHome } from '../../dist/legacy/config.js'

const base = process.env.WECOM_TEST_HOME
if (!base || resolve(base) !== base) throw new Error('absolute synthetic test home required')
const request = JSON.parse(readFileSync(0, 'utf8'))
if (!['wecom_group_poll', 'wecom_message_send'].includes(request.tool_name)) throw new Error('fixture tool denied')
configureRuntimeHome(join(base, 'runtime'))
const providers = new ProviderSet({ wecom: fileURLToPath(new URL('./groupMonitorProvider.mjs', import.meta.url)) }, {
  nodeExecutable: process.execPath, baseEnv: { SystemRoot: process.env.SystemRoot ?? '', PATH: process.env.PATH ?? '',
    TEMP: base, TMP: base, APPDATA: base, LOCALAPPDATA: base, WECOM_TEST_HOME: base }, shutdownTimeoutMs: 3000,
})
let result
try {
  await runInvocation({ invocation_id: randomUUID(), provider: 'wecom', tool_name: request.tool_name,
    arguments: request.arguments, claim_token: 'synthetic', timeout_seconds: 60,
    lease_expires_at: new Date(Date.now() + 120000).toISOString() }, {
    providers, api: { started: async () => {}, progress: async () => ({ seq: 0, cancel: false }),
      result: async (_id, payload) => { if (result) throw new Error('duplicate result'); result = payload } },
    desktopCheck: async () => true, desktopResourceKey: 'fixture-' + base.replace(/[^a-z0-9]/gi, ''),
    runtimeDataDir: join(base, 'runtime'), retryBaseMs: 10, retryMaxMs: 20,
  })
  if (!result) throw new Error('missing Runtime result')
} finally { await providers.shutdownAll(); configureRuntimeHome(undefined) }
process.stdout.write(JSON.stringify(result) + '\n')
