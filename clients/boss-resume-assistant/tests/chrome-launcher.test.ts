import assert from 'node:assert/strict'
import test from 'node:test'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { EventEmitter } from 'node:events'
import {
  parseDevToolsActivePort,
  findChromeExecutable,
  ChromeLauncher,
} from '../src/main/chrome/ChromeLauncher.js'

test('parseDevToolsActivePort：正常解析首行端口', () => {
  assert.deepEqual(parseDevToolsActivePort('51234\n/devtools/browser/abc'), { port: 51234 })
  assert.deepEqual(parseDevToolsActivePort('9222\r\n/devtools/browser/x'), { port: 9222 })
})

test('parseDevToolsActivePort：非法内容 fail-loud', () => {
  assert.throws(() => parseDevToolsActivePort(''), /非法/)
  assert.throws(() => parseDevToolsActivePort('abc\n/path'), /非法/)
  assert.throws(() => parseDevToolsActivePort('0\n/path'), /非法/)
  assert.throws(() => parseDevToolsActivePort('70000\n/path'), /非法/)
})

test('findChromeExecutable：探测到存在的路径；全部不存在时 fail-loud', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'chrome-launcher-'))
  const fake = path.join(tmp, 'chrome.exe')
  fs.writeFileSync(fake, 'fake')
  assert.equal(findChromeExecutable(['/nonexistent/a.exe', fake]), fake)
  assert.throws(() => findChromeExecutable(['/nonexistent/a.exe', '/nonexistent/b.exe']), /未找到 Chrome/)
  fs.rmSync(tmp, { recursive: true, force: true })
})

/** 假 Chrome 子进程：spawn 后从参数解析 user-data-dir 并写入 DevToolsActivePort */
class FakeChild extends EventEmitter {
  exitCode: number | null = null
  pid = 4321
  kill() {
    this.exitCode = 0
    queueMicrotask(() => this.emit('exit', 0))
    return true
  }
}

test('launch：读取 DevToolsActivePort 返回端口，dispose 清理 profile', async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'chrome-profile-root-'))
  const fakeExe = path.join(tmp, 'chrome.exe')
  fs.writeFileSync(fakeExe, 'fake')

  const launcher = new ChromeLauncher({
    chromePathCandidates: [fakeExe],
    profileRoot: tmp,
    pollIntervalMs: 10,
    devToolsPortTimeoutMs: 3000,
    spawnImpl: ((_exe: string, args: string[]) => {
      const child = new FakeChild()
      const profileArg = args.find((a) => a.startsWith('--user-data-dir='))!
      const profileDir = profileArg.slice('--user-data-dir='.length)
      // 模拟 Chrome 异步写入 DevToolsActivePort
      setTimeout(() => {
        fs.writeFileSync(path.join(profileDir, 'DevToolsActivePort'), '56789\n/devtools/browser/fake')
      }, 30)
      return child
    }) as never,
  })

  const instance = await launcher.launch()
  assert.equal(instance.port, 56789)
  assert.equal(instance.httpEndpoint, 'http://127.0.0.1:56789')
  assert.equal(instance.pid, 4321)
  assert.ok(fs.existsSync(instance.profileDir))

  await launcher.dispose()
  assert.ok(!fs.existsSync(instance.profileDir), 'dispose 应清理临时 profile')
  fs.rmSync(tmp, { recursive: true, force: true })
})

test('launch：DevToolsActivePort 超时 fail-loud，且回收已 spawn 的 Chrome 进程', async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'chrome-profile-root-'))
  const fakeExe = path.join(tmp, 'chrome.exe')
  fs.writeFileSync(fakeExe, 'fake')

  const child = new FakeChild()
  const launcher = new ChromeLauncher({
    chromePathCandidates: [fakeExe],
    profileRoot: tmp,
    pollIntervalMs: 10,
    devToolsPortTimeoutMs: 100,
    spawnImpl: (() => child) as never,
  })

  await assert.rejects(() => launcher.launch(), /超时/)
  // 失败的 Chrome 必须被 kill，否则进程残留且下次 launch 覆盖引用后永远无法回收
  assert.equal(child.exitCode, 0, '超时失败应 kill 已 spawn 的 Chrome')
  assert.equal(launcher.isRunning, false, '失败后不应视为运行中')
  // 失败遗留的临时 profile 目录应被清理
  const leftovers = fs.readdirSync(tmp).filter((d) => d.startsWith('session-'))
  assert.deepEqual(leftovers, [], '失败后应清理临时 profile 目录')
  fs.rmSync(tmp, { recursive: true, force: true })
})
