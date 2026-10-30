import { randomUUID } from 'node:crypto'
import { getOperationEntry } from '../../operations/registry.js'
import { failResult } from '../../operations/types.js'
import { runCliOperation } from '../render.js'
import { flagString, hasFlag, type ParsedArgs } from '../args.js'

export async function readSessionsCommand(args: ParsedArgs): Promise<number> {
  let sessions: unknown
  let invalid: string | undefined
  for (const [key, value] of args.flags) {
    if (!['sessions', 'cursor', 'limit', 'json'].includes(key)) invalid = `未知参数 --${key}`
    else if (key === 'json' ? value !== true : typeof value !== 'string') invalid = `参数 --${key}缺值或格式无效`
  }
  if (args.positional.length !== 1) invalid = '批量读取不接受额外位置参数'
  const raw = flagString(args, 'sessions')
  if (raw !== undefined) {
    try { sessions = JSON.parse(raw) }
    catch { invalid = 'sessions须为JSON字符串数组' }
  }
  if (invalid) {
    const result = failResult(randomUUID(), 'INVALID_ARGUMENT', invalid, 'none')
    if (hasFlag(args, 'json')) console.log(JSON.stringify(result))
    else console.error(invalid)
    return 2
  }
  return runCliOperation(getOperationEntry('wecom_read_sessions').operation, {
    ...(raw === undefined ? {} : { sessions }),
    ...(flagString(args, 'cursor') === undefined ? {} : { cursor: flagString(args, 'cursor') }),
    ...(flagString(args, 'limit') === undefined ? {} : { limit: Number(flagString(args, 'limit')) }),
  }, { exclusive: true, json: hasFlag(args, 'json'), renderData(data) { console.log(JSON.stringify(data)) } })
}
