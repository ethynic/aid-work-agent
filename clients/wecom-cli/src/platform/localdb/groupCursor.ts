/** Poll cursors are independent of the CLI watch watermark and bound to an exact target name. */
import { decodeCursor, encodeCursor, type ReadCursor } from './messages.js'
import { CodedOperationError } from '../../operations/types.js'

export function groupCursorError(): CodedOperationError {
  return new CodedOperationError('CURSOR_UNSUPPORTED', '群轮询游标失效或目标不一致；已停止，不自动重建基线或切换群')
}

export function decodeGroupCursor(value: string, targetName: string): ReadCursor {
  const match = /^gp1\|([^|]+)\|(v2\|.*)$/.exec(value)
  if (!match) throw groupCursorError()
  try { if (decodeURIComponent(match[1]!) !== targetName) throw groupCursorError() }
  catch { throw groupCursorError() }
  const cursor = decodeCursor(match[2]!)
  if (!cursor.ok || !cursor.conversationId.startsWith('R:') || cursor.conversationId.length <= 2) throw groupCursorError()
  return cursor
}

export function encodeGroupCursor(cursor: ReadCursor, targetName: string): string {
  return `gp1|${encodeURIComponent(targetName)}|${encodeCursor(cursor)}`
}
