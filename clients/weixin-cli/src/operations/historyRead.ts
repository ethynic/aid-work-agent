import { randomUUID } from 'node:crypto'
import { writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { z } from 'zod'
import { runPowerShellDriver, type RunPowerShellDriverFn } from '../platform/powershell.js'
import { verifyTargetRef, type VerifyTargetRefFn } from '../platform/targetRef.js'
import { runWeixinOperation } from './context.js'
import { CodedOperationError, type WeixinOperation } from './types.js'

const HISTORY_CAPTURE_PATH = fileURLToPath(new URL('../../../drivers/ps1/history-read.ps1', import.meta.url))
const DEFAULT_MAX_PAGES = 3
const MAX_PAGES_LIMIT = 10
const INLINE_MESSAGE_LIMIT = 200
const HISTORY_TIMEOUT_MS = 600_000

const historyRowSchema = z.object({
  kind: z.enum(['text', 'time', 'media', 'unknown']),
  text: z.string(),
  sender: z.enum(['self', 'peer', 'system', 'unknown']),
  sender_name: z.string().nullish(),
  runtime_id: z.string(),
  clipped: z.boolean(),
})
type HistoryRow = z.infer<typeof historyRowSchema>

const historyCaptureSchema = z.object({
  title: z.string(),
  is_group: z.boolean(),
  pages: z.array(z.array(historyRowSchema)).min(1).max(MAX_PAGES_LIMIT),
})

export interface WeixinHistoryReadArgs {
  target_ref?: string
  target_name?: string
  since_days?: number
  max_pages?: number
}

export interface HistoryMessage {
  sender: 'me' | 'peer' | 'time' | 'unknown'
  text: string
  sender_name?: string | null
  kind?: 'media' | 'unknown'
}

function sameHistoryRow(earlier: HistoryRow, later: HistoryRow): boolean {
  return Boolean(earlier.runtime_id && earlier.runtime_id === later.runtime_id
    && earlier.kind === later.kind && earlier.text === later.text
    && (earlier.sender === later.sender || earlier.sender === 'unknown' || later.sender === 'unknown')
    && (!earlier.sender_name || !later.sender_name || earlier.sender_name === later.sender_name))
}

function mergeHistoryPages(pages: HistoryRow[][]): HistoryRow[] {
  let merged = pages[0]!
  for (const older of pages.slice(1)) {
    let overlap = Math.min(older.length, merged.length)
    for (; overlap > 0; overlap--) {
      if (older.slice(-overlap).every((row, index) => sameHistoryRow(row, merged[index]!))) break
    }
    if (overlap === 0) throw new CodedOperationError('CONTENT_UNAVAILABLE', '无法确认微信历史分页连续，请缩小读取范围')
    const shared = older.slice(-overlap).map((earlier, index) => {
      const later = merged[index]!
      return {
        ...later,
        sender: later.sender === 'unknown' ? earlier.sender : later.sender,
        sender_name: later.sender_name ?? earlier.sender_name,
      }
    })
    merged = [...older.slice(0, -overlap), ...shared, ...merged.slice(overlap)]
  }
  return merged
}

function toHistoryMessage(row: HistoryRow, isGroup: boolean): HistoryMessage {
  if (row.kind === 'time') return { sender: 'time', text: row.text }
  const sender = row.sender === 'self' ? 'me' : row.sender === 'peer' ? 'peer' : 'unknown'
  return {
    sender,
    text: row.text,
    ...(isGroup && sender !== 'me' ? { sender_name: row.sender_name ?? null } : {}),
    ...(row.kind !== 'text' ? { kind: row.kind } : {}),
  }
}

function renderHistory(messages: HistoryMessage[], target: string): string {
  return messages.map((message) => {
    const speaker = message.sender === 'time' ? '时间' : message.sender === 'me' ? '我'
      : message.sender === 'peer' ? message.sender_name ?? (message.sender_name === null ? '未确认' : target) : '未确认'
    return `[${speaker}] ${message.text}`
  }).join('\n')
}

export function createWeixinHistoryReadOperation(deps: {
  groupOnly?: boolean
  runDriverFn?: RunPowerShellDriverFn
  verifyRefFn?: VerifyTargetRefFn
  writeFileFn?: (path: string, contents: string) => Promise<void>
} = {}): WeixinOperation<WeixinHistoryReadArgs> {
  const runDriverFn = deps.runDriverFn ?? runPowerShellDriver
  const verifyRefFn = deps.verifyRefFn ?? verifyTargetRef
  const writeFileFn = deps.writeFileFn ?? ((path, contents) => writeFile(path, contents, { flag: 'wx', mode: 0o600 }))
  return {
    name: 'weixin_history_read',
    execute(args, ctx) {
      return runWeixinOperation('readonly', ctx, () => {
        if (args === null || typeof args !== 'object' || Array.isArray(args)) return '参数必须是对象'
        if ((args.target_ref === undefined) === (args.target_name === undefined)) return 'target_ref 与 target_name 必须且只能提供一项'
        if (args.target_ref !== undefined && (typeof args.target_ref !== 'string' || !args.target_ref)) return 'target_ref 必须是非空字符串'
        if (args.target_name !== undefined && (typeof args.target_name !== 'string' || !args.target_name.trim() || args.target_name.length > 128)) {
          return 'target_name 必须是当前已打开的微信会话全名（1..128 字）'
        }
        if (args.since_days !== undefined && (typeof args.since_days !== 'number' || !Number.isFinite(args.since_days) || args.since_days <= 0)) {
          return 'since_days 必须是 > 0 的数字'
        }
        if (args.max_pages !== undefined && (!Number.isInteger(args.max_pages) || args.max_pages < 1 || args.max_pages > MAX_PAGES_LIMIT)) {
          return `max_pages 必须是 1..${MAX_PAGES_LIMIT} 的整数`
        }
        return null
      }, async (opCtx) => {
        if (args.target_ref !== undefined) {
          verifyRefFn(args.target_ref)
          throw new CodedOperationError('TARGET_AMBIGUOUS', '旧会话引用仅含显示名，无法区分重名会话。请在微信中打开目标，再用 target_name 读取')
        }
        const targetName = args.target_name!
        const maxPages = args.max_pages ?? DEFAULT_MAX_PAGES
        opCtx.progress({ stage: 'execute', message: `在本机读取微信聊天记录（最多 ${maxPages} 页）` })
        const raw = await runDriverFn({
          script: HISTORY_CAPTURE_PATH,
          stdin: JSON.stringify({
            target_name: targetName,
            max_pages: maxPages,
            group_only: deps.groupOnly ? true : undefined,
            since_days: args.since_days === undefined ? undefined : Math.ceil(args.since_days),
          }),
          timeoutMs: HISTORY_TIMEOUT_MS,
          signal: opCtx.signal,
          useVisionProxy: false,
        })
        const capture = historyCaptureSchema.safeParse(raw)
        if (!capture.success) throw new CodedOperationError('UI_CHANGED', '微信原生历史返回格式无效')
        const { title, is_group: isGroup, pages } = capture.data
        if (title !== targetName) {
          throw new CodedOperationError('TARGET_AMBIGUOUS', '原生历史标题与当前会话不一致')
        }
        if (deps.groupOnly && !isGroup) {
          throw new CodedOperationError('TARGET_NOT_FOUND', '当前会话未核实为微信群聊')
        }
        const messages = mergeHistoryPages(pages).map((row) => toHistoryMessage(row, isGroup))
        const truncated = messages.length > INLINE_MESSAGE_LIMIT
        const data: Record<string, unknown> = {
          target: targetName,
          total: messages.length,
          messages: truncated ? messages.slice(-INLINE_MESSAGE_LIMIT) : messages,
        }
        if (truncated) {
          const file = join(tmpdir(), `weixin-history-${randomUUID()}.txt`)
          await writeFileFn(file, renderHistory(messages, targetName)).catch(() => {
            throw new CodedOperationError('INTERNAL_ERROR', '保存本机微信历史文件失败')
          })
          data.truncated = true
          data.file = file
        }
        return {
          message: `已读取微信聊天记录 ${messages.length} 条${truncated ? `（内联最新 ${INLINE_MESSAGE_LIMIT} 条）` : ''}`,
          data,
        }
      })
    },
  }
}
