import { createHash, createHmac, timingSafeEqual } from 'node:crypto'
import { z } from 'zod'
import { CodedOperationError } from '../operations/types.js'

export const bridgeEnvelopeSchema = z.object({ context: z.string().min(1).max(32_768), signature: z.string().regex(/^[a-f0-9]{64}$/) }).strict()
export const bridgeBaseSchema = z.object({ version: z.literal(1), kind: z.literal('weixin.send.v2'), request_id: z.string().uuid(), issued_at_ms: z.number().int(), expires_at_ms: z.number().int() })
export const sendContextSchema = bridgeBaseSchema.extend({
  invocation_id: z.string().uuid(), permit_id: z.string().min(1).max(128),
  conversation_binding_id: z.string().uuid(), binding_version: z.number().int().nonnegative(), account_identity_version: z.literal(0),
  target_name: z.string().min(1).max(128), expected_frame: z.string().regex(/^[a-f0-9]{64}$/),
  text: z.string().min(1).max(500).refine(v => !/[\x00-\x1f\x7f-\x9f\u2028\u2029]/.test(v) && v.trim().length > 0),
  payload_hash: z.string().regex(/^[a-f0-9]{64}$/),
}).strict()
export function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`
  if (value && typeof value === 'object') return `{${Object.entries(value).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0).map(([k,v]) => `${JSON.stringify(k)}:${canonical(v)}`).join(',')}}`
  return JSON.stringify(value)
}
export function digest(value: unknown): string { return createHash('sha256').update(canonical(value)).digest('hex') }
export function requireBridgeKey(key = process.env.AIDWORK_WEIXIN_BRIDGE_KEY): Buffer {
  if (!key || !/^[a-f0-9]{64}$/.test(key)) throw new CodedOperationError('BLOCKED', '受信Runtime执行桥不可用')
  return Buffer.from(key, 'hex')
}
export function verifyBridgeEnvelope(raw: unknown, now = Date.now(), key?: string): Record<string, unknown> {
  const secret = requireBridgeKey(key)
  const envelope = bridgeEnvelopeSchema.safeParse(raw)
  if (!envelope.success) throw new CodedOperationError('INVALID_ARGUMENT', '执行桥参数非法')
  const expected = createHmac('sha256', secret).update(envelope.data.context).digest()
  if (!timingSafeEqual(expected, Buffer.from(envelope.data.signature, 'hex'))) throw new CodedOperationError('BLOCKED', '执行桥签名无效')
  let parsed: unknown
  try { parsed = JSON.parse(envelope.data.context) } catch { throw new CodedOperationError('INVALID_ARGUMENT', '执行桥上下文非法') }
  const base = bridgeBaseSchema.safeParse(parsed)
  if (!base.success || base.data.issued_at_ms > now || base.data.expires_at_ms <= now || base.data.expires_at_ms <= base.data.issued_at_ms || base.data.expires_at_ms - base.data.issued_at_ms > 90_000) throw new CodedOperationError('BLOCKED', '执行桥许可过期或时间不合法')
  return parsed as Record<string, unknown>
}
