import { createHash, randomUUID } from 'node:crypto'
import { join } from 'node:path'
import { runWeixinOperation } from './context.js'
import { CodedOperationError, type WeixinOperation } from './types.js'
import { ConversationAligner } from '../platform/aligner.js'
import { ocrTextMatches } from '../platform/ocrTextMatch.js'
import { readNameSession, sendNameSessionOnce, type NameDriver, type NameWindow } from '../platform/nameSession.js'
import { sendContextSchema, verifyBridgeEnvelope } from '../platform/liveBridge.js'
import { sessionObserverRequestSchema } from '../platform/sessionObserver.js'
import { createWeixinSessionObserveOperation, type SessionObserveArgs } from './sessionObserve.js'

export function createNameOperations(deps: { driver?: NameDriver; stateDir?: string; bridgeKey?: string } = {}) {
  const resolved = new Map<string, NameWindow>()
  const states = new Map<string, { window: NameWindow; aligner: ConversationAligner; fingerprints: Set<string> }>()
  const fallback = createWeixinSessionObserveOperation()
  const resolve: WeixinOperation<{ target_name: string }> = {
    name: 'weixin_name_resolve',
    execute(args, ctx) { return runWeixinOperation('readonly', ctx, () => typeof args?.target_name === 'string' && args.target_name.trim() && args.target_name.length <= 128 ? null : 'target_name必填', async () => {
      const window = await readNameSession(args.target_name, { open: true, driver: deps.driver, signal: ctx.signal })
      if (window.unchanged) throw new CodedOperationError('UI_CHANGED', '定位必须产生实际证据')
      resolved.delete(args.target_name); resolved.set(args.target_name, window)
      if (resolved.size > 32) resolved.delete(resolved.keys().next().value!)
      // title_exact retains legacy canonical-target confirmation, not literal OCR equality.
      return { message: '唯一名称会话已定位', data: { target_name: args.target_name, title_exact: true, unique_match: true, name_match_mode:'ocr_fuzzy_unique', evidence_ref: window.evidence_ref } }
    }) },
  }
  const observe: WeixinOperation<SessionObserveArgs & { target_name?: string }> = {
    name: 'weixin_session_observe',
    execute(args, ctx) {
      if (!args?.target_name) return fallback.execute(args, ctx)
      return runWeixinOperation('readonly', ctx, () => sessionObserverRequestSchema.safeParse(args).success && typeof args.target_name === 'string' && args.target_name.length <= 128 ? null : '观察参数非法', async () => {
        const name=args.target_name!, key=JSON.stringify([name,args.conversation_binding_id,args.binding_version,args.account_identity_version])
        const old=states.get(key)
        const gap=(reason: string) => ({ message:'会话观察断层',data:{ observation_id:randomUUID(),conversation_binding_id:args.conversation_binding_id,binding_version:args.binding_version,account_identity_version:args.account_identity_version,observed_at:new Date().toISOString(),coverage:'gap',gap_reason:reason,ordered_messages:[],window_fingerprint:null } })
        if (args.account_identity_version !== 0) throw new CodedOperationError('BLOCKED','名称场景仅支持当前登录上下文')
        if (args.watermark && !old?.fingerprints.has(args.watermark.window_fingerprint)) return gap('alignment_broken')
        const previous=old?.window ?? resolved.get(name)
        const read=await readNameSession(name,{open:!previous,previousFrame:previous?.frame,driver:deps.driver,signal:ctx.signal})
        const window=read.unchanged ? previous! : read
        if (window.objects.length && window.objects.every(o=>o.clipped)) return gap('alignment_broken')
        const aligner=old?.aligner ?? new ConversationAligner(ocrTextMatches)
        const aligned=aligner.align(window.objects.map(o=>({sender:o.sender,text:o.text,unsupported:o.clipped||!['text','system'].includes(o.type)})),args.watermark?.last_local_message_id ?? null,args.watermark != null && args.watermark.last_local_message_id === null)
        if (aligned.kind==='gap') return gap(aligned.reason)
        const fp='fp_'+createHash('sha256').update(JSON.stringify([key,window.frame,aligned.messages])).digest('hex')
        const fingerprints=old?.fingerprints ?? new Set<string>();fingerprints.add(fp)
        if(fingerprints.size>256)fingerprints.delete(fingerprints.values().next().value!)
        // The aligner owns first-seen text and IDs. Screenshot objects are only
        // the latest visual evidence, not a second whole-window identity gate.
        states.delete(key);states.set(key,{window,aligner,fingerprints});if(states.size>32)states.delete(states.keys().next().value!)
        const anchor=args.watermark?.last_local_message_id
        const messages=anchor ? aligned.messages.slice(aligned.messages.findIndex(m=>m.local_message_id===anchor)+1) : aligned.messages
        return {message:'名称会话观察完成',data:{observation_id:randomUUID(),conversation_binding_id:args.conversation_binding_id,binding_version:args.binding_version,account_identity_version:0,observed_at:new Date().toISOString(),coverage:'complete_window',gap_reason:null,ordered_messages:messages.map(m=>({...m,source_evidence_ref:window.evidence_ref})),window_fingerprint:fp,frame:window.frame}}
      })
    },
  }
  const send: WeixinOperation<unknown> = {
    name:'weixin_message_send_v2',
    async execute(args,ctx) {
      const result=await runWeixinOperation('write',ctx,()=>null,async(_ctx,tracker)=>{
        const parsed=sendContextSchema.safeParse(verifyBridgeEnvelope(args,Date.now(),deps.bridgeKey))
        if(!parsed.success)throw new CodedOperationError('INVALID_ARGUMENT','发送上下文非法')
        const c=parsed.data
        if(createHash('sha256').update(c.text).digest('hex')!==c.payload_hash)throw new CodedOperationError('BLOCKED','正文摘要不符')
        const key=JSON.stringify([c.target_name,c.conversation_binding_id,c.binding_version,c.account_identity_version])
        const state=states.get(key)
        if(!state || state.window.frame!==c.expected_frame)throw new CodedOperationError('UI_CHANGED','缺少当前观察基线')
        const root=deps.stateDir ?? (process.env.LOCALAPPDATA ? join(process.env.LOCALAPPDATA,'aid-weixin','name-requests') : '')
        if(!root)throw new CodedOperationError('CONFIG_MISSING','本地执行目录不可用')
        // Node timeout uses elapsed time; wall-clock rollback cannot extend this lease.
        const remaining=Math.floor(Math.min(c.expires_at_ms-Date.now(),c.expires_at_ms-c.issued_at_ms))
        if(remaining<=0)throw new CodedOperationError('BLOCKED','发送许可失效')
        const leaseSignal=AbortSignal.any([ctx.signal,AbortSignal.timeout(remaining)])
        const sent=await sendNameSessionOnce({targetName:c.target_name,text:c.text,requestId:c.request_id,baseline:state.window,deadlineMs:c.expires_at_ms},{stateDir:root,driver:deps.driver,signal:leaseSignal})
        if(sent.status!=='submitted')throw new CodedOperationError('EXECUTION_UNKNOWN','单条发送结果未知，不会重发')
        tracker.completed=1
        return {message:'已执行发送，未核验送达',data:{request_id:c.request_id,permit_id:c.permit_id,evidence_ref:sent.evidence_ref}}
      })
      return Object.assign(result,{phase:result.success?'submitted':result.effect==='unknown'?'unknown':'prepared',safe_to_retry:false,evidence_ref:result.data.evidence_ref})
    },
  }
  return {resolve,observe,send}
}
