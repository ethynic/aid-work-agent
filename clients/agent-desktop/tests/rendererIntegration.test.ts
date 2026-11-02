import assert from 'node:assert/strict'
import { readFile, readdir, stat } from 'node:fs/promises'
import path from 'node:path'
import test from 'node:test'
import { mapSchemeRequest } from '../electron/security.js'

test('Runtime品牌图标进入真实renderer assets且能经桌面自定义协议读取', async () => {
  const assets = path.resolve('dist/renderer/assets')
  const files = await readdir(assets)
  const icons = files.filter(file => /^runtime-.+\.ico$/.test(file))
  assert.equal(icons.length, 1, 'Public-root favicon paths are rejected by the desktop protocol; the renderer must bundle its brand asset')
  assert.deepEqual(mapSchemeRequest(`aidagent://app/assets/${icons[0]}`), { kind: 'file', relativePath: `assets/${icons[0]}` })
  await stat(path.join(assets, icons[0]!))
  const entry = files.find(file => /^desktop-.+\.js$/.test(file))!
  assert.ok((await readFile(path.join(assets, entry), 'utf8')).includes(`/assets/${icons[0]}`))
})

test('桌面壳装载独立 Desktop renderer，而不是 Web 回退入口', async () => {
  const html = await readFile(path.resolve('dist/renderer/index.html'), 'utf8')
  assert.match(html, /<div id="app"><\/div>/)
  assert.match(html, /\/assets\/[^"']+\.js/)
  assert.doesNotMatch(html, /Phase 0|stream-path|upload-path/)
  await stat(path.resolve('dist/renderer/desktop-module-manifest.json'))
  const modules = JSON.parse(await readFile(path.resolve('dist/renderer/desktop-module-manifest.json'), 'utf8')) as string[]
  assert.ok(modules.some((module) => module.replaceAll('\\', '/').endsWith('desktop/main.ts')))
  assert.ok(modules.every((module) => !/(^|\/)web\//.test(module.replaceAll('\\', '/'))))
})

test('preload 只暴露版本化白名单能力，不暴露任意 channel 调用入口', async () => {
  const preload = await readFile(path.resolve('dist/electron/preload.cjs'), 'utf8')
  assert.match(preload, /version:\s*3/)
  assert.match(preload, /Object\.freeze/)
  assert.match(preload, /desktop:credentials:hydrate/)
  assert.match(preload, /desktop:startup:get-state/)
  assert.match(preload, /desktop:save-download/)
  assert.match(preload, /desktop:update:get-state/)
  assert.match(preload, /desktop:update:download/)
  assert.doesNotMatch(preload, /AID_AGENT_UPDATE_BASE_URL|updateBaseUrl/)
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
