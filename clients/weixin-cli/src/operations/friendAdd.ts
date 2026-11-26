import { fileURLToPath } from 'node:url'
import { runPowerShellDriver, type RunPowerShellDriverFn } from '../platform/powershell.js'
import { runWeixinOperation } from './context.js'
import { CancelledError, CodedOperationError, type WeixinOperation } from './types.js'

const DRIVER_PATH = fileURLToPath(new URL('../../../drivers/ps1/friend-add.ps1', import.meta.url))
const MAX_IDENTIFIER_LENGTH = 64
const MAX_VERIFY_MESSAGE_LENGTH = 60

export interface WeixinFriendAddArgs {
  identifier?: string
  verifyMessage?: string
  searchOnly?: boolean
}

export function createWeixinFriendAddOperation(
  runDriver: RunPowerShellDriverFn = runPowerShellDriver,
): WeixinOperation<WeixinFriendAddArgs> {
  return {
    name: 'weixin_friend_add',
    execute(args, ctx) {
      const searchOnly = args?.searchOnly === true
      return runWeixinOperation(searchOnly ? 'readonly' : 'write', ctx, () => {
        const identifier = args?.identifier
        if (typeof identifier !== 'string' || identifier.trim().length > MAX_IDENTIFIER_LENGTH
          || !/^(?:\+?[0-9]+|[A-Za-z0-9_-]+)$/.test(identifier.trim())) {
          return 'identifier 必须是一个手机号或微信号'
        }
        const message = args.verifyMessage
        if (searchOnly && message !== undefined) return '只查模式不接受验证语'
        if (message !== undefined && (typeof message !== 'string' || message.trim().length === 0
          || [...message].length > MAX_VERIFY_MESSAGE_LENGTH || /\p{Cc}/u.test(message))) {
          return 'verify-message 必须是 1 至 60 字的单行验证语'
        }
        return null
      }, async (opCtx) => {
        opCtx.progress({ stage: 'execute', message: '在微信查找目标并检查好友申请状态' })
        let data: Record<string, unknown>
        try {
          data = await runDriver({
            script: DRIVER_PATH,
            stdin: JSON.stringify({
              identifier: args.identifier!.trim(), verify_message: args.verifyMessage,
              ...(searchOnly ? { search_only: true } : {}),
            }),
            signal: opCtx.signal,
            ...(process.env.AIDWORK_RUNTIME_SERVER?.trim() || process.env.AIDWORK_RUNTIME_DEVICE_TOKEN?.trim()
              ? { useVisionProxy: false } : {}),
          })
        } catch (err) {
          if (searchOnly) throw err
          // A terminated process cannot attest whether the final click was already dispatched.
          if (err instanceof CancelledError) {
            throw new CodedOperationError('CANCELLED', '添加好友操作已取消，申请状态需在微信中核对', 'unknown')
          }
          if (err instanceof CodedOperationError && err.code === 'RESULT_TIMEOUT') {
            throw new CodedOperationError('EXECUTION_UNKNOWN', '添加好友操作超时，申请状态需在微信中核对')
          }
          if (err instanceof CodedOperationError && err.code !== 'INTERNAL_ERROR') throw err
          throw new CodedOperationError('INTERNAL_ERROR', '添加好友驱动执行失败，申请状态需在微信中核对')
        }

        if (searchOnly && data.status !== 'addable' && data.status !== 'already_friend' && data.status !== 'already_requested') {
          throw new CodedOperationError('UI_CHANGED', '无法确认微信搜索结果')
        }
        switch (data.status) {
          case 'addable':
            if (searchOnly) return { message: '已找到可添加的目标，搜索结果已保留', data: { status: data.status }, effect: 'none' }
            throw new CodedOperationError('EXECUTION_UNKNOWN', '无法确认微信好友申请结果，请在微信中核对')
          case 'already_friend':
            return { message: '目标已是微信好友', data: { status: data.status }, effect: 'none' }
          case 'already_requested':
            return { message: '目标已有待处理的微信好友申请', data: { status: data.status }, effect: 'none' }
          case 'request_sent':
            return { message: '微信好友申请已提交', data: { status: data.status }, effect: 'applied' }
          case 'friend_added':
            return { message: '已直接添加微信好友，未发送验证语', data: { status: data.status }, effect: 'applied' }
          default:
            throw new CodedOperationError('EXECUTION_UNKNOWN', '无法确认微信好友申请结果，请在微信中核对')
        }
      })
    },
  }
}
