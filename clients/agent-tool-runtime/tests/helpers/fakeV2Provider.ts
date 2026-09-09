/**
 * 双消费方 v2 中立操作协议契约假 Provider（P1-D 交付物 2，测试资产）。
 *
 * 注册 weixin_message_send_v2 与 boss_send_to_v2 两个受控写 tool，schema 共享同一字段集
 * （R15 统一操作描述 + permit handle + 测试注入字段），无群/候选人等场景专用参数。
 *
 * 行为契约（宪章 P1-D §4 交付物 2 + 总工 CR 裁决）：
 * - 无 permit handle / 重放 permit 不一致 → PERMIT_REQUIRED（effect none）；
 *   新请求操作 deadline 已过（permit 过期）→ PERMIT_REQUIRED——仅对未进台账的新请求生效
 * - 重复 request_id 先查台账（计划 §5.3）：四项一致性（epoch/hash/permit/target）相符即回
 *   既有回执（同 evidence_ref/run_id，副作用计数不增；deadline 过期不改变重放回执）；
 *   may_have_started/unknown 状态下的重复请求不重新执行
 * - request_id 首见 → 先进 may_have_started 台账（计划 §5.2：提交可能发生的动作前先进入
 *   may_have_started）→ 执行（副作用计数文件 clicks_file）→ 回 applied/verified + evidence_ref
 * - 跨 invocation 复用 target_handle/target_ref → TARGET_BINDING_INVALID（一次性绑定）
 * - payload_hash 非法形态 / 重放不一致 → 拒绝
 * - authorization_epoch 低于已见水位（过期授权/防降级重放）→ AUTHORIZATION_EPOCH_EXPIRED
 * - 状态持久：JSON 状态文件（state_file，tmp+rename 原子写）跨进程记录 request_id → 回执，
 *   跨栈/跨进程幂等真实可验
 * - 故障注入：fault='crash'（副作用后进程退出，状态停在 may_have_started）/
 *   fault='unknown'（副作用后回 unknown 回执）
 */
import { appendFileSync, closeSync, fsyncSync, mkdirSync, openSync, readFileSync, renameSync, writeSync } from 'node:fs'
import path from 'node:path'
import { randomUUID } from 'node:crypto'
import { pathToFileURL } from 'node:url'
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js'
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js'
import { z } from 'zod'

export const V2_TOOL_NAMES = ['weixin_message_send_v2', 'boss_send_to_v2'] as const
export type V2ToolName = (typeof V2_TOOL_NAMES)[number]

/**
 * 双消费方共享的 v2 tool 入参 schema：R15 统一操作描述 + permit handle（Runtime
 * write-authorize 后注入，LLM 不可自填）+ 测试注入字段。两 tool 引用同一对象——
 * 字段名集合必然完全一致（契约测试断言 diff 为空）。
 *
 * 最小必填集 {request_id, target_ref, payload_ref, payload_hash}（幂等台账与载荷引用
 * 的最小身份，schema 层即拒绝缺失）；不含 permit 字段——由 Runtime 授权后注入。
 * 其余字段由 handler 行为层逐一校验（拒绝消息可指明字段，供契约测试逐一断言）。
 */
const SHARED_V2_INPUT_SCHEMA = {
  // R15 统一操作描述（正文不进参数：payload_ref/payload_hash 引用）
  protocol_version: z.number().optional(),
  operation: z.string().optional(),
  provider_key: z.string().optional(),
  target_ref: z.string(),
  target_handle: z.string().optional(),
  target_version: z.string().optional(),
  payload_ref: z.string(),
  payload_hash: z.string(),
  request_id: z.string(),
  delivery_id: z.string().optional(),
  authorization_revision: z.string().optional(),
  authorization_epoch: z.number().optional(),
  resource_key: z.string().optional(),
  deadline_at: z.string().optional(),
  // permit handle（本地已校验许可身份）
  permit_id: z.string().optional(),
  permit_token: z.string().optional(),
  // 测试注入（观测/故障，两 tool 共享）
  state_file: z.string().optional(),
  clicks_file: z.string().optional(),
  fault: z.enum(['crash', 'unknown']).optional(),
} as const

