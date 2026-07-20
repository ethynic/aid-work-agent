import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import test from 'node:test'
import { DesktopUpdater, type UpdateAdapter } from '../electron/desktopUpdater.js'

class FakeUpdater extends EventEmitter implements UpdateAdapter {
  autoDownload = true
  autoInstallOnAppQuit = true
  feedUrl = ''
  checks = 0
  downloads = 0
  installs = 0
  setFeedURL(options: { provider: 'generic'; url: string }) { this.feedUrl = options.url }
  async checkForUpdates() { this.checks += 1 }
  async downloadUpdate() { this.downloads += 1 }
  quitAndInstall() { this.installs += 1 }
}

test('可用更新必须由用户动作下载并在下载完成后安装', async () => {
  const adapter = new FakeUpdater()
  const states: string[] = []
  const updater = new DesktopUpdater(adapter, '0.0.2', (state) => states.push(state.status))
  updater.configure('https://updates.example/agent')
  assert.equal(adapter.autoDownload, false)
  assert.equal(adapter.autoInstallOnAppQuit, false)
  adapter.emit('update-available', { version: '0.0.3' })
  await updater.download()
  assert.equal(adapter.downloads, 1)
  adapter.emit('update-downloaded', { version: '0.0.3' })
  updater.restartAndInstall()
  assert.equal(adapter.installs, 1)
  assert.deepEqual(states, ['available', 'downloaded'])
})

test('并发检查复用同一动作，禁用状态不会访问适配器', async () => {
  let resolveCheck!: () => void
  const adapter = new FakeUpdater()
  adapter.checkForUpdates = () => new Promise<void>((resolve) => { adapter.checks += 1; resolveCheck = resolve })
  const updater = new DesktopUpdater(adapter, '0.0.2', () => undefined)
  const first = updater.check()
  const second = updater.check()
  assert.equal(adapter.checks, 1)
  assert.equal(first, second)
  resolveCheck()
  await first

  const disabled = new DesktopUpdater(null, '0.0.2', () => undefined, 'development-unsigned')
  await disabled.check()
  assert.equal(disabled.getState().status, 'disabled')
})

test('更新错误只向 renderer 返回通用消息且清除旧版本状态', () => {
  const adapter = new FakeUpdater()
  const updater = new DesktopUpdater(adapter, '0.0.2', () => undefined)
  adapter.emit('update-available', { version: '0.0.3' })
  adapter.emit('error', new Error('download C:\\Users\\secret\\update.exe failed: https://updates.example/?token=sensitive'))
  assert.deepEqual(updater.getState(), {
    status: 'error',
    currentVersion: '0.0.2',
    availableVersion: undefined,
    percent: undefined,
    message: '更新失败，请稍后重试',
  })
})
