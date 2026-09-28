/**
 * wecom_message_send operation（M6：智能分发）：向 target_ref 目标发送 1 条文本消息。
 *
 * 编排（M7 起由 navigate.ts 的 runStageWithNavigation 共享承载，send-image 同源复用）：
 * verifyTargetRef 解出目标（过期 TARGET_REF_STALE / 篡改 INVALID_ARGUMENT；坐标可有无
 * 皆可，本命令不要求）→ spawn message-send.ps1（发送阶段驱动：OCR 两带 + Jev 三问判定
 * 当前会话/输入点/草稿）→ 驱动返回 navigate_required=true（当前会话不是目标/无法判定）
 * → 内部编排：chatSearch（Jev 选 best，须与目标消歧键一致，否则规则唯一匹配项）→
 * chatSelect（点击进会话并校验标题）→ 再次 spawn message-send.ps1 完成发送。两轮都不在
 * 目标会话 → TARGET_NOT_FOUND；编排失败透传对应错误码（此时未发送任何消息），不重试。
 *
 * 发送阶段驱动契约：当前会话对 → 点输入框（Jev 选 token / 降级比例坐标）→ 输入
 * （单行 Send-WeComText 逐字；多行剪贴板粘贴通道 + OCR 回读校验，副作用：覆盖用户
 * 剪贴板且不恢复）→ 发送前规则复核标题（不一致 → 清空自己刚输入的草稿 + UI_CHANGED；
 * 用户草稿一律 UI_CHANGED 中止不清除）→ Enter → Jev 终态判定（降级 M2 三选二）。
 *
 * 写语义：发送后校验失败 → EXECUTION_UNKNOWN，绝不自动重试；驱动超时（写可能已落地）
 * 同样归并 EXECUTION_UNKNOWN，而不是 RESULT_TIMEOUT；驱动执行中被取消 →
 * CANCELLED/effect=unknown。定位阶段（search/select）失败或取消时尚未输入任何文字，
 * effect=none 且 message 注明未发送消息。
 */
import { fileURLToPath } from 'node:url'
import { runWecomOperation } from './context.js'
import { CodedOperationError, type OperationResult, type OpContext, type WecomOperation } from './types.js'
import { runPowerShellDriver, type RunPowerShellDriverFn } from '../platform/powershell.js'
import { artifactDir } from '../platform/environment.js'
import {
  createTargetRef,
  verifyTargetRef,
  type CreateTargetRefFn,
  type VerifyTargetRefFn,
} from '../platform/targetRef.js'
import { runStageWithNavigation, sanitizeTiming } from './navigate.js'

/** dist/src/operations → 包根 drivers/ps1 */
const DRIVER_PATH = fileURLToPath(new URL('../../../drivers/ps1/message-send.ps1', import.meta.url))

export interface WecomMessageSendArgs {
  target_ref: string
  text: string
}

const MAX_TEXT_LENGTH = 2000

