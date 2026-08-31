/**
 * wecom_chat_search operation（M2）：企业微信搜索联系人/群聊，只读。
 *
 * 流程：TS 校验参数 → spawn drivers/ps1/chat-search.ps1（解析主窗口 → PostMessage
 * 点搜索框 → 输入 query（中文 WM_CHAR 逐字）→ 等 SearchResultWindow2 → 截图 OCR
 * （drivers/py/chat_ocr.py search 模式）→ 关闭搜索 overlay 恢复原状 → DRIVER_JSON
 * 输出 items）→ TS 按 type/limit 过滤并为每个 item 签发 target_ref 一并返回。
 *
 * 语义：只读，不打开会话、不读取历史消息（effect 恒为 none）；
 * 无结果 → TARGET_NOT_FOUND；overlay 未出现 / 页面结构变化 → UI_CHANGED。
 */
import { fileURLToPath } from 'node:url'
import { runWecomOperation } from './context.js'
import { CodedOperationError, type OperationResult, type OpContext, type WecomOperation } from './types.js'
import { runPowerShellDriver, type RunPowerShellDriverFn } from '../platform/powershell.js'
import { createTargetRef, type CreateTargetRefFn } from '../platform/targetRef.js'

/** dist/src/operations → 包根 drivers/ps1 */
const DRIVER_PATH = fileURLToPath(new URL('../../../drivers/ps1/chat-search.ps1', import.meta.url))

export interface WecomChatSearchArgs {
  query: string
  /** contact=联系人 / group=群聊 / any=全部（含聊天记录等分区），默认 any */
  type?: 'contact' | 'group' | 'any'
  limit?: number
}

export interface ChatSearchItem {
  name: string
  subtitle: string
  /** 搜索结果分区原名：联系人 / 群聊 / 聊天记录 等 */
  section: string
  target_ref: string
}

const SEARCH_TYPES = ['contact', 'group', 'any'] as const
const DEFAULT_LIMIT = 10
const MAX_LIMIT = 20
const MAX_QUERY_LENGTH = 100

/** 搜索结果分区 → target_ref 目标分类（ref 的 type 仅作提示，发送时仍由驱动 OCR 校验标题） */
function sectionToTargetType(section: string): string {
  if (section === '联系人') return 'contact'
  if (section === '群聊') return 'group'
  return 'other'
}

/** type 过滤：contact 只留联系人分区，group 只留群聊分区，any 全留 */
function matchType(section: string, type: 'contact' | 'group' | 'any'): boolean {
  if (type === 'any') return true
  return sectionToTargetType(section) === type
}

export function createWecomChatSearchOperation(
  deps: { runDriverFn?: RunPowerShellDriverFn; createRefFn?: CreateTargetRefFn } = {},
): WecomOperation<WecomChatSearchArgs> {
  const runDriverFn = deps.runDriverFn ?? runPowerShellDriver
  const createRefFn = deps.createRefFn ?? createTargetRef
  return {
    name: 'wecom_chat_search',
    execute(args: WecomChatSearchArgs, ctx: OpContext): Promise<OperationResult> {
      return runWecomOperation(
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
            return 'type 必须是 contact/group/any'
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
            args: ['-Query', args.query.trim()],
            signal: ctx.signal,
          })
          // PS 5.1 ConvertTo-Json 对单元素数组有解包怪癖，TS 侧防御性归一
          const rawItems = Array.isArray(data.items) ? data.items : data.items != null ? [data.items] : []
          const items: ChatSearchItem[] = rawItems
            .map((it) => ({
              name: String((it as { name?: unknown })?.name ?? ''),
              subtitle: String((it as { subtitle?: unknown })?.subtitle ?? ''),
              section: String((it as { section?: unknown })?.section ?? ''),
            }))
            .filter((it) => it.name.length > 0 && matchType(it.section, type))
            .slice(0, limit)
            .map((it) => ({ ...it, target_ref: createRefFn(it.name, sectionToTargetType(it.section), it.subtitle) }))
          if (items.length === 0) {
            throw new CodedOperationError('TARGET_NOT_FOUND', `搜索「${args.query}」无匹配结果（type=${type}）`)
          }
          return {
            message: `搜索「${args.query}」完成：${items.length} 个结果`,
            data: { items, query: args.query, type },
          }
        },
      )
    },
  }
}
