/**
 * CLI 子命令 unread：读取主窗口会话列表的未读会话快照（只读，不开会话不清角标）。
 *
 * 用法：
 *   aid-wecom unread [--name <名>] [--json]
 *
 * 与 MCP tool wecom_unread_list 调用同一 operation（业务能力只实现一次）。
 */
import { getOperationEntry } from '../../operations/registry.js'
import { runCliOperation } from '../render.js'
import type { WecomUnreadListArgs } from '../../operations/unreadList.js'

export interface UnreadCommandOptions {
  name?: string
  /** true 时 stdout 最后一行输出 OperationResult JSON */
  json?: boolean
}

export async function unreadCommand(opts: UnreadCommandOptions): Promise<number> {
  const args: WecomUnreadListArgs = { name: opts.name }
  return runCliOperation(getOperationEntry('wecom_unread_list').operation, args, { exclusive: true, json: opts.json })
}
