/**
 * node:test 收集器。递归收集 tests 下所有 .test.mjs。
 * 照搬 agent-tool-runtime 范式：零第三方测试框架。
 *
 * wecomGroupPoll / wecomSendStatus 会拉起真实 wecom-cli Provider（跨进程互斥锁
 * 「另一个 aid-wecom 进程正在操作企业微信」按会话全局命名），两个文件并行跑会互斥
 * 失败（提速专项 2026-11-13 实证：8 实例并发 7 败）。串行跑这两个文件，其余照旧并行。
 */
import { spawnSync } from 'node:child_process'
import { readdirSync, statSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const root = path.resolve(__dirname, '..')

function collectTestFiles(dir, acc = []) {
  for (const entry of readdirSync(dir)) {
    const full = path.join(dir, entry)
    const st = statSync(full)
    if (st.isDirectory()) {
      if (entry === 'fixtures') continue
      collectTestFiles(full, acc)
    } else if (entry.endsWith('.test.mjs')) {
      acc.push(full)
    }
  }
  return acc
}

const files = collectTestFiles(path.join(root, 'tests'))
if (files.length === 0) {
  console.error('no test files matched tests/**/*.test.mjs')
  process.exit(1)
}

// wecom-cli Provider 走会话级跨进程互斥：拉真实 Provider 的文件串行，避免互斥误报
const serialSuites = new Set(['wecomGroupPoll.test.mjs', 'wecomSendStatus.test.mjs'])
const serial = files.filter((file) => serialSuites.has(path.basename(file)))
const parallel = files.filter((file) => !serialSuites.has(path.basename(file)))

console.log(`running ${files.length} test file(s) (serial: ${serial.length}, parallel: ${parallel.length})`)
for (const [batch, options] of [[serial, ['--test-concurrency=1']], [parallel, []]]) {
  if (batch.length === 0) continue
  const result = spawnSync(process.execPath, ['--test', ...options, ...batch], { stdio: 'inherit', cwd: root })
  if (result.status !== 0) process.exit(result.status ?? 1)
}
