/**
 * CLI 子命令 read：本机会话历史及未读读取。
 *
 * 用法：
 *   aid-weixin read --domain chat-history --target-name <当前会话全名> [--since-days N] [--max-pages N]
 *   aid-weixin read --domain unread [--name <名>]
 *
 * article 域尚未产品化，明确拒绝（退出码 2）。
 * 与 MCP tool weixin_history_read / weixin_unread_list 调用同一 operation。
 */
import { getOperationEntry } from '../../operations/registry.js'
import { runCliOperation } from '../render.js'
import type { CliDomain } from '../args.js'
import { createWeixinHistoryReadOperation, type WeixinHistoryReadArgs } from '../../operations/historyRead.js'
import type { WeixinUnreadListArgs } from '../../operations/unreadList.js'
import { createWeixinChatLatestOperation } from '../../operations/chatLatest.js'

export interface ReadCommandOptions {
  domain: CliDomain | undefined | 'invalid'
  targetRef?: string
  targetName?: string
  sinceDays?: string
  maxPages?: string
  name?: string
  /** true 时 stdout 最后一行输出 OperationResult JSON */
  json?: boolean
}

export async function readCommand(opts: ReadCommandOptions): Promise<number> {
  if (opts.domain === 'invalid') {
    console.error('非法 --domain 值（合法值：souyisou/article/chat/chat-history/group-history/chat-latest/unread）')
    return 2
  }
  switch (opts.domain) {
    case 'chat-latest':
      return runCliOperation(createWeixinChatLatestOperation({ localOutput: true }),
        { target_name: opts.targetName! }, { exclusive: true, json: true })
    case 'chat-history':
    case 'group-history': {
      // 数字参数原样转换；非法值（NaN）留给 operation 统一返回 INVALID_ARGUMENT
      const args: WeixinHistoryReadArgs = {
        target_ref: opts.targetRef,
        target_name: opts.targetName,
        since_days: opts.sinceDays === undefined ? undefined : Number(opts.sinceDays),
        max_pages: opts.maxPages === undefined ? undefined : Number(opts.maxPages),
      }
      const operation = opts.domain === 'group-history'
        ? createWeixinHistoryReadOperation({ groupOnly: true })
        : getOperationEntry('weixin_history_read').operation
      return runCliOperation(operation, args, { exclusive: true, json: opts.json })
    }
    case 'unread': {
      const args: WeixinUnreadListArgs = { name: opts.name }
      return runCliOperation(getOperationEntry('weixin_unread_list').operation, args, { exclusive: true, json: opts.json })
    }
    case undefined:
      console.error('read 必须显式指定 --domain（chat-latest / chat-history / group-history / unread）')
      return 2
    default:
      console.error(`read --domain ${opts.domain} 尚未产品化（支持 chat-latest / chat-history / group-history / unread 域）`)
      return 2
  }
}
