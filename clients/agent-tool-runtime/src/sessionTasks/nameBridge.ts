/** Local execution bridge for the currently logged-in WeChat, scoped by name.
 * Neither the bridge key nor frozen text is persisted or passed in process args.
 */
import { createHash, createHmac } from 'node:crypto'
import type { ApiClient, ClaimedInvocation } from '../apiClient.js'
import type { ProviderSet } from '../providerManager.js'
import type { WritePermit } from '../writeAuthorize.js'
import type { ObserverFn, ObserverResult } from './engine.js'

export class NameSessionBridge {
  private readonly frames = new Map<string, { name: string; version: number; frame: string }>()
  private readonly modes = new Map<string, 'name' | 'legacy'>()

  constructor(private readonly providers: Pick<ProviderSet, 'get'>,
    private readonly api: Pick<ApiClient, 'invocationPayload'>, private readonly key: string) {
    if (!/^[a-f0-9]{64}$/.test(key)) throw new Error('执行桥密钥无效')
  }

  readonly observe: ObserverFn = async (task, request) => {
    this.frames.delete(task.conversationBindingId)
    this.modes.set(task.conversationBindingId, task.targetName ? 'name' : 'legacy')
    if (this.modes.size > 64) this.modes.delete(this.modes.keys().next().value!)
    const envelope = await this.providers.get('weixin').callTool('weixin_session_observe', {
      conversation_binding_id: task.conversationBindingId,
      binding_version: task.expectedBindingVersion,
      account_identity_version: task.expectedAccountIdentityVersion,
      ...(task.targetName ? { target_name: task.targetName } : {}),
      watermark: request.watermark,
    }, { timeoutMs: 75_000 })
    if (envelope.success !== true) throw new Error(`观察失败: ${String(envelope.code ?? 'UNKNOWN')}`)
    const data = envelope.data as Record<string, unknown> | undefined
    if (!data || typeof data.observation_id !== 'string' || !['complete_window', 'gap', 'unavailable'].includes(String(data.coverage)) || !Array.isArray(data.ordered_messages)) {
      throw new Error('观察结果契约无效')
    }
    if (task.targetName && data.coverage === 'complete_window' &&
      data.conversation_binding_id === task.conversationBindingId && data.binding_version === task.expectedBindingVersion &&
      data.account_identity_version === 0 && task.expectedAccountIdentityVersion === 0 &&
      typeof data.frame === 'string' && /^[a-f0-9]{64}$/.test(data.frame)) {
      this.frames.set(task.conversationBindingId, { name: task.targetName, version: task.expectedBindingVersion, frame: data.frame })
      if (this.frames.size > 64) this.frames.delete(this.frames.keys().next().value!)
    }
    return data as unknown as ObserverResult
  }

  readonly prepare = async (inv: ClaimedInvocation, permit: WritePermit, signal: AbortSignal): Promise<Record<string, unknown>> => {
    if (inv.tool_name !== 'weixin_message_send_v2') return { ...inv.arguments, permit_id: permit.permitId, permit_token: permit.permitToken }
    const binding = String(inv.arguments.target_ref ?? '')
    if (this.modes.get(binding) === 'legacy') return { ...inv.arguments, permit_id: permit.permitId, permit_token: permit.permitToken }
    if(inv.arguments.receipt_mode!=='submission'||inv.arguments.receipt_context!=='weixin_name')throw new Error('名称发送缺少冻结执行回执模式')
    const observed = this.frames.get(binding)
    if (!observed || inv.arguments.target_version !== `iv-${observed.version}` || !permit.isValid()) throw new Error('发送缺少当前名称观察依据')
    const bytes = await this.api.invocationPayload(inv.invocation_id, signal)
    if (createHash('sha256').update(bytes).digest('hex') !== inv.arguments.payload_hash) throw new Error('冻结载荷摘要不符')
    const text = new TextDecoder('utf-8', { fatal: true }).decode(bytes)
    if (!text.trim() || text.length > 500 || /[\x00-\x1f\x7f-\x9f\u2028\u2029]/.test(text)) throw new Error('冻结正文格式无效')
    const ttl = Math.floor(Math.min(90_000, permit.msRemaining()))
    if (ttl <= 0 || !permit.isValid() || signal.aborted) throw new Error('发送许可失效')
    const issued = Date.now()
    const context = JSON.stringify({ version: 1, kind: 'weixin.send.v2', request_id: inv.arguments.request_id,
      invocation_id: inv.invocation_id, permit_id: permit.permitId, issued_at_ms: issued, expires_at_ms: issued + ttl,
      conversation_binding_id: binding, binding_version: observed.version, account_identity_version: 0,
      target_name: observed.name, expected_frame: observed.frame, text, payload_hash: inv.arguments.payload_hash })
    return { context, signature: createHmac('sha256', Buffer.from(this.key, 'hex')).update(context).digest('hex') }
  }
}
