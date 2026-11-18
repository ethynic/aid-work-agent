import { createHash, randomUUID } from 'node:crypto'
import { mkdir, readFile, rename, unlink, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { z } from 'zod'
import { runPowerShellDriver, type RunPowerShellDriverFn } from '../platform/powershell.js'
import { protectText, unprotectText, runDpapiCommand, type DpapiRunnerFn } from '../security/dpapi.js'
import { runWeixinOperation } from './context.js'
import { CodedOperationError, type WeixinOperation } from './types.js'

const DRIVER_PATH = fileURLToPath(new URL('../../../drivers/ps1/history-read.ps1', import.meta.url))
const requestSchema = z.object({ target_name: z.string().min(1).max(128).refine((s) => Boolean(s.trim()) && !/[\x00-\x1f\x7f]/.test(s)) }).strict()
const identitySchema = z.object({
  title: z.string().min(1), session_identity: z.string().min(1), process_started_at: z.string().regex(/^\d+$/),
})
const rowSchema = z.object({
  runtime_id: z.string().min(1), kind: z.enum(['text', 'time', 'media', 'unknown']),
  text: z.string(), sender: z.enum(['self', 'peer', 'system', 'unknown']),
  sender_name: z.string().nullish(), clipped: z.boolean(),
})
const messageSchema = z.object({
  side: z.enum(['self', 'peer', 'timeline', 'unknown']),
  kind: z.enum(['text', 'media', 'unknown']),
  text: z.string(), time: z.string(), sender_name: z.string().nullable(),
})
const anchorContextSchema = z.object({ minute: z.string().regex(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/), offset: z.number().int().positive() }).nullable().default(null)
const copySchema = identitySchema.extend({
  version: z.literal(1), messages: z.array(messageSchema),
  anchor: z.array(rowSchema).max(6), empty_proven: z.boolean(), complete_history: z.boolean().default(false),
  anchor_context: anchorContextSchema,
})
const captureSchema = identitySchema.extend({
  rows: z.array(rowSchema), anchor_end: z.number().int().min(-1),
  pages_scanned: z.number().int().min(1).max(100), empty_proven: z.boolean(), complete_history: z.boolean().default(false),
  anchor_context: anchorContextSchema,
})
type NativeRow = z.infer<typeof rowSchema>
type LocalCopy = z.infer<typeof copySchema>

function normalizeNewRows(rows: NativeRow[], anchorEnd: number): z.infer<typeof messageSchema>[] {
  let time = ''
  const messages: z.infer<typeof messageSchema>[] = []
  rows.forEach((row, index) => {
    if (row.kind === 'time') { time = row.text; return }
    if (index <= anchorEnd) return
    if (row.clipped) throw new CodedOperationError('CONTENT_UNAVAILABLE', '消息不完整')
    messages.push({
      side: row.sender === 'system' ? 'timeline' : row.sender,
      kind: row.kind, text: row.text, time, sender_name: row.sender_name ?? null,
    })
  })
  return messages
}

export function createWeixinChatLatestOperation(deps: {
  runDriverFn?: RunPowerShellDriverFn
  stateDir?: string
  dpapiRunFn?: DpapiRunnerFn
  /** This is a local construction choice. It is never a tool input. */
  localOutput?: boolean
} = {}): WeixinOperation<{ target_name: string }> {
  const runDriver = deps.runDriverFn ?? runPowerShellDriver
  const stateDir = deps.stateDir ?? join(process.env.LOCALAPPDATA ?? tmpdir(), 'aid-weixin', 'chat-latest')
  return {
    name: 'weixin_chat_latest',
    async execute(args, ctx) {
      const result = await runWeixinOperation('readonly', ctx,
        () => requestSchema.safeParse(args).success ? null : '最新消息读取参数无效',
        async () => {
          const dpapi = deps.dpapiRunFn ?? ((command: string, input: string) => runDpapiCommand(command, input, ctx.signal))
          const invoke = (request: Record<string, unknown>) => runDriver({
            script: DRIVER_PATH, stdin: JSON.stringify({ target_name: args.target_name, ...request }),
            signal: ctx.signal, timeoutMs: 600_000, useVisionProxy: false,
          })
          const identity = identitySchema.parse(await invoke({ mode: 'identity' }))
          if (identity.title !== args.target_name) throw new CodedOperationError('UI_CHANGED', '会话不一致')
          const key = createHash('sha256').update(JSON.stringify(identity)).digest('hex')
          const path = join(stateDir, key + '.dpapi')
          let previous: LocalCopy | undefined
          try {
            const encrypted = await readFile(path, 'utf8')
            previous = copySchema.parse(JSON.parse(await unprotectText(encrypted, dpapi)))
            if (previous.title !== identity.title || previous.session_identity !== identity.session_identity
              || previous.process_started_at !== identity.process_started_at) {
              throw new CodedOperationError('CONTENT_UNAVAILABLE', '副本身份不一致')
            }
          } catch (error) {
            if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error
          }
          ctx.progress({ stage: 'execute', message: '在本机核对最新消息' })
          const capture = captureSchema.parse(await invoke({
            mode: 'latest', session_identity: identity.session_identity, process_started_at: identity.process_started_at,
            anchor: previous?.anchor ?? [], has_cache: previous !== undefined, previous_empty: previous?.empty_proven ?? false,
            complete_history: previous?.complete_history ?? false, cached_count: previous?.messages.length ?? 0,
            anchor_context: previous?.anchor_context ?? null,
          }))
          if (capture.title !== identity.title || capture.session_identity !== identity.session_identity
            || capture.process_started_at !== identity.process_started_at || capture.anchor_end >= capture.rows.length) {
            throw new CodedOperationError('UI_CHANGED', '采集会话身份已变化')
          }
          if (previous && previous.anchor.length && capture.anchor_end < 0) {
            throw new CodedOperationError('CONTENT_UNAVAILABLE', '副本锚点缺失')
          }
          const messages = normalizeNewRows(capture.rows, capture.anchor_end)
          const anchor = capture.rows.filter((row) => row.kind !== 'time' && !row.clipped).slice(-6)
          if (!anchor.length && !capture.empty_proven) throw new CodedOperationError('CONTENT_UNAVAILABLE', '没有可核对的消息锚点')
          const copy: LocalCopy = {
            ...identity, version: 1, messages: [...(previous?.messages ?? []), ...messages],
            anchor, empty_proven: capture.empty_proven,
            complete_history: previous?.complete_history ?? capture.complete_history,
            anchor_context: capture.anchor_context,
          }
          const status = previous === undefined ? 'initialized' : messages.length ? 'updated' : 'unchanged'
          if (status !== 'unchanged') {
            const encrypted = await protectText(JSON.stringify(copy), dpapi)
            ctx.signal.throwIfAborted()
            await mkdir(stateDir, { recursive: true })
            const temporary = path + '.' + randomUUID() + '.tmp'
            try {
              await writeFile(temporary, encrypted, { flag: 'wx', mode: 0o600, signal: ctx.signal })
              ctx.signal.throwIfAborted()
              await rename(temporary, path)
            } finally { await unlink(temporary).catch(() => {}) }
          }
          return {
            message: '本机最新消息同步完成',
            data: {
              status, pages_scanned: capture.pages_scanned, new_count: messages.length,
              ...(deps.localOutput ? { messages } : {}),
            },
          }
        })
      // Both errorMapping and the native driver may contain local diagnostics.
      // This fixed projection also applies to failures and never echoes input.
      return {
        ...result, code: !result.success && ctx.signal.aborted ? 'CANCELLED' : result.code,
        message: result.success ? '本机最新消息同步完成' : '本机最新消息同步失败',
        data: result.success ? result.data : {},
      }
    },
  }
}
