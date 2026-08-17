/**
 * 共享契约套件（clients/shared/mcp-conformance）对 boss MCP 的端到端校验（规格 m02 §9）。
 *
 * 目标：node dist/src/cli/index.js mcp --stdio。
 * 套件位置：编译后本测试在 dist/tests/，到 clients/shared 的相对路径是 ../../../shared/。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import net from 'node:net'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const DIST_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const CLI = path.join(DIST_ROOT, 'src', 'cli', 'index.js')

async function freePort(): Promise<number> {
  const srv = net.createServer()
  await new Promise<void>((r) => srv.listen(0, '127.0.0.1', r))
  const port = (srv.address() as net.AddressInfo).port
  await new Promise<void>((r) => srv.close(() => r()))
  return port
}

test('mcp-conformance 套件全项通过', async () => {
  const suiteUrl = new URL('../../../shared/mcp-conformance/suite.mjs', import.meta.url)
  // 类型与 clients/shared/mcp-conformance/suite.d.ts 对齐（.mjs 动态 import 不走 TS 模块解析，这里显式声明）
  interface CheckResult {
    name: string
    ok: boolean
    detail: string
  }
  interface ConformanceReport {
    passed: number
    failed: number
    results: CheckResult[]
  }
  const { runConformance } = (await import(suiteUrl.href)) as {
    runConformance: (opts: {
      requireBase?: string
      spawn: { command: string; args: string[] }
      expectTools?: string[]
      callProbe: { name: string; arguments: Record<string, unknown> }
      invalidProbe: { name: string; arguments: Record<string, unknown> }
      busyProbe?: {
        makeSpawn: (port: number) => { command: string; args: string[] }
        tool: { name: string; arguments: Record<string, unknown> }
      }
    }) => Promise<ConformanceReport>
  }

  const baseArgs = [CLI, 'mcp', '--stdio', '--cdp-port', String(await freePort())]
  const report = await runConformance({
    requireBase: import.meta.url,
    spawn: { command: process.execPath, args: baseArgs },
    expectTools: [
      'boss_filter',
      'boss_clear_filter',
      'boss_goto',
      'boss_greet',
      'boss_accept_resume',
      'boss_reject_current',
      'boss_interview_demo',
      'boss_send_to',
      'boss_send_current',
      'boss_list_jobs',
      'boss_select_job',
      'boss_read_resume',
    ],
    // 无 Chrome：空闲端口 → 快速返回结构化 CHROME_UNAVAILABLE
    callProbe: { name: 'boss_goto', arguments: { target: 'chat' } },
    invalidProbe: { name: 'boss_goto', arguments: { target: 'nope' } },
    // 悬挂端口让第一个 call 卡在 connect，验证并发 BUSY 与取消后锁释放
    busyProbe: {
      makeSpawn: (port) => ({
        command: process.execPath,
        args: [CLI, 'mcp', '--stdio', '--cdp-port', String(port)],
      }),
      tool: { name: 'boss_goto', arguments: { target: 'chat' } },
    },
  })
  assert.equal(
    report.failed,
    0,
    `契约套件存在失败项：${report.results.filter((r) => !r.ok).map((r) => `${r.name}: ${r.detail}`).join(' | ')}`,
  )
  assert.ok(report.passed >= 8, `应至少 8 项检查（实际 ${report.passed}）`)
})

test('mcp-conformance 套件 CLI 模式可独立运行', async () => {
  const { execFileSync } = await import('node:child_process')
  const suitePath = path.resolve(DIST_ROOT, '..', '..', 'shared', 'mcp-conformance', 'suite.mjs')
  const port = await freePort()
  execFileSync(
    process.execPath,
    [
      suitePath,
      // SDK 从 boss 包的 node_modules 解析（套件自身位置上没有 SDK）
      '--require-base',
      path.join(DIST_ROOT, '..', 'package.json'),
      '--expect-tools',
      'boss_filter,boss_clear_filter,boss_goto,boss_greet,boss_accept_resume,boss_reject_current,boss_interview_demo,boss_send_to,boss_send_current,boss_list_jobs,boss_select_job,boss_read_resume',
      '--call',
      'boss_goto:{"target":"chat"}',
      '--invalid',
      'boss_goto:{"target":"nope"}',
      '--',
      process.execPath,
      CLI,
      'mcp',
      '--stdio',
      '--cdp-port',
      String(port),
    ],
    { stdio: 'pipe' },
  )
  // exit code 0 = 全部通过（execFileSync 非零会 throw）
})
