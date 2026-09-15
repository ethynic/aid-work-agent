/**
 * 会话任务真实进程联测专用 weixin Provider 桩（C3 验收：隔离真实 DB + 真实
 * Runtime 子进程 + fake Provider）。
 *
 * 纯 Node 手写 MCP stdio（JSON-RPC 2.0 按行分帧，无 SDK 依赖），受控文件驱动：
 * - $STUB_HOME/observe-state.json：{ binding_id, messages: [{sender,text,id}] }——
 *   每次观察现读现算（水位过滤），测试进程可随时改写以推进剧本；
 * - $STUB_HOME/sends.jsonl：每次 weixin_message_send_v2 追加一行
 *   {ts, request_id, payload_ref, permit_id}，供发送次数/幂等断言。
 * 真机 BLOCKED 语义保留：observe-state.json 缺失 → unavailable。
 */
import { appendFileSync, existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { createInterface } from 'node:readline'
import { randomUUID } from 'node:crypto'

const HOME = process.env.STUB_HOME ?? '.'
const STATE_FILE = join(HOME, 'observe-state.json')
const SENDS_FILE = join(HOME, 'sends.jsonl')
let obsSeq = 0

function reply(id, result) {
  process.stdout.write(JSON.stringify({ jsonrpc: '2.0', id, result }) + '\n')
}

function observe(args) {
  try {
    appendFileSync(join(HOME, 'observe-calls.jsonl'), JSON.stringify({ ts: new Date().toISOString(), watermark: args?.watermark ?? null }) + String.fromCharCode(10))
  } catch { /* 调试日志失败不影响 */ }
  const state = existsSync(STATE_FILE) ? JSON.parse(readFileSync(STATE_FILE, 'utf8')) : null
  if (!state) {
    return {
      success: false, code: 'UNAVAILABLE', message: '真机观察 BLOCKED（联测桩无状态文件）',
      effect: 'none',
    }
  }
  const messages = state.messages ?? []
  const after = args?.watermark?.last_local_message_id ?? null
  let start = 0
  if (after) {
    const found = messages.findIndex((m) => m.id === after)
    start = found >= 0 ? found + 1 : messages.length
  }
  obsSeq += 1
  return {
    success: true,
    code: 'OK',
    data: {
      observation_id: `stub-obs-${obsSeq}`,
      account_identity_version: Number(state.account_identity_version ?? 1),
      conversation_binding_id: String(args?.conversation_binding_id ?? state.binding_id ?? ''),
      binding_version: Number(state.binding_version ?? 0),
      observed_at: new Date().toISOString(),
      coverage: 'complete_window',
      ordered_messages: messages.slice(start).map((m) => ({
        sender: m.sender, text: m.text, local_message_id: m.id, source_evidence_ref: `ev-${m.id}`,
      })),
      window_fingerprint: `stub-fp-${obsSeq}`,
      gap_reason: null,
    },
  }
}

function sendV2(args) {
  const entry = {
    ts: new Date().toISOString(),
    request_id: args?.request_id ?? '',
    payload_ref: args?.payload_ref ?? '',
    permit_id: args?.permit_id ?? '',
  }
  appendFileSync(SENDS_FILE, JSON.stringify(entry) + '\n')
  return {
    success: true,
    code: 'OK',
    effect: 'applied',
    phase: 'verified',
    evidence_ref: `weixin-evidence:${args?.request_id ?? randomUUID()}:1`,
    data: { stub: true },
  }
}

const TOOLS = [
  { name: 'weixin_session_observe', description: '会话观察（联测桩）', inputSchema: { type: 'object', properties: { watermark: { type: 'object' } } } },
  { name: 'weixin_message_send_v2', description: '会话 v2 发送（联测桩）', inputSchema: { type: 'object', properties: { request_id: { type: 'string' }, payload_ref: { type: 'string' } }, required: ['request_id', 'payload_ref'] } },
]

const rl = createInterface({ input: process.stdin })
rl.on('line', (line) => {
  const trimmed = line.trim()
  if (!trimmed) return
  let msg
  try {
    msg = JSON.parse(trimmed)
  } catch {
    return
  }
  if (msg.id === undefined || msg.id === null) return // notification（initialized 等）忽略
  const method = msg.method ?? ''
  if (method === 'initialize') {
    reply(msg.id, { protocolVersion: '2024-11-05', capabilities: { tools: {} }, serverInfo: { name: 'live-weixin-stub', version: '0.0.1' } })
    return
  }
  if (method === 'tools/list') {
    reply(msg.id, { tools: TOOLS })
    return
  }
  if (method === 'ping') {
    reply(msg.id, {})
    return
  }
  if (method === 'tools/call') {
    const name = msg.params?.name ?? ''
    const args = msg.params?.arguments ?? {}
    let result
    if (name === 'weixin_session_observe') result = observe(args)
    else if (name === 'weixin_message_send_v2') result = sendV2(args)
    else result = { success: false, code: 'TOOL_NOT_FOUND', message: `联测桩未实现 ${name}`, effect: 'none' }
    reply(msg.id, { content: [{ type: 'text', text: JSON.stringify(result) }], structuredContent: result })
    return
  }
  reply(msg.id, { error: { code: -32601, message: `method not found: ${method}` } })
})
