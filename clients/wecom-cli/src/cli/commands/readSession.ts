/**
 * CLI read-session：只通过受支持企业微信版本的本地库读取消息。
 * target_ref 与 target_name 二选一；旧无账号 ref 拒绝，同名按本地库精确消歧。
 * 日期模式可传稳定 session、date、timezone 和日期游标；均不切换会话或清角标。
 * 旧 --channel ui / --max-pages 不再支持，返回明确错误。
 */
import { getOperationEntry } from '../../operations/registry.js'
import { runCliOperation } from '../render.js'
import type { WecomReadSessionArgs } from '../../operations/readSession.js'
import { randomUUID } from 'node:crypto'
import { failResult } from '../../operations/types.js'

export interface ReadSessionCommandOptions {
  session?: string
  targetRef?: string
  targetName?: string
  maxPages?: string
  sinceDays?: string
  channel?: string
  limit?: string
  cursor?: string
  attachment?: string
  date?: string
  timezone?: string
  includeContext?: boolean
  /** true 时 stdout 最后一行输出 OperationResult JSON */
  json?: boolean
}

export async function readSessionCommand(opts: ReadSessionCommandOptions): Promise<number> {
  let attachment: WecomReadSessionArgs['attachment']
  if (opts.attachment !== undefined) {
    try { attachment = JSON.parse(opts.attachment) }
    catch {
      const result = failResult(randomUUID(), 'INVALID_ARGUMENT', 'attachment须为JSON对象', 'none')
      if (opts.json) console.log(JSON.stringify(result))
      else console.error(result.message)
      return 2
    }
  }
  // 数字参数原样转换；非法值（NaN）留给 operation 统一返回 INVALID_ARGUMENT
  const args: WecomReadSessionArgs = {
    ...(opts.session === undefined ? {} : { session: opts.session }),
    ...(opts.targetRef !== undefined ? { target_ref: opts.targetRef } : {}),
    ...(opts.targetName !== undefined ? { target_name: opts.targetName } : {}),
    max_pages: opts.maxPages === undefined ? undefined : Number(opts.maxPages),
    since_days: opts.sinceDays === undefined ? undefined : Number(opts.sinceDays),
    ...(opts.channel !== undefined ? { channel: opts.channel as WecomReadSessionArgs['channel'] } : {}),
    ...(opts.limit !== undefined ? { limit: Number(opts.limit) } : {}),
    ...(opts.cursor !== undefined ? { cursor: opts.cursor } : {}),
    ...(opts.attachment === undefined ? {} : { attachment }),
    ...(opts.date !== undefined ? { date: opts.date } : {}),
    ...(opts.timezone !== undefined ? { timezone: opts.timezone } : {}),
    ...(opts.includeContext === true ? { include_context: true } : {}),
  }
  return runCliOperation(getOperationEntry('wecom_read_session').operation, args, { exclusive: true, json: opts.json })
}
