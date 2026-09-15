/**
 * weixin_session_observe operation（C2，设计 §6 session_observer_v1 契约）。
 *
 * 完整链路（评审 P1-4）：captureFn 截图 → 常驻 OCR → 拆行/分区 → 对齐器 →
 * 冻结契约观察结果。真机 captureFn 未注入时显式 unavailable（C0 BLOCKED，
 * 不冒充无人回复）。每绑定维护独立对齐器实例（状态跨请求稳定 ID）。
 * 只读（effect=none）；不落明文日志。
 */
import { createHash, randomUUID } from 'node:crypto'
import { runWeixinOperation } from './context.js'
import type { OperationResult, OpContext, WeixinOperation } from './types.js'
import { ConversationAligner, boxesToBubbles, type AlignBox } from '../platform/aligner.js'
import { ResidentOcr, OcrServerUnavailable } from '../platform/ocrResident.js'
import { sessionObserverRequestSchema } from '../platform/sessionObserver.js'

export interface SessionObserveArgs {
  conversation_binding_id?: string
  binding_version?: number
  account_identity_version?: number
  watermark?: { last_local_message_id: string | null; window_fingerprint: string } | null
}

/** 截图注入点：返回 PNG 路径 + 消息区配置（真机 BLOCKED；测试合成截图） */
export type CaptureFn = (bindingId: string) => Promise<{
  image: string
  region: { minX: number; maxX: number; selfStartX: number; crop?: [number, number, number, number] }
  /** 来自受信采集器的实际核验结果，不能复制请求的期望版本。 */
  identity: { conversation_binding_id: string; binding_version: number; account_identity_version: number }
  /** 已持久化的受控截图引用；不得使用占位引用或明文路径。 */
  evidence_ref: string
}>


function observerUnavailable(
  args: SessionObserveArgs,
  reason: string,
  message: string,
): { message: string; data: Record<string, unknown> } {
  return {
    message,
    data: {
      observation_id: randomUUID(),
      account_identity_version: args.account_identity_version ?? 0,
      conversation_binding_id: args.conversation_binding_id ?? '',
      binding_version: args.binding_version ?? 0,
      observed_at: new Date().toISOString(),
      coverage: 'unavailable',
      ordered_messages: [],
      window_fingerprint: null,
      gap_reason: reason,
    },
  }
}

