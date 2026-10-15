/** read-session：按doctor状态选择本地库或既有UI读取，业务能力由CLI/MCP共用。
 * 本地库返回账号/会话/代际绑定游标，不切会话；取消、参数、身份和目标错误不回退。
 * UI保留model/ocr及费用规则，必要时搜索/导航并清除角标；既有legacy ref仅在UI兼容。
 */
import { readFile, stat } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { runWecomOperation, type OperationOutcome } from './context.js'
import { CancelledError, CodedOperationError, type OperationResult, type OpContext, WecomOperation } from './types.js'
import { runPowerShellDriver, type RunPowerShellDriverFn } from '../platform/powershell.js'
import { artifactDir } from '../platform/environment.js'
import {
  parseSessionHistory,
  ProxyError,
  serverUrlConfigured,
  SESSION_IMAGE_MAX_BYTES,
  type ParseSessionHistoryParams,
  type SessionHistoryResponse,
} from '../platform/serverProxy.js'
import {
  createTargetRef,
  verifyTargetRef,
  type CreateTargetRefFn,
  type VerifyTargetRefFn,
  type TargetRefIdentity,
} from '../platform/targetRef.js'
import {
  resolveTargetByName,
  runStageWithNavigation,
  sanitizeTiming,
  validateTargetSelector,
  type ResolvedByNameTarget,
} from './navigate.js'

import { probeCurrentAccount, checkCancelled, type CurrentAccount } from '../platform/localdb/identity.js'
import { readLocaldbSession, LOCALDB_FALLBACK_CODES, type LocaldbReadDeps } from '../platform/localdb/readSession.js'
import { readLocalState, writeLocalState } from '../platform/localdb/matrix.js'
import { logLocaldbEvent } from '../platform/localdb/log.js'
import { decodeCursor } from '../platform/localdb/messages.js'
import { mapExecutorError } from './errorMapping.js'

/** dist/src/operations → 包根 drivers/ps1 */
const DRIVER_PATH = fileURLToPath(new URL('../../../drivers/ps1/read-session.ps1', import.meta.url))

export interface WecomReadSessionArgs {
  channel?: 'local-db' | 'ui'
  limit?: number
  cursor?: string
  target_ref?: string
  /** 按会话名定位，与 target_ref 二选一；DB 精确消歧，UI 内部搜索导航。 */
  target_name?: string
  /** 最多向上翻几屏（含底部当前屏），默认 1，上限 10 */
  max_pages?: number
  /** 最近 N 天；DB 按 send_time 过滤，UI 按既有模型/OCR规则处理。 */
  since_days?: number
}

export interface SessionMessage {
  /**
   * side 语义（锁定，勿改）：气泡**左缘锚定 = peer（对方发的）**、**右缘锚定 = self
   * （自己发的）**、timeline = 时间分割线。OCR 通道实现见 chat_ocr.py classify_side
   * （OCR 启发式，长行可能误判，调用方不得依赖 side 做安全判定）；模型通道由
   * 服务端 GLM 多模态判定（M10b 主通道，side 质量碾压 OCR）。
   */
  side: 'self' | 'peer' | 'timeline'
  text: string
  /**
   * 时间戳沿袭（M9）：最近一条时间分割线的**原文**（如「7月16日 09:01」「08:23」），
   * 近似时间；首条分割线之前的消息无此字段。timeline 条目本身不带 time（其 text 即时间）。
   */
  time?: string
  /**
   * 消息种类（M10b 模型通道才有；OCR 通道不产出）：text=普通文本、image=图片消息
   * （text 为「[图片] 图内可见文字摘要」）、file=文件消息（text 为「[文件] 文件名」）、
   * timeline=时间分割线。
   */
  kind?: 'text' | 'image' | 'file' | 'timeline'
}

const DEFAULT_MAX_PAGES = 1
const MAX_PAGES_LIMIT = 10
/** 历史抓取按屏滚动 + OCR 较慢，预算 600s（模型通道单次驱动不 OCR 更快，同预算不收紧） */
const READ_SESSION_TIMEOUT_MS = 600_000

const VALID_KINDS: ReadonlySet<string> = new Set(['text', 'image', 'file', 'timeline'])

/** 驱动 data.messages（OCR 通道）防御性归一；side 非法值归并 peer（启发式本就不可依赖）；time 非空字符串才沿袭 */
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

/**
 * 服务端响应 messages（模型通道）归一：side 非法归并 peer、kind 非法丢弃、time 非空
 * 才保留（服务端 _sanitize_page_messages 已清洗过，此处仅防御客户端侧契约漂移）。
 */
