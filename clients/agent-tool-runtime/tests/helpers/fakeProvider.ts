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
  'wecom_probe',
  'wecom_message_send',
  'wecom_read_session',
  'wecom_read_sessions',
  'wecom_session_directory',
  'wecom_unread_list',
] as const

const WRITE_TOOLS = new Set(['boss_greet', 'boss_accept_resume', 'boss_reject_current', 'boss_interview_demo', 'weixin_message_send', 'wecom_message_send'])

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
  const legacyWecom = process.env.FAKE_WECOM_LEGACY === '1'
  const searchDirectory = process.env.FAKE_WECOM_DIRECTORY_SEARCH === '1'
  const searchModeOnly = process.env.FAKE_WECOM_DIRECTORY_SEARCH_NO_TYPE === '1'
  // 代理任务 P3 夹具：FAKE_WECOM_CONFIG_TOOLS=1 模拟新增配置工具的新版 wecom CLI；
  // FAKE_WECOM_CONFIG_DOCTOR_MUTATED=1 模拟 schema 变形（readOnlyHint 丢失），
  // 对应 runtime 端「真实 schema 检查不通过 → 能力串不上报」路径
  const configTools = process.env.FAKE_WECOM_CONFIG_TOOLS === '1'
  const doctorMutated = process.env.FAKE_WECOM_CONFIG_DOCTOR_MUTATED === '1'

  for (const name of TOOL_NAMES) {
    if (legacyWecom && name === 'wecom_read_sessions') continue
    if (name === 'wecom_session_directory' && process.env.FAKE_WECOM_DIRECTORY !== '1') continue
    server.registerTool(
      name,
      {
        title: name,
        description: `fake ${name}`,
        inputSchema: {
          ...(name === 'wecom_session_directory' ? {
            mode: z.enum(searchDirectory || searchModeOnly ? ['list', 'preview', 'resolve', 'search'] : ['list', 'preview', 'resolve']),
            ...(searchDirectory || searchModeOnly ? { query: z.string().optional() } : {}),
            ...(searchDirectory ? { type: z.enum(['contact', 'group', 'any']).optional() } : {}),
          } : {}),
          ...(!legacyWecom ? {
            session: z.string().optional(), sessions: z.array(z.string()).optional(),
            cursor: z.string().optional(), limit: z.number().int().optional(),
          } : {}),
          target_name: z.string().optional(),
          text: z.string().optional(),
          call_log_file: z.string().optional(),
          durationMs: z.number().optional(),
          stepMs: z.number().optional(),
          crash: z.boolean().optional(),
          fail: z.boolean().optional(),
          request_id: z.string().optional(),
          permit_id: z.string().optional(),
          permit_token: z.string().optional(),
          clicks_file: z.string().optional(),
        },
        ...(name === 'wecom_session_directory' ? { annotations: { readOnlyHint: true } } : {}),
      },
      async (args, extra) => {
        if (args.call_log_file) appendFileSync(args.call_log_file, JSON.stringify({ name, session: args.session, target_name: args.target_name }) + '\n')
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

  // 代理任务 P3 配置工具（契约 §2：无参输入；doctor 只读诊断，calibrate 写动作）。
  // 结果沿外层 OperationResult.data 内契约业务报告形状的最小夹具；
  // 输入 schema 保持空对象（真实发现按无参校验），测试控制用环境变量而非未声明参数
  if (configTools) {
    if (doctorMutated) {
      // schema 变形夹具仅用于真实发现检查（能力串不上报路径），调用方不会被派发到该工具
      server.registerTool('wecom_config_doctor', {
        title: 'wecom_config_doctor',
        description: 'fake wecom_config_doctor (mutated schema)',
        inputSchema: { broken: z.string().optional() },
        annotations: { readOnlyHint: false },
      }, async () => {
        const result = { success: false, code: 'FAKE_FAIL', message: 'mutated fixture', effect: 'none', data: null, retryable: false, run_id: randomUUID() }
        return { content: [{ type: 'text' as const, text: JSON.stringify(result) }], structuredContent: result }
      })
    } else {
      server.registerTool('wecom_config_doctor', {
        title: 'wecom_config_doctor',
        description: 'fake wecom_config_doctor',
        inputSchema: {},
        annotations: { readOnlyHint: true },
      }, async () => {
        const runId = randomUUID()
        const data = {
          schema_version: 1,
          self_user_id: '1688fixture',
          success: true,
          checks: [{ name: 'fixture', ok: true, severity: 'gate', detail: 'fake doctor' }],
          runtime_readiness: { ready: true, reason: '' },
          localdb: {
            success: true,
            steps: [{ step: 1, name: 'fixture', ok: true, kind: null, detail: '', elapsed_ms: 1 }],
            localdb_ready: true,
            mode_decision: { mode: 'db', kind: null, version: 'fixture', checked_at: new Date().toISOString() },
            ui_ready: { ready: true, platform_ok: true, interactive_session: true, powershell_available: true },
            state_saved: true,
            guidance: [],
          },
        }
        const result = { success: true, code: 'OK', message: 'fake doctor 完成', effect: 'none', data, retryable: false, run_id: runId }
        return { content: [{ type: 'text' as const, text: JSON.stringify(result) }], structuredContent: result }
      })
    }
    server.registerTool('wecom_config_calibrate', {
      title: 'wecom_config_calibrate',
      description: 'fake wecom_config_calibrate',
      inputSchema: {},
      annotations: { readOnlyHint: false },
    }, async () => {
      const runId = randomUUID()
      const result = {
        success: true, code: 'OK', message: 'fake 标定完成', effect: 'applied',
        data: { schema_version: 1, self_user_id: '1688fixture', title_point: { x: 1310, y: 218 },
          input_point: { x: 1310, y: 976 }, width: 2560, height: 1440, dpi: 96 },
        retryable: false, run_id: runId,
      }
      return { content: [{ type: 'text' as const, text: JSON.stringify(result) }], structuredContent: result }
    })
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
