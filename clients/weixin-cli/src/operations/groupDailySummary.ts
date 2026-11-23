import { fileURLToPath } from 'node:url'
import { z } from 'zod'
import { runPowerShellDriver, type RunPowerShellDriverFn } from '../platform/powershell.js'
import { runWeixinOperation } from './context.js'
import { CancelledError, CodedOperationError, type WeixinOperation } from './types.js'

const HISTORY_DRIVER = fileURLToPath(new URL('../../../drivers/ps1/history-read.ps1', import.meta.url))
export const dailySummaryShape = {
  target_name: z.string().min(1).max(128),
  business_date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
  tenant_id: z.string().min(1).max(128),
  grant: z.string().regex(/^[A-Za-z0-9_-]{43}$/),
}
const requestSchema = z.object(dailySummaryShape).strict()
type DailySummaryArgs = z.infer<typeof requestSchema>
const historySchema = z.object({ messages: z.array(z.string().min(1)).max(500), partial: z.boolean() })
const summarySchema = z.object({ summary: z.string().min(1).max(4000) })

export function createWeixinGroupDailySummaryOperation(deps: {
  runDriverFn?: RunPowerShellDriverFn
  fetchFn?: typeof fetch
  serverUrl?: string
} = {}): WeixinOperation<DailySummaryArgs> {
  const runDriver = deps.runDriverFn ?? runPowerShellDriver
  const fetchSummary = deps.fetchFn ?? fetch
  return {
    name: 'weixin_group_daily_summary',
    async execute(args, ctx) {
      const result = await runWeixinOperation('readonly', ctx, () => {
        if (!requestSchema.safeParse(args).success || !args.target_name.trim() || /[\x00-\x1f\x7f]/.test(args.target_name)) {
          return '微信总结参数无效'
        }
        return null
      }, async () => {
        ctx.progress({ stage: 'execute', message: '在本机读取已核对群聊的当天文字' })
        const raw = await runDriver({
          script: HISTORY_DRIVER,
          stdin: JSON.stringify({ mode: 'daily', group_only: true, target_name: args.target_name, business_date: args.business_date }),
          signal: ctx.signal, timeoutMs: 120_000, useVisionProxy: false,
        })
        const history = historySchema.safeParse(raw)
        if (!history.success) throw new CodedOperationError('CONTENT_UNAVAILABLE', '微信历史回执无效')
        const { messages, partial } = history.data
        if (!messages.length) {
          if (partial) throw new CodedOperationError('SUMMARY_COVERAGE_UNAVAILABLE', '无法确认当天文字的覆盖范围')
          return { message: '当天未读到文字消息', data: { summary: null, message_count: 0, partial } }
        }

        if (ctx.signal.aborted) throw new CancelledError()
        ctx.progress({ stage: 'execute', message: '正在生成今日总结' })
        let summary: string
        try {
          const base = new URL(deps.serverUrl ?? process.env.AID_WEIXIN_SERVER_URL ?? '')
          const loopback = ['localhost', '127.0.0.1', '[::1]'].includes(base.hostname)
          if (base.username || base.password || (base.protocol !== 'https:' && !(loopback && base.protocol === 'http:'))) {
            throw new Error('Invalid paired service')
          }
          const endpoint = new URL('/api/local-tools/runtime/weixin-summary', base)
          const response = await fetchSummary(endpoint, {
            method: 'POST', redirect: 'error',
            headers: { Authorization: `Bearer ${args.grant}`, 'Content-Type': 'application/json' },
            body: JSON.stringify({ tenant_id: args.tenant_id, business_date: args.business_date, messages }),
            signal: AbortSignal.any([ctx.signal, AbortSignal.timeout(65_000)]),
          })
          if (response.status === 403 && (await response.json())?.code === 'NO_CREDIT') {
            throw new CodedOperationError('NO_CREDIT', '积分余额已耗尽')
          }
          if (!response.ok) throw new Error('Summary service refused the request')
          summary = summarySchema.parse(await response.json()).summary
        } catch (error) {
          if (ctx.signal.aborted) throw new CancelledError()
          if (error instanceof CodedOperationError && error.code === 'NO_CREDIT') throw error
          throw new CodedOperationError('SUMMARY_FAILED', '微信今日总结生成失败')
        }
        return { message: '微信今日总结已生成', data: { summary, message_count: messages.length, partial } }
      })
      // Native errors can contain UI diagnostics and validation errors can contain inputs.
      return result.success ? result : { ...result, message: '微信今日总结未完成', data: {} }
    },
  }
}
