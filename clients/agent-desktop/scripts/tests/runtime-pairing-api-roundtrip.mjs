import assert from 'node:assert/strict'
import { mkdir, readFile } from 'node:fs/promises'
import path from 'node:path'
import { beginCleanRegistration, checkCleanRegistration, confirmCleanRegistration, installCleanRegistration,
  prepareCleanRegistration, readCleanRegistration } from '../../../agent-tool-runtime/dist/src/cleanRegistration.js'

const [origin, metadata] = process.argv.slice(2)
let code = ''
for await (const chunk of process.stdin) code += chunk
const draft = await beginCleanRegistration(metadata, { server: origin, device_name: 'Protocol round trip' }, [])
await prepareCleanRegistration(metadata, draft, code)
assert.equal((await readCleanRegistration(metadata)).phase, 'prepared')
await assert.rejects(confirmCleanRegistration(metadata, draft), { status: 503 })

const restarted = await readCleanRegistration(metadata)
assert.equal(restarted.phase, 'confirming')
assert.equal(await checkCleanRegistration(metadata, restarted), 'committed')
const home = path.join(metadata, 'candidate')
await mkdir(home)
await installCleanRegistration(metadata, restarted, home)
const config = JSON.parse(await readFile(path.join(home, 'config.json'), 'utf8'))
assert.equal(config.device_id, restarted.device_id)
const heartbeat = await fetch(`${origin}/api/local-tools/runtime/heartbeat`, {
  method: 'POST', headers: { Authorization: `Bearer ${restarted.request.registration_token}`, 'Content-Type': 'application/json' }, body: '{}',
})
assert.equal(heartbeat.status, 200)
assert.equal((await heartbeat.json()).selected, true)
