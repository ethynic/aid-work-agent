/**
 * CLI 子命令 read：M2 落地 chat-history / unread 两个域。
 *
 * 用法：
 *   aid-weixin read --domain chat-history --target-ref <ref> [--since-days N] [--max-pages N]
 *   aid-weixin read --domain unread [--name <名>]
 *
 * article 域尚未产品化，明确拒绝（退出码 2）。
 * 与 MCP tool weixin_history_read / weixin_unread_list 调用同一 operation。
 */
import { getOperationEntry } from '../../operations/registry.js'
import { runCliOperation } from '../render.js'
import type { CliDomain } from '../args.js'
import type { WeixinHistoryReadArgs } from '../../operations/historyRead.js'
import type { WeixinUnreadListArgs } from '../../operations/unreadList.js'

export interface ReadCommandOptions {
  domain: CliDomain | undefined | 'invalid'
  targetRef?: string
  sinceDays?: string
  maxPages?: string
  name?: string
  /** true 时 stdout 最后一行输出 OperationResult JSON */
  json?: boolean
}

export async function readCommand(opts: ReadCommandOptions): Promise<number> {
  if (opts.domain === 'invalid') {
    console.error('非法 --domain 值（合法值：souyisou/article/chat/chat-history/unread）')
    return 2
  }
  switch (opts.domain) {
    case 'chat-history': {
      // 数字参数原样转换；非法值（NaN）留给 operation 统一返回 INVALID_ARGUMENT
      const args: WeixinHistoryReadArgs = {
        target_ref: opts.targetRef as string,
        since_days: opts.sinceDays === undefined ? undefined : Number(opts.sinceDays),
        max_pages: opts.maxPages === undefined ? undefined : Number(opts.maxPages),
      }
      return runCliOperation(getOperationEntry('weixin_history_read').operation, args, { exclusive: true, json: opts.json })
    }
    case 'unread': {
      const args: WeixinUnreadListArgs = { name: opts.name }
      return runCliOperation(getOperationEntry('weixin_unread_list').operation, args, { exclusive: true, json: opts.json })
    }
    case undefined:
      console.error('read 必须显式指定 --domain（chat-history / unread）')
      return 2
    default:
      console.error(`read --domain ${opts.domain} 尚未产品化（M2 仅支持 chat-history / unread 域）`)
      return 2
  }
}
