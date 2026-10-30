import { runWecomOperation } from './context.js'
import type { WecomOperation } from './types.js'
import { readBusinessSessions, validateSessionRead, type SessionReadArgs } from '../platform/localdb/sessions.js'
import type { LocaldbReadDeps } from '../platform/localdb/readSession.js'

export function createWecomReadSessionsOperation(local: LocaldbReadDeps = {}): WecomOperation<SessionReadArgs> {
  return { name: 'wecom_read_sessions', execute: (args, ctx) => runWecomOperation('readonly', ctx,
    () => {
      if (args && typeof args === 'object' && Object.keys(args).some(key => !['sessions', 'cursor', 'limit'].includes(key))) return '批量读取仅接受sessions、cursor、limit'
      return validateSessionRead(args)
    }, opCtx => readBusinessSessions(args, opCtx, local)) }
}
