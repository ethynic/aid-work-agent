/**
 * 用例 1：pair 成功 / 配对码错误失败 / token 落盘为 DPAPI 密文（非明文）。
 */
import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync, existsSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { test, beforeEach, afterEach } from 'node:test'
import { main } from '../src/cli.js'
import { loadDeviceToken, hasDeviceToken } from '../src/credentials.js'
import { loadConfig, saveConfig } from '../src/config.js'
import { openRuntimeHost } from '../src/runtimeHost.js'
import { FakeCloud } from './helpers/fakeCloud.js'
import { setTimeout as delay } from 'node:timers/promises'

let home: string
let cloud: FakeCloud

beforeEach(async () => {
  home = mkdtempSync(path.join(tmpdir(), 'runtime-pair-'))
  process.env.AIDWORK_RUNTIME_HOME = home
  cloud = new FakeCloud()
  await cloud.start()
})

afterEach(async () => {
  await cloud.stop()
  delete process.env.AIDWORK_RUNTIME_HOME
  rmSync(home, { recursive: true, force: true })
})

test('pair 成功：config.json 不含 token，credentials.bin 为 DPAPI 密文且可解密回原文', async () => {
  cloud.issueCode('ABCD1234')
  const code = await main(['pair', '--code', 'ABCD1234', '--server', cloud.baseUrl, '--name', 'dev-box'])
  assert.equal(code, 0)

  const token = cloud.lastPairToken!
  assert.ok(token, 'fake cloud 应签发 token')

  // config.json：有 device_id/server，无 token 明文
  const config = loadConfig()
  assert.ok(config)
  assert.equal(config!.server, cloud.baseUrl)
  assert.ok(config!.device_id)
  const configRaw = readFileSync(path.join(home, 'config.json'), 'utf8')
  assert.ok(!configRaw.includes(token), 'config.json 不得含 token 明文')

  // credentials.bin：存在且内容不是明文 token
  assert.ok(hasDeviceToken())
  const credRaw = readFileSync(path.join(home, 'credentials.bin'), 'utf8')
  assert.ok(!credRaw.includes(token), 'credentials.bin 不得为 token 明文')

  // DPAPI 解密 round-trip 回原文
  const decrypted = await loadDeviceToken()
  assert.equal(decrypted, token)
  const operations = readFileSync(path.join(home, 'management-operations.json'), 'utf8')
  assert.ok(!operations.includes('ABCD1234'), '管理幂等记录不得落配对码明文')
  assert.ok(!operations.includes(token), '管理幂等记录不得落设备 token')
  assert.equal(existsSync(path.join(home, 'pairing.pending.json')), false)
})

test('pair 失败（配对码无效）：退出码 1，不落设备配置与凭证', async () => {
  const code = await main(['pair', '--code', 'WRONG999', '--server', cloud.baseUrl])
  assert.equal(code, 1)
  assert.ok(!existsSync(path.join(home, 'config.json')))
  assert.ok(!hasDeviceToken())
})

test('pair 缺少 --code：退出码 1', async () => {
  const code = await main(['pair', '--server', cloud.baseUrl])
  assert.equal(code, 1)
})

test('重新配对保留管理员显式配置，避免身份替换顺带丢失 Provider 配置', async () => {
  saveConfig({ server: cloud.baseUrl, device_id: 'old-device', name: 'legacy-name', bossCliEntry: 'C:\\local\\boss.js', providers: { weixin: { entry: 'C:\\local\\weixin.js', v2Send: true } } })
  cloud.issueCode('REPAIR01')
  assert.equal(await main(['pair', '--code', 'REPAIR01']), 0)
  const config = loadConfig()!
  assert.notEqual(config.device_id, 'old-device')
  assert.equal(config.name, 'legacy-name')
  assert.equal(config.bossCliEntry, 'C:\\local\\boss.js')
  assert.deepEqual(config.providers, { weixin: { entry: 'C:\\local\\weixin.js', v2Send: true } })
})

test('unpair refuses a leased Host before touching old identity, then clears safely once released', async () => {
  cloud.issueCode('UNPAIR01')
  assert.equal(await main(['pair', '--code', 'UNPAIR01', '--server', cloud.baseUrl]), 0)
  const config = readFileSync(path.join(home, 'config.json'), 'utf8')
  const cipher = readFileSync(path.join(home, 'credentials.bin'), 'utf8')
  const host = await openRuntimeHost()
  try {
    assert.equal(await main(['unpair']), 1)
    assert.equal(readFileSync(path.join(home, 'config.json'), 'utf8'), config)
    assert.equal(readFileSync(path.join(home, 'credentials.bin'), 'utf8'), cipher)
  } finally { await host.dispose() }
  assert.equal(await main(['unpair']), 0)
  assert.equal(loadConfig(), null)
  assert.equal(hasDeviceToken(), false)
  // Removing the pairing does not remove the stable management dedup identity.
  assert.equal(existsSync(path.join(home, 'management-secret.bin')), true)
})

test('CLI start waits for running and repeated stop signals drain before releasing the same Host lease', { timeout: 10_000 }, async () => {
  cloud.issueCode('START001')
  assert.equal(await main(['pair', '--code', 'START001', '--server', cloud.baseUrl]), 0)
  const signalsBefore = process.listenerCount('SIGINT')
  assert.equal(await main(['start']), 0)
  assert.equal(existsSync(path.join(home, 'host-running.json')), true)
  assert.equal(await main(['unpair']), 1, 'a running CLI owns the shared lease')
  process.emit('SIGINT')
  process.emit('SIGINT')
  for (let tries = 0; process.listenerCount('SIGINT') > signalsBefore && tries < 200; tries++) await delay(25)
  assert.equal(process.listenerCount('SIGINT'), signalsBefore, 'only safe stop removes signal handlers')
  assert.equal(existsSync(path.join(home, 'host-running.json')), false)
  const host = await openRuntimeHost()
  try { assert.equal(host.getState().state, 'stopped') }
  finally { await host.dispose() }
})

test('unpaired CLI start reports failure and releases lease instead of pretending to run', async () => {
  const signalsBefore = process.listenerCount('SIGINT')
  assert.equal(await main(['start']), 1)
  assert.equal(process.listenerCount('SIGINT'), signalsBefore)
  const host = await openRuntimeHost()
  await host.dispose()
})

test('无参运行：输出 usage，退出码 0', async () => {
  const code = await main([])
  assert.equal(code, 0)
})

test('doctor 无配置状态：fail-loud 返回 1，不崩溃', async () => {
  const code = await main(['doctor'])
  assert.equal(code, 1)
})
