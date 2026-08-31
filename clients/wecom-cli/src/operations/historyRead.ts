/**
 * wecom_history_read operation（M3）：读取与 target_ref 目标的会话消息（只读消息内容，
 * effect=none）。
 *
 * 副作用注意：进入会话会清除该会话未读角标（企微客户端固有行为），此外无写副作用；
 * 抓取完成后驱动会滚回底部恢复原位。
 *
 * 流程：TS 校验参数 → verifyTargetRef 解出目标（过期 TARGET_REF_STALE / 篡改
 * INVALID_ARGUMENT）→ spawn drivers/ps1/history-read.ps1（搜索定位进会话 → OCR 标题
 * 严格校验防串会话 → 先下滚到底 → 逐屏上滚截图 OCR history 模式 → 页间最大重叠去重 →
 * finally 滚回底部）→ TS 归一 messages 返回。
 */
import { mkdirSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { runWecomOperation } from './context.js'
import { CodedOperationError, type OperationResult, type OpContext, type WecomOperation } from './types.js'
import { runPowerShellDriver, type RunPowerShellDriverFn } from '../platform/powershell.js'
import { artifactDir } from '../platform/environment.js'
import { verifyTargetRef, type VerifyTargetRefFn } from '../platform/targetRef.js'

/** dist/src/operations → 包根 drivers/ps1 */
const DRIVER_PATH = fileURLToPath(new URL('../../../drivers/ps1/history-read.ps1', import.meta.url))

export interface WecomHistoryReadArgs {
  target_ref: string
  /** 最多向上翻几屏（含底部当前屏），默认 1，上限 10 */
  max_pages?: number
  /** 只读最近 N 天：某屏最早「M月D日」分割线超龄即停止上翻（简单版，可选） */
  since_days?: number
}

export interface HistoryMessage {
  /** self=我方（右侧气泡）/ peer=对方（左侧气泡）/ timeline=时间分割线 */
  side: 'self' | 'peer' | 'timeline'
  text: string
}

const DEFAULT_MAX_PAGES = 1
const MAX_PAGES_LIMIT = 10
/** 历史抓取按屏滚动 + OCR 较慢，预算 600s */
const HISTORY_TIMEOUT_MS = 600_000

/** 驱动 data.messages 防御性归一；side 非法值归并 peer（启发式本就不可依赖，见 chat_ocr.py classify_side） */
export function parseHistoryMessages(data: Record<string, unknown>): HistoryMessage[] {
  const raw = Array.isArray(data.messages) ? data.messages : data.messages != null ? [data.messages] : []
  return raw
    .map((it) => {
      const side = String((it as { side?: unknown })?.side ?? '')
      return {
        side: (side === 'self' || side === 'timeline' ? side : 'peer') as HistoryMessage['side'],
        text: String((it as { text?: unknown })?.text ?? ''),
      }
    })
    .filter((m) => m.text.length > 0)
}

export function createWecomHistoryReadOperation(
  deps: { runDriverFn?: RunPowerShellDriverFn; verifyRefFn?: VerifyTargetRefFn; artifactDirFn?: () => string | null } = {},
): WecomOperation<WecomHistoryReadArgs> {
  const runDriverFn = deps.runDriverFn ?? runPowerShellDriver
  const verifyRefFn = deps.verifyRefFn ?? verifyTargetRef
  const artifactDirFn = deps.artifactDirFn ?? (() => artifactDir())
  return {
    name: 'wecom_history_read',
    execute(args: WecomHistoryReadArgs, ctx: OpContext): Promise<OperationResult> {
      return runWecomOperation(
        'readonly',
        ctx,
        () => {
          if (args === null || typeof args !== 'object' || Array.isArray(args)) {
            return '参数必须是对象（target_ref 必填；max_pages/since_days 可选）'
          }
          if (typeof args.target_ref !== 'string' || args.target_ref.length === 0) {
            return 'target_ref 必填且必须是非空字符串（先经 wecom_chat_search 获取）'
          }
          if (
            args.max_pages !== undefined &&
            (!Number.isInteger(args.max_pages) || args.max_pages < 1 || args.max_pages > MAX_PAGES_LIMIT)
          ) {
            return `max_pages 必须是 1..${MAX_PAGES_LIMIT} 的整数`
          }
          if (
            args.since_days !== undefined &&
            (!Number.isInteger(args.since_days) || args.since_days < 1)
          ) {
            return 'since_days 必须是 ≥1 的整数'
          }
          return null
        },
        async (opCtx) => {
          // 逐屏截图与 driver-log 存档目录：<artifact 根>/history-<run 时间戳>/（真机排障，同 message-send）
          const root = artifactDirFn()
          if (!root) {
            throw new CodedOperationError('INTERNAL_ERROR', '%LOCALAPPDATA% 未设置，无法定位 artifact 目录')
          }
          const dir = join(root, `history-${new Date().toISOString().replace(/[:.]/g, '-')}`)
          mkdirSync(dir, { recursive: true })

          const target = verifyRefFn(args.target_ref)
          const maxPages = args.max_pages ?? DEFAULT_MAX_PAGES
          const driverArgs = [
            '-TargetName', target.name,
            '-Subtitle', target.subtitle,
            '-Section', target.type,
            '-MaxPages', String(maxPages),
            '-OpenMode', 'search',
            '-ArtifactDir', dir,
          ]
          if (args.since_days !== undefined) driverArgs.push('-SinceDays', String(args.since_days))
          opCtx.progress({ stage: 'execute', message: `打开与「${target.name}」的会话并滚动抓取（最多 ${maxPages} 屏）` })
          const data = await runDriverFn({
            script: DRIVER_PATH,
            args: driverArgs,
            timeoutMs: HISTORY_TIMEOUT_MS,
            signal: opCtx.signal,
          })
          const messages = parseHistoryMessages(data)
          return {
            message: `已读取与「${target.name}」的会话消息 ${messages.length} 行（${Number(data.pages_read ?? 1)} 屏）`,
            data: {
              target: target.name,
              title: String(data.title ?? target.name),
              messages,
              pages_read: Number(data.pages_read ?? 1),
              screenshot_paths: Array.isArray(data.screenshot_paths) ? data.screenshot_paths : [],
            },
          }
        },
      )
    },
  }
}
