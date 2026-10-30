/**
 * wecom_message_send operation（M6：智能分发；P2 任务 3.4 增本地库发送核验，设计 §4.3）。
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
 *
 * M11a 直达模式：target_name（可选 --subtitle 消歧）与 target_ref 二选一——name 模式
 * 经 resolveTargetByName 内部 search 挑唯一目标转 ref 后走同一链路，data 附
 * resolved_target 供调用方观察实际定位到谁；effect/错误码/零重试契约与 ref 模式一致。
 *
 * P2 任务 3.4 本地库发送核验（state=db / ui(transient) 时接管终态判定，分派读法同
 * unread——沿用身份链 probeCurrentAccount；本地库不可用/resolve 失败/基线失败 → 完全
 * 回退既有单次调用路径【驱动内 OCR 核验，零改动】）：
 *   1) 发送前基线：本地库快照解密一次，resolve 目标 conversation_id（规则同 readSession，
 *      复用 unread 的 resolveFocusConversation；失败 → 老路径）+ conversations 任务取该会话
 *      当前 max(message_id) 为基线（时序保证：基线在发送动作前记录，规避 send_time 秒级
 *      精度与迟到回退，设计 §4.3 时间基准）；
 *   2) 发送：driver `-SkipVerify`（动作与核验分离，不产生 EXECUTION_UNKNOWN）；
 *   3) 本地库核验：重新快照解密 → query send-messages --after-id=基线（有界分页） → 匹配：方向=本人 ∧
 *      会话匹配 ∧ trusted 正文与发送文本全等（设计 §7：发送核验仅使用可信正文）。命中 →
 *      applied + data.verification='local-record'（verification_detail 含匹配 message_id）；
 *      发送后短轮询等待落库（P0：发送后落库延迟未实测，轮询次数/间隔可注入，缺省 3 次 × 1s）；
 *   4) 未命中/本地库核验失败 → 既有 OCR 核验：driver `-VerifyOnly`（仅终态证据 + 判定，
 *      不重入发送阶段）→ 命中 → applied + verification='ocr-bubble'；不命中 →
 *      EXECUTION_UNKNOWN。发送完成后核验阶段的一切错误（含 RESULT_TIMEOUT/UI_CHANGED/
 *      驱动失败）→ EXECUTION_UNKNOWN（绝不误报 effect=none），取消保持 CANCELLED/partial；
 *      不重入发送阶段，绝不自动重发。
 * 核验语义（决策 #47）：message_table 无发送状态字段——success/applied=客户端动作已记录
 * 并经本地核验（local-record）/OCR 气泡证据（ocr-bubble），**不代表对方已收到/已读**；
 * EXECUTION_UNKNOWN 时 data.verification 缺省（三值：local-record/ocr-bubble/缺省）。
 */
import { mkdirSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { runWecomOperation } from './context.js'
import { CancelledError, CodedOperationError, type OperationResult, type OpContext, type WecomOperation } from './types.js'
import { DEFAULT_DRIVER_TIMEOUT_MS, runPowerShellDriver, type RunPowerShellDriverFn } from '../platform/powershell.js'
import { artifactDir } from '../platform/environment.js'
import {
  createTargetRef,
  verifyTargetRef,
  type CreateTargetRefFn,
  type VerifyTargetRefFn,
} from '../platform/targetRef.js'
import {
  resolveTargetByName,
  runStageWithNavigation,
  sanitizeTiming,
  validateTargetSelector,
  type ResolvedByNameTarget,
} from './navigate.js'
import { withSendSnapshot, verifySendReceipt, type SendBaseline, type SendReceipt, type SendCheck } from '../platform/localdb/sendVerification.js'
import { invalidateSendReceipt, saveSendReceipt, type ReceiptCrypto } from '../platform/sendReceipt.js'
import { mapExecutorError } from './errorMapping.js'
import { readLocalState } from '../platform/localdb/matrix.js'
import { probeCurrentAccount, checkCancelled } from '../platform/localdb/identity.js'
import { resolveFocusConversation } from '../platform/localdb/unread.js'
import { hasExternalWechatHint } from '../platform/localdb/contactScope.js'
import { parseConversationRows } from '../platform/localdb/watch.js'
import type { LocaldbReadDeps } from '../platform/localdb/readSession.js'
import { decodeGroupCursor, groupCursorError } from '../platform/localdb/groupCursor.js'
import { queryLocaldbGroups } from '../platform/localdb/groups.js'
import { readBusinessSessions, sessionLocator } from '../platform/localdb/sessions.js'
import { hash } from '../platform/localdb/access.js'

/** dist/src/operations → 包根 drivers/ps1 */
const DRIVER_PATH = fileURLToPath(new URL('../../../drivers/ps1/message-send.ps1', import.meta.url))

export interface WecomMessageSendArgs {
  session?: string
  target_ref?: string
  /** M11a 直达模式：按会话名定位（与 target_ref 二选一；内部 search + 身份校验转 ref） */
  target_name?: string
  /** 直达模式可选消歧副标题（同名多候选时收紧匹配，如「微信联系人」） */
  subtitle?: string
  text: string
  /** Monitoring sends must carry the exact poll cursor, preventing a same-name group switch. */
  group_cursor?: string
}

const MAX_TEXT_LENGTH = 2000

/** 本地库核验轮询参数（P0：发送后落库延迟未实测，缺省 3 次 × 1s；测试可注入 0 延迟） */
export interface LocaldbVerifyPoll {
  attempts?: number
  delayMs?: number
}

/** 可中断睡眠：abort 时立即抛 CancelledError（发送已完成，取消按既有 partial 语义归并） */
async function sleepAbortable(ms: number, signal: AbortSignal): Promise<void> {
  if (signal.aborted) throw new CancelledError()
  await new Promise<void>((resolve, reject) => {
    const timer = setTimeout(() => {
      signal.removeEventListener('abort', onAbort)
      resolve()
    }, ms)
    const onAbort = () => {
      clearTimeout(timer)
      reject(new CancelledError())
    }
    signal.addEventListener('abort', onAbort, { once: true })
  })
}

export function createWecomMessageSendOperation(
  deps: {
    runDriverFn?: RunPowerShellDriverFn
    verifyRefFn?: VerifyTargetRefFn
    /** 内部 chatSearch 签发 target_ref 用（与 verifyRefFn 配套注入，测试可替换） */
    createRefFn?: CreateTargetRefFn
    artifactDirFn?: () => string | null
    /** P2 任务 3.4 本地库依赖（LocaldbReadDeps，测试注入 mock；缺省读真实状态/驱动） */
    localdb?: LocaldbReadDeps
    /** Internal DB-resolved locator; never accepted from MCP tool arguments. */
    resolvedSession?: { account: string; conversationId: string }
    /** 本地库核验轮询参数（缺省 3 次 × 1s；测试注入 0 延迟） */
    localdbVerifyPoll?: LocaldbVerifyPoll
    receiptCrypto?: ReceiptCrypto
  } = {},
): WecomOperation<WecomMessageSendArgs> {
  const runDriverFn = deps.runDriverFn ?? runPowerShellDriver
  const verifyRefFn = deps.verifyRefFn ?? verifyTargetRef
  // 与 chatSearch 的默认签发适配保持一致（坐标随条目写入 payload，select 消费）
  const createRefFn: CreateTargetRefFn =
    deps.createRefFn ?? ((name, type, subtitle, coords) => createTargetRef(name, type, subtitle, { coords }))
  const artifactDirFn = deps.artifactDirFn ?? (() => artifactDir())
  const local = deps.localdb ?? {}
  const getLocalState = local.readStateFn ?? (() => readLocalState(local.stateDir))
  const poll = { attempts: deps.localdbVerifyPoll?.attempts ?? 3, delayMs: deps.localdbVerifyPoll?.delayMs ?? 1000 }
  return {
    name: 'wecom_message_send',
    execute(args: WecomMessageSendArgs, ctx: OpContext): Promise<OperationResult> {
      if (args && args.session !== undefined) {
        return (async () => {
          const prepared = await runWecomOperation('readonly', ctx, () => {
            if (typeof args.session !== 'string' || !args.session.trim() || args.session.length > 2048) return 'session须为会话名称或读取返回的会话标识'
            if (args.target_ref !== undefined || args.target_name !== undefined || args.group_cursor !== undefined || args.subtitle !== undefined) return 'session不能与旧目标参数同时传入'
            if (typeof args.text !== 'string' || !args.text.trim() || args.text.length > MAX_TEXT_LENGTH) return 'text须为1–2000字非空消息'
            return null
          }, opCtx => readBusinessSessions({ sessions: [args.session!], single: true, limit: 1, metadataOnly: true }, opCtx, local))
          if (!prepared.success) return prepared
          const data = prepared.data
          if (typeof data.title !== 'string' || !data.title.trim()) return {
            ...prepared, success: false, code: 'TARGET_NOT_FOUND', message: '原会话存在但当前名称未取得，未发送消息',
            data: { session: data.session, stage: 'resolve', recovery_action: 'refresh_original_session' },
          }
          const account = data.self_user_id as string
          const conversationId = data.conversation_id as string
          const ref = createRefFn(data.title, data.kind === 'group' ? 'group' : 'contact',
            data.peer_platform === 'wechat' ? '微信联系人' : '', undefined, account)
          const result = await createWecomMessageSendOperation({ ...deps, resolvedSession: { account, conversationId } })
            .execute({ target_ref: ref, text: args.text }, ctx)
          result.data.session = sessionLocator(account, conversationId)
          return result
        })()
      }
      return runWecomOperation(
        'write',
        ctx,
        () => {
          if (args === null || typeof args !== 'object' || Array.isArray(args)) {
            return '参数必须是对象（target_ref/target_name 二选一；text 必填）'
          }
          const selectorErr = validateTargetSelector(args)
          if (selectorErr !== null) return selectorErr
          if (args.group_cursor !== undefined && (typeof args.group_cursor !== 'string' || !args.group_cursor ||
              args.group_cursor.length > 2048 || !args.target_name || args.target_ref)) return 'group_cursor仅支持完整群名target_name'
          if (args.subtitle !== undefined && typeof args.subtitle !== 'string') {
            return 'subtitle 必须是字符串（直达模式消歧用，可选）'
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
          // Check scope before any UI search. A monitored send never falls back to OCR-only admission.
          const bound = args.group_cursor === undefined ? undefined : decodeGroupCursor(args.group_cursor, args.target_name!.trim())
          if (bound) {
            const current = await (local.probeAccountFn ?? probeCurrentAccount)(opCtx)
            await queryLocaldbGroups({ targetName: args.target_name!.trim(), cursor: args.group_cursor, poll: true, limit: 50 }, opCtx, current, local)
          }

          // M11a 直达模式：内部 search 身份定位转 ref（定位失败 effect=none 且注明未发送
          // 消息），定位成功后走与 ref 模式完全一致的既有链路
          let resolved: ResolvedByNameTarget | null = null
          const directState = getLocalState()
          const directDbReady = directState?.mode === 'db' || (directState?.mode === 'ui' && directState.kind === 'transient')
          if (directDbReady && args.target_name !== undefined) {
            // Explicit external targets can be resolved from trusted DB evidence without opening search.
            // The driver must still confirm the exact WeChat title before typing and Enter.
            const current = await (local.probeAccountFn ?? probeCurrentAccount)(opCtx)
            let conversationId = ''
            await withSendSnapshot(current, opCtx, local, 'send-baseline', async query => {
              const meta = await query(['--task', 'context', '--account', current.account])
              if (meta.self_user_id !== current.account) throw new CodedOperationError('IDENTITY_UNCONFIRMED', '本人身份映射未确认')
              conversationId = await resolveFocusConversation(query, args.target_name!, current.account,
                bound ? 'group' : hasExternalWechatHint(args.target_name!, 'contact', args.subtitle) ? 'contact' : undefined,
                hasExternalWechatHint(args.target_name!, 'contact', args.subtitle) ? '微信联系人' : args.subtitle)
            })
            resolved = {
              name: args.target_name.trim(), subtitle: hasExternalWechatHint(args.target_name, 'contact', args.subtitle) ? '微信联系人' : '',
              section: conversationId.startsWith('R:') ? '群聊' : '联系人',
              target_ref: createRefFn(args.target_name.trim(), conversationId.startsWith('R:') ? 'group' : 'contact',
                hasExternalWechatHint(args.target_name, 'contact', args.subtitle) ? '微信联系人' : '', undefined, current.account),
            }
          } else if (args.target_name !== undefined) {
            resolved = await resolveTargetByName(
              { runDriverFn, createRefFn, opCtx, root },
              args.target_name,
              typeof args.subtitle === 'string' ? args.subtitle : '',
              { notSentNote: '未发送消息' },
            )
          }
          const target = verifyRefFn(resolved !== null ? resolved.target_ref : args.target_ref!)
          if (bound && (target.type !== 'group' || target.name !== args.target_name!.trim())) throw groupCursorError()
          // 第八轮评审 P1：target_ref 账号绑定校验必须在任何发送或降级之前——本地库 try
          // 之外，不被 planLocaldb 的兜底 catch 吞掉转旧发送路径（跨账号签发的有效 ref
          // 不得 OK/applied）。probe 身份链不依赖版本矩阵，老路径同样可用（对齐 readSession
          // 的账号绑定核对语义）；probe 失败按其自身错误码透传，不静默跳过核对。
          if (target.account !== undefined) {
            const refAccount = await (local.probeAccountFn ?? probeCurrentAccount)(opCtx)
            checkCancelled(opCtx.signal)
            if (target.account !== refAccount.account) {
              throw new CodedOperationError('TARGET_REF_STALE', '目标句柄属于另一个账号，请重新搜索')
            }
          }
          opCtx.progress({ stage: 'execute', message: `解析目标「${target.name}」，发送前检查当前会话` })

          /**
           * 本地库方案准备（发送前）：分派读法同 unread（db / ui(transient)），probe 身份链 →
           * resolve 目标 conversation_id → conversations 任务取基线。任何失败（身份/版本/
           * 快照/resolve 等）→ null = 回退老路径（此时未发送任何消息，零损失）；取消照常抛。
           */
          const planLocaldb = async (): Promise<SendBaseline | null> => {
            try {
              const state = getLocalState()
              if (!bound && (!state || !(state.mode === 'db' || (state.mode === 'ui' && state.kind === 'transient')))) return null
              const current = await (local.probeAccountFn ?? probeCurrentAccount)(opCtx)
              checkCancelled(opCtx.signal)
              let conversationId: string | null = null
              let baseline = 0
              let anchor = ''
              let source = ''
              let schema = 0
              await withSendSnapshot(current, opCtx, local, 'send-baseline', async (query, sourceId) => {
                const meta = await query(['--task', 'context', '--account', current.account])
                checkCancelled(opCtx.signal)
                if (typeof meta.self_user_id !== 'string' || meta.self_user_id !== current.account || !Number.isSafeInteger(meta.schema_version)) {
                  throw new CodedOperationError('IDENTITY_UNCONFIRMED', '本人 sender_id 域身份未确认')
                }
                conversationId = deps.resolvedSession?.conversationId ?? await resolveFocusConversation(query, target.name, meta.self_user_id as string,
                  bound ? 'group' : target.type === 'contact' ? 'contact' : undefined, target.subtitle)
                if (deps.resolvedSession && (current.account !== deps.resolvedSession.account || conversationId !== deps.resolvedSession.conversationId)) {
                  throw new CodedOperationError('IDENTITY_UNCONFIRMED', '当前会话定位与原读取目标不一致，未发送消息')
                }
                if (bound && (bound.account !== current.account || bound.conversationId !== conversationId ||
                    bound.generation !== hash([sourceId, meta.schema_version]))) throw groupCursorError()
                const row = parseConversationRows((await query(['--task', 'conversations'])).conversations)
                  .find((c) => c.conversation_id === conversationId)
                baseline = row ? row.last_message_id : 0
                anchor = row?.last_anchor ?? ''
                if (baseline > 0 && !/^[a-f0-9]{64}$/.test(anchor)) throw new Error('missing baseline anchor')
                source = sourceId
                schema = meta.schema_version as number
              })
              checkCancelled(opCtx.signal)
              opCtx.progress({ stage: 'execute', message: `本地库发送基线已记录（「${target.name}」max message_id=${baseline}）` })
              return { account: current.account, conversationId: conversationId!, baseline, anchor, sourceId: source, schema }
            } catch (err) {
              if (err instanceof CancelledError || (err instanceof CodedOperationError && err.code === 'CANCELLED')) throw err
              if (bound || deps.resolvedSession || hasExternalWechatHint(target.name, target.type, target.subtitle)) throw err
              checkCancelled(opCtx.signal)
              // 本地库不可用/resolve 失败：基线阶段未发送任何消息，完全回退老路径（零改动）
              opCtx.progress({ stage: 'execute', message: `本地库发送核验不可用（${err instanceof CodedOperationError ? err.code : 'LOCALDB_UNAVAILABLE'}），走驱动内 OCR 核验` })
              return null
            }
          }

          // —— 本地库方案（state db/ui(transient)）：基线 → 发送（-SkipVerify）→ 本地库核验 —— //
          const plan = await planLocaldb()
          if (plan !== null) {
            let receipt: SendReceipt | undefined
            let receiptId: string | undefined
            const diagnostics: SendCheck[] = []
            try {
              const spawnSendDriver = async (dir: string): Promise<Record<string, unknown>> => {
                // A navigation-only stage is retired before the next stage gets its own checkpoint.
                receipt = undefined
                receiptId = undefined
                let driverInvoked = false
                const retire = () => {
                  try { invalidateSendReceipt(dir) } catch {
                    throw new CodedOperationError('INTERNAL_ERROR',
                      '已知无发送动作，但恢复凭据失效失败；已停止本次调用，勿用该凭据自动确认或重发', 'unknown',
                      { receipt_invalidation_failed: true })
                  }
                }
                try {
                  const now = (local.now ?? Date.now)()
                  const checkpoint: SendReceipt = { ...plan, version: 1, text: args.text,
                    since: Math.floor(now / 1000) - 2, until: Math.ceil((now + DEFAULT_DRIVER_TIMEOUT_MS) / 1000) + 2 }
                  try {
                    receiptId = await saveSendReceipt(dir, checkpoint, deps.receiptCrypto)
                    receipt = checkpoint
                  } catch (err) {
                    if (err instanceof CodedOperationError && err.effectOverride === 'unknown') throw err
                    throw new CodedOperationError('INTERNAL_ERROR', '发送前加密凭据未落盘，未调用发送驱动', 'none')
                  }
                  opCtx.progress({ stage: 'checkpoint', message: `发送恢复凭据：${receiptId}（结果未知时仅用 send-status 对账，勿重发）` })
                  if (opCtx.signal.aborted) throw new CodedOperationError('CANCELLED', '发送前已取消，未调用发送驱动', 'none')
                  driverInvoked = true
                  const result = await runDriverFn({
                    script: DRIVER_PATH,
                    args: [
                      '-TargetName', target.name,
                      '-Subtitle', target.subtitle,
                      '-Section', target.type,
                      '-Text', args.text,
                      '-ArtifactDir', dir,
                      '-SkipVerify', // 动作与核验分离：终态判定由本地库接管（设计 §4.3 接线）
                    ],
                    signal: opCtx.signal,
                  })
                  if (result.navigate_required === true) retire()
                  return result
                } catch (err) {
                  // These codes are the existing driver's pre-Enter rejection contract. A timeout,
                  // in-driver cancellation, crash or unknown is never proof of no write.
                  const knownNone = err instanceof CodedOperationError &&
                    (err.effectOverride === 'none' || (err.effectOverride === undefined &&
                      !['RESULT_TIMEOUT', 'EXECUTION_UNKNOWN', 'CANCELLED', 'INTERNAL_ERROR'].includes(err.code)))
                  if (receiptId && (!driverInvoked || knownNone)) retire()
                  if (!driverInvoked && !(err instanceof CodedOperationError && err.effectOverride === 'unknown')) {
                    const mapped = mapExecutorError(err)
                    throw new CodedOperationError(mapped.code, mapped.message, 'none',
                      err instanceof CodedOperationError ? err.data : undefined)
                  }
                  throw err
                }
              }
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
              // 发送动作已完成（driver ok = Enter 已按、无 EXECUTION_UNKNOWN）：此后任何失败
              // 都不能重入发送阶段——本地库核验失败转 -VerifyOnly，仍失败 EXECUTION_UNKNOWN
              tracker.completed = 1

              // 本地库核验（短轮询等待落库；轮询参数可注入）
              let localHit: number | null = null
              let dbVerifyFailed: string | null = null
              for (let attempt = 1; attempt <= Math.max(1, poll.attempts); attempt++) {
                if (attempt > 1) await sleepAbortable(poll.delayMs, opCtx.signal)
                try {
                  const check = await verifySendReceipt(receipt!, opCtx, local)
                  diagnostics.push(check)
                  localHit = check.message_id ?? null
                  if (check.status === 'confirmed') break
                } catch (err) {
                  if (err instanceof CancelledError || (err instanceof CodedOperationError && err.code === 'CANCELLED')) throw err
                  checkCancelled(opCtx.signal)
                  dbVerifyFailed = err instanceof CodedOperationError ? err.code : 'LOCALDB_UNAVAILABLE'
                  break
                }
              }

              const sentVerificationRaw = (data.sent_verification ?? null) as Record<string, unknown> | null
              const sentVerification =
                sentVerificationRaw !== null && typeof sentVerificationRaw === 'object'
                  ? {
                      method: sentVerificationRaw.method === 'rule_2of3'
                        ? 'rule_2of3'
                        : sentVerificationRaw.method === 'skipped' ? 'skipped' : 'jev',
                      result: typeof sentVerificationRaw.result === 'string' ? sentVerificationRaw.result : 'unknown',
                      ...(typeof sentVerificationRaw.failure_mode === 'string' ? { failure_mode: sentVerificationRaw.failure_mode } : {}),
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
              const baseData = {
                receipt_id: receiptId,
                localdb_verification: diagnostics,
                target: target.name,
                ...(resolved !== null
                  ? { resolved_target: { name: resolved.name, subtitle: resolved.subtitle, section: resolved.section } }
                  : {}),
                title: typeof data.title === 'string' && data.title.length > 0 ? data.title : target.name,
                navigated,
                sent_verification: sentVerification,
                input_point: inputPoint,
                timing_ms: sanitizeTiming(data.timing_ms),
                screenshot_paths: Array.isArray(data.screenshot_paths)
                  ? data.screenshot_paths.filter((p): p is string => typeof p === 'string')
                  : [],
              }

              if (localHit !== null) {
                // db 核验命中：客户端已记录该出站消息（本地核验），非服务端回执（决策 #47）
                return {
                  message: `已向「${target.name}」发送消息，本地库核验命中（客户端记录已落库匹配 message_id=${localHit}；仅代表客户端动作已记录，不代表对方已收到）`,
                  data: {
                    ...baseData,
                    verification: 'local-record' as const,
                    verification_detail: { method: 'local-record' as const, message_id: localHit },
                  },
                }
              }

              // 未命中/本地库核验失败 → 既有 OCR 核验：driver -VerifyOnly（仅终态证据与判定，
              // 不重入发送阶段）；验证驱动失败按「消息可能已发出」归并 EXECUTION_UNKNOWN
              opCtx.progress({ stage: 'execute', message: dbVerifyFailed !== null ? `本地库核验失败（${dbVerifyFailed}），转 OCR 气泡核验` : '本地库未匹配到新记录，转 OCR 气泡核验' })
              const verifyDir = join(root, `send-verify-${new Date().toISOString().replace(/[:.]/g, '-')}`)
              mkdirSync(verifyDir, { recursive: true })
              try {
                await runDriverFn({
                  script: DRIVER_PATH,
                  args: [
                    '-TargetName', target.name,
                    '-Subtitle', target.subtitle,
                    '-Section', target.type,
                    '-Text', args.text,
                    '-ArtifactDir', verifyDir,
                    '-VerifyOnly', // 仅核验：不输入、不发送（驱动内 step0.5-4 全跳过）
                  ],
                  signal: opCtx.signal,
                })
              } catch (err) {
                // 核验调用直调驱动、不经 runStageWithNavigation 的写动作翻译：发送已完成
                // （tracker.completed=1），核验阶段的任何失败（RESULT_TIMEOUT/UI_CHANGED/
                // 驱动失败等）都不得透传原码——会被 writeEffect 归为 effect=none（消息已
                // 发出却报「未发生写动作」，误导消费侧重发），统一按 §4.3「发送后的失败保持
                // EXECUTION_UNKNOWN」归并（effect=unknown）；取消保持既有 CANCELLED/partial
                if (err instanceof CancelledError || (err instanceof CodedOperationError && err.code === 'CANCELLED')) throw err
                checkCancelled(opCtx.signal)
                throw new CodedOperationError('EXECUTION_UNKNOWN',
                  `核验链路失败（${err instanceof CodedOperationError ? err.code : '驱动执行失败'}）：` +
                  '消息可能已发出，但本地库核验与 OCR 气泡核验均未完成（不会自动重试，请用 send-status 对账）', undefined, baseData)
              }
              // 驱动正常返回 = 终态核验通过（EXECUTION_UNKNOWN 由驱动抛出、原样向上传播）
              return {
                message: `已向「${target.name}」发送消息，发送后校验通过（OCR 气泡证据；本地库未命中${dbVerifyFailed !== null ? `，本地库核验失败 ${dbVerifyFailed}` : ''}）`,
                data: {
                  ...baseData,
                  verification: 'ocr-bubble' as const,
                  ...(dbVerifyFailed !== null ? { localdb_verify_failed: dbVerifyFailed } : {}),
                },
              }
            } catch (err) {
              const mapped = mapExecutorError(err)
              throw new CodedOperationError(mapped.code, mapped.message,
                err instanceof CodedOperationError ? err.effectOverride : undefined,
                { ...(err instanceof CodedOperationError ? err.data : {}),
                  ...(receiptId ? { receipt_id: receiptId, send_status: 'unknown' } : {}),
                  localdb_verification: diagnostics })
            }
          }

          // —— 既有单次调用路径（本地库不可用/基线失败回退；行为零改动）—— //
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
              ...(resolved !== null
                ? { resolved_target: { name: resolved.name, subtitle: resolved.subtitle, section: resolved.section } }
                : {}),
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