/** 必填字符串字段（缺失 → INVALID_ARGUMENT，逐一可测） */
const REQUIRED_STRING_FIELDS = [
  'operation',
  'provider_key',
  'target_ref',
  'target_handle',
  'target_version',
  'payload_ref',
  'payload_hash',
  'request_id',
  'delivery_id',
  'authorization_revision',
  'resource_key',
  'deadline_at',
] as const

type RequestStatus = 'verified' | 'may_have_started' | 'unknown'

interface ReceiptRecord {
  status: RequestStatus
  request_id: string
  permit_id: string
  payload_hash: string
  authorization_epoch: number
  target_ref: string
  target_handle: string
  receipt: Record<string, unknown>
}

interface V2ProviderState {
  /** request_id → 执行记录（幂等回执台账） */
  requests: Record<string, ReceiptRecord>
  /** target_handle / target_ref → 首次绑定的 request_id（一次性绑定） */
  targets: Record<string, { request_id: string }>
  /** 已见最大 authorization_epoch（低于水位 = 过期授权） */
  epoch_watermark: number
  /** evidence_ref 序号（单调递增） */
  evidence_seq: number
}

/** 未指定 state_file 时的进程内兜底（独立直连调用仍可用，不持久） */
const memoryState: V2ProviderState = { requests: {}, targets: {}, epoch_watermark: 0, evidence_seq: 0 }

function emptyState(): V2ProviderState {
  return { requests: {}, targets: {}, epoch_watermark: 0, evidence_seq: 0 }
}

function loadState(stateFile?: string): V2ProviderState {
  if (!stateFile) return memoryState
  try {
    const parsed = JSON.parse(readFileSync(stateFile, 'utf8')) as Partial<V2ProviderState>
    return {
      requests: parsed.requests ?? {},
      targets: parsed.targets ?? {},
      epoch_watermark: typeof parsed.epoch_watermark === 'number' ? parsed.epoch_watermark : 0,
      evidence_seq: typeof parsed.evidence_seq === 'number' ? parsed.evidence_seq : 0,
    }
  } catch {
    return emptyState()
  }
}

/** tmp + fsync + rename 原子写（与 resultOutbox 同标准）。
 *  单写者（单会话串行）假设：跨进程并发写同一 state_file 属未定义行为。 */
function saveState(stateFile: string | undefined, state: V2ProviderState): void {
  if (!stateFile) return
  mkdirSync(path.dirname(stateFile), { recursive: true })
  const tmp = `${stateFile}.tmp`
  const fd = openSync(tmp, 'w')
  try {
    writeSync(fd, JSON.stringify(state, null, 2))
    fsyncSync(fd)
  } finally {
    closeSync(fd)
  }
  renameSync(tmp, stateFile)
}

function structured(result: Record<string, unknown>): { content: Array<{ type: 'text'; text: string }>; structuredContent: Record<string, unknown> } {
  return { content: [{ type: 'text', text: JSON.stringify(result) }], structuredContent: result }
}

function rejectResult(code: string, message: string, opts: { safeToRetry?: boolean } = {}) {
  return structured({
    success: false,
    code,
    message,
    effect: 'none',
    phase: 'prepared',
    safe_to_retry: opts.safeToRetry ?? false,
    data: null,
    run_id: randomUUID(),
  })
}

