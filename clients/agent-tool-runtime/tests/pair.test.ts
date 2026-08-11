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
import { loadConfig } from '../src/config.js'
import { FakeCloud } from './helpers/fakeCloud.js'

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
})

test('pair 失败（配对码无效）：退出码 1，不落任何文件', async () => {
  const code = await main(['pair', '--code', 'WRONG999', '--server', cloud.baseUrl])
  assert.equal(code, 1)
  assert.ok(!existsSync(path.join(home, 'config.json')))
  assert.ok(!hasDeviceToken())
})

test('pair 缺少 --code：退出码 1', async () => {
  const code = await main(['pair', '--server', cloud.baseUrl])
  assert.equal(code, 1)
})

test('无参运行：输出 usage，退出码 0', async () => {
  const code = await main([])
  assert.equal(code, 0)
})

test('doctor 无配置状态：fail-loud 返回 1，不崩溃', async () => {
  const code = await main(['doctor'])
  assert.equal(code, 1)
})
