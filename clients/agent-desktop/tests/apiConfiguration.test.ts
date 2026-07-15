import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { resolveApiConfiguration } from '../electron/apiConfiguration.js'

function fixture() {
  const root = mkdtempSync(path.join(os.tmpdir(), 'aidagent-config-'))
  const userDataPath = path.join(root, 'user')
  const resourcesPath = path.join(root, 'resources')
  mkdirSync(path.join(resourcesPath, 'config'), { recursive: true })
  return { root, userDataPath, resourcesPath }
}

function writeConfig(file: string, apiBaseUrl: string) {
  mkdirSync(path.dirname(file), { recursive: true })
  writeFileSync(file, JSON.stringify({ schemaVersion: 1, apiBaseUrl }))
}

test('配置优先级为环境变量、用户配置、包内默认值', () => {
  const f = fixture()
  try {
    writeConfig(path.join(f.resourcesPath, 'config', 'desktop-config.json'), 'https://packaged.example/api')
    writeConfig(path.join(f.userDataPath, 'desktop-config.json'), 'https://user.example/api')
    assert.deepEqual(resolveApiConfiguration({ ...f, environmentValue: 'https://env.example/api/' }).source, 'environment')
    rmSync(path.join(f.userDataPath, 'desktop-config.json'))
    assert.deepEqual(resolveApiConfiguration(f).source, 'packaged')
    assert.equal(resolveApiConfiguration(f).source, 'user')
  } finally { rmSync(f.root, { recursive: true, force: true }) }
})

test('首次启动从包内默认值原子初始化用户配置', () => {
  const f = fixture()
  try {
    writeConfig(path.join(f.resourcesPath, 'config', 'desktop-config.json'), 'https://agent2.aidingyi.cn/api')
    const result = resolveApiConfiguration(f)
    assert.equal(result.apiBaseUrl, 'https://agent2.aidingyi.cn/api')
    assert.deepEqual(JSON.parse(readFileSync(result.userConfigPath, 'utf8')), {
      schemaVersion: 1, apiBaseUrl: 'https://agent2.aidingyi.cn/api',
    })
  } finally { rmSync(f.root, { recursive: true, force: true }) }
})

test('用户配置非法时明确失败且不回退包内默认值', () => {
  const f = fixture()
  try {
    writeConfig(path.join(f.resourcesPath, 'config', 'desktop-config.json'), 'https://packaged.example/api')
    writeConfig(path.join(f.userDataPath, 'desktop-config.json'), 'http://unsafe.example/api')
    assert.throws(() => resolveApiConfiguration(f), /HTTPS/)
  } finally { rmSync(f.root, { recursive: true, force: true }) }
})

test('缺少包内配置或环境变量非法时明确失败', () => {
  const f = fixture()
  try {
    assert.throws(() => resolveApiConfiguration(f), /packaged desktop config unreadable/)
    assert.throws(() => resolveApiConfiguration({ ...f, environmentValue: 'https://u:p@example.com/api' }), /凭证/)
  } finally { rmSync(f.root, { recursive: true, force: true }) }
})
