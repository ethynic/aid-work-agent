/**
 * weixin_history_read operation（M2）：读取与目标的聊天记录（只读，effect=none）。
 *
 * 注意：会打开会话窗口、清除该会话未读角标，但无外部写副作用。
 *
 * 流程：TS 验 ref → drivers/ps1/resolve-open.ps1（搜索+定位+点击+校验标题，
 * 与 message_send 共用 Open-WeixinChat）→ 调 experiments/probes/p4-history-capture/
 * run.ps1（滚动截屏 + RapidOCR）抓取 → TS 读输出文本，解析 `[时间]/[我]/[对方名]`
 * 行为 messages。内联最多 200 条，超出只返回最新 200 条并给 file 路径。
 */
import { randomUUID } from 'node:crypto'
import { readFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { runWeixinOperation } from './context.js'
import { CodedOperationError, type OperationResult, type OpContext, type WeixinOperation } from './types.js'
import {
  runPowerShellDriver,
  runPowerShellScript,
  type RunPowerShellDriverFn,
  type RunPowerShellScriptFn,
} from '../platform/powershell.js'
import { verifyTargetRef, type VerifyTargetRefFn } from '../platform/targetRef.js'

/** dist/src/operations → 包根 drivers/ps1 / experiments/probes/p4-history-capture */
const RESOLVE_OPEN_PATH = fileURLToPath(new URL('../../../drivers/ps1/resolve-open.ps1', import.meta.url))
const HISTORY_CAPTURE_PATH = fileURLToPath(
  new URL('../../../experiments/probes/p4-history-capture/run.ps1', import.meta.url),
)

export interface WeixinHistoryReadArgs {
  target_ref: string
  since_days?: number
  max_pages?: number
}

export interface HistoryMessage {
  sender: 'me' | 'peer' | 'time'
  text: string
}

const DEFAULT_MAX_PAGES = 3
const MAX_PAGES_LIMIT = 10
/** 内联消息上限：超出只返回最新 INLINE_MESSAGE_LIMIT 条并落 file 路径 */
const INLINE_MESSAGE_LIMIT = 200
/** 历史抓取按页滚动+OCR 较慢，预算 600s */
const HISTORY_TIMEOUT_MS = 600_000

const MESSAGE_LINE = /^\[(.+?)\]\s?(.*)$/

/** 解析 p4 输出文本行：`[时间] xxx` / `[我] xxx` / `[对方名] xxx` */
export function parseHistoryLines(text: string): HistoryMessage[] {
  const messages: HistoryMessage[] = []
  for (const line of text.split(/\r?\n/)) {
    if (!line.trim()) continue
    const m = MESSAGE_LINE.exec(line)
    if (!m) continue
    const tag = m[1]!
    const sender: HistoryMessage['sender'] = tag === '时间' ? 'time' : tag === '我' ? 'me' : 'peer'
    messages.push({ sender, text: m[2] ?? '' })
  }
  return messages
}

export function createWeixinHistoryReadOperation(
  deps: {
    runDriverFn?: RunPowerShellDriverFn
    runScriptFn?: RunPowerShellScriptFn
    verifyRefFn?: VerifyTargetRefFn
    readFileFn?: (path: string) => Promise<string>
  } = {},
): WeixinOperation<WeixinHistoryReadArgs> {
  const runDriverFn = deps.runDriverFn ?? runPowerShellDriver
  const runScriptFn = deps.runScriptFn ?? runPowerShellScript
  const verifyRefFn = deps.verifyRefFn ?? verifyTargetRef
  const readFileFn = deps.readFileFn ?? ((p: string) => readFile(p, 'utf8'))
  return {
    name: 'weixin_history_read',
    execute(args: WeixinHistoryReadArgs, ctx: OpContext): Promise<OperationResult> {
      return runWeixinOperation(
        'readonly',
        ctx,
        () => {
          if (args === null || typeof args !== 'object' || Array.isArray(args)) {
            return '参数必须是对象（target_ref 必填；since_days/max_pages 可选）'
          }
          if (typeof args.target_ref !== 'string' || args.target_ref.length === 0) {
            return 'target_ref 必填且必须是非空字符串（先经 weixin_chat_search 获取）'
          }
          if (args.since_days !== undefined && (typeof args.since_days !== 'number' || !Number.isFinite(args.since_days) || args.since_days <= 0)) {
            return 'since_days 必须是 > 0 的数字'
          }
          if (
            args.max_pages !== undefined &&
            (!Number.isInteger(args.max_pages) || args.max_pages < 1 || args.max_pages > MAX_PAGES_LIMIT)
          ) {
            return `max_pages 必须是 1..${MAX_PAGES_LIMIT} 的整数`
          }
          return null
        },
        async (opCtx) => {
          const target = verifyRefFn(args.target_ref)
          const maxPages = args.max_pages ?? DEFAULT_MAX_PAGES

          opCtx.progress({ stage: 'execute', message: `打开与「${target.name}」的会话` })
          await runDriverFn({ script: RESOLVE_OPEN_PATH, args: ['-TargetName', target.name], signal: opCtx.signal })

          const outFile = join(tmpdir(), `weixin-history-${randomUUID()}.txt`)
          const scriptArgs = ['-PeerName', target.name, '-MaxPages', String(maxPages), '-OutFile', outFile]
          if (args.since_days !== undefined) scriptArgs.push('-UntilDaysAgo', String(Math.ceil(args.since_days)))
          opCtx.progress({ stage: 'execute', message: `滚动抓取聊天记录（最多 ${maxPages} 页）` })
          const capture = await runScriptFn({
            script: HISTORY_CAPTURE_PATH,
            args: scriptArgs,
            timeoutMs: HISTORY_TIMEOUT_MS,
            signal: opCtx.signal,
          })
          if (capture.exitCode !== 0) {
            const stderrSummary = capture.stderr.trim().slice(0, 300)
            throw new CodedOperationError(
              'INTERNAL_ERROR',
              `历史抓取脚本异常退出（exit=${capture.exitCode}）${stderrSummary ? `：${stderrSummary}` : ''}`,
            )
          }
          if (!capture.stdout.includes('PROBE_RESULT: CAPTURED')) {
            throw new CodedOperationError('INTERNAL_ERROR', '历史抓取脚本未报告 CAPTURED（抓取失败）')
          }

          let text: string
          try {
            text = await readFileFn(outFile)
          } catch (err) {
            throw new CodedOperationError(
              'INTERNAL_ERROR',
              `读取抓取结果文件失败：${err instanceof Error ? err.message : String(err)}`,
            )
          }
          const messages = parseHistoryLines(text)
          const truncated = messages.length > INLINE_MESSAGE_LIMIT
          const inline = truncated ? messages.slice(-INLINE_MESSAGE_LIMIT) : messages
          const data: Record<string, unknown> = {
            target: target.name,
            total: messages.length,
            messages: inline,
          }
          if (truncated) {
            data.truncated = true
            data.file = outFile
          }
          return {
            message: `已读取与「${target.name}」的聊天记录 ${messages.length} 条${truncated ? `（内联最新 ${INLINE_MESSAGE_LIMIT} 条，完整见 file）` : ''}`,
            data,
          }
        },
      )
    },
  }
}
