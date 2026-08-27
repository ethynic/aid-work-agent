/**
 * weixin_unread_list operation（M2）：读取主窗口会话列表的未读消息（只读，effect=none）。
 *
 * 不打开任何会话、不清除未读角标：PrintWindow 截主窗口（窗口被遮挡/非前台也能出图）
 * → drivers/py/unread_list.py（RapidOCR，只取窗口宽 29% 以左的会话列表区）→
 * 按 y 聚合出行 {name, preview, unread_count}。没有未读的会话不出现。
 * 单栏模式（无左侧会话列表）→ UI_CHANGED（message 说明需恢复两栏）。
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
          ctx.progress({ stage: 'execute', message: '截取主窗口并 OCR 会话列表' })
          const data = await runDriverFn({ script: DRIVER_PATH, signal: ctx.signal })
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