/** 双消费方共享的 v2 受控操作处理（两 tool 仅 tool 名不同，契约行为完全一致） */
function handleV2Tool(toolName: V2ToolName, args: Record<string, unknown>): ReturnType<typeof structured> | Promise<never> {
  const str = (key: string): string | undefined => {
    const v = args[key]
    return typeof v === 'string' && v.length > 0 ? v : undefined
  }

  // 1) R15 字段完整性（缺失逐一拒绝）
  for (const field of REQUIRED_STRING_FIELDS) {
    if (!str(field)) return rejectResult('INVALID_ARGUMENT', `缺少必填字段 ${field}`)
  }
  if (args['protocol_version'] !== 2) {
    return rejectResult('INVALID_ARGUMENT', 'protocol_version 必须为 2')
  }
  const epoch = args['authorization_epoch']
  if (typeof epoch !== 'number' || !Number.isInteger(epoch) || epoch < 1) {
    return rejectResult('INVALID_ARGUMENT', 'authorization_epoch 必须为正整数')
  }
  // 2) payload_hash 形态（sha256 hex64——伪造就地拒绝）
  if (!/^[0-9a-f]{64}$/.test(str('payload_hash')!)) {
    return rejectResult('PAYLOAD_HASH_INVALID', 'payload_hash 必须为 sha256 hex64 小写')
  }
  // 3) permit handle 完整性（缺任一半句柄即拒绝；重放一致性在台账四查中判）
  const permitId = str('permit_id')
  const permitToken = str('permit_token')
  if (!permitId || !permitToken) {
    return rejectResult('PERMIT_REQUIRED', 'v2 受控写操作缺少 permit handle', { safeToRetry: true })
  }

  const stateFile = str('state_file')
  const clicksFile = str('clicks_file')
  const requestId = str('request_id')!
  const targetRef = str('target_ref')!
  const targetHandle = str('target_handle')!
  const payloadHash = str('payload_hash')!
  const state = loadState(stateFile)

  // 4) 防重放：先查台账（计划 §5.3——deadline 过期不改变重放回执）。已进台账的
  //    request_id（may_have_started/unknown/verified）四项一致性相符即回既有回执，不重新执行
  const existing = state.requests[requestId]
  if (existing) {
    if (epoch < existing.authorization_epoch) {
      return rejectResult('AUTHORIZATION_EPOCH_EXPIRED', `重放请求授权 epoch 已过期（首次执行 epoch=${existing.authorization_epoch}）`)
    }
    if (payloadHash !== existing.payload_hash) {
      return rejectResult('PAYLOAD_HASH_MISMATCH', '重放请求 payload_hash 与首次执行不一致')
    }
    if (permitId !== existing.permit_id) {
      return rejectResult('PERMIT_REQUIRED', '重放请求 permit 与首次执行不一致')
    }
    if (targetRef !== existing.target_ref || targetHandle !== existing.target_handle) {
      return rejectResult('TARGET_BINDING_INVALID', '重放请求 target 绑定与首次执行不一致')
    }
    return structured(existing.receipt)
  }

  // 5) permit 有效期（仅未进台账的新请求）：操作 deadline 已过 → 许可不可再用
  const deadlineMs = Date.parse(str('deadline_at')!)
  if (!Number.isFinite(deadlineMs) || deadlineMs <= Date.now()) {
    return rejectResult('PERMIT_REQUIRED', '操作 deadline_at 已过（permit 过期）', { safeToRetry: true })
  }

  // 6) 跨 invocation 复用 target_handle / target_ref 拒绝（一次性绑定）
  for (const [bindingKey, bound] of Object.entries(state.targets)) {
    if ((bindingKey === targetHandle || bindingKey === targetRef) && bound.request_id !== requestId) {
      return rejectResult(
        'TARGET_BINDING_INVALID',
        `${bindingKey === targetHandle ? 'target_handle' : 'target_ref'} 已绑定其他 invocation（${bound.request_id}）`,
      )
    }
  }
  // 7) 过期授权：epoch 低于已见水位（授权已推进到更新版本）
  if (epoch < state.epoch_watermark) {
    return rejectResult('AUTHORIZATION_EPOCH_EXPIRED', `授权 epoch 已过期（已见水位 ${state.epoch_watermark}）`)
  }

  // 8) 执行：先进 may_have_started 台账（计划 §5.2：提交可能发生的动作前先进入
  //    may_have_started），再落副作用 clicks（真实「输入」开始），最后收敛终态
  const runId = randomUUID()
  state.evidence_seq += 1
  const evidenceRef = `v2-fake-evidence:${requestId}:${state.evidence_seq}`
  state.epoch_watermark = Math.max(state.epoch_watermark, epoch)
  state.targets[targetHandle] = { request_id: requestId }
  state.targets[targetRef] = { request_id: requestId }
  const recordBase = {
    request_id: requestId,
    permit_id: permitId,
    payload_hash: payloadHash,
    authorization_epoch: epoch,
    target_ref: targetRef,
    target_handle: targetHandle,
  }
  state.requests[requestId] = {
    ...recordBase,
    status: 'may_have_started',
    receipt: {
      success: false,
      code: 'EXECUTION_UNKNOWN',
      message: '执行中断（输入可能已开始），结果未知',
      effect: 'unknown',
      phase: 'may_have_started',
      safe_to_retry: false,
      evidence_ref: evidenceRef,
      data: { tool: toolName },
      run_id: runId,
    },
  }
  saveState(stateFile, state)

  // 副作用（真实「输入」动作）：台账已标 may_have_started 后才允许发生
  if (clicksFile) {
    try {
      appendFileSync(clicksFile, JSON.stringify({ tool: toolName, request_id: requestId, permit_id: permitId, ts: new Date().toISOString() }) + '\n', 'utf8')
    } catch {
      // clicks 文件仅测试观测用，失败不影响执行语义
    }
  }

  // 9) 故障注入：crash（状态停在 may_have_started，重放不重新执行）
  if (args['fault'] === 'crash') {
    process.stderr.write(`[fake-v2-provider] 注入崩溃 request_id=${requestId}\n`)
    setTimeout(() => process.exit(1), 100)
    // 永不返回，等进程退出
    return new Promise<never>(() => {})
  }
  // 故障注入：unknown（副作用后无法证明结果，重放回同一 unknown 回执）
  if (args['fault'] === 'unknown') {
    state.requests[requestId] = {
      ...recordBase,
      status: 'unknown',
      receipt: {
        success: false,
        code: 'EXECUTION_UNKNOWN',
        message: '注入：执行后无法证明结果',
        effect: 'unknown',
        phase: 'unknown',
        safe_to_retry: false,
        evidence_ref: evidenceRef,
        data: { tool: toolName, fault: 'unknown' },
        run_id: runId,
      },
    }
    saveState(stateFile, state)
    return structured(state.requests[requestId]!.receipt)
  }

  // 10) 终态：applied/verified + evidence_ref
  state.requests[requestId] = {
    ...recordBase,
    status: 'verified',
    receipt: {
      success: true,
      code: 'OK',
      message: 'v2 受控操作完成',
      effect: 'applied',
      phase: 'verified',
      safe_to_retry: false,
      evidence_ref: evidenceRef,
      data: {
        tool: toolName,
        request_id: requestId,
        permit_id: permitId,
        payload_ref: str('payload_ref'),
        target_version: str('target_version'),
      },
      run_id: runId,
    },
  }
  saveState(stateFile, state)
  return structured(state.requests[requestId]!.receipt)
}

export async function startFakeV2Provider(): Promise<void> {
  const server = new McpServer({ name: 'fake-v2-provider', version: '0.0.1' })
  for (const name of V2_TOOL_NAMES) {
    server.registerTool(
      name,
      {
        title: name,
        description: `v2 契约样例 ${name}（双消费方共享字段集）`,
        inputSchema: SHARED_V2_INPUT_SCHEMA,
      },
      async (args) => handleV2Tool(name, args as Record<string, unknown>),
    )
  }
  const transport = new StdioServerTransport()
  transport.onclose = () => process.exit(0)
  await server.connect(transport)
  process.stderr.write('[fake-v2-provider] started\n')
}

// 只有被直接执行（node fakeV2Provider.js mcp --stdio）才启动 server
const invokedAsScript = process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href
if (invokedAsScript) {
  startFakeV2Provider().catch((err) => {
    process.stderr.write(`[fake-v2-provider] start failed: ${err}\n`)
    process.exit(1)
  })
}
