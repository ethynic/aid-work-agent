/**
 * wecom_unread_list operation（M3）：读取主窗口会话列表的未读会话快照（只读，effect=none）。
 *
 * 不打开任何会话、不清除未读角标：PrintWindow 截主窗口（窗口被遮挡/非前台也能出图）
 * → drivers/py/chat_ocr.py unread 模式（会话列表列中心 x∈[0.08w,0.30w]，排除左侧导航栏；
 * 红色角标像素检测 + 裁切放大 OCR 读数）→ 聚合 {name, preview, unread_count, x, y}。
 * 没有未读的会话不出现；x/y 为名称行中心（图像坐标系），供 wecom_watch_poll 直点会话行。
 */
import { fileURLToPath } from 'node:url'
import { runWecomOperation } from './context.js'
import type { OperationResult, OpContext, WecomOperation } from './types.js'
import { runPowerShellDriver, type RunPowerShellDriverFn } from '../platform/powershell.js'

/** dist/src/operations → 包根 drivers/ps1 */
const DRIVER_PATH = fileURLToPath(new URL('../../../drivers/ps1/unread-list.ps1', import.meta.url))

export interface WecomUnreadListArgs {
  name?: string
}

export interface UnreadEntry {
  name: string
  preview: string
  unread_count: number
  /** 名称行中心（图像坐标系，原点 = 主窗口左上角）；watch 直点会话行用 */
  x: number
  y: number
}

/** 驱动 data.unread 防御性归一（PS 5.1 ConvertTo-Json 单元素数组解包怪癖） */
export function parseUnreadEntries(data: Record<string, unknown>): UnreadEntry[] {
  const raw = Array.isArray(data.unread) ? data.unread : data.unread != null ? [data.unread] : []
  return raw
    .map((it) => ({
      name: String((it as { name?: unknown })?.name ?? ''),
      preview: String((it as { preview?: unknown })?.preview ?? ''),
      unread_count: Number((it as { unread_count?: unknown })?.unread_count ?? 0),
      x: Number((it as { x?: unknown })?.x ?? -1),
      y: Number((it as { y?: unknown })?.y ?? -1),
    }))
    .filter((e) => e.name.length > 0 && e.unread_count > 0)
}

export function createWecomUnreadListOperation(
  deps: { runDriverFn?: RunPowerShellDriverFn } = {},
): WecomOperation<WecomUnreadListArgs> {
  const runDriverFn = deps.runDriverFn ?? runPowerShellDriver
  return {
    name: 'wecom_unread_list',
    execute(args: WecomUnreadListArgs, ctx: OpContext): Promise<OperationResult> {
      return runWecomOperation(
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
          let unread = parseUnreadEntries(data)
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
