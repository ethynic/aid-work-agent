/**
 * wecom_message_send operation（M2）：受控写动作，向 target_ref 目标发送 1 条文本消息。
 *
 * 流程：TS 校验参数 → verifyTargetRef 解出目标（过期 TARGET_REF_STALE /
 * 篡改 INVALID_ARGUMENT）→ spawn drivers/ps1/message-send.ps1 全链路
 * （重新搜索定位：按 name+section+subtitle 精确匹配，歧义 TARGET_AMBIGUOUS →
 * 点进会话 → OCR 校验会话标题一致（不一致 UI_CHANGED 中止）→ 校验输入框无残留
 * 草稿（有残留 UI_CHANGED 中止）→ 输入 text → 发送前再校验标题 → PostMessage
 * Enter → 终态校验：输入框清空 + 最后气泡含 text + 会话列表 preview 匹配，
 * 三选二，不过 EXECUTION_UNKNOWN 绝不重试）。
 *
 * 写语义：发送后校验失败 → EXECUTION_UNKNOWN，绝不自动重试；驱动超时
 * （写可能已落地）同样归并 EXECUTION_UNKNOWN，而不是 RESULT_TIMEOUT；
 * 驱动执行中被取消 → CANCELLED/effect=unknown（动作发出后取消且无法确认，
 * 不可误报 none）。
 */
import { mkdirSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { runWecomOperation } from './context.js'
import {
  CancelledError,
  CodedOperationError,
  type OperationResult,
  type OpContext,
  type WecomOperation,
} from './types.js'
import { runPowerShellDriver, type RunPowerShellDriverFn } from '../platform/powershell.js'
import { artifactDir } from '../platform/environment.js'
import { verifyTargetRef, type VerifyTargetRefFn } from '../platform/targetRef.js'

/** dist/src/operations → 包根 drivers/ps1 */
const DRIVER_PATH = fileURLToPath(new URL('../../../drivers/ps1/message-send.ps1', import.meta.url))

export interface WecomMessageSendArgs {
  target_ref: string
  text: string
}

const MAX_TEXT_LENGTH = 2000

export function createWecomMessageSendOperation(
  deps: { runDriverFn?: RunPowerShellDriverFn; verifyRefFn?: VerifyTargetRefFn; artifactDirFn?: () => string | null } = {},
): WecomOperation<WecomMessageSendArgs> {
  const runDriverFn = deps.runDriverFn ?? runPowerShellDriver
  const verifyRefFn = deps.verifyRefFn ?? verifyTargetRef
  const artifactDirFn = deps.artifactDirFn ?? (() => artifactDir())
  return {
    name: 'wecom_message_send',
    execute(args: WecomMessageSendArgs, ctx: OpContext): Promise<OperationResult> {
      return runWecomOperation(
        'write',
        ctx,
        () => {
          if (args === null || typeof args !== 'object' || Array.isArray(args)) {
            return '参数必须是对象（target_ref/text 必填）'
          }
          if (typeof args.target_ref !== 'string' || args.target_ref.length === 0) {
            return 'target_ref 必填且必须是非空字符串（先经 wecom_chat_search 获取）'
          }
          if (typeof args.text !== 'string' || args.text.length === 0) {
            return 'text 必填且必须是非空字符串'
          }
          if (args.text.length > MAX_TEXT_LENGTH) {
            return `text 长度不能超过 ${MAX_TEXT_LENGTH} 字（请调用方分段发送）`
          }
          // 逐字输入不支持换行：换行可能提前触发发送或静默丢弃，
          // 破坏「单次单目标 1 条」硬上限与发送后校验
          if (/[\r\n]/.test(args.text)) {
            return 'text 不能包含换行符（仅支持单条单行文本消息）'
          }
          return null
        },
        async (opCtx, tracker) => {
          // 关键步骤截图存档目录：<artifact 根>/send-<run 时间戳>/（真机排障，同 add-customer）
          const root = artifactDirFn()
          if (!root) {
            throw new CodedOperationError('INTERNAL_ERROR', '%LOCALAPPDATA% 未设置，无法定位 artifact 目录')
          }
          const dir = join(root, `send-${new Date().toISOString().replace(/[:.]/g, '-')}`)
          mkdirSync(dir, { recursive: true })

          const target = verifyRefFn(args.target_ref)
          opCtx.progress({ stage: 'execute', message: `解析目标「${target.name}」，执行发送链路` })
          let data: Record<string, unknown>
          try {
            data = await runDriverFn({
              script: DRIVER_PATH,
              args: ['-TargetName', target.name, '-Subtitle', target.subtitle, '-Section', target.type, '-Text', args.text, '-ArtifactDir', dir],
              signal: opCtx.signal,
            })
          } catch (err) {
            // 驱动超时/被取消都无法确认写是否落地：按写动作 unknown 语义上报，绝不自动重试
            if (err instanceof CodedOperationError && err.code === 'RESULT_TIMEOUT') {
              throw new CodedOperationError('EXECUTION_UNKNOWN', '发送链路超时：消息可能已发出但无法确认（不会自动重试，请人工核对）')
            }
            if (err instanceof CancelledError) {
              throw new CodedOperationError(
                'CANCELLED',
                '发送链路被取消：消息可能已发出但无法确认（不会自动重试，请人工核对）',
                'unknown',
              )
            }
            throw err
          }
          tracker.completed = 1
          return {
            message: `已向「${target.name}」发送消息，发送后校验通过`,
            data: {
              target: target.name,
              title: data.title ?? target.name,
              screenshot_paths: Array.isArray(data.screenshot_paths) ? data.screenshot_paths : [],
            },
          }
        },
      )
    },
  }
}
