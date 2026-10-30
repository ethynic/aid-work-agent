/** local-db 群查询；CLI/MCP 共用，不执行 UI 搜索或切换会话。 */
import { runWecomOperation } from './context.js'
import type { WecomOperation } from './types.js'
import { checkCancelled, probeCurrentAccount } from '../platform/localdb/identity.js'
import { queryLocaldbGroups, type GroupQueryArgs } from '../platform/localdb/groups.js'
import type { LocaldbReadDeps } from '../platform/localdb/readSession.js'

export interface GroupListArgs { query?: string; limit?: number }
export interface GroupReadArgs { target_name: string; limit?: number; from_time?: string; to_time?: string }
export interface GroupPollArgs { target_name: string; cursor?: string }

/** One bounded local read; scheduling and cursor persistence belong to Runner. */
export function createWecomGroupPollOperation(local: LocaldbReadDeps = {}): WecomOperation<GroupPollArgs> {
  return { name: 'wecom_group_poll', execute: (args, ctx) => runWecomOperation('readonly', ctx, () => {
    if (!args || typeof args !== 'object' || Array.isArray(args) ||
        typeof args.target_name !== 'string' || !args.target_name.trim() || args.target_name.length > 100 ||
        Object.keys(args).some(key => key !== 'target_name' && key !== 'cursor')) return '只接受完整群名target_name及可选cursor'
    if (args.cursor !== undefined && (typeof args.cursor !== 'string' || !args.cursor || args.cursor.length > 2048)) return 'cursor必须为原轮询游标'
    return null
  }, async opCtx => {
    const current = await (local.probeAccountFn ?? probeCurrentAccount)(opCtx)
    checkCancelled(opCtx.signal)
    return queryLocaldbGroups({ targetName: args.target_name.trim(), cursor: args.cursor,
      limit: args.cursor === undefined ? 10 : 50, poll: true }, opCtx, current, local)
  }) }
}

/** 无时区的完整时间固定按北京时间解释；不依赖执行电脑的时区。 */
export function parseGroupTime(value: string): number {
  const match = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(Z|[+-]\d{2}:\d{2})?$/.exec(value)
  if (!match) throw new Error('时间须为 YYYY-MM-DD HH:mm:ss（北京时间）或带时区的 RFC3339，精确到秒')
  const [, y, mo, d, h, mi, s, zone] = match
  const [year, month, day, hour, minute, second] = [y, mo, d, h, mi, s].map(Number) as [number, number, number, number, number, number]
  const calendar = new Date(Date.UTC(year, month - 1, day, hour, minute, second))
  if (year < 1970 || calendar.getUTCFullYear() !== year || calendar.getUTCMonth() !== month - 1 ||
      calendar.getUTCDate() !== day || calendar.getUTCHours() !== hour || calendar.getUTCMinutes() !== minute ||
      calendar.getUTCSeconds() !== second) throw new Error('时间包含无效日期或时分秒')
  if (zone && zone !== 'Z' && (Number(zone.slice(1, 3)) > 23 || Number(zone.slice(4)) > 59)) {
    throw new Error('时间的时区偏移无效')
  }
  const seconds = Date.parse(`${y}-${mo}-${d}T${h}:${mi}:${s}${zone ?? '+08:00'}`) / 1000
  if (!Number.isSafeInteger(seconds) || seconds < 0) throw new Error('时间须为有效的非负 Unix 秒时间')
  return seconds
}

function validateCommon(args: GroupListArgs | GroupReadArgs): string | null {
  if (!args || typeof args !== 'object' || Array.isArray(args)) return '参数必须是对象'
  if (args.limit !== undefined && (!Number.isSafeInteger(args.limit) || args.limit < 1)) return 'limit 必须为正安全整数'
  return null
}

export function createWecomGroupListOperation(local: LocaldbReadDeps = {}): WecomOperation<GroupListArgs> {
  return {
    name: 'wecom_group_list',
    execute: (args, ctx) => runWecomOperation('readonly', ctx, () => {
      const invalid = validateCommon(args)
      if (invalid) return invalid
      if (args.query !== undefined && (typeof args.query !== 'string' || !args.query.trim() || args.query.length > 100)) {
        return 'query 须为1–100字的非空关键词；列出群时不传 query'
      }
      return null
    }, async opCtx => {
      const current = await (local.probeAccountFn ?? probeCurrentAccount)(opCtx)
      checkCancelled(opCtx.signal)
      return queryLocaldbGroups({ query: args.query?.trim(), limit: args.limit ?? 100 }, opCtx, current, local)
    }),
  }
}

export function createWecomGroupReadOperation(local: LocaldbReadDeps = {}): WecomOperation<GroupReadArgs> {
  return {
    name: 'wecom_group_read',
    execute(args, ctx) {
      let from: number | undefined
      let to: number | undefined
      return runWecomOperation('readonly', ctx, () => {
        const invalid = validateCommon(args)
        if (invalid) return invalid
        if (typeof args.target_name !== 'string' || !args.target_name.trim() || args.target_name.length > 100) {
          return 'target_name 须为1–100字的完整群名'
        }
        for (const field of ['from_time', 'to_time'] as const) {
          if (args[field] !== undefined && typeof args[field] !== 'string') return `${field} 必须为时间字符串`
        }
        try {
          from = args.from_time === undefined ? undefined : parseGroupTime(args.from_time)
          to = args.to_time === undefined ? undefined : parseGroupTime(args.to_time)
        } catch (err) { return err instanceof Error ? err.message : '时间格式无效' }
        if (from !== undefined && to !== undefined && from > to) return 'from_time 不能晚于 to_time'
        return null
      }, async opCtx => {
        const current = await (local.probeAccountFn ?? probeCurrentAccount)(opCtx)
        checkCancelled(opCtx.signal)
        const query: GroupQueryArgs = { targetName: args.target_name.trim(), limit: args.limit ?? 100, from, to }
        return queryLocaldbGroups(query, opCtx, current, local)
      })
    },
  }
}
