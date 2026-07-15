import assert from 'node:assert/strict'
import { readFile, stat } from 'node:fs/promises'
import path from 'node:path'
import test from 'node:test'

test('桌面壳装载真实 Agent renderer，而不是 Phase 0 静态 Spike', async () => {
  const html = await readFile(path.resolve('dist/renderer/index.html'), 'utf8')
  assert.match(html, /<div id="app"><\/div>/)
  assert.match(html, /\/assets\/[^"']+\.js/)
  assert.doesNotMatch(html, /Phase 0|stream-path|upload-path/)
  await stat(path.resolve('dist/renderer/desktop-module-manifest.json'))
})

test('preload 只暴露版本化白名单能力，不暴露任意 channel 调用入口', async () => {
  const preload = await readFile(path.resolve('dist/electron/preload.cjs'), 'utf8')
  assert.match(preload, /version:\s*1/)
  assert.match(preload, /Object\.freeze/)
  assert.match(preload, /desktop:credentials:hydrate/)
  assert.match(preload, /desktop:save-download/)
  assert.doesNotMatch(preload, /send\s*:\s*|invoke\s*:\s*ipcRenderer\.invoke/)
})

test('sensitive IPC validates sender and download validates redirects and size before atomic replacement', async () => {
  const main = await readFile(path.resolve('dist/electron/main.js'), 'utf8')
  assert.match(main, /assertTrustedSender\(event\)/)
  assert.match(main, /redirect:\s*['"]manual['"]/)
  assert.match(main, /readDownloadBody\(response\)/)
  assert.match(main, /\.aidagent-/)
  assert.match(main, /renameSync/)
})
