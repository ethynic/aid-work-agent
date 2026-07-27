import assert from 'node:assert/strict'
import test from 'node:test'
import fs from 'node:fs'
import path from 'node:path'
import { IPC, BRIDGE_VERSION } from '../src/shared/ipc.js'

test('shared/ipc 通道名稳定', () => {
  assert.equal(IPC.DB_HEALTH, 'boss:db:health')
  assert.equal(IPC.APP_VERSION, 'boss:app:version')
  assert.equal(IPC.APP_RUNTIME, 'boss:app:runtime')
})

test('BRIDGE_VERSION 为正整数', () => {
  assert.equal(typeof BRIDGE_VERSION, 'number')
  assert.ok(BRIDGE_VERSION >= 1)
})

test('IPC 通道命名前缀统一为 boss:', () => {
  for (const channel of Object.values(IPC)) {
    assert.ok(channel.startsWith('boss:'), `${channel} should start with boss:`)
  }
})

test('preload 源码内联契约与 shared/ipc.ts 一致（防 preload 漂移）', () => {
  const preloadSrc = fs.readFileSync(
    path.resolve('src/main/preload.cts'),
    'utf8',
  )
  // preload 是 CJS，内联了 BRIDGE_VERSION 和 IPC 常量，必须与 shared/ipc.ts 同步
  const versionMatch = preloadSrc.match(/const BRIDGE_VERSION = (\d+)/)
  assert.ok(versionMatch, 'preload should define BRIDGE_VERSION')
  assert.equal(Number(versionMatch[1]), BRIDGE_VERSION, 'preload BRIDGE_VERSION drift')

  assert.ok(preloadSrc.includes(`DB_HEALTH: '${IPC.DB_HEALTH}'`), 'preload DB_HEALTH drift')
  assert.ok(preloadSrc.includes(`APP_VERSION: '${IPC.APP_VERSION}'`), 'preload APP_VERSION drift')
  assert.ok(preloadSrc.includes(`APP_RUNTIME: '${IPC.APP_RUNTIME}'`), 'preload APP_RUNTIME drift')
})
