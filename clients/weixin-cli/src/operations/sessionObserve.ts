/**
 * weixin_session_observe operation（C2，设计 §6 session_observer_v1 契约）。
 *
 * 完整链路（评审 P1-4）：captureFn 截图 → 常驻 OCR → 拆行/分区 → 对齐器 →
 * 冻结契约观察结果。真机 captureFn 未注入时显式 unavailable（C0 BLOCKED，
 * 不冒充无人回复）。每绑定维护独立对齐器实例（状态跨请求稳定 ID）。
 * 只读（effect=none）；不落明文日志。
 */
import { randomUUID } from 'node:crypto'
import { runWeixinOperation } from './context.js'
import type { OperationResult, OpContext, WeixinOperation } from './types.js'
import { ConversationAligner, boxesToBubbles, type AlignBox } from '../platform/aligner.js'
import { ResidentOcr, OcrServerUnavailable } from '../platform/ocrResident.js'

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
}>

const ocr = new ResidentOcr()
const aligners = new Map<string, ConversationAligner>()

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

export function createWeixinSessionObserveOperation(deps: { captureFn?: CaptureFn } = {}): WeixinOperation<SessionObserveArgs> {
  return {
    name: 'weixin_session_observe',
    execute(args: SessionObserveArgs, ctx: OpContext): Promise<OperationResult> {
      return runWeixinOperation(
        'readonly',
        ctx,
        () => {
          if (typeof args.conversation_binding_id !== 'string' || !args.conversation_binding_id) {
            return 'conversation_binding_id 必填'
          }
          return null
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
          // 2) 常驻 OCR（崩溃自动重启一次；不可用显式 unavailable）
          let boxes: Array<{ text: string; x0: number; y0: number; x1: number; y1: number }>
          try {
            const ocrBoxes = await ocr.recognize(captured.image, captured.region.crop)
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
          const aligner = aligners.get(bindingId) ?? new ConversationAligner()
          aligners.set(bindingId, aligner)
          const anchor = args.watermark?.last_local_message_id ?? null
          const aligned = aligner.align(bubbles, anchor)
          const fingerprint = `fp_${randomUUID().slice(0, 12)}`
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
                source_evidence_ref: `evd:${m.local_message_id}`,
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