export function createWeixinSessionObserveOperation(deps: {
  captureFn?: CaptureFn
  recognizeFn?: (image: string, crop?: [number, number, number, number]) => Promise<AlignBox[]>
} = {}): WeixinOperation<SessionObserveArgs> {
  const ocr = new ResidentOcr()
  const recognize = deps.recognizeFn ?? ((image, crop) => ocr.recognize(image, crop))
  // LRU按绑定与身份版本隔离；淘汰后旧水位必须报gap。
  const states = new Map<string, { aligner: ConversationAligner; fingerprints: Set<string> }>()
  return {
    name: 'weixin_session_observe',
    execute(args: SessionObserveArgs, ctx: OpContext): Promise<OperationResult> {
      return runWeixinOperation(
        'readonly',
        ctx,
        () => {
          return sessionObserverRequestSchema.safeParse(args).success ? null : '观察请求不符合 session_observer_v1 契约'
        },
        async () => {
          const captureFn = deps.captureFn
          if (!captureFn) {
            return observerUnavailable(args, 'viewport_unreachable', '观察不可用：真机截图接线未开放（C0 BLOCKED）')
          }
          const bindingId = args.conversation_binding_id as string
          // 1) 截图（区域裁剪在 OCR 侧执行）
          let captured: Awaited<ReturnType<CaptureFn>>
          try {
            captured = await captureFn(bindingId)
          } catch {
            return observerUnavailable(args, 'window_missing', '观察不可用：会话窗口不可达')
          }
          const actual = captured.identity
          if (!actual || actual.conversation_binding_id !== bindingId ||
              actual.binding_version !== args.binding_version || actual.account_identity_version !== args.account_identity_version) {
            for (const key of states.keys()) {
              if (JSON.parse(key)[0] === bindingId) states.delete(key)
            }
            const unavailable = observerUnavailable(args, 'account_identity_changed', '观察不可用：采集身份与绑定不一致')
            // 可信实际版本必须传给Runtime，使其按身份漂移阻断而非普通等待。
            if (actual && typeof actual.conversation_binding_id === 'string' &&
                Number.isInteger(actual.binding_version) && Number.isInteger(actual.account_identity_version)) {
              Object.assign(unavailable.data, actual)
            }
            return unavailable
          }
          if (!captured.evidence_ref || !/^[a-z][a-z0-9_-]*:[A-Za-z0-9_-]+$/.test(captured.evidence_ref)) {
            return observerUnavailable(args, 'viewport_unreachable', '观察不可用：缺少受控采集证据')
          }
          const stateKey = JSON.stringify([bindingId, actual.binding_version, actual.account_identity_version])
          const state = states.get(stateKey) ?? { aligner: new ConversationAligner(), fingerprints: new Set<string>() }
          const knownFingerprints = state.fingerprints
          if (args.watermark && !knownFingerprints.has(args.watermark.window_fingerprint)) {
            return {
              message: '观察断层：水位不属于当前连续窗口',
              data: { ...observerUnavailable(args, 'alignment_broken', '').data, coverage: 'gap' },
            }
          }
          // 2) 常驻 OCR（崩溃自动重启一次；不可用显式 unavailable）
          let boxes: Array<{ text: string; x0: number; y0: number; x1: number; y1: number }>
          try {
            const ocrBoxes = await recognize(captured.image, captured.region.crop)
            boxes = ocrBoxes.map((b) => ({ text: b.text, x0: b.x0, y0: b.y0, x1: b.x1, y1: b.y1 }))
          } catch (err) {
            if (err instanceof OcrServerUnavailable) {
              return observerUnavailable(args, 'engine_unavailable', `观察不可用：${err.message}`)
            }
            return observerUnavailable(args, 'ocr_failed', '观察不可用：OCR 失败')
          }
          // 3) 拆行/分区 → 气泡（sender unknown 的框由对齐器整体降级 gap）
          const bubbles = boxesToBubbles(boxes as AlignBox[], captured.region)
          // 4) 对齐（水位锚定/稳定 ID/gap 判定）
          const aligner = state.aligner
          const anchor = args.watermark?.last_local_message_id ?? null
          const aligned = aligner.align(bubbles, anchor, args.watermark != null && anchor === null)
          if (aligned.kind === 'gap') {
            return {
              message: `观察完成：coverage=gap（${aligned.reason}），阻断该会话自动回复`,
              data: {
                observation_id: randomUUID(),
                account_identity_version: args.account_identity_version ?? 0,
                conversation_binding_id: bindingId,
                binding_version: args.binding_version ?? 0,
                observed_at: new Date().toISOString(),
                coverage: 'gap',
                ordered_messages: [],
                window_fingerprint: null,
                gap_reason: aligned.reason,
              },
            }
          }
          const fingerprint = `fp_${createHash('sha256').update(JSON.stringify([actual, captured.region, aligned.messages])).digest('hex')}`
          knownFingerprints.add(fingerprint)
          // 已退休窗口无须无限保留；过旧水位显式 gap。
          if (knownFingerprints.size > 256) knownFingerprints.delete(knownFingerprints.values().next().value!)
          states.delete(stateKey)
          states.set(stateKey, state)
          if (states.size > 64) states.delete(states.keys().next().value!)
          // complete_window：新于水位的消息（基线时为整窗 → 引擎侧锚定不回复）
          const anchorIdx = aligned.messages.findIndex((m) => m.local_message_id === anchor)
          const newMessages = anchor ? aligned.messages.slice(anchorIdx + 1) : aligned.messages
          return {
            message: `观察完成：${newMessages.length} 条新消息（coverage=complete_window）`,
            data: {
              observation_id: randomUUID(),
              account_identity_version: args.account_identity_version ?? 0,
              conversation_binding_id: bindingId,
              binding_version: args.binding_version ?? 0,
              observed_at: new Date().toISOString(),
              coverage: 'complete_window',
              ordered_messages: newMessages.map((m) => ({
                sender: m.sender,
                text: m.text,
                local_message_id: m.local_message_id,
                source_evidence_ref: captured.evidence_ref,
              })),
              window_fingerprint: fingerprint,
              gap_reason: null,
            },
          }
        },
      )
    },
  }
}