export function parseModelMessages(raw: unknown): SessionMessage[] {
  const arr = Array.isArray(raw) ? raw : raw != null ? [raw] : []
  return arr
    .map((r) => {
      const it = (r ?? {}) as { side?: unknown; kind?: unknown; text?: unknown; time?: unknown }
      const side = String(it.side ?? '')
      const kind = String(it.kind ?? '')
      const time = typeof it.time === 'string' && it.time.length > 0 ? it.time : undefined
      return {
        side: (side === 'self' || side === 'timeline' ? side : 'peer') as SessionMessage['side'],
        text: String(it.text ?? ''),
        ...(VALID_KINDS.has(kind) ? { kind: kind as SessionMessage['kind'] } : {}),
        ...(time !== undefined ? { time } : {}),
      }
    })
    .filter((m) => m.text.length > 0)
}

/** 驱动 data.page_paths 防御性解析（string 数组） */
function parsePagePaths(data: Record<string, unknown>): string[] {
  return Array.isArray(data.page_paths) ? data.page_paths.filter((p): p is string => typeof p === 'string' && p.length > 0) : []
}

/**
 * 读取 page-*.png 转 base64 并反转为时间序旧→新（驱动采集序为新→旧：page-1=底部
 * 最新屏）。文件缺失/超 5MB（服务端单图上限）抛 Error → 调用方降级 OCR。
 */
async function readPageImages(paths: string[]): Promise<string[]> {
  const images: string[] = []
  for (const p of paths) {
    const st = await stat(p) // 缺失/不可读 → ENOENT 抛出
    if (st.size > SESSION_IMAGE_MAX_BYTES) {
      throw new Error(`截图 ${p} 超过 ${Math.round(SESSION_IMAGE_MAX_BYTES / 1024 / 1024)}MB 服务端上限`)
    }
    images.push((await readFile(p)).toString('base64'))
  }
  return images.reverse()
}

/** 模型通道调用替身类型（默认 serverProxy.parseSessionHistory，测试注入 mock） */
export type ParseHistoryFn = (params: ParseSessionHistoryParams) => Promise<SessionHistoryResponse>

