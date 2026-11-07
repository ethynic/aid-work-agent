/** CLI unread：只通过受支持企业微信版本的本地库读取待回复快照。 */
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