export function createWecomMessageSendOperation(
  deps: {
    runDriverFn?: RunPowerShellDriverFn
    verifyRefFn?: VerifyTargetRefFn
    /** 内部 chatSearch 签发 target_ref 用（与 verifyRefFn 配套注入，测试可替换） */
    createRefFn?: CreateTargetRefFn
    artifactDirFn?: () => string | null
  } = {},
): WecomOperation<WecomMessageSendArgs> {
  const runDriverFn = deps.runDriverFn ?? runPowerShellDriver
  const verifyRefFn = deps.verifyRefFn ?? verifyTargetRef
  // 与 chatSearch 的默认签发适配保持一致（坐标随条目写入 payload，select 消费）
  const createRefFn: CreateTargetRefFn =
    deps.createRefFn ?? ((name, type, subtitle, coords) => createTargetRef(name, type, subtitle, { coords }))
  const artifactDirFn = deps.artifactDirFn ?? (() => artifactDir())
  return {
    name: 'wecom_message_send',
    execute(args: WecomMessageSendArgs, ctx: OpContext): Promise<OperationResult> {
      return runWecomOperation(
        'write',
        ctx,
        () => {
          if (args === null || typeof args !== 'object' || Array.isArray(args)) {
            return '参数必须是对象（target_ref/text 必填）'
          }
          if (typeof args.target_ref !== 'string' || args.target_ref.length === 0) {
            return 'target_ref 必填且必须是非空字符串（先经 wecom_chat_search 获取）'
          }
          if (typeof args.text !== 'string' || args.text.length === 0) {
            return 'text 必填且必须是非空字符串'
          }
          // 纯空白拒绝：驱动侧归一化会把全部空白剥掉，前缀变 '' 使终态校验的
          // Contains('') 恒真（三选二失真，可能假报成功）。\u0085 补齐 .NET IsWhiteSpace
          // 与 JS \s 的差集（驱动侧会剥它，TS 侧必须同步拒绝）
          if (/^[\s\u0085]*$/.test(args.text)) {
            return 'text 去除空白后不能为空（纯空白消息无法通过发送后校验）'
          }
          if (args.text.length > MAX_TEXT_LENGTH) {
            return `text 长度不能超过 ${MAX_TEXT_LENGTH} 字（请调用方分段发送）`
          }
          // 多行文本（含 \r\n）放行：驱动侧经剪贴板粘贴通道输入（attachstate Ctrl+V，
          // 2026-09-28 真机验证），副作用是覆盖用户剪贴板且不恢复
          return null
        },
        async (opCtx, tracker) => {
          const root = artifactDirFn()
          if (!root) {
            throw new CodedOperationError('INTERNAL_ERROR', '%LOCALAPPDATA% 未设置，无法定位 artifact 目录')
          }

          const target = verifyRefFn(args.target_ref)
          opCtx.progress({ stage: 'execute', message: `解析目标「${target.name}」，发送前检查当前会话` })

          const spawnSendDriver = (dir: string): Promise<Record<string, unknown>> =>
            runDriverFn({
              script: DRIVER_PATH,
              args: [
                '-TargetName', target.name,
                '-Subtitle', target.subtitle,
                '-Section', target.type,
                '-Text', args.text,
                '-ArtifactDir', dir,
              ],
              signal: opCtx.signal,
            })

          const { data, navigated } = await runStageWithNavigation({
            runDriverFn,
            verifyRefFn,
            createRefFn,
            opCtx,
            root,
            target,
            artifactPrefix: 'send',
            spawnStage: spawnSendDriver,
            texts: {
              writeDesc: '消息',
              notSentNote: '未发送消息',
              checkDesc: '发送前检查',
              afterEnter: '执行发送',
            },
          })
          tracker.completed = 1

          // 驱动字段防御性归一（sent_verification / input_point / timing / screenshots）
          const svRaw = (data.sent_verification ?? null) as Record<string, unknown> | null
          const sentVerification =
            svRaw !== null && typeof svRaw === 'object'
              ? {
                  method: svRaw.method === 'rule_2of3' ? 'rule_2of3' : 'jev',
                  result: typeof svRaw.result === 'string' ? svRaw.result : 'unknown',
                  ...(typeof svRaw.failure_mode === 'string' ? { failure_mode: svRaw.failure_mode } : {}),
                }
              : null
          const ipRaw = (data.input_point ?? null) as Record<string, unknown> | null
          const inputPoint =
            ipRaw !== null && typeof ipRaw === 'object'
              ? {
                  x: typeof ipRaw.x === 'number' && Number.isFinite(ipRaw.x) ? Math.round(ipRaw.x) : null,
                  y: typeof ipRaw.y === 'number' && Number.isFinite(ipRaw.y) ? Math.round(ipRaw.y) : null,
                }
              : null
          const methodDesc = sentVerification?.method === 'rule_2of3' ? '规则三选二' : 'Jev'
          return {
            message: `已向「${target.name}」发送消息，发送后校验通过（${methodDesc}校验${navigated ? '，已自动搜索并切换会话' : '，当前会话直发'}）`,
            data: {
              target: target.name,
              title: typeof data.title === 'string' && data.title.length > 0 ? data.title : target.name,
              navigated,
              sent_verification: sentVerification,
              input_point: inputPoint,
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
