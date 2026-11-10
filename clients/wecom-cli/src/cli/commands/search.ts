/**
 * CLI 子命令 search：在本地库匹配会话/联系人名称（只读，不操作窗口）。
 *
 * 用法：
 *   aid-wecom search --query <词> [--type contact|group|any] [--limit N] [--json]
 *
 * 唯一匹配返回 target_ref；无匹配或多匹配返回候选名称供 agent/用户确认。
 * 与 MCP tool wecom_chat_search 调用同一 operation（业务能力只实现一次）。
 */
import { getOperationEntry } from '../../operations/registry.js'
import { runCliOperation } from '../render.js'
import type { WecomChatSearchArgs } from '../../operations/chatSearch.js'

export interface SearchCommandOptions {
  query?: string
  type?: string
  limit?: string
  /** true 时 stdout 最后一行输出 OperationResult JSON */
  json?: boolean
}

export async function searchCommand(opts: SearchCommandOptions): Promise<number> {
  // limit 原样转数字；非法值（NaN）留给 operation 统一返回 INVALID_ARGUMENT
  const args: WecomChatSearchArgs = {
    query: opts.query as string,
    type: opts.type as WecomChatSearchArgs['type'],
    limit: opts.limit === undefined ? undefined : Number(opts.limit),
  }
  return runCliOperation(getOperationEntry('wecom_chat_search').operation, args, { exclusive: true, json: opts.json })
}
