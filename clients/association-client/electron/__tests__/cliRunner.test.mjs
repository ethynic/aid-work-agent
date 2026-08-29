/**
 * cliRunner 停止行为测试（node:test）。
 *
 * 运行方式：npm test（先 tsc 构建 dist/electron，再 node --test）。
 * 覆盖：
 *   1. collect 注入 ASSOCIATION_STOP_FILE 环境变量；
 *   2. kill() 写停止文件 → 协作式退出的子进程自行退出（优雅停止，不强杀）；
 *   3. kill() 对顽固进程在宽限期后强杀进程树（Windows taskkill /T /F）；
 *   4. close 后停止文件被清理。
 *
 * 用 node -e 脚本模拟 CLI 子进程：CliRunner(cliPath=node, argsPrefix=['-e', script])，
 * collect 追加的 collect/--output 等参数对 -e 脚本无害（落在 argv 里被忽略）。
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { existsSync } from 'node:fs'
import { CliRunner } from '../../dist/electron/cliRunner.js'

/** 协作式子进程：把停止文件路径经 stdout 事件报回，文件出现即退出（码 2）。 */
const COOPERATIVE_SCRIPT = `
const { existsSync } = require('node:fs')
const f = process.env.ASSOCIATION_STOP_FILE || ''
console.log(JSON.stringify({ event: 'log', level: 'INFO', message: 'stopfile:' + f }))
setInterval(() => { if (f && existsSync(f)) process.exit(2) }, 30)
`

/** 顽固子进程：忽略停止文件，永不退出（模拟无停止逻辑的采集进程）。 */
const STUBBORN_SCRIPT = `setInterval(() => {}, 1000)`

/** 等 runner 发出 close 事件（带超时兜底）。 */
function waitClose(runner, timeoutMs = 15000) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('close 事件超时')), timeoutMs)
    runner.on('close', (code) => {
      clearTimeout(timer)
      resolve(code)
    })
  })
}

/** 等 runner 发出满足条件的事件。 */
function waitEvent(runner, predicate, timeoutMs = 5000) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('event 等待超时')), timeoutMs)
    runner.on('event', (evt) => {
      if (predicate(evt)) {
        clearTimeout(timer)
        resolve(evt)
      }
    })
  })
}

test('collect 注入 ASSOCIATION_STOP_FILE，kill() 写文件后子进程优雅退出', async () => {
  const runner = new CliRunner(process.execPath, ['-e', COOPERATIVE_SCRIPT])
  runner.collect(['甲协会'], 'out.xlsx', 'http://localhost', 'token')

  // 子进程报回停止文件路径 → 验证环境变量注入
  const evt = await waitEvent(runner, (e) => String(e.message || '').startsWith('stopfile:'))
  const stopFile = evt.message.slice('stopfile:'.length)
  assert.match(stopFile, /association-cli-stop-/)
  assert.equal(existsSync(stopFile), false, 'kill 前停止文件不应存在')

  const closed = waitClose(runner)
  runner.kill()
  const code = await closed
  assert.equal(code, 2, '子进程检测到停止文件后应以退出码 2 优雅退出')
  assert.equal(existsSync(stopFile), false, 'close 后停止文件应被清理')
})

test('kill() 对顽固进程在宽限期后强杀进程树并清理停止文件', async () => {
  const oldGrace = CliRunner.killGraceMs
  CliRunner.killGraceMs = 200
  try {
    const runner = new CliRunner(process.execPath, ['-e', STUBBORN_SCRIPT])
    runner.collect(['甲协会'], 'out.xlsx', 'http://localhost', 'token')
    const stopFile = runner.stopFilePath

    const closed = waitClose(runner)
    runner.kill()
    // 宽限期内停止文件已写入（协作式信号）
    assert.equal(existsSync(stopFile), true, 'kill 后停止文件应立即写入')
    await closed // 宽限期后 taskkill /T /F（或非 Windows process.kill）终结进程
    assert.equal(existsSync(stopFile), false, 'close 后停止文件应被清理')
  } finally {
    CliRunner.killGraceMs = oldGrace
  }
})

test('无运行进程时 kill() 是 no-op', () => {
  const runner = new CliRunner(process.execPath)
  assert.doesNotThrow(() => runner.kill())
})
