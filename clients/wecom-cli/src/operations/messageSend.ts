/**
 * wecom_message_send：本地库准入/基线 → 企微双点坐标标定及无 OCR 导航/发送 → 本地库唯一出站记录核验。
 * 缺双点缓存时驱动从已验证的本人显示名搜索自会话，GLM 一次定位两个点，复制标题全等后缓存。
 * 目标由本地库唯一解析；驱动搜索并 Enter 打开默认结果，随后清理输入区并发送。
 * Enter 后未知结果仅保留原凭据供 send-status 只读对账，不自动重发。
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
  sanitizeTiming,
  validateTargetSelector,
  type ResolvedByNameTarget,
} from './navigate.js'
import { withSendSnapshot, verifySendReceipt, type SendBaseline, type SendReceipt, type SendCheck } from '../platform/localdb/sendVerification.js'
import { invalidateSendReceipt, saveSendReceipt, type ReceiptCrypto } from '../platform/sendReceipt.js'
import { mapExecutorError } from './errorMapping.js'
import { probeCurrentAccount, checkCancelled } from '../platform/localdb/identity.js'
import { assertSendDisplayNameUnique, resolveFocusConversation } from '../platform/localdb/unread.js'
import { hasExternalWechatHint } from '../platform/localdb/contactScope.js'
import { parseConversationRows } from '../platform/localdb/watch.js'
import type { LocaldbReadDeps } from '../platform/localdb/readSession.js'
import { decodeGroupCursor, groupCursorError } from '../platform/localdb/groupCursor.js'
import { queryLocaldbGroups } from '../platform/localdb/groups.js'
import { readBusinessSessions, sessionLocator } from '../platform/localdb/sessions.js'
import { normalizeMessages, type RawMessageRow } from '../platform/localdb/messages.js'
import { hash } from '../platform/localdb/access.js'

/** dist/src/operations → 包根 drivers/ps1 */
const DRIVER_PATH = fileURLToPath(new URL('../../../drivers/ps1/message-send.ps1', import.meta.url))

/** observed_chat 片段字节预算（设计 §3.1.1：按结果预算装入，装不下省略并注明缺口） */
const OBSERVED_CHAT_BUDGET_BYTES = 6 * 1024
/** 片段包装（游标/字段名/confirmed 标注）计入预算的固定开销（评审 P2-20） */
const ENVELOPE_OVERHEAD_BYTES = 256

/**
 * 发送核验片段 observed_chat（设计 §3.1.1）：只复用同次核验已读快照的窗口记录，
 * 不为日志额外读库/操作UI/延长轮询。优先本次确认的出站记录与邻近记录；装不下
 * 的记录省略并标 truncated。片段是可选数据扩展：组装失败由调用方整体省略，
 * 不改变原发送结论（confirmed/unknown 均不含糊）。
 */
