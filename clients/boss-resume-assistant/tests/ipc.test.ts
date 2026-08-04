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

test('Phase 8 新增通道存在且命名稳定', () => {
  assert.equal(IPC.CHROME_LAUNCH, 'boss:chrome:launch')
  assert.equal(IPC.SESSION_CONFIRM_LOGIN, 'boss:session:confirm-login')
  assert.equal(IPC.SESSION_START, 'boss:session:start')
  assert.equal(IPC.SESSION_PAUSE, 'boss:session:pause')
  assert.equal(IPC.SESSION_RESUME, 'boss:session:resume')
  assert.equal(IPC.SESSION_STOP, 'boss:session:stop')
  assert.equal(IPC.SESSION_STATUS, 'boss:session:status')
  assert.equal(IPC.SESSION_RESET, 'boss:session:reset')
  assert.equal(IPC.SESSION_EVENT, 'boss:session:event')
  assert.equal(IPC.JOB_LIST, 'boss:jobs:list')
  assert.equal(IPC.JOB_CREATE, 'boss:jobs:create')
  assert.equal(IPC.JOB_UPDATE, 'boss:jobs:update')
  assert.equal(IPC.JOB_DELETE, 'boss:jobs:delete')
  assert.equal(IPC.REVIEW_LIST, 'boss:review:list')
  assert.equal(IPC.REVIEW_OVERRIDE, 'boss:review:override')
  assert.equal(IPC.AUDIT_ACTIONS, 'boss:audit:actions')
  assert.equal(IPC.AUDIT_CDP, 'boss:audit:cdp')
  assert.equal(IPC.EXPORT_EVALUATIONS, 'boss:export:evaluations')
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

test('IPC 通道名无重复', () => {
  const values = Object.values(IPC)
  assert.equal(new Set(values).size, values.length)
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

  // 所有通道都必须在 preload 内联表中出现（防漏加导致渲染层调用 undefined 通道）
  for (const [key, channel] of Object.entries(IPC)) {
    assert.ok(preloadSrc.includes(`${key}: '${channel}'`), `preload ${key} drift`)
  }
})
