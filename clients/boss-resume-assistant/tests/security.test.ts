import assert from 'node:assert/strict'
import test from 'node:test'
import path from 'node:path'
import { createSecureWebPreferences, resolveRendererFile } from '../src/main/security.js'

test('webPreferences 全开安全：四项关键配置', () => {
  const prefs = createSecureWebPreferences('/fake/preload.cjs')
  assert.equal(prefs.nodeIntegration, false)
  assert.equal(prefs.contextIsolation, true)
  assert.equal(prefs.sandbox, true)
  assert.equal(prefs.webSecurity, true)
  assert.equal(prefs.allowRunningInsecureContent, false)
  assert.equal(prefs.preload, '/fake/preload.cjs')
})

test('resolveRendererFile: 有扩展名直接取文件', () => {
  const root = path.resolve('/tmp/renderer')
  const f = resolveRendererFile(root, '/assets/app.js')
  assert.ok(f?.endsWith(path.join('assets', 'app.js')))
})

test('resolveRendererFile: 无扩展名回退 index.html（SPA）', () => {
  const root = path.resolve('/tmp/renderer')
  const f = resolveRendererFile(root, '/some/spa/route')
  assert.equal(f, path.resolve(root, 'index.html'))
})

test('resolveRendererFile: 拒绝点段穿越', () => {
  const root = path.resolve('/tmp/renderer')
  assert.equal(resolveRendererFile(root, '/../../../etc/passwd'), null)
})

test('resolveRendererFile: 拒绝编码穿越', () => {
  const root = path.resolve('/tmp/renderer')
  assert.equal(resolveRendererFile(root, '/%2e%2e/secret'), null)
})
