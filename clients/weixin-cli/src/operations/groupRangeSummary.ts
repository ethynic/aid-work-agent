import { fileURLToPath } from 'node:url'
import { z } from 'zod'
import { requestWeixinSummary, type SummaryDependencies } from './groupDailySummary.js'
import { runPowerShellDriver } from '../platform/powershell.js'
import { runWeixinOperation } from './context.js'
import { CancelledError, CodedOperationError, type WeixinOperation } from './types.js'

const HISTORY_DRIVER = fileURLToPath(new URL('../../../drivers/ps1/history-read.ps1', import.meta.url))
const utc = z.string().regex(/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$/)
export const rangeSummaryShape = {
  target_name: z.string().min(1).max(128),
  start_at: utc,
  end_at: utc,
  tenant_id: z.string().min(1).max(128),
  grant: z.string().regex(/^[A-Za-z0-9_-]{43}$/),
  allow_open_group: z.literal(true).optional(),
}
const schema = z.object(rangeSummaryShape).strict()
type RangeSummaryArgs = z.infer<typeof schema>
const historySchema = z.object({ messages: z.array(z.string().min(1)).max(500), partial: z.boolean() })

export function createWeixinGroupRangeSummaryOperation(deps: SummaryDependencies = {}): WeixinOperation<RangeSummaryArgs> {
  return {
    name: 'weixin_group_range_summary',
    async execute(args, ctx) {
      const result = await runWeixinOperation('readonly', ctx, () => {
        if (!schema.safeParse(args).success || !args.target_name.trim() || /[\x00-\x1f\x7f]/.test(args.target_name)) {
          return '微信总结参数无效'
        }
        const start = Date.parse(args.start_at), end = Date.parse(args.end_at)
        if (!Number.isFinite(start) || !Number.isFinite(end) || start >= end || end - start > 168 * 60 * 60 * 1000
          || new Date(start).toISOString() !== args.start_at || new Date(end).toISOString() !== args.end_at) {
          return '微信总结时段无效'
        }
        return null
      }, async () => {
        const { target_name, start_at, end_at, tenant_id, grant, allow_open_group } = args
        ctx.progress({ stage: 'execute', message: '在本机核对微信群聊和时间范围' })
        const raw = await (deps.runDriverFn ?? runPowerShellDriver)({
          script: HISTORY_DRIVER,
          stdin: JSON.stringify({ mode: 'range', target_name, start_at, end_at, allow_open_group }),
          signal: ctx.signal, timeoutMs: 120_000, useVisionProxy: false,
        })
        const history = historySchema.safeParse(raw)
        if (!history.success || history.data.messages.reduce((count, item) => count + item.length, 0) > 20000) {
          throw new CodedOperationError('CONTENT_UNAVAILABLE', '微信历史回执无效')
        }
        const { messages, partial } = history.data
        if (!messages.length) {
          if (partial) throw new CodedOperationError('SUMMARY_COVERAGE_UNAVAILABLE', '无法确认时段内文字的覆盖情况')
          return { message: '所选时间段未读到文字消息', data: { summary: null, message_count: 0, partial } }
        }
        if (ctx.signal.aborted) throw new CancelledError()
        ctx.progress({ stage: 'execute', message: '正在生成微信群聊总结' })
        const summary = await requestWeixinSummary(deps, grant, { tenant_id, start_at, end_at, messages }, ctx.signal)
        return { message: '微信群聊总结已生成', data: { summary, message_count: messages.length, partial } }
      })
      return result.success ? result : { ...result, message: '微信群聊总结未完成', data: {} }
    },
  }
}
