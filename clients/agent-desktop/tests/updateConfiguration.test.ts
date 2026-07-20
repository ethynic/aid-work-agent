import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { normalizeUpdateBaseUrl, resolveUpdateConfiguration } from '../electron/updateConfiguration.js'

test('更新源只接受无凭据、query、fragment 的 HTTPS 地址', () => {
  assert.equal(normalizeUpdateBaseUrl('https://updates.example/agent/'), 'https://updates.example/agent')
  for (const value of ['http://updates.example', 'https://u:p@updates.example', 'https://updates.example?q=1', 'https://updates.example#x']) {
    assert.throws(() => normalizeUpdateBaseUrl(value))
  }
})

test('非安装包、冒烟和开发未签名构建绝不启用更新网络', () => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'aidagent-update-config-'))
  try {
    mkdirSync(path.join(root, 'config'))
    writeFileSync(path.join(root, 'config', 'update-config.json'), JSON.stringify({ schemaVersion: 1, channel: 'development-unsigned', updateBaseUrl: null }))
    assert.equal(resolveUpdateConfiguration({ isPackaged: false, smokeMode: false, resourcesPath: root }).enabled, false)
    assert.equal(resolveUpdateConfiguration({ isPackaged: true, smokeMode: true, resourcesPath: root }).enabled, false)
    assert.deepEqual(resolveUpdateConfiguration({ isPackaged: true, smokeMode: false, resourcesPath: root }), { enabled: false, reason: 'development-unsigned' })
  } finally { rmSync(root, { recursive: true, force: true }) }
})

test('只有包内 release 配置能启用固定更新源', () => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'aidagent-update-config-'))
  try {
    mkdirSync(path.join(root, 'config'))
    writeFileSync(path.join(root, 'config', 'update-config.json'), JSON.stringify({ schemaVersion: 1, channel: 'release', updateBaseUrl: 'https://updates.example/agent/' }))
    assert.deepEqual(resolveUpdateConfiguration({ isPackaged: true, smokeMode: false, resourcesPath: root }), { enabled: true, updateBaseUrl: 'https://updates.example/agent' })
  } finally { rmSync(root, { recursive: true, force: true }) }
})

test('缺失或非法的包内 release 配置必须 fail-closed', () => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'aidagent-update-config-'))
  try {
    assert.deepEqual(resolveUpdateConfiguration({ isPackaged: true, smokeMode: false, resourcesPath: root }), {
      enabled: false,
      reason: 'missing-configuration',
    })
    mkdirSync(path.join(root, 'config'))
    const configPath = path.join(root, 'config', 'update-config.json')
    for (const value of [
      '{broken',
      JSON.stringify({ schemaVersion: 2, channel: 'release', updateBaseUrl: 'https://updates.example' }),
      JSON.stringify({ schemaVersion: 1, channel: 'release', updateBaseUrl: 'http://updates.example' }),
      JSON.stringify({ schemaVersion: 1, channel: 'release', updateBaseUrl: 'https://updates.example', extra: true }),
    ]) {
      writeFileSync(configPath, value)
      assert.deepEqual(resolveUpdateConfiguration({ isPackaged: true, smokeMode: false, resourcesPath: root }), {
        enabled: false,
        reason: 'invalid-configuration',
      })
    }
  } finally { rmSync(root, { recursive: true, force: true }) }
})
