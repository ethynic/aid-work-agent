/**
 * fakeProvider：最小 MCP stdio server（node dist 后以 `node fakeProvider.js mcp --stdio` 启动）。
 *
 * 行为由 arguments 驱动：
 * - durationMs：模拟工作时长，期间每 stepMs 发一次 progress 通知（有 progressToken 时）
 * - crash: true：200ms 后 process.exit(1)（模拟 Provider 执行中崩溃）
 * - fail: true：返回 success=false 结构化结果
 * - 收到 abort signal（Host 取消）：立即返回 CANCELLED 结构化结果（effect=partial）
 */
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
] as const

const WRITE_TOOLS = new Set(['boss_greet', 'boss_accept_resume', 'boss_reject_current', 'boss_interview_demo'])

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
        },
      },
      async (args, extra) => {
        const runId = randomUUID()
        const effect = WRITE_TOOLS.has(name) ? 'applied' : 'none'
        const progressToken = extra._meta?.progressToken

        if (args.crash === true) {
          setTimeout(() => process.exit(1), 200)
          // 永不返回，等进程被杀
          await new Promise(() => {})
        }

        if (args.fail === true) {
          const result = { success: false, code: 'FAKE_FAIL', message: 'fake 指示失败', effect: 'none', data: null, retryable: false, run_id: runId }
          return { content: [{ type: 'text' as const, text: JSON.stringify(result) }], structuredContent: result }
        }

        const duration = typeof args.durationMs === 'number' ? args.durationMs : 100
        const step = typeof args.stepMs === 'number' ? args.stepMs : Math.max(50, Math.floor(duration / 4))
        const total = Math.max(1, Math.ceil(duration / step))
        for (let i = 1; i <= total; i++) {
          const alive = await sleep(step, extra.signal)
          if (!alive) {
            const result = { success: false, code: 'CANCELLED', message: 'fake 收到取消', effect: 'partial', data: { done: i - 1, total }, retryable: false, run_id: runId }
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
        const result = { success: true, code: 'OK', message: 'fake 完成', effect, data: { echo_tool: name, total }, retryable: false, run_id: runId }
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
