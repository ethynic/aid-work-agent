/**
 * 共享契约套件（clients/shared/mcp-conformance）对 aid-weixin MCP 的端到端校验（上位规范 §11）。
 *
 * 目标：node dist/src/cli/index.js mcp --stdio。
 * 套件位置：编译后本测试在 dist/tests/，到 clients/shared 的相对路径是 ../../../shared/。
 *
 * busyProbe 用测试 hook AID_WEIXIN_TEST_HANG=1（env 注入，替代 BOSS 的悬挂 CDP 端口）
 * 让第一个 call 悬挂，验证并发 BUSY 与取消后锁释放。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const DIST_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const CLI = path.join(DIST_ROOT, 'src', 'cli', 'index.js')

test('mcp-conformance 套件全项通过', { timeout: 120000 }, async () => {
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
      spawn: { command: string; args: string[]; env?: Record<string, string> }
      expectTools?: string[]
      callProbe: { name: string; arguments: Record<string, unknown> }
      invalidProbe: { name: string; arguments: Record<string, unknown> }
      busyProbe?: {
        makeSpawn: (port: number) => { command: string; args: string[]; env?: Record<string, string> }
        tool: { name: string; arguments: Record<string, unknown> }
      }
    }) => Promise<ConformanceReport>
  }

  const report = await runConformance({
    requireBase: import.meta.url,
    spawn: { command: process.execPath, args: [CLI, 'mcp', '--stdio'] },
    expectTools: ['weixin_probe', 'weixin_chat_search', 'weixin_message_send', 'weixin_history_read', 'weixin_session_observe', 'weixin_unread_list', 'weixin_name_resolve', 'weixin_message_send_v2'],
    // 无微信环境：probe 快速返回结构化结果（OK 或环境违规，均满足契约）
    callProbe: { name: 'weixin_probe', arguments: {} },
    invalidProbe: { name: 'weixin_probe', arguments: { verbose: 'yes' } },
    // 测试 hook 悬挂 + 独立互斥 scope，验证并发 BUSY 与取消后锁释放
    busyProbe: {
      makeSpawn: () => ({
        command: process.execPath,
        args: [CLI, 'mcp', '--stdio'],
        env: { AID_WEIXIN_TEST_HANG: '1', AID_WEIXIN_MUTEX_SCOPE: `conformance-busy-${process.pid}` },
      }),
      tool: { name: 'weixin_probe', arguments: {} },
    },
  })
  assert.equal(
    report.failed,
    0,
    `契约套件存在失败项：${report.results.filter((r) => !r.ok).map((r) => `${r.name}: ${r.detail}`).join(' | ')}`,
  )
  assert.ok(report.passed >= 8, `应至少 8 项检查（实际 ${report.passed}）`)
})

test('mcp-conformance 套件 CLI 模式可独立运行', { timeout: 60000 }, async () => {
  const { execFileSync } = await import('node:child_process')
  const suitePath = path.resolve(DIST_ROOT, '..', '..', 'shared', 'mcp-conformance', 'suite.mjs')
  execFileSync(
    process.execPath,
    [
      suitePath,
      // SDK 从 aid-weixin 包的 node_modules 解析（套件自身位置上没有 SDK）
      '--require-base',
      path.join(DIST_ROOT, '..', 'package.json'),
      '--expect-tools',
      'weixin_probe,weixin_chat_search,weixin_message_send,weixin_history_read,weixin_session_observe,weixin_unread_list,weixin_name_resolve,weixin_message_send_v2',
      '--call',
      'weixin_probe:{}',
      '--invalid',
      'weixin_probe:{"verbose":"yes"}',
      '--',
      process.execPath,
      CLI,
      'mcp',
      '--stdio',
    ],
    { stdio: 'pipe' },
  )
  // exit code 0 = 全部通过（execFileSync 非零会 throw）
})
