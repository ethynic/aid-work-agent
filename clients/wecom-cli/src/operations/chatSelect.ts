/**
 * wecom_chat_select operation（M5）：点击 search 留下的结果条目进入会话（不发送消息）。
 *
 * 流程：TS 校验参数 → verifyTargetRef 解出目标与 overlay 相对坐标（过期
 * TARGET_REF_STALE / 篡改 INVALID_ARGUMENT；**旧版签发的无坐标 ref → INVALID_ARGUMENT**，
 * 提示重新 search）→ spawn drivers/ps1/chat-select.ps1（找可见 SearchResultWindow2
 * （面板已关 → TARGET_REF_STALE）→ OCR 复核条目（坐标信任策略见驱动头注释）→
 * PostMessage 点击结果行 → 等面板自动关闭 → 重新解析主窗口 + OCR 会话标题严格校验
 * （不一致 UI_CHANGED））→ TS 防御性归一驱动字段透传。
 *
 * effect 语义：恒为 none（无出站消息）；但这是「半写」动作——进入会话会清除该会话
 * 未读角标（企微客户端固有行为，与 read 同款副作用）并切换当前会话视图。
 * 超时按 EXECUTION_UNKNOWN 上报（可能已切换会话但无法确认，不自动重试）；
 * 取消 → CANCELLED。
 */
import { mkdirSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { runWecomOperation } from './context.js'
import {
  CancelledError,
  CodedOperationError,
  type OperationResult,
  type OpContext,
  type WecomOperation,
} from './types.js'
import { runPowerShellDriver, type RunPowerShellDriverFn } from '../platform/powershell.js'
import { artifactDir } from '../platform/environment.js'
import { verifyTargetRef, type VerifyTargetRefFn } from '../platform/targetRef.js'

/** dist/src/operations → 包根 drivers/ps1 */
const DRIVER_PATH = fileURLToPath(new URL('../../../drivers/ps1/chat-select.ps1', import.meta.url))

export interface WecomChatSelectArgs {
  target_ref: string
}

/** select 链路较短（无搜索输入、无滚动），预算 120s */
const SELECT_TIMEOUT_MS = 120_000

/** 驱动字段防御性解析（PS 5.1 ConvertTo-Json 单元素数组解包等怪癖由调用方归一） */
function toIntOrNull(v: unknown): number | null {
  if (typeof v !== 'number' || !Number.isFinite(v)) return null
  return Math.round(v)
}

function sanitizeTiming(raw: unknown): Record<string, number> {
  const out: Record<string, number> = {}
  if (raw === null || typeof raw !== 'object') return out
  for (const [k, v] of Object.entries(raw as Record<string, unknown>)) {
    if (typeof v === 'number' && Number.isFinite(v)) out[k] = Math.round(v)
  }
  return out
}

export function createWecomChatSelectOperation(
  deps: { runDriverFn?: RunPowerShellDriverFn; verifyRefFn?: VerifyTargetRefFn; artifactDirFn?: () => string | null } = {},
): WecomOperation<WecomChatSelectArgs> {
  const runDriverFn = deps.runDriverFn ?? runPowerShellDriver
  const verifyRefFn = deps.verifyRefFn ?? verifyTargetRef
  const artifactDirFn = deps.artifactDirFn ?? (() => artifactDir())
  return {
    name: 'wecom_chat_select',
    execute(args: WecomChatSelectArgs, ctx: OpContext): Promise<OperationResult> {
      return runWecomOperation(
        'readonly',
        ctx,
        () => {
          if (args === null || typeof args !== 'object' || Array.isArray(args)) {
            return '参数必须是对象（target_ref 必填）'
          }
          if (typeof args.target_ref !== 'string' || args.target_ref.length === 0) {
            return 'target_ref 必填且必须是非空字符串（先经 wecom_chat_search 获取）'
          }
          return null
        },
        async (opCtx) => {
          const root = artifactDirFn()
          if (!root) {
            throw new CodedOperationError('INTERNAL_ERROR', '%LOCALAPPDATA% 未设置，无法定位 artifact 目录')
          }
          // 点击前后截图 + driver-log 存档目录：<artifact 根>/select-<run 时间戳>/（同 search）
          const dir = join(root, `select-${new Date().toISOString().replace(/[:.]/g, '-')}`)
          mkdirSync(dir, { recursive: true })

          const target = verifyRefFn(args.target_ref)
          // select 依赖 payload 内的 overlay 相对坐标：旧版签发（无 x/y）的 ref 无法消费
          if (target.x === undefined || target.y === undefined) {
            throw new CodedOperationError('INVALID_ARGUMENT', '该 target_ref 不含坐标（旧版签发），请重新 search')
          }
          const x = Math.round(target.x)
          const y = Math.round(target.y)
          opCtx.progress({ stage: 'execute', message: `点击搜索结果进入「${target.name}」的会话` })
          let data: Record<string, unknown>
          try {
            data = await runDriverFn({
              script: DRIVER_PATH,
              args: [
                '-TargetName', target.name,
                '-Subtitle', target.subtitle,
                '-Section', target.type,
                '-X', String(x),
                '-Y', String(y),
                '-ArtifactDir', dir,
              ],
              timeoutMs: SELECT_TIMEOUT_MS,
              signal: opCtx.signal,
            })
          } catch (err) {
            // 超时：点击可能已生效（会话已切换）但无法确认——按 EXECUTION_UNKNOWN 上报，不自动重试
            if (err instanceof CodedOperationError && err.code === 'RESULT_TIMEOUT') {
              throw new CodedOperationError(
                'EXECUTION_UNKNOWN',
                '进入会话链路超时：可能已切换会话但无法确认（不会自动重试，请人工核对当前会话）',
              )
            }
            if (err instanceof CancelledError) {
              throw new CodedOperationError('CANCELLED', '进入会话链路被取消（点击是否已生效无法确认，请人工核对当前会话）')
            }
            throw err
          }
          // 驱动字段防御性归一（target/title/clicked/timing/screenshot_paths）
          const tRaw = (data.target ?? null) as Record<string, unknown> | null
          const targetOut = {
            name: typeof tRaw?.name === 'string' && tRaw.name.length > 0 ? tRaw.name : target.name,
            subtitle: typeof tRaw?.subtitle === 'string' ? tRaw.subtitle : target.subtitle,
            section: typeof tRaw?.section === 'string' ? tRaw.section : target.type,
          }
          const cRaw = (data.clicked ?? null) as Record<string, unknown> | null
          const clicked = {
            x: cRaw !== null && typeof cRaw === 'object' ? (toIntOrNull(cRaw.x) ?? x) : x,
            y: cRaw !== null && typeof cRaw === 'object' ? (toIntOrNull(cRaw.y) ?? y) : y,
          }
          const title = typeof data.title === 'string' && data.title.length > 0 ? data.title : target.name
          return {
            message: `已进入会话「${title}」（进入会话会清除其未读角标）`,
            data: {
              target: targetOut,
              title,
              clicked,
              timing_ms: sanitizeTiming(data.timing_ms),
              screenshot_paths: Array.isArray(data.screenshot_paths)
                ? data.screenshot_paths.filter((p): p is string => typeof p === 'string')
                : [],
            },
          }
        },
      )
    },
  }
}
