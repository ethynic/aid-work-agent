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
import { serializeMessage } from '@modelcontextprotocol/sdk/shared/stdio.js'
import { TOOL_DEFS } from './toolDefs.js'
import { providerVersion } from './manifest.js'
import { attachReceipt1_1 } from '../main/operations/receipt.js'
import { OPERATIONS } from '../main/operations/index.js'
import { failResult, type OperationResult } from '../main/operations/types.js'

/**
 * server instructions（标准 §5.4：前 512 字符内含关键前提、副作用和数量限制）
 */
const INSTRUCTIONS =
  'BOSS 直聘招聘操作 Provider。前提：仅 Windows；需要已登录 BOSS 直聘的 Chrome 以调试端口（默认 9222）运行；' +
  '需要未锁屏的交互桌面，操作期间借用真实鼠标，请勿移动鼠标、勿遮挡 BOSS 窗口。' +
  '外部写副作用与单次硬上限：boss_greet 打招呼（最大 3 人）、boss_greet_selected 选定卡片打招呼（固定 1 人）、boss_accept_resume 同意接收附件简历（最大 1 人）、' +
  'boss_reject_current 标记不合适（固定 1 人）；boss_filter/boss_clear_filter 只改页面筛选状态；' +
  'boss_interview_demo 只填表单不发送（演示）。' +
  '结果约定：structuredContent 含 success/code/effect/data/retryable/run_id；' +
  'effect=unknown 或 partial 时系统不会自动重试写动作，请人工查看页面。'

/** stderr 日志（脱敏）：不含参数值/页面内容，只记操作与结果元数据 */
function logLine(msg: string): void {
  process.stderr.write(`[boss-mcp] ${msg}\n`)
}

/**
 * 执行回执标准 1.1 协商态：Runtime 仅在服务端领取响应附加 receipt_version 时，经 MCP
 * tools/call 请求级 _meta["aidwork/receipt_version"] 传入（协议探针实证 SDK 1.30.0 stdio
 * 透传 extra._meta）。未协商（旧 Runtime/无能力/直调）返回 undefined——沿既有回执，
 * data 无 1.1 字段、不截图。导出以便单测锁定协商/未协商两态行为。
 */
export function negotiatedReceiptVersion(meta: unknown): '1.1' | undefined {
  const value = (meta as Record<string, unknown> | undefined)?.['aidwork/receipt_version']
  return value === '1.1' ? '1.1' : undefined
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

// Match the Host's existing maximum unread MCP stdio frame (128 MiB).
const MCP_STDIO_RESPONSE_BUDGET = 128 * 1024 * 1024

export function encodeOperationResult(result: OperationResult, requestId: string | number,
                                      budget = MCP_STDIO_RESPONSE_BUDGET) {
  const encode = (value: OperationResult) => ({
    content: [{ type: 'text' as const, text: JSON.stringify(value) }],
    structuredContent: value as unknown as Record<string, unknown>,
  })
  const response = encode(result)
  if (result.data.receipt_version !== '1.1' || result.data.screenshot === undefined) return response
  const frame = serializeMessage({ jsonrpc: '2.0', id: requestId, result: response })
  if (Buffer.byteLength(frame) <= budget) return response

  const { screenshot, ...data } = result.data
  return encode({ ...result, data })
}

export interface McpServerOptions {
  /** Chrome 调试端口（默认 9222）；tool schema 不暴露此参数，只在 server 启动时指定 */
  cdpPort?: number
}

/**
 * 注册全部工具（stdio 服务与协议测试共用同一注册入口，保证测试走实际 SDK 注册路径）。
 */
export function registerBossTools(server: McpServer, opts: McpServerOptions, running: { current: string | null }): void {
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
        const { _boss_target_ref: browserTargetRef, _boss_expected_contact_ref: expectedContactRef, ...operationArgs } = args ?? {}
        const startedAt = Date.now()
        const progressToken = extra._meta?.progressToken
        const executionReceipt = negotiatedReceiptVersion(extra._meta)
        const outcome = await runSingleFlight(running, def.name, () =>
          operation.execute(operationArgs, {
            ...(typeof browserTargetRef === 'string' ? { browserTargetRef } : {}),
            ...(typeof expectedContactRef === 'string' ? { expectedContactRef } : {}),
            ...(executionReceipt ? { executionReceipt } : {}),
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
        const result = executionReceipt
          ? await attachReceipt1_1(outcome, { step: outcome.code === 'BUSY' ? 'dispatch' : 'execute' })
          : outcome
        logLine(
          `tool=${def.name} run_id=${result.run_id} success=${result.success} code=${result.code} ` +
            `effect=${result.effect} elapsed_ms=${Date.now() - startedAt}`,
        )
        return encodeOperationResult(result, extra.requestId)
      },
    )
  }
}

export async function startMcpServer(opts: McpServerOptions = {}): Promise<void> {
  const server = new McpServer(
    { name: 'boss-recruiting', version: providerVersion() },
    { instructions: INSTRUCTIONS },
  )
  /** 进程级单飞锁：写动作与读动作都单飞 */
  const running: { current: string | null } = { current: null }

  registerBossTools(server, opts, running)

  const transport = new StdioServerTransport()
  // Host 关闭 stdin（transport close）时退出进程，不留孤儿
  transport.onclose = () => {
    logLine('stdin closed, exiting')
    process.exit(0)
  }
  await server.connect(transport)
  logLine(`boss-recruiting MCP server started (cdpPort=${opts.cdpPort ?? 9222}, tools=${TOOL_DEFS.length})`)
}