export function buildObservedChat(
  windowRows: Record<string, unknown>[],
  account: string,
  conversationId: string,
  sourceGeneration: string,
  confirmedMessageId: number | null,
  windowTruncated: boolean,
): Record<string, unknown> | null {
  try {
    const normalized = normalizeMessages(windowRows as unknown as RawMessageRow[], account)
    if (normalized.length === 0) return null
    const size = (value: unknown) => Buffer.byteLength(JSON.stringify(value), 'utf8')
    // 选择顺序：确认命中优先，其后向两侧邻近扩展；未确认时取窗口尾部（最新邻近）。
    let order: typeof normalized
    if (confirmedMessageId !== null) {
      const hit = normalized.findIndex(message => message.msg_id === confirmedMessageId)
      if (hit >= 0) {
        order = [normalized[hit]!]
        for (let distance = 1; order.length < normalized.length; distance++) {
          if (hit - distance >= 0) order.push(normalized[hit - distance]!)
          if (hit + distance < normalized.length) order.push(normalized[hit + distance]!)
        }
      } else {
        order = normalized
      }
    } else {
      order = [...normalized].reverse()
    }
    const kept: typeof normalized = []
    let used = ENVELOPE_OVERHEAD_BYTES  // 片段包装（session/source_generation/字段名）计入预算
    for (const message of order) {
      const bytes = size(message)
      if (used + bytes > OBSERVED_CHAT_BUDGET_BYTES) break  // 首条同样受预算约束
      kept.push(message)
      used += bytes
    }
    if (kept.length === 0) {
      // 确认命中的正文也装不下：整体省略（logsink 仍可用回执精确归因），不伪装完整
      return windowTruncated ? { session: sessionLocator(account, conversationId),
        source_generation: sourceGeneration, messages: [], ...(confirmedMessageId !== null
          ? { confirmed_message_id: confirmedMessageId } : {}), truncated: true } : null
    }
    const truncated = windowTruncated || kept.length < normalized.length
    kept.sort((a, b) => a.msg_id - b.msg_id)
    return {
      session: sessionLocator(account, conversationId),
      source_generation: sourceGeneration,
      messages: kept,
      ...(confirmedMessageId !== null ? { confirmed_message_id: confirmedMessageId } : {}),
      ...(truncated ? { truncated: true } : {}),
    }
  } catch {
    return null  // 片段组装失败整体省略，不影响原结果
  }
}

