/**
 * wecom_read_session operation（M9，前身 M3 wecom_history_read 改名重写）：读取与
 * target_ref 目标的会话消息（只读消息内容，effect=none）。
 *
 * 副作用注意：进入会话会清除该会话未读角标（企微客户端固有行为），此外无写副作用；
 * 抓取完成后驱动会滚回底部恢复原位。
 *
 * 流程（M9 起导航迁移到共享智能分发，与 send/send-image/send-file 同款）：
 * TS 校验参数 → verifyTargetRef 解出目标（过期 TARGET_REF_STALE / 篡改
 * INVALID_ARGUMENT）→ spawn drivers/ps1/read-session.ps1（读取阶段驱动：搜索框残留
 * 防御 → 标题带 OCR → Jev #1 单问 right_conversation 判定当前会话——read 是只读动作，
 * 不点输入框不输入文字，has_draft 不适用，**绝不因草稿中止**；判定非目标/无法判定 →
 * navigate_required=true → 内部 chatSearch+chatSelect → 二次调用；判定过 → 标题严格
 * 校验防串会话 → 先下滚到底 → 逐屏上滚截图 OCR history 模式 → 页间最大重叠去重 →
 * 时间戳沿袭分割线原文 → finally 滚回底部）→ TS 归一 messages 返回。
 * 旧 M2 搜索链（驱动内 Open-WeComSearchOverlay 固定像素/固定带/× 清空/ESC 关闭）自
 * M9 起退役：窄窗口残留误判 bug + ESC 最小化风险（README M4 段），会话切换统一由
 * TS 编排的 chatSearch+chatSelect 完成。
 */
import { fileURLToPath } from 'node:url'
import { runWecomOperation } from './context.js'
import { CodedOperationError, type OperationResult, type OpContext, WecomOperation } from './types.js'
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
const DRIVER_PATH = fileURLToPath(new URL('../../../drivers/ps1/read-session.ps1', import.meta.url))

export interface WecomReadSessionArgs {
  target_ref: string
  /** 最多向上翻几屏（含底部当前屏），默认 1，上限 10 */
  max_pages?: number
  /** 只读最近 N 天：某屏最早「M月D日」分割线超龄即停止上翻（简单版，可选） */
  since_days?: number
}

export interface SessionMessage {
  /**
   * side 语义（锁定，勿改）：气泡**左缘锚定 = peer（对方发的）**、**右缘锚定 = self
   * （自己发的）**、timeline = 时间分割线。实现见 chat_ocr.py classify_side（OCR
   * 启发式，长行可能误判，调用方不得依赖 side 做安全判定）。
   */
  side: 'self' | 'peer' | 'timeline'
  text: string
  /**
   * 时间戳沿袭（M9）：最近一条时间分割线的**原文**（如「7月16日 09:01」「08:23」），
   * 近似时间；首条分割线之前的消息无此字段。timeline 条目本身不带 time（其 text 即时间）。
   */
  time?: string
}

const DEFAULT_MAX_PAGES = 1
const MAX_PAGES_LIMIT = 10
/** 历史抓取按屏滚动 + OCR 较慢，预算 600s */
const READ_SESSION_TIMEOUT_MS = 600_000

/** 驱动 data.messages 防御性归一；side 非法值归并 peer（启发式本就不可依赖，见 chat_ocr.py classify_side）；time 非空字符串才沿袭 */
export function parseSessionMessages(data: Record<string, unknown>): SessionMessage[] {
  const raw = Array.isArray(data.messages) ? data.messages : data.messages != null ? [data.messages] : []
  return raw
    .map((r) => {
      const it = (r ?? {}) as { side?: unknown; text?: unknown; time?: unknown }
      const side = String(it.side ?? '')
      const time = typeof it.time === 'string' && it.time.length > 0 ? it.time : undefined
      return {
        side: (side === 'self' || side === 'timeline' ? side : 'peer') as SessionMessage['side'],
        text: String(it.text ?? ''),
        ...(time !== undefined ? { time } : {}),
      }
    })
    .filter((m) => m.text.length > 0)
}

export function createWecomReadSessionOperation(
  deps: {
    runDriverFn?: RunPowerShellDriverFn
    verifyRefFn?: VerifyTargetRefFn
    /** 内部 chatSearch 签发 target_ref 用（与 verifyRefFn 配套注入，测试可替换） */
    createRefFn?: CreateTargetRefFn
    artifactDirFn?: () => string | null
  } = {},
): WecomOperation<WecomReadSessionArgs> {
  const runDriverFn = deps.runDriverFn ?? runPowerShellDriver
  const verifyRefFn = deps.verifyRefFn ?? verifyTargetRef
  // 与 chatSearch 的默认签发适配保持一致（坐标随条目写入 payload，select 消费）
  const createRefFn: CreateTargetRefFn =
    deps.createRefFn ?? ((name, type, subtitle, coords) => createTargetRef(name, type, subtitle, { coords }))
  const artifactDirFn = deps.artifactDirFn ?? (() => artifactDir())
  return {
    name: 'wecom_read_session',
    execute(args: WecomReadSessionArgs, ctx: OpContext): Promise<OperationResult> {
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
          const root = artifactDirFn()
          if (!root) {
            throw new CodedOperationError('INTERNAL_ERROR', '%LOCALAPPDATA% 未设置，无法定位 artifact 目录')
          }

          const target = verifyRefFn(args.target_ref)
          const maxPages = args.max_pages ?? DEFAULT_MAX_PAGES
          opCtx.progress({ stage: 'execute', message: `解析目标「${target.name}」，读取前检查当前会话（最多 ${maxPages} 屏）` })

          // 读取阶段驱动（智能分发共享编排承载；readonly：阶段超时/取消不套写动作 unknown 语义）
          const spawnReadSessionDriver = (dir: string): Promise<Record<string, unknown>> =>
            runDriverFn({
              script: DRIVER_PATH,
              args: [
                '-TargetName', target.name,
                '-Subtitle', target.subtitle,
                '-Section', target.type,
                '-MaxPages', String(maxPages),
                '-ArtifactDir', dir,
                ...(args.since_days !== undefined ? ['-SinceDays', String(args.since_days)] : []),
              ],
              timeoutMs: READ_SESSION_TIMEOUT_MS,
              signal: opCtx.signal,
            })

          const { data, navigated } = await runStageWithNavigation({
            runDriverFn,
            verifyRefFn,
            createRefFn,
            opCtx,
            root,
            target,
            artifactPrefix: 'read-session',
            spawnStage: spawnReadSessionDriver,
            readonly: true,
            texts: {
              writeDesc: '读取消息',
              notSentNote: '未读取消息',
              checkDesc: '读取前检查',
              afterEnter: '执行消息读取',
              refuseDesc: '已中止读取',
            },
          })

          const messages = parseSessionMessages(data)
          return {
            message: `已读取与「${target.name}」的会话消息 ${messages.length} 行（${Number(data.pages_read ?? 1)} 屏${navigated ? '，已自动搜索并切换会话' : '，当前会话直读'}）`,
            data: {
              target: target.name,
              title: typeof data.title === 'string' && data.title.length > 0 ? data.title : target.name,
              navigated,
              messages,
              pages_read: Number(data.pages_read ?? 1),
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
