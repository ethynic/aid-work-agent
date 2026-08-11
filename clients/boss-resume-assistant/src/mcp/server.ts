/**
 * BOSS MCP server（stdio，实施规格 m02 §6 / 标准 §3.3）。
 *
 * - stdout 只承载 MCP 协议：本进程在 mcp 模式下禁止任何 console.log，日志只写 stderr
 *   且脱敏（不输出简历正文/坐标/CDP payload，只记 tool/run_id/code/effect/时长）。
 * - 单飞锁：进程级只允许一个 tool call 在执行（共享真实鼠标/页面），
 *   并发第二个立即返回 BUSY 结构化结果（不是 MCP 协议错误）。
 * - 写动作零重试：本层与 operation 层都没有任何自动重试逻辑。
 * - Host 关闭 stdin 时进程退出（不留孤儿）。
 */
import { randomUUID } from 'node:crypto'
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js'
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js'
import { TOOL_DEFS } from './toolDefs.js'
import { providerVersion } from './manifest.js'
import { OPERATIONS } from '../main/operations/index.js'
import { failResult, type OperationResult } from '../main/operations/types.js'

/**
 * server instructions（标准 §5.4：前 512 字符内含关键前提、副作用和数量限制）
 */
const INSTRUCTIONS =
  'BOSS 直聘招聘操作 Provider。前提：仅 Windows；需要已登录 BOSS 直聘的 Chrome 以调试端口（默认 9222）运行；' +
  '需要未锁屏的交互桌面，操作期间借用真实鼠标，请勿移动鼠标、勿遮挡 BOSS 窗口。' +
  '外部写副作用与单次硬上限：boss_greet 打招呼（最大 3 人）、boss_accept_resume 同意接收附件简历（最大 1 人）、' +
  'boss_reject_current 标记不合适（固定 1 人）；boss_filter/boss_clear_filter 只改页面筛选状态；' +
  'boss_interview_demo 只填表单不发送（演示）。' +
  '结果约定：structuredContent 含 success/code/effect/data/retryable/run_id；' +
  'effect=unknown 或 partial 时系统不会自动重试写动作，请人工查看页面。'

/** stderr 日志（脱敏）：不含参数值/页面内容，只记操作与结果元数据 */
function logLine(msg: string): void {
  process.stderr.write(`[boss-mcp] ${msg}\n`)
}

/** 单飞锁执行器（导出以便单测）：占用中立即返回 BUSY 结构化结果 */
export async function runSingleFlight<T extends OperationResult>(
  running: { current: string | null },
  toolName: string,
  fn: () => Promise<T>,
): Promise<T | OperationResult> {
  if (running.current !== null) {
    return failResult(
      randomUUID(),
      'BUSY',
      `另一个操作「${running.current}」正在执行中（共享真实鼠标/页面，单飞），请等其完成后重试`,
      'none',
    )
  }
  running.current = toolName
  try {
    return await fn()
  } finally {
    running.current = null
  }
}

export interface McpServerOptions {
  /** Chrome 调试端口（默认 9222）；tool schema 不暴露此参数，只在 server 启动时指定 */
  cdpPort?: number
}

export async function startMcpServer(opts: McpServerOptions = {}): Promise<void> {
  const server = new McpServer(
    { name: 'boss-recruiting', version: providerVersion() },
    { instructions: INSTRUCTIONS },
  )
  /** 进程级单飞锁：写动作与读动作都单飞 */
  const running: { current: string | null } = { current: null }

  for (const def of TOOL_DEFS) {
    server.registerTool(
      def.name,
      {
        title: def.title,
        description: def.description,
        inputSchema: def.zodShape,
        annotations: def.annotations,
      },
      async (args, extra) => {
        const operation = OPERATIONS[def.name]!.operation
        const startedAt = Date.now()
        const progressToken = extra._meta?.progressToken
        const result = await runSingleFlight(running, def.name, () =>
          operation.execute(args ?? {}, {
            signal: extra.signal,
            cdpPort: opts.cdpPort,
            progress: (p) => {
              // progress 是可选增强：Host 不传 progressToken 则跳过，最终结果不受影响
              if (progressToken === undefined || progressToken === null) return
              void extra
                .sendNotification({
                  method: 'notifications/progress',
                  params: {
                    progressToken,
                    progress: p.current ?? 0,
                    ...(p.total !== undefined ? { total: p.total } : {}),
                    message: `${p.stage} ${p.message}`.trim(),
                  },
                })
                .catch(() => {})
            },
          }),
        )
        logLine(
          `tool=${def.name} run_id=${result.run_id} success=${result.success} code=${result.code} ` +
            `effect=${result.effect} elapsed_ms=${Date.now() - startedAt}`,
        )
        return {
          content: [{ type: 'text' as const, text: JSON.stringify(result) }],
          structuredContent: result as unknown as Record<string, unknown>,
        }
      },
    )
  }

  const transport = new StdioServerTransport()
  // Host 关闭 stdin（transport close）时退出进程，不留孤儿
  transport.onclose = () => {
    logLine('stdin closed, exiting')
    process.exit(0)
  }
  await server.connect(transport)
  logLine(`boss-recruiting MCP server started (cdpPort=${opts.cdpPort ?? 9222}, tools=${TOOL_DEFS.length})`)
}
