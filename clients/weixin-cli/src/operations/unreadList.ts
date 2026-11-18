/**
 * weixin_unread_list operation（M2）：读取主窗口会话列表的未读消息（只读，effect=none）。
 *
 * PowerShell 读取原生会话行并在本机核对角标，只返回有未读计数的
 * {name, preview, unread_count}。读取不会打开会话或清除未读。
 */
import { fileURLToPath } from 'node:url'
import { runWeixinOperation } from './context.js'
import type { OperationResult, OpContext, WeixinOperation } from './types.js'
import { runPowerShellDriver, type RunPowerShellDriverFn } from '../platform/powershell.js'

/** dist/src/operations → 包根 drivers/ps1 */
const DRIVER_PATH = fileURLToPath(new URL('../../../drivers/ps1/unread-list.ps1', import.meta.url))

export interface WeixinUnreadListArgs {
  name?: string
}

export interface UnreadEntry {
  name: string
  preview: string
  unread_count: number
}

export function createWeixinUnreadListOperation(
  deps: { runDriverFn?: RunPowerShellDriverFn } = {},
): WeixinOperation<WeixinUnreadListArgs> {
  const runDriverFn = deps.runDriverFn ?? runPowerShellDriver
  return {
    name: 'weixin_unread_list',
    execute(args: WeixinUnreadListArgs, ctx: OpContext): Promise<OperationResult> {
      return runWeixinOperation(
        'readonly',
        ctx,
        () => {
          if (args === null || typeof args !== 'object' || Array.isArray(args)) {
            return '参数必须是对象（当前仅支持可选字段 name: string）'
          }
          if (args.name !== undefined && typeof args.name !== 'string') {
            return 'name 必须是字符串'
          }
          return null
        },
        async () => {
          ctx.progress({ stage: 'execute', message: '读取微信未读会话' })
          const data = await runDriverFn({ script: DRIVER_PATH, signal: ctx.signal, useVisionProxy: false })
          // PS 5.1 ConvertTo-Json 对单元素数组有解包怪癖，TS 侧防御性归一
          const rawUnread = Array.isArray(data.unread) ? data.unread : data.unread != null ? [data.unread] : []
          let unread: UnreadEntry[] = rawUnread.map((it) => ({
            name: String((it as { name?: unknown })?.name ?? ''),
            preview: String((it as { preview?: unknown })?.preview ?? ''),
            unread_count: Number((it as { unread_count?: unknown })?.unread_count ?? 0),
          }))
          const nameFilter = args.name?.trim()
          if (nameFilter) unread = unread.filter((u) => u.name.includes(nameFilter))
          return {
            message: nameFilter ? `未读会话（匹配「${nameFilter}」）${unread.length} 个` : `未读会话 ${unread.length} 个`,
            data: { unread },
          }
        },
      )
    },
  }
}
