/**
 * CLI 子命令 search：M2 落地 chat 域（微信全局搜索好友/群）。
 *
 * 用法：
 *   aid-weixin search --domain chat --query <词> [--type friend|group|any] [--limit N]
 *
 * souyisou/article 域尚未产品化，明确拒绝（退出码 2），不做半成品行为。
 * 与 MCP tool weixin_chat_search 调用同一 operation（业务能力只实现一次）。
 */
import { getOperationEntry } from '../../operations/registry.js'
import { runCliOperation } from '../render.js'
import type { CliDomain } from '../args.js'
import type { WeixinChatSearchArgs } from '../../operations/chatSearch.js'

export interface SearchCommandOptions {
  domain: CliDomain | undefined | 'invalid'
  query?: string
  type?: string
  limit?: string
  /** true 时 stdout 最后一行输出 OperationResult JSON */
  json?: boolean
}

export async function searchCommand(opts: SearchCommandOptions): Promise<number> {
  if (opts.domain === 'invalid') {
    console.error('非法 --domain 值（合法值：souyisou/article/chat/chat-history/unread）')
    return 2
  }
  if (opts.domain === undefined) {
    console.error('search 必须显式指定 --domain（如 search --domain chat --query <词>）')
    return 2
  }
  if (opts.domain !== 'chat') {
    console.error(`search --domain ${opts.domain} 尚未产品化（M2 仅支持 chat 域）`)
    return 2
  }
  // limit 原样转数字；非法值（NaN）留给 operation 统一返回 INVALID_ARGUMENT
  const args: WeixinChatSearchArgs = {
    query: opts.query as string,
    type: opts.type as WeixinChatSearchArgs['type'],
    limit: opts.limit === undefined ? undefined : Number(opts.limit),
  }
  return runCliOperation(getOperationEntry('weixin_chat_search').operation, args, { exclusive: true, json: opts.json })
}
