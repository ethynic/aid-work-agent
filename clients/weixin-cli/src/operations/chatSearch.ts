/**
 * weixin_chat_search operation（设计 §5.3，M2）：微信全局搜索好友/群，只读。
 *
 * 流程：TS 校验参数 → spawn drivers/ps1/chat-search.ps1（激活主窗口 → PostMessage
 * 点击搜索框 → WM_CHAR 输入 query → 截搜索结果 overlay → Kimi 视觉抽取结果列表 →
 * 关闭面板 → DRIVER_JSON 输出 items）→ TS 为每个 item 签发 target_ref 一并返回。
 *
 * 语义：只读，不打开会话、不读取历史消息（effect 恒为 none）；
 * 无结果 → TARGET_NOT_FOUND；找不到 overlay / 单栏布局 → UI_CHANGED。
 */
import { fileURLToPath } from 'node:url'
import { runWeixinOperation } from './context.js'
import type { OperationResult, OpContext, WeixinOperation } from './types.js'
import { runPowerShellDriver, type RunPowerShellDriverFn } from '../platform/powershell.js'
import { createTargetRef, type CreateTargetRefFn } from '../platform/targetRef.js'

/** dist/src/operations → 包根 drivers/ps1 */
const DRIVER_PATH = fileURLToPath(new URL('../../../drivers/ps1/chat-search.ps1', import.meta.url))

export interface WeixinChatSearchArgs {
  query: string
  type?: 'friend' | 'group' | 'any'
  limit?: number
}

export interface ChatSearchItem {
  label: string
  section: string
  /** 头像形态判断的目标类型：group=拼图头像群聊，friend=单头像好友，other=功能项等 */
  kind: string
  target_ref: string
}

const SEARCH_TYPES = ['friend', 'group', 'any'] as const
const DEFAULT_LIMIT = 10
const MAX_LIMIT = 20
const MAX_QUERY_LENGTH = 100

/** 搜索结果 → target_ref 目标分类：优先用驱动按头像形态判定的 kind，缺省退回分组名映射（ref 的 type 仅作提示，发送时仍由驱动校验标题） */
function itemToTargetType(kind: string, section: string): string {
  if (kind === 'friend' || kind === 'group') return kind
  if (section === '联系人') return 'friend'
  if (section === '群聊') return 'group'
  return 'other'
}

export function createWeixinChatSearchOperation(
  deps: { runDriverFn?: RunPowerShellDriverFn; createRefFn?: CreateTargetRefFn } = {},
): WeixinOperation<WeixinChatSearchArgs> {
  const runDriverFn = deps.runDriverFn ?? runPowerShellDriver
  const createRefFn = deps.createRefFn ?? createTargetRef
  return {
    name: 'weixin_chat_search',
    execute(args: WeixinChatSearchArgs, ctx: OpContext): Promise<OperationResult> {
      return runWeixinOperation(
        'readonly',
        ctx,
        () => {
          if (args === null || typeof args !== 'object' || Array.isArray(args)) {
            return '参数必须是对象（query 必填；type/limit 可选）'
          }
          if (typeof args.query !== 'string' || args.query.trim().length === 0) {
            return 'query 必填且必须是非空字符串'
          }
          if (args.query.length > MAX_QUERY_LENGTH) {
            return `query 长度不能超过 ${MAX_QUERY_LENGTH} 字`
          }
          if (args.type !== undefined && !(SEARCH_TYPES as readonly string[]).includes(args.type)) {
            return 'type 必须是 friend/group/any'
          }
          if (args.limit !== undefined && (!Number.isInteger(args.limit) || args.limit < 1 || args.limit > MAX_LIMIT)) {
            return `limit 必须是 1..${MAX_LIMIT} 的整数`
          }
          return null
        },
        async () => {
          const type = args.type ?? 'any'
          const limit = args.limit ?? DEFAULT_LIMIT
          ctx.progress({ stage: 'execute', message: `搜索「${args.query}」（type=${type}）` })
          const data = await runDriverFn({
            script: DRIVER_PATH,
            args: ['-Query', args.query.trim(), '-Type', type, '-Limit', String(limit)],
            signal: ctx.signal,
          })
          // PS 5.1 ConvertTo-Json 对单元素数组有解包怪癖，TS 侧防御性归一
          const rawItems = Array.isArray(data.items) ? data.items : data.items != null ? [data.items] : []
          const items: ChatSearchItem[] = rawItems.slice(0, limit).map((it) => {
            const label = String((it as { label?: unknown })?.label ?? '')
            const section = String((it as { section?: unknown })?.section ?? '')
            const kind = String((it as { kind?: unknown })?.kind ?? '')
            return { label, section, kind, target_ref: createRefFn(label, itemToTargetType(kind, section)) }
          })
          return {
            message: `搜索「${args.query}」完成：${items.length} 个结果`,
            data: { items, query: args.query, type },
          }
        },
      )
    },
  }
}
