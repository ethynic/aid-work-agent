import { fileURLToPath } from 'node:url'
import { runWeixinOperation } from './context.js'
import { CancelledError, CodedOperationError, type WeixinOperation } from './types.js'
import { runPowerShellDriver, type RunPowerShellDriverFn } from '../platform/powershell.js'
import { verifyTargetRef, type VerifyTargetRefFn } from '../platform/targetRef.js'

const DRIVER_PATH = fileURLToPath(new URL('../../../drivers/ps1/message-send.ps1', import.meta.url))
const MAX_TEXT_LENGTH = 500

export interface WeixinMessageSendArgs {
  target_ref: string
  text: string
}

export function createWeixinMessageSendOperation(
  deps: { runDriverFn?: RunPowerShellDriverFn; verifyRefFn?: VerifyTargetRefFn } = {},
): WeixinOperation<WeixinMessageSendArgs> {
  const runDriverFn = deps.runDriverFn ?? runPowerShellDriver
  const verifyRefFn = deps.verifyRefFn ?? verifyTargetRef
  return {
    name: 'weixin_message_send',
    execute(args, ctx) {
      return runWeixinOperation('write', ctx, () => {
        if (args === null || typeof args !== 'object' || Array.isArray(args)) return '参数必须是对象（target_ref/text 必填）'
        if (typeof args.target_ref !== 'string' || args.target_ref.length === 0) return 'target_ref 必填且必须是非空字符串（先经 weixin_chat_search 获取）'
        if (typeof args.text !== 'string' || args.text.length === 0) return 'text 必填且必须是非空字符串'
        if (args.text.length > MAX_TEXT_LENGTH) return `text 长度不能超过 ${MAX_TEXT_LENGTH} 字`
        if (/[\r\n]/.test(args.text)) return 'text 不能包含换行符（仅支持单条单行文本消息）'
        return null
      }, async (opCtx, tracker) => {
        const target = verifyRefFn(args.target_ref)
        opCtx.progress({ stage: 'execute', message: '核对微信发送目标与输入框' })
        let data: Record<string, unknown>
        try {
          data = await runDriverFn({
            script: DRIVER_PATH,
            stdin: JSON.stringify({ target_name: target.name, target_type: target.type, text: args.text }),
            useVisionProxy: true,
            signal: opCtx.signal,
          })
        } catch (err) {
          if (err instanceof CodedOperationError && err.code === 'RESULT_TIMEOUT') {
            throw new CodedOperationError('EXECUTION_UNKNOWN', '发送链路超时，状态无法确认，请在微信中人工核对')
          }
          if (err instanceof CancelledError) {
            throw new CodedOperationError('CANCELLED', '发送链路已取消，状态无法确认，请在微信中人工核对', 'unknown')
          }
          throw err
        }
        if (data.title !== target.name || data.verified !== true) {
          throw new CodedOperationError('EXECUTION_UNKNOWN', '微信发送回执无法确认，请在微信中人工核对')
        }
        tracker.completed = 1
        return {
          message: '已提交，微信界面已出现对应新增消息',
          data: { target: target.name, title: data.title },
        }
      })
    },
  }
}