export interface WecomMessageSendArgs {
  session?: string
  target_ref?: string
  /** 直达模式：按本地库唯一会话名定位（与 target_ref 二选一） */
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
          // 纯空白不是有效消息；保留既有输入约束。
          if (/^[\s\u0085]*$/.test(args.text)) {
            return 'text 去除空白后不能为空'
          }
          if (args.text.length > MAX_TEXT_LENGTH) {
            return `text 长度不能超过 ${MAX_TEXT_LENGTH} 字（请调用方分段发送）`
          }
          // 单行与多行均清草稿并经剪贴板粘贴；发送前不回读输入文本。
          return null
        },
        async (opCtx, tracker) => {
          const root = artifactDirFn()
          if (!root) {
            throw new CodedOperationError('INTERNAL_ERROR', '%LOCALAPPDATA% 未设置，无法定位 artifact 目录')
          }
          // 任何 UI 写入前核对监控游标与本地库目标身份。
          const bound = args.group_cursor === undefined ? undefined : decodeGroupCursor(args.group_cursor, args.target_name!.trim())
          if (bound) {
            const current = await (local.probeAccountFn ?? probeCurrentAccount)(opCtx)
            await queryLocaldbGroups({ targetName: args.target_name!.trim(), cursor: args.group_cursor, poll: true, limit: 50 }, opCtx, current, local)
          }

          // 直达模式先以本地库唯一解析目标，失败时不写入 UI。
          let resolved: ResolvedByNameTarget | null = null
          if (args.target_name !== undefined) {
            // Explicit external targets can be resolved from trusted DB evidence without opening search.
            const current = await (local.probeAccountFn ?? probeCurrentAccount)(opCtx)
            let conversationId = ''
            await withSendSnapshot(current, opCtx, local, 'send-baseline', async query => {
              const meta = await query(['--task', 'context', '--account', current.account])
              if (meta.self_user_id !== current.account) throw new CodedOperationError('IDENTITY_UNCONFIRMED', '本人身份映射未确认')
              if (!bound && !args.subtitle && args.target_name!.trim().endsWith('@微信')) {
                try {
                  const exactId = await resolveFocusConversation(query, args.target_name!, current.account, undefined, undefined, true)
                  if (exactId.startsWith('R:')) conversationId = exactId
                } catch (err) {
                  if (!(err instanceof CodedOperationError && err.code === 'TARGET_NOT_FOUND')) throw err
                }
              }
              if (!conversationId) conversationId = await resolveFocusConversation(query, args.target_name!, current.account,
                bound ? 'group' : hasExternalWechatHint(args.target_name!, 'contact', args.subtitle) ? 'contact' : undefined,
                hasExternalWechatHint(args.target_name!, 'contact', args.subtitle) ? '微信联系人' : args.subtitle)
            })
            const resolvedSubtitle = conversationId.startsWith('R:') ? '' :
              hasExternalWechatHint(args.target_name, 'contact', args.subtitle) ? '微信联系人' : ''
            resolved = {
              name: args.target_name.trim(), subtitle: resolvedSubtitle,
              section: conversationId.startsWith('R:') ? '群聊' : '联系人',
              target_ref: createRefFn(args.target_name.trim(), conversationId.startsWith('R:') ? 'group' : 'contact',
                resolvedSubtitle, undefined, current.account),
            }
          }
          const target = verifyRefFn(resolved !== null ? resolved.target_ref : args.target_ref!)
          if (bound && (target.type !== 'group' || target.name !== args.target_name!.trim())) throw groupCursorError()
          // target_ref 账号绑定校验必须在任何 UI 写入前完成。
          if (target.account !== undefined) {
            const refAccount = await (local.probeAccountFn ?? probeCurrentAccount)(opCtx)
            checkCancelled(opCtx.signal)
            if (target.account !== refAccount.account) {
              throw new CodedOperationError('TARGET_REF_STALE', '目标句柄属于另一个账号，请重新搜索')
            }
          }
          opCtx.progress({ stage: 'execute', message: `解析目标「${target.name}」，发送前检查当前会话` })

          /**
           * 本地库发送前准备：版本/身份准入、唯一目标 conversation_id、消息基线和本人显示名。
           * 任一失败都在 UI 写入前拒发。
           */
          let selfName = ''
          const planLocaldb = async (): Promise<SendBaseline> => {
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
                if (typeof meta.self_user_id !== 'string' || meta.self_user_id !== current.account || !Number.isSafeInteger(meta.schema_version) ||
                    typeof meta.self_name !== 'string' || !meta.self_name.trim()) {
                  throw new CodedOperationError('IDENTITY_UNCONFIRMED', '本人 sender_id 域身份未确认')
                }
                selfName = meta.self_name as string
                const searchName = target.type === 'group' ? target.name : target.name.replace(/@微信$/, '').trim()
                const globalId = await resolveFocusConversation(query, searchName, current.account, undefined, undefined, true)
                conversationId = deps.resolvedSession?.conversationId ?? await resolveFocusConversation(query, target.name, meta.self_user_id as string,
                  target.type === 'group' ? 'group' : target.type === 'contact' ? 'contact' : undefined, target.subtitle)
                if (conversationId !== globalId) throw new CodedOperationError('TARGET_AMBIGUOUS', '本地库目标身份不唯一，未发送')
                await assertSendDisplayNameUnique(query, target.name, target.type, target.subtitle, current.account)
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
              }, deps.resolvedSession !== undefined)
              checkCancelled(opCtx.signal)
              opCtx.progress({ stage: 'execute', message: `本地库发送基线已记录（「${target.name}」max message_id=${baseline}）` })
              return { account: current.account, conversationId: conversationId!, baseline, anchor, sourceId: source, schema }
          }

          // —— 本地库方案：基线 → 发送 → 本地库核验 —— //
          const plan = await planLocaldb()
          {
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
                      '-SelfName', selfName,
                      '-ArtifactDir', dir,
                    ],
                    signal: opCtx.signal,
                  })
                  if (result.navigate_required === true) {
                    retire()
                    throw new CodedOperationError('TARGET_NOT_FOUND', '目标会话未确认，未发送消息', 'none')
                  }
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
                  if (err instanceof CodedOperationError && err.code === 'RESULT_TIMEOUT') {
                    throw new CodedOperationError('EXECUTION_UNKNOWN', '发送驱动超时，结果未知；仅用 send-status 对账，勿重发', 'unknown')
                  }
                  if (err instanceof CancelledError) {
                    throw new CodedOperationError('CANCELLED', '发送驱动取消，结果未知；仅用 send-status 对账，勿重发', 'unknown')
                  }
                  throw err
                }
              }
              const dir = join(root, `send-${new Date().toISOString().replace(/[:.]/g, '-')}`)
              mkdirSync(dir, { recursive: true })
              const data = await spawnSendDriver(dir)
              const navigated = true
              // 发送动作已完成（driver ok = Enter 已按、无 EXECUTION_UNKNOWN）：此后任何失败
              // 都不能重入发送阶段；本地库未确认保持 EXECUTION_UNKNOWN。
              tracker.completed = 1

              // 本地库核验（短轮询等待落库；轮询参数可注入）
              let localHit: number | null = null
              let dbVerifyFailed: string | null = null
              let observedWindow: { rows: Record<string, unknown>[]; truncated: boolean; generation: string } | null = null
              for (let attempt = 1; attempt <= Math.max(1, poll.attempts); attempt++) {
                if (attempt > 1) await sleepAbortable(poll.delayMs, opCtx.signal)
                try {
                  const check = await verifySendReceipt(receipt!, opCtx, local)
                  const { observed, observed_truncated: observedTruncated, ...lean } = check
                  diagnostics.push(lean)
                  localHit = check.message_id ?? null
                  if (observed && observed.length > 0) {
                    observedWindow = { rows: observed, truncated: observedTruncated === true, generation: check.source_generation }
                  }
                  if (check.status === 'confirmed') break
                } catch (err) {
                  if (err instanceof CancelledError || (err instanceof CodedOperationError && err.code === 'CANCELLED')) throw err
                  checkCancelled(opCtx.signal)
                  dbVerifyFailed = err instanceof CodedOperationError ? err.code : 'LOCALDB_UNAVAILABLE'
                  break
                }
              }

              const sentVerification = { method: 'skipped', result: 'unverified' }
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
                navigated,
                sent_verification: sentVerification,
                input_point: inputPoint,
                timing_ms: sanitizeTiming(data.timing_ms),
                screenshot_paths: Array.isArray(data.screenshot_paths)
                  ? data.screenshot_paths.filter((p): p is string => typeof p === 'string')
                  : [],
                // 可选 observed_chat（设计 §3.1.1）：复用同次核验已读快照；缺失/组装失败
                // 整体省略，不改变原发送结论。confirmed 时带精确命中ID供 Agent 代发标注。
                ...(observedWindow !== null
                  ? (() => {
                      const fragment = buildObservedChat(observedWindow.rows, plan.account, plan.conversationId,
                        observedWindow.generation, localHit, observedWindow.truncated)
                      return fragment !== null ? { observed_chat: fragment } : {}
                    })()
                  : {}),
              }

              if (localHit !== null) {
                // db 核验命中：客户端已记录该出站消息（本地核验），非服务端回执（决策 #47）
                return {
                  message: `已向「${target.name}」发送消息，本地库核验命中（客户端记录已落库匹配 message_id=${localHit}；仅代表客户端动作已记录，不代表对方已收到）`,
                  data: {
                    ...baseData,
                    verification: 'local-record' as const,
                    verification_detail: { method: 'local-record' as const, message_id: localHit,
                      source_generation: hash([plan.sourceId, plan.schema]) },
                  },
                }
              }

              throw new CodedOperationError('EXECUTION_UNKNOWN',
                dbVerifyFailed !== null
                  ? `本地库发送核验失败（${dbVerifyFailed}），结果未知；不会自动重试，请用 send-status 只读对账`
                  : '本地库未找到唯一匹配的出站记录，结果未知；不会自动重试，请用 send-status 只读对账',
                undefined, baseData)
            } catch (err) {
              const mapped = mapExecutorError(err)
              throw new CodedOperationError(mapped.code, mapped.message,
                err instanceof CodedOperationError ? err.effectOverride : undefined,
                { ...(err instanceof CodedOperationError ? err.data : {}),
                  ...(receiptId ? { receipt_id: receiptId, send_status: 'unknown' } : {}),
                  localdb_verification: diagnostics })
            }
          }

        },
      )
    },
  }
}
