import test from 'node:test'
import assert from 'node:assert/strict'
import { RuntimeConnectionRecovery } from '../electron/runtimeConnectionRecovery.js'
import type { RuntimeSupervisor } from '../electron/runtimeSupervisor.js'

test('old renderer new_device is explicitly rejected before any HTTP, Host request, or staged identity', async () => {
  const recovery = new RuntimeConnectionRecovery({ home: 'unused-old-home', node: 'unused-node', hostEntry: 'unused-host',
    supervisor: new Proxy({} as RuntimeSupervisor, { get() { throw new Error('old renderer must not touch Host') } }),
    async onBeforeRecovery() { throw new Error('old renderer must not change intent') } })
  const result = await recovery.reconfigure({ mode: 'new_device', confirmed: true, server: 'https://service.example', device_name: '旧界面测试', pairing_code: 'fixture' })
  assert.equal(result.new_device, false)
  assert.equal(result.diagnosis.category, 'CLIENT_UPDATE_REQUIRED')
  assert.equal(result.diagnosis.can_reconfigure, false)
  assert.equal(recovery.busy, false)
})
