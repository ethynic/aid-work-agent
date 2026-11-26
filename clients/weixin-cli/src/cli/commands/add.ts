import { randomUUID } from 'node:crypto'
import { createWeixinFriendAddOperation } from '../../operations/friendAdd.js'
import { failResult } from '../../operations/types.js'
import { flagString, hasFlag, type ParsedArgs } from '../args.js'
import { runCliOperation } from '../render.js'

const operation = createWeixinFriendAddOperation()
const allowedFlags = new Set(['domain', 'identifier', 'verify-message', 'search-only', 'json'])

export async function addCommand(args: ParsedArgs): Promise<number> {
  const json = hasFlag(args, 'json')
  const searchOnly = hasFlag(args, 'search-only')
  const malformed = args.positional.length !== 1
    || args.duplicateFlags.size > 0
    || [...args.flags].some(([name, value]) => !allowedFlags.has(name)
      || (name === 'json' || name === 'search-only' ? value !== true : typeof value !== 'string'))
    || (searchOnly && hasFlag(args, 'verify-message'))
  if (malformed || flagString(args, 'domain') !== 'friend') {
    const message = '用法：add --domain friend --identifier <手机号或微信号> [--verify-message <验证语> | --search-only] [--json]'
    if (json) console.log(JSON.stringify(failResult(randomUUID(), 'INVALID_ARGUMENT', message, 'none')))
    else console.error(message)
    return 2
  }

  return runCliOperation(operation, {
    identifier: flagString(args, 'identifier'),
    verifyMessage: flagString(args, 'verify-message'),
    searchOnly,
  }, { exclusive: true, json })
}
