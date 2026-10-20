/**
 * CLI 子命令 unread：读取会话快照（只读，不开会话不清角标）。
 *
 * 用法：
 *   aid-wecom unread [--name <名>] [--channel local-db|ui] [--focus <名1,名2>] [--json]
 *
 * P2 任务 3.1：缺省按 doctor 状态分派通道——本地库可用返回待回复快照（last_sender 判定，
 * 可发现已读未回复），否则 UI 角标快照；--channel 显式覆盖。--focus 为关注会话名列表
 * （本地库逐名解析，失败进 failed 部分返回；UI 通道忽略并注明）。
 * 与 MCP tool wecom_unread_list 调用同一 operation（业务能力只实现一次）。
 */
import { getOperationEntry } from '../../operations/registry.js'
import { runCliOperation } from '../render.js'
import type { WecomUnreadListArgs } from '../../operations/unreadList.js'

export interface UnreadCommandOptions {
  name?: string
  channel?: string
  /** 逗号分隔的会话名列表（--focus 名1,名2） */
  focus?: string
  /** true 时 stdout 最后一行输出 OperationResult JSON */
  json?: boolean
}

export async function unreadCommand(opts: UnreadCommandOptions): Promise<number> {
  const args: WecomUnreadListArgs = {
    name: opts.name,
    ...(opts.channel !== undefined ? { channel: opts.channel as 'local-db' | 'ui' } : {}),
    ...(opts.focus !== undefined
      ? { focus: opts.focus.split(',').map((s) => s.trim()).filter((s) => s.length > 0) }
      : {}),
  }
  return runCliOperation(getOperationEntry('wecom_unread_list').operation, args, { exclusive: true, json: opts.json })
}
