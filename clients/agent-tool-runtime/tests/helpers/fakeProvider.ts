/**
 * fakeProvider：最小 MCP stdio server（node dist 后以 `node fakeProvider.js mcp --stdio` 启动）。
 *
 * 行为由 arguments 驱动：
 * - durationMs：模拟工作时长，期间每 stepMs 发一次 progress 通知（有 progressToken 时）
 * - crash: true：200ms 后 process.exit(1)（模拟 Provider 执行中崩溃）
 * - fail: true：返回 success=false 结构化结果
 * - 收到 abort signal（Host 取消）：立即返回 CANCELLED 结构化结果（effect=partial）
 * - v2 写工具样例（*_v2）：接受 runner 注入的 permit handle（缺许可 → PERMIT_REQUIRED），
 *   回 effect/phase/safe_to_retry/evidence_ref；clicks_file 记录每次真实"输入"供副作用断言
 */
import { appendFileSync } from 'node:fs'
import { randomUUID } from 'node:crypto'
import { pathToFileURL } from 'node:url'
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js'
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js'
import { z } from 'zod'

const TOOL_NAMES = [
  'boss_filter',
  'boss_clear_filter',
  'boss_goto',
  'boss_greet',
  'boss_accept_resume',
  'boss_reject_current',
  'boss_interview_demo',
  'weixin_probe',
  'weixin_chat_search',
  'weixin_message_send',
  'weixin_history_read',
  'weixin_unread_list',
  'weixin_probe_v2',
  'weixin_message_send_v2',
] as const

const WRITE_TOOLS = new Set(['boss_greet', 'boss_accept_resume', 'boss_reject_current', 'boss_interview_demo', 'weixin_message_send'])

/** v2 受控写工具样例（宪章 P1-C：接受 permit handle 并回 effect/phase） */
const V2_WRITE_TOOLS = new Set(['weixin_message_send_v2'])

function sleep(ms: number, signal?: AbortSignal): Promise<boolean> {
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve(true), ms)
    signal?.addEventListener('abort', () => {
      clearTimeout(timer)
      resolve(false)
    }, { once: true })
  })
}

export async function startFakeProvider(): Promise<void> {
  const server = new McpServer({ name: 'fake-provider', version: '0.0.1' })

  for (const name of TOOL_NAMES) {
    server.registerTool(
      name,
      {
        title: name,
        description: `fake ${name}`,
        inputSchema: {
          durationMs: z.number().optional(),
          stepMs: z.number().optional(),
          crash: z.boolean().optional(),
          fail: z.boolean().optional(),
          request_id: z.string().optional(),
          permit_id: z.string().optional(),
          permit_token: z.string().optional(),
          clicks_file: z.string().optional(),
        },
      },
      async (args, extra) => {
        const runId = randomUUID()
        const isV2 = name.endsWith('_v2')
        const v2Write = isV2 && V2_WRITE_TOOLS.has(name)
        const effect = isV2
          ? (v2Write ? 'applied' : 'none')
          : WRITE_TOOLS.has(name) ? 'applied' : 'none'
        const progressToken = extra._meta?.progressToken
        const requestId = typeof args.request_id === 'string' ? args.request_id : ''

        if (v2Write && (!args.permit_id || !args.permit_token)) {
          // v2 契约：受控写操作必须携带本地已校验 permit handle
          const result = { success: false, code: 'PERMIT_REQUIRED', message: 'v2 写动作缺少 permit handle', effect: 'none', phase: 'prepared', safe_to_retry: true, data: null, run_id: runId }
          return { content: [{ type: 'text' as const, text: JSON.stringify(result) }], structuredContent: result }
        }

        if (args.crash === true) {
          setTimeout(() => process.exit(1), 200)
          // 永不返回，等进程被杀
          await new Promise(() => {})
        }

        // v2 写工具：真实"输入"动作在此发生（点击/输入后即 may_have_started），落 clicks 文件供副作用断言
        if (v2Write && args.clicks_file) {
          try {
            appendFileSync(args.clicks_file, JSON.stringify({ tool: name, request_id: requestId, permit_id: args.permit_id, ts: new Date().toISOString() }) + '\n', 'utf8')
          } catch {
            // clicks 文件仅测试观测用，失败不影响执行语义
          }
        }

        if (args.fail === true) {
          const result = isV2
            ? { success: false, code: 'FAKE_FAIL', message: 'fake 指示失败', effect: 'none', phase: 'prepared', safe_to_retry: true, data: null, run_id: runId }
            : { success: false, code: 'FAKE_FAIL', message: 'fake 指示失败', effect: 'none', data: null, retryable: false, run_id: runId }
          return { content: [{ type: 'text' as const, text: JSON.stringify(result) }], structuredContent: result }
        }

        const duration = typeof args.durationMs === 'number' ? args.durationMs : 100
        const step = typeof args.stepMs === 'number' ? args.stepMs : Math.max(50, Math.floor(duration / 4))
        const total = Math.max(1, Math.ceil(duration / step))
        for (let i = 1; i <= total; i++) {
          const alive = await sleep(step, extra.signal)
          if (!alive) {
            // v2 取消：写动作可能已开始输入 → unknown/may_have_started（§5.3：不能漏为可重试）
            const result = isV2
              ? { success: false, code: 'CANCELLED', message: 'fake 收到取消', effect: v2Write ? 'unknown' : 'none', phase: v2Write ? 'may_have_started' : 'prepared', safe_to_retry: false, data: { done: i - 1, total }, run_id: runId }
              : { success: false, code: 'CANCELLED', message: 'fake 收到取消', effect: 'partial', data: { done: i - 1, total }, retryable: false, run_id: runId }
            return { content: [{ type: 'text' as const, text: JSON.stringify(result) }], structuredContent: result }
          }
          if (progressToken !== undefined && progressToken !== null) {
            void extra
              .sendNotification({
                method: 'notifications/progress',
                params: { progressToken, progress: i, total, message: `working step ${i}/${total}` },
              })
              .catch(() => {})
          }
        }
        const result = isV2
          ? {
              success: true, code: 'OK', message: 'fake v2 完成', effect, phase: v2Write ? 'verified' : 'prepared',
              safe_to_retry: false, evidence_ref: `fake-evidence:${requestId}`,
              data: { echo_tool: name, request_id: requestId, permit_id: args.permit_id ?? null }, run_id: runId,
            }
          : { success: true, code: 'OK', message: 'fake 完成', effect, data: { echo_tool: name, total }, retryable: false, run_id: runId }
        return { content: [{ type: 'text' as const, text: JSON.stringify(result) }], structuredContent: result }
      },
    )
  }

  const transport = new StdioServerTransport()
  transport.onclose = () => process.exit(0)
  await server.connect(transport)
  process.stderr.write('[fake-provider] started\n')
}

// 只有被直接执行（node fakeProvider.js mcp --stdio）才启动 server
const invokedAsScript = process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href
if (invokedAsScript) {
  startFakeProvider().catch((err) => {
    process.stderr.write(`[fake-provider] start failed: ${err}\n`)
    process.exit(1)
  })
}
