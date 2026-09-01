/**
 * 文件日志落盘测试（2026-09-01：脱离 start_runtime.bat 启动也必须有日志）。
 * AIDWORK_RUNTIME_HOME 指向临时目录隔离，不影响真实 %APPDATA%。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { mkdtempSync, readFileSync, rmSync, writeFileSync, existsSync, statSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'

const prevHome = process.env.AIDWORK_RUNTIME_HOME
const home = mkdtempSync(path.join(tmpdir(), 'rt-log-test-'))
process.env.AIDWORK_RUNTIME_HOME = home

// 置于 import 之后：log/config 模块读 env 是每次调用时读（runtimeHomeDir 非常量），
// 但保险起见动态 import 确保模块在本测试内首次加载
const { logInfo, logError, logProviderChunk } = await import('../src/log.js')

const logFile = path.join(home, 'logs', 'runtime.log')

test.after(() => {
  process.env.AIDWORK_RUNTIME_HOME = prevHome
  rmSync(home, { recursive: true, force: true })
})

test('logInfo/logError：stderr 输出 + 文件落盘，格式含时间戳与级别', () => {
  logInfo('hello 世界')
  logError('boom')
  const content = readFileSync(logFile, 'utf-8')
  assert.match(content, /\[runtime\]\[INFO\] hello 世界/)
  assert.match(content, /\[runtime\]\[ERROR\] boom/)
  // ISO 时间戳前缀
  assert.match(content, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/m)
})

test('脱敏兜底：32+ 位长串打码，UUID 放行', () => {
  const uuid = '4193fe95-7d80-4aa1-b809-eaca18602bb9'
  const token = 'a'.repeat(40)
  // 用空格分隔（"token=xxx" 形态会被 redact 连前缀整体打码，属既有行为）
  logInfo(`uuid ${uuid} token ${token}`)
  const content = readFileSync(logFile, 'utf-8')
  assert.ok(content.includes(uuid), 'UUID 应原样保留')
  assert.ok(content.includes('aaaaaa***'), '长 token 应打码')
  assert.ok(!content.includes(token), '完整 token 不得出现')
})

test('logProviderChunk：多行 chunk 逐行转发落盘，空行跳过', () => {
  logProviderChunk('[boss-mcp] server started\n\n[boss-mcp] tool=boss_greet success=true\r\n')
  const content = readFileSync(logFile, 'utf-8')
  assert.match(content, /\[boss-mcp\] server started/)
  assert.match(content, /tool=boss_greet success=true/)
  assert.ok(!/\n\n/.test(content.slice(-200)), '不应写入空行')
})

test('轮转：超 5MB 时 rename 为 runtime.old.log，新文件重新累计', () => {
  // 预置超限文件（复用现有 logFile，先写大内容）
  const big = 'x'.repeat(5 * 1024 * 1024 + 100)
  writeFileSync(logFile, big, 'utf-8')
  logInfo('trigger rotate')
  const oldFile = `${logFile}.old.log`
  assert.ok(existsSync(oldFile), '旧档应存在')
  assert.ok(statSync(oldFile).size > 5 * 1024 * 1024, '旧档应含预置大内容')
  const fresh = readFileSync(logFile, 'utf-8')
  assert.match(fresh, /trigger rotate/)
  assert.ok(!fresh.includes('xxxx'), '新文件不应含旧内容')
})