export function createWecomReadSessionOperation(
  deps: {
    localdb?: LocaldbReadDeps
    readDbFn?: typeof readLocaldbSession
    runDriverFn?: RunPowerShellDriverFn
    verifyRefFn?: VerifyTargetRefFn
    /** 内部 chatSearch 签发 target_ref 用（与 verifyRefFn 配套注入，测试可替换） */
    createRefFn?: CreateTargetRefFn
    artifactDirFn?: () => string | null
    /** M10b 模型通道环境变量来源（默认 process.env；测试注入保持密闭） */
    env?: NodeJS.ProcessEnv
    /** M10b 模型通道服务端调用（默认 parseSessionHistory；测试注入 mock） */
    parseHistoryFn?: ParseHistoryFn
  } = {},
): WecomOperation<WecomReadSessionArgs> {
  const runDriverFn = deps.runDriverFn ?? runPowerShellDriver
  const verifyRefFn = deps.verifyRefFn ?? verifyTargetRef
  // 与 chatSearch 的默认签发适配保持一致（坐标随条目写入 payload，select 消费）
  const createRefFn: CreateTargetRefFn =
    deps.createRefFn ?? ((name, type, subtitle, coords, account) => createTargetRef(name, type, subtitle, { coords, account }))
  const artifactDirFn = deps.artifactDirFn ?? (() => artifactDir())
  const env = deps.env ?? process.env
  const parseHistoryFn: ParseHistoryFn = deps.parseHistoryFn ?? ((params) => parseSessionHistory(params))
  return {
    name: 'wecom_read_session',
    execute(args: WecomReadSessionArgs, ctx: OpContext): Promise<OperationResult> {
      return runWecomOperation(
        'readonly',
        ctx,
        () => {
          if (args === null || typeof args !== 'object' || Array.isArray(args)) {
            return '参数必须是对象（target_ref/target_name 二选一；max_pages/since_days 可选）'
          }
          if (args.channel !== undefined && args.channel !== 'local-db' && args.channel !== 'ui') return 'channel 必须为 local-db/ui'
          if (args.limit !== undefined && (!Number.isSafeInteger(args.limit) || args.limit < 1)) return 'limit 必须为正安全整数'
          if (args.cursor !== undefined && (typeof args.cursor !== 'string' || !args.cursor)) return 'cursor 必须为非空字符串'
          const selectorErr = validateTargetSelector(args)
          if (selectorErr !== null) return selectorErr
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
          const runUi = async (): Promise<OperationOutcome> => {
            const root = artifactDirFn()
            if (!root) {
              throw new CodedOperationError('INTERNAL_ERROR', '%LOCALAPPDATA% 未设置，无法定位 artifact 目录')
            }

            // M11a 直达模式：内部 search 身份定位转 ref（定位失败 effect=none 且注明未读取
            // 消息），定位成功后走与 ref 模式完全一致的既有链路（search→取 ref→read-session
            // 分发：overlay 由分发链的二次 search 残留清空/新 select 消费，同款生命周期）
            let resolved: ResolvedByNameTarget | null = null
            if (args.target_name !== undefined) {
              resolved = await resolveTargetByName(
                { runDriverFn, createRefFn, opCtx, root },
                args.target_name,
                '',
                { notSentNote: '未读取消息', refuseDesc: '已中止读取' },
              )
            }
            const target = verifyRefFn(resolved !== null ? resolved.target_ref : args.target_ref!)
            const maxPages = args.max_pages ?? DEFAULT_MAX_PAGES
            const modelEnabled = serverUrlConfigured(env) !== null
            opCtx.progress({
              stage: 'execute',
              message: `解析目标「${target.name}」，读取前检查当前会话（最多 ${maxPages} 屏，${modelEnabled ? '模型通道' : 'OCR 通道'}）`,
            })

            // 读取阶段驱动（智能分发共享编排承载；readonly：阶段超时/取消不套写动作 unknown 语义）
            const spawnReadSessionDriver = (dir: string, parseMode: 'ocr' | 'none'): Promise<Record<string, unknown>> =>
              runDriverFn({
                script: DRIVER_PATH,
                args: [
                  '-TargetName', target.name,
                  '-Subtitle', target.subtitle,
                  '-Section', target.type,
                  '-MaxPages', String(maxPages),
                  '-ParseMode', parseMode,
                  '-ArtifactDir', dir,
                  ...(args.since_days !== undefined ? ['-SinceDays', String(args.since_days)] : []),
                ],
                timeoutMs: READ_SESSION_TIMEOUT_MS,
                signal: opCtx.signal,
              })

            /** 一次完整读取阶段（智能分发 + 指定解析模式） */
            const runReadStage = async (parseMode: 'ocr' | 'none') =>
              runStageWithNavigation({
                runDriverFn,
                verifyRefFn,
                createRefFn,
                opCtx,
                root,
                target,
                artifactPrefix: 'read-session',
                spawnStage: (dir) => spawnReadSessionDriver(dir, parseMode),
                readonly: true,
                texts: {
                  writeDesc: '读取消息',
                  notSentNote: '未读取消息',
                  checkDesc: '读取前检查',
                  afterEnter: '执行消息读取',
                  refuseDesc: '已中止读取',
                },
              })

            /** OCR 通道结果（既有字段 + channel；fallbackReason 模型通道降级时附原因） */
            const buildOcrOutcome = (
              data: Record<string, unknown>,
              navigated: boolean,
              fallbackReason?: string,
            ) => {
              const messages = parseSessionMessages(data)
              const pages = Number(data.pages_read ?? 1)
              const channelNote = fallbackReason !== undefined ? '，OCR 通道（模型通道不可用已降级）' : '，OCR 通道'
              return {
                message: `已读取与「${target.name}」的会话消息 ${messages.length} 行（${pages} 屏${navigated ? '，已自动搜索并切换会话' : '，当前会话直读'}${channelNote}）`,
                data: {
                  target: target.name,
                  ...(resolved !== null
                    ? { resolved_target: { name: resolved.name, subtitle: resolved.subtitle, section: resolved.section } }
                    : {}),
                  title: typeof data.title === 'string' && data.title.length > 0 ? data.title : target.name,
                  navigated,
                  channel: 'ocr' as const,
                  messages,
                  pages_read: pages,
                  timing_ms: sanitizeTiming(data.timing_ms),
                  screenshot_paths: Array.isArray(data.screenshot_paths)
                    ? data.screenshot_paths.filter((p): p is string => typeof p === 'string')
                    : [],
                  ...(fallbackReason !== undefined ? { fallback_reason: fallbackReason } : {}),
                },
              }
            }

            // —— 通道选择：未配置服务端 → OCR 直连（channel=ocr，不报错）—— //
            if (!modelEnabled) {
              const { data, navigated } = await runReadStage('ocr')
              return buildOcrOutcome(data, navigated)
            }

            // —— 模型主通道：驱动只截图不 OCR → TS 读文件转 base64 → 服务端解析 —— //
            const { data: shotData, navigated } = await runReadStage('none')
            const pagePaths = parsePagePaths(shotData)
            const shotTitle = typeof shotData.title === 'string' && shotData.title.length > 0 ? shotData.title : target.name

            let images: string[]
            try {
              if (pagePaths.length === 0) {
                throw new Error('驱动未返回截图页（page_paths 为空）')
              }
              images = await readPageImages(pagePaths)
            } catch (err) {
              // 截图文件缺失/超限：模型通道不可用 → OCR 兜底（驱动重跑完整 OCR 链）
              const reason = err instanceof Error ? err.message : String(err)
              opCtx.progress({ stage: 'execute', message: `模型通道截图不可用（${reason}），降级本地 OCR 重新读取` })
              const fallback = await runReadStage('ocr')
              return buildOcrOutcome(fallback.data, fallback.navigated, reason)
            }

            opCtx.progress({ stage: 'execute', message: `模型通道解析中（${images.length} 页截图上传服务端，约需数十秒）` })
            let resp: SessionHistoryResponse
            try {
              resp = await parseHistoryFn({ images, sessionTitle: shotTitle, signal: opCtx.signal })
            } catch (err) {
              if (err instanceof CancelledError) throw err
              if (err instanceof ProxyError) {
                // 402 余额不足 / 配置错误（token 缺失/无效）：不降级，明确报给用户（走 OCR 会让用户以为模型通道免费/正常）
                if (err.kind === 'insufficient_credit') {
                  throw new CodedOperationError('INSUFFICIENT_CREDIT', err.message, 'none')
                }
                if (err.kind === 'config') {
                  throw new CodedOperationError('CONFIG_MISSING', `模型通道不可用（未降级）：${err.message}`, 'none')
                }
                // unavailable（网络/超时/5xx/422）→ OCR 兜底：驱动二次调用完整 OCR 链
                opCtx.progress({ stage: 'execute', message: `模型通道不可用（${err.message}），降级本地 OCR 重新读取` })
                const fallback = await runReadStage('ocr')
                return buildOcrOutcome(fallback.data, fallback.navigated, err.message)
              }
              throw err
            }

            // —— 模型通道成功：messages 透传（side/kind/time），计量/计费/延迟透传 —— //
            const messages = parseModelMessages(resp.messages)
            const pages = resp.pages > 0 ? resp.pages : pagePaths.length
            const credits =
              typeof resp.billing.credits_charged === 'number' ? `，计费 ${resp.billing.credits_charged} 积分` : ''
            return {
              message: `已读取与「${target.name}」的会话消息 ${messages.length} 行（${pages} 屏${navigated ? '，已自动搜索并切换会话' : '，当前会话直读'}，模型通道${credits}）`,
              data: {
                target: target.name,
                ...(resolved !== null
                  ? { resolved_target: { name: resolved.name, subtitle: resolved.subtitle, section: resolved.section } }
                  : {}),
                title: shotTitle,
                navigated,
                channel: 'model' as const,
                messages,
                pages_read: pages,
                timing_ms: sanitizeTiming(shotData.timing_ms),
                screenshot_paths: Array.isArray(shotData.screenshot_paths)
                  ? shotData.screenshot_paths.filter((p): p is string => typeof p === 'string')
                  : [],
                model_usage: resp.model_usage,
                billing: resp.billing,
                model_latency_ms: resp.latency_ms,
                ...(resp.failed_pages !== undefined && resp.failed_pages.length > 0 ? { failed_pages: resp.failed_pages } : {}),
                ...(resp.warning !== undefined ? { warning: resp.warning } : {}),
              },
            }
          }
          const local = deps.localdb ?? {}
          const getState = local.readStateFn ?? (() => readLocalState(local.stateDir))
          const state = getState()
          const target: TargetRefIdentity | null = args.target_ref ? verifyRefFn(args.target_ref) : null
          if (args.cursor !== undefined && !decodeCursor(args.cursor).ok) {
            throw new CodedOperationError('CURSOR_UNSUPPORTED', '游标格式或版本不支持，请重新建立读取基线')
          }
          let current: CurrentAccount | null = null
          const getAccount = async (): Promise<CurrentAccount> => {
            try {
              const account = await (local.probeAccountFn ?? probeCurrentAccount)(opCtx)
              checkCancelled(opCtx.signal)
              return account
            } catch (err) {
              // 已观察到登录/身份变化，旧扫描失败上下文不再有效；不改原模式。
              if (state?.scan_failure && err instanceof CodedOperationError &&
                  ['NOT_LOGGED_IN', 'WECOM_NOT_FOUND', 'IDENTITY_UNCONFIRMED'].includes(err.code)) {
                const latest = getState()
                if (latest) (local.writeStateFn ?? (value => writeLocalState(value, local.stateDir)))({ ...latest, scan_failure: undefined })
              }
              throw err
            }
          }
          if (target?.account !== undefined) {
            current = await getAccount()
            if (target.account !== current.account) throw new CodedOperationError('TARGET_REF_STALE', '目标句柄属于另一个账号，请重新搜索')
          }
          const uiOutcome = async (reason?: string, hint?: string, cleanup?: unknown): Promise<OperationOutcome> => {
            checkCancelled(opCtx.signal)
            if (args.cursor !== undefined) throw new CodedOperationError('CURSOR_UNSUPPORTED', 'UI 通道不能等价执行数据库游标，请保留原游标后重试')
            let result: OperationOutcome
            try {
              result = await runUi()
              checkCancelled(opCtx.signal)
              if (target?.account !== undefined) {
                const afterUi = await getAccount()
                if (afterUi.account !== target.account) throw new CodedOperationError('TARGET_REF_STALE', 'UI 读取期间账号发生变化，结果未返回，请重新搜索')
              }
            } catch (err) {
              if (cleanup === undefined) throw err
              const mapped = mapExecutorError(err)
              throw new CodedOperationError(mapped.code, mapped.message, 'none', {
                ...(err instanceof CodedOperationError ? err.data : {}), cleanup_failed: cleanup,
              })
            }
            result.data ??= {}
            if (target && target.account === undefined) result.data.target_ref_legacy = true
            if (reason) {
              result.message += `（本地库不可用，已走 UI 兜底：${reason}）`
              result.data.localdb_fallback_reason = reason
              // 模型→OCR已有fallback_reason保留，避免覆盖旧解析通道的失败原因。
              if (result.data.fallback_reason === undefined) result.data.fallback_reason = reason
              result.data.capability_difference = 'UI 读取可能清除角标；消息时间/方向可能近似，无数据库消息ID或增量游标，跨通道可能重复'
              if (cleanup !== undefined) result.data.cleanup_failed = cleanup
            }
            if (hint) {
              result.data.hint = hint
              result.message += `；${hint}`
            }
            return result
          }
          if (args.channel === 'ui') return uiOutcome()
          if (args.channel !== 'local-db' && (!state || state.mode === 'unset')) {
            return uiOutcome(undefined, '运行 aid-wecom doctor --localdb 可启用本地库通道')
          }
          if (args.channel !== 'local-db' && state?.mode === 'ui' && state.kind === 'compat') {
            return uiOutcome('mode_ui_compat', '当前状态为 UI 兼容模式，可重跑 doctor --localdb 核对')
          }
          current ??= await getAccount()
          if (target && target.account === undefined) {
            throw new CodedOperationError('TARGET_REF_STALE', '旧目标句柄未绑定账号，不能用于本地库读取；请重新搜索或按名称读取')
          }
          try {
            return await (deps.readDbFn ?? readLocaldbSession)({
              name: target?.name ?? args.target_name!, ...(target ? { target } : {}),
              limit: args.limit, cursor: args.cursor, sinceDays: args.since_days, forced: args.channel === 'local-db',
            }, opCtx, current, local)
          } catch (err) {
            if (err instanceof CancelledError || (err instanceof CodedOperationError && err.code === 'CANCELLED')) throw err
            checkCancelled(opCtx.signal)
            if (!(err instanceof CodedOperationError) || !LOCALDB_FALLBACK_CODES.has(err.code) || args.channel === 'local-db') throw err
            if (args.cursor !== undefined) throw new CodedOperationError('CURSOR_UNSUPPORTED', '本次数据库通道不可用，原游标未推进；请稍后重试', 'none', {
              cursor: args.cursor, localdb_reason: err.code, ...err.data,
            })
            // 已知绑定ref在降级前再核对，不能因版本失败而绕过账号约束。
            if (target?.account !== undefined) {
              const beforeUi = await getAccount()
              if (beforeUi.account !== target.account) throw new CodedOperationError('TARGET_REF_STALE', '读取期间账号发生变化，请重新搜索')
            }
            ;(local.logFn ?? logLocaldbEvent)({ type: 'db_fallback', stage: 'read-session', reason: err.code })
            return uiOutcome(err.code, '可运行 aid-wecom doctor --localdb 核对本地库状态', err.data?.cleanup_failed)
          }
        },
      )
    },
  }
}
