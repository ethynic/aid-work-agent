/** 群查询的 CLI 薄适配。 */
import { randomUUID } from 'node:crypto'
import { getOperationEntry } from '../../operations/registry.js'
import { failResult } from '../../operations/types.js'
import { runCliOperation } from '../render.js'
import { flagString, hasFlag, type ParsedArgs } from '../args.js'

/** 新群命令严格校验，避免缺值/拼错时间参数静默扩大读取范围。 */
export async function groupsCommand(args: ParsedArgs): Promise<number> {
  const list = args.positional[0] === 'list-groups'
  const allowed = list ? ['query', 'limit', 'json'] : ['target-name', 'limit', 'from-time', 'to-time', 'json']
  let invalid = args.positional.length !== 1 ? '群查询不接受额外位置参数' : null
  for (const [key, value] of args.flags) {
    if (!allowed.includes(key)) invalid = `未知参数 --${key}`
    else if (key === 'json' ? value !== true : typeof value !== 'string') invalid = `参数 --${key}${key === 'json' ? '不接受值' : '必须提供值'}`
    if (invalid) break
  }
  if (invalid) {
    const result = failResult(randomUUID(), 'INVALID_ARGUMENT', invalid, 'none')
    if (hasFlag(args, 'json')) console.log(JSON.stringify(result))
    else console.error(`❌ ${invalid}（code=INVALID_ARGUMENT）`)
    return 2
  }
  if (list) return listGroupsCommand({ query: flagString(args, 'query'), limit: flagString(args, 'limit'), json: hasFlag(args, 'json') })
  return readGroupCommand({ targetName: flagString(args, 'target-name'), limit: flagString(args, 'limit'),
    fromTime: flagString(args, 'from-time'), toTime: flagString(args, 'to-time'), json: hasFlag(args, 'json') })
}

export async function listGroupsCommand(opts: { query?: string; limit?: string; json?: boolean }): Promise<number> {
  return runCliOperation(getOperationEntry('wecom_group_list').operation, {
    ...(opts.query === undefined ? {} : { query: opts.query }),
    ...(opts.limit === undefined ? {} : { limit: Number(opts.limit) }),
  }, { exclusive: true, json: opts.json, renderData(data) {
    for (const group of data.groups as Array<{ name: string; conversation_id: string }>) console.log(`${group.name}\t${group.conversation_id}`)
  } })
}

export async function readGroupCommand(opts: {
  targetName?: string; limit?: string; fromTime?: string; toTime?: string; json?: boolean
}): Promise<number> {
  return runCliOperation(getOperationEntry('wecom_group_read').operation, {
    target_name: opts.targetName,
    ...(opts.limit === undefined ? {} : { limit: Number(opts.limit) }),
    ...(opts.fromTime === undefined ? {} : { from_time: opts.fromTime }),
    ...(opts.toTime === undefined ? {} : { to_time: opts.toTime }),
  }, { exclusive: true, json: opts.json, renderData(data) {
    for (const msg of data.messages as Array<{ time: string; sender_id: string | null; sender_name: string | null; text: string; kind?: string; parse_trusted: boolean }>) {
      const warning = msg.kind === 'text' && !msg.parse_trusted ? '[正文解析待核验] ' : ''
      const sender = msg.sender_name ?? `未知用户(${msg.sender_id ?? '无ID'})`
      console.log(`[${msg.time}] ${sender}: ${warning}${msg.text}`)
    }
    if (data.naming_incomplete) console.log('部分用户姓名未能从本地库确认，已保留ID并标明未知用户')
    if (data.invalid_time_count) console.log(`时间异常未列入时间线：${data.invalid_time_count} 条`)
  } })
}
