import { createHash, randomUUID } from 'node:crypto'
import { runWeixinOperation } from './context.js'
import { CodedOperationError, type WeixinOperation } from './types.js'
import { ConversationAligner } from '../platform/aligner.js'
import { sessionObserverRequestSchema } from '../platform/sessionObserver.js'
import { readWeixinNativeObservation, type NativeMessageWindow } from '../platform/nativeMessages.js'
import type { RunPowerShellDriverFn } from '../platform/powershell.js'
import { createWeixinSessionObserveOperation, observerUnavailable, type SessionObserveArgs } from './sessionObserve.js'

interface NameObservationState {
  window?: NativeMessageWindow
  aligner: ConversationAligner
  fingerprints: Set<string>
  nativeSessionIdentity?: string
}

export function createNameOperations(deps: {
  nativeDriver?: RunPowerShellDriverFn
  observeCurrentOnly?: boolean
} = {}) {
  const states = new Map<string, NameObservationState>()
  const fallback = createWeixinSessionObserveOperation()
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
        const previous=old?.window
        let read: Awaited<ReturnType<typeof readWeixinNativeObservation>>
        try{
          read=await readWeixinNativeObservation({
            targetName:name,nativeTitle:previous?.native_title,
            nativeSessionIdentity:old?.nativeSessionIdentity,
            includeEvidence:!deps.observeCurrentOnly,previousFrame:previous?.frame,signal:ctx.signal,
          },deps.nativeDriver)
        }catch(error){
          if(error instanceof CodedOperationError){
            if(error.code==='WEIXIN_NOT_FOUND'||error.code==='WINDOW_AMBIGUOUS')
              return observerUnavailable(args,'window_missing','观察不可用：微信主窗口不可达')
            if(error.code==='TARGET_NOT_FOUND'||error.code==='TARGET_AMBIGUOUS')
              return observerUnavailable(args,'viewport_unreachable','观察不可用：无法核对目标会话')
            if(error.code==='UI_CHANGED')return gap('layout_changed')
          }
          throw error
        }
        const window=read.unchanged ? previous! : read
        const state: NameObservationState=old ?? {
          aligner:new ConversationAligner(),
          fingerprints:new Set<string>(),
        }
        // A verified selection remains bound even if the message viewport cannot establish a watermark yet.
        state.nativeSessionIdentity=read.nativeSessionIdentity
        states.delete(key);states.set(key,state);if(states.size>32)states.delete(states.keys().next().value!)
        if(window.objects.slice(1).some(o=>o.clipped))return gap('viewport_out_of_range')
        if (window.objects.length && window.objects.every(o=>o.clipped)) return gap('alignment_broken')
        const aligned=state.aligner.align(window.objects.map(o=>({sender:o.sender,text:o.text,unsupported:o.clipped||o.type!=='text'})),args.watermark?.last_local_message_id ?? null,args.watermark != null && args.watermark.last_local_message_id === null)
        if (aligned.kind==='gap') return gap(aligned.reason)
        const fp='fp_'+createHash('sha256').update(JSON.stringify([key,window.frame,aligned.messages])).digest('hex')
        const fingerprints=state.fingerprints;fingerprints.add(fp)
        if(fingerprints.size>256)fingerprints.delete(fingerprints.values().next().value!)
        // The aligner owns first-seen text and IDs. Screenshot objects are only
        // the latest visual evidence, not a second whole-window identity gate.
        state.window=window
        const anchor=args.watermark?.last_local_message_id
        const messages=anchor ? aligned.messages.slice(aligned.messages.findIndex(m=>m.local_message_id===anchor)+1) : aligned.messages
        return {message:'名称会话观察完成',data:{observation_id:randomUUID(),conversation_binding_id:args.conversation_binding_id,binding_version:args.binding_version,account_identity_version:0,observed_at:new Date().toISOString(),coverage:'complete_window',gap_reason:null,ordered_messages:messages.map(m=>({...m,source_evidence_ref:window.evidence_ref})),window_fingerprint:fp,frame:window.frame}}
      })
    },
  }
  return {observe}
}
