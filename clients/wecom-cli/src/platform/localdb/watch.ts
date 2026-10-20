/**
 * P2 任务 3.2：watch_poll 本地库执行器（多会话增量；设计 §4.2 / 冻结点 4）。
 *
 * 复用共享访问段（access.ts：版本准入 + 密钥/快照解密，message 库必需；user 必需——
 * context 任务的本人 sender 域映射；session 可选容忍——focus 解析需要，缺 session 时
 * 该 focus 项进 failed 部分返回）。业务段：
 *   context（user_table.id 唯一映射本人）→ conversations（全部会话当前 max(message_id)）
 *   → 源库代际比对（hash(源库 fileIdentity + schema_version)，同 cursor generation 同源
 *   同构；缺失/不一致/水位回退 → 明确报告 generation_changed 并重建基线，不当「正常无
 *   消息轮」——同账号换库后小 message_id 不被旧水位压住）→ focus 绑定（第八轮评审 P1：
 *   建立时一次性解析并持久绑定到 {conversation_id, display_name}，后续只按持久身份读数、
 *   不再 resolve 名称——改名不影响跟踪；ref 输入 verify + 账号核对后同样绑定；失败进
 *   failed 且不自动解绑）→ DB 水位存储比对（显式关注的空会话水位存 0——首批消息全量
 *   按增量上报；未传 focus 的全量模式首见会话才按当前 max 建基线）
 *   → 对 max > 水位的会话调 py `watch` 任务（watermarks 经 workdir 内临时 JSON 文件传入）
 *   → normalizeMessages 归一 → 组冻结点 4 事件 → 水位推进（只到 next_watermarks = 已返回消息）。
 *
 * 事件形态（冻结点 4）：data = {channel:'local-db', new_messages:[{conversation_id,
 * messages:[全部新消息，system:true 标注], has_more?}], last_sender:{<conv_id>:'self'|'peer'|null},
 * failed?}；增量独立于待回复判定（返回水位以来全部新消息）；最后发言方按"最后一条可参与
 * 回复判定的消息"计算（排除表 {1017,38,31}，py 侧已按此回退）。
 *
 * 水位语义：只推进成功返回的会话、只到 next_watermarks（=已返回消息；has_more 截断时
 * 剩余下轮继续）；无新消息/解析失败的会话水位不动；DB 不可用（版本/指纹/密钥/快照失败）
 * → 抛出（operation 层转结构化 db_unavailable，**绝不自动落 UI**——设计 §4.2 v1 决策：
 * UI 消息无 msg_id，跨通道结果无法去重且会污染水位语义）；取消/身份失败（IDENTITY_
 * UNCONFIRMED）照常抛，不吞。
 *
 * 消费侧规则（评审 v4 #38 归属交付）：**恢复 DB 后消费增量前，消费方应核对会话最新发言方
 * 与已回复状态**（中断期间水位连续、但「是否需要回复」应结合 unread 快照复核；watch 不
 * 回放历史，新增关注基线 = 加入时刻水位，存量待回复由调用方初始化流程处理）。
 *
 * 代际语义（第八轮评审 P1）：水位存储带 generation = hash(源库 fileIdentity +
 * schema_version)（与 read-session cursor generation 同源同构）。同账号下源库被替换/重建
 * （新 fileIdentity）、状态缺代际、或当前 max 回退到存储水位之下时，旧水位不可信——本轮
 * 输出 generation_changed=true + generation_reason 并按当前 max 重建基线（focus 绑定保留，
 * 只按 conversation_id 重录水位），绝不冒充「正常无消息轮」压住新库消息。
 *
 * 水位锚点（第九轮评审 P1）：代际比对覆盖不了「同文件重写」——DELETE+重插可原地重新产生
 * 更小的 message_id 且 fileIdentity 与 schema_version 都不变（DML 不动 schema_version、
 * write_bytes 原地截断不改文件身份），旧水位（如 4）之下重生的新行（如新 ID 1–4）会被静默
 * 压住。故每会话水位条目增 anchor = 水位行的消息稳定字段摘要（py `_message_anchor`，与
 * read-session 游标锚同一函数，排除 server_id/sequence）：基线建立时取该行摘要、watch 推进
 * 时取返回集末端摘要；py watch 对非零水位先校验该行仍存在且摘要一致，失效（重写/行删除/
 * 旧文件无锚）→ 按 0 处理（该会话全量重报）并标注 watermark_reset，TS 透传
 * watermark_rebuilt + watermark_reset 明确报告，不做静默处理。重建路径的水位保存允许
 * 回退（第十轮评审 P1）：该会话以本批返回末端 nextWatermark 与对应新锚点替换旧状态
 * （重建后的新基线；limit 截断时分轮推进 [1,2]→[3,4]→…），否则「不回退」约束会卡住
 * 较小的新水位、每轮重复第一页；正常增量路径不回退约束不变。max 未变的重写（行内容已换、
 * 行数相同）py 不会被调用——TS 侧以 conversations 任务的水位行锚比对补位（见基线段），
 * 失效同样走 py 失效路径全量重报。trade-off：库重建/恢复旧备份时会重报旧内容一次——
 * 「漏消息」与「重复/旧内容」之间取不漏（消费侧已有去重/状态核对规则：
 * 归一化 msg_id 去重 + resume_note）。
 *
 * 安全语义全部继承：identity_confirmed、keyscan 负缓存、KEY_VERIFY_FAILED 重扫一次、
 * finally 清理产物。快照语义（一次性）：读后不复核账号/源文件可接受（对齐 unread；游标
 * 代际/水位锚点核验属 read-session 单读语义，watch 的会话级水位由 py 端 message_id 单调性
 * 与轮间代际比对保证——账号切换经账号字段全量重置基线，同账号代际变化经 generation 重建）。
 */
import { basename, join } from 'node:path'
import { writeFileSync } from 'node:fs'
import { CancelledError, CodedOperationError, type ErrorCode, type OpContext } from '../../operations/types.js'
import type { OperationOutcome } from '../../operations/context.js'
import { admitLocaldbVersion, hash, prepareDecryptedDbs, unavailable } from './access.js'
import { readLocalState, writeLocalState } from './matrix.js'
import { runLocaldbPy } from './driver.js'
import { logLocaldbEvent } from './log.js'
import { checkCancelled, type CurrentAccount } from './identity.js'
import { normalizeMessages, type NormalizedMessage, type RawMessageRow } from './messages.js'
import { resolveFocusConversation, type FocusFailure } from './unread.js'
import { verifyTargetRef, type TargetRefIdentity } from '../targetRef.js'
import type { LocaldbReadDeps } from './readSession.js'
import {
  loadLocaldbWatchState,
  localdbWatchStatePath,
  saveLocaldbWatchState,
  type LocaldbWatchFocusBinding,
  type LocaldbWatchState,
} from './watchDbState.js'

export interface LocaldbWatchArgs {
  /**
   * focus 关注输入列表（operation 层已校验非空数组；未传 = 全部会话）。每项为会话名或
   * 账号绑定 target_ref（形态判定见 TARGET_REF_SHAPE；第八轮评审 P1：建立时一次性解析
   * 并持久绑定，后续轮次只按 conversation_id 读数，失败进 failed）。
   */
  focus?: string[]
  /** 强制通道覆盖落盘状态选择（channel='local-db' 时；不绕过实际版本矩阵） */
  forced?: boolean
  /** 每会话增量上限（py 缺省 100；正安全整数） */
  limit?: number
}

/** 单会话新消息事件（冻结点 4：messages 为全部新消息；has_more=true 表示仍有未返回消息；
 * display_name 仅显式 focus 绑定的会话携带，取绑定时显示名——改名不影响跟踪后仍可读；
 * watermark_rebuilt=true 表示存储水位经锚点校验失效、本会话按 0 全量重报（第九轮评审 P1：
 * 消费侧按 msg_id 去重，宁重报不漏报） */
export interface LocaldbWatchConversation {
  conversation_id: string
  display_name?: string
  messages: NormalizedMessage[]
  has_more?: boolean
  watermark_rebuilt?: true
}

/** conversations 任务行（py 契约：全表按 conversation_id 聚合，last_message_id 降序）。
 *  P2 任务 3.4 起 send 本地库核验的基线读取复用同一解析（导出而非复制）。 */
export interface ConversationRow {
  conversation_id: string
  count: number
  last_send_time: number | null
  last_message_id: number
  /** 水位行（last_message_id 那行）的消息稳定字段摘要（py _message_anchor 同源）；
   *  watch 按 max 建基线时一并存入水位，供下轮锚点有效性校验。缺失（旧 py）按 '' 兜底——
   *  非零水位缺锚时 py 校验必然失效重建，方向 fail-safe（宁重报不漏报） */
  last_anchor: string
}

function isSafeInt(v: unknown): v is number {
  return typeof v === 'number' && Number.isSafeInteger(v)
}

/** conversations 任务行的防御性解析（非对象/缺字段行跳过；水位字段非法丢弃） */
export function parseConversationRows(value: unknown): ConversationRow[] {
  const raw = Array.isArray(value) ? value : []
  const rows: ConversationRow[] = []
  for (const item of raw) {
    if (item === null || typeof item !== 'object') continue
    const rec = item as Record<string, unknown>
    if (typeof rec.conversation_id !== 'string' || rec.conversation_id.length === 0) continue
    if (!isSafeInt(rec.last_message_id) || rec.last_message_id < 0) continue
    rows.push({
      conversation_id: rec.conversation_id,
      count: isSafeInt(rec.count) ? rec.count : 0,
      last_send_time: isSafeInt(rec.last_send_time) ? rec.last_send_time : null,
      last_message_id: rec.last_message_id,
      last_anchor: typeof rec.last_anchor === 'string' ? rec.last_anchor : '',
    })
  }
  return rows
}

/** py watch 任务返回的会话块（防御性逐字段归一，不信任 py 侧契约漂移） */
interface WatchPerConversation {
  conversation_id: string
  rows: RawMessageRow[]
  lastSenderState: 'self' | 'peer' | null
  hasMore: boolean
  /** next_watermarks 中该会话的新水位（=已返回消息的最大 message_id） */
  nextWatermark: number
  /** 已返回消息末端行的消息摘要（py _message_anchor 同源；推进水位时一并存储）。
   *  非法/缺失 → undefined（水位仍推进但锚保持旧值——下轮校验必然失效重建，fail-safe） */
  nextAnchor?: string
  /** py 侧判定存储水位失效（行缺失/锚不符）按 0 全量重报（第九轮评审 P1） */
  watermarkReset: boolean
}

/** 消息摘要形态校验（64-hex，与 read-session 游标锚同源同构） */
const ANCHOR_RE = /^[0-9a-f]{64}$/

/** py watch 响应的防御性解析（导出供单测；非信任边界——字段非法一律归一/丢弃） */
export function parseWatchResponse(value: Record<string, unknown>): {
  per: WatchPerConversation[]
  nextWatermarks: Record<string, number>
} {
  const nextRaw = (value.next_watermarks ?? null) as Record<string, unknown> | null
  const nextWatermarks: Record<string, number> = {}
  if (nextRaw !== null && typeof nextRaw === 'object' && !Array.isArray(nextRaw)) {
    for (const [conv, id] of Object.entries(nextRaw)) {
      if (isSafeInt(id) && id >= 0) nextWatermarks[conv] = id
    }
  }
  const per: WatchPerConversation[] = []
  const raw = Array.isArray(value.per_conversation) ? value.per_conversation : []
  for (const item of raw) {
    if (item === null || typeof item !== 'object') continue
    const rec = item as Record<string, unknown>
    if (typeof rec.conversation_id !== 'string' || rec.conversation_id.length === 0) continue
    const rows = (Array.isArray(rec.messages) ? rec.messages : []).filter(
      (r): r is RawMessageRow => r !== null && typeof r === 'object',
    )
    const state = rec.last_sender_state
    const next = nextWatermarks[rec.conversation_id]
    const anchor = typeof rec.next_anchor === 'string' && ANCHOR_RE.test(rec.next_anchor)
      ? rec.next_anchor
      : undefined
    per.push({
      conversation_id: rec.conversation_id,
      rows,
      lastSenderState: state === 'self' || state === 'peer' ? state : null,
      hasMore: rec.has_more === true,
      // 防御：py 契约保证返回过消息的会话必在 next_watermarks；缺失时按返回集末端兜底，
      // 不合法（非安全整数/小于返回集）则该会话水位不动（下轮重复读取，不丢消息）
      nextWatermark: isSafeInt(next) ? next : -1,
      nextAnchor: anchor,
      watermarkReset: rec.watermark_reset === true,
    })
  }
  return { per, nextWatermarks }
}

/** watch 执行器依赖：LocaldbReadDeps 超集 + DB 水位存储注入面（测试/定制布局替换） */
export interface LocaldbWatchDeps extends LocaldbReadDeps {
  watchStatePathFn?: () => string | null
  loadWatchDbStateFn?: (path: string, account: string) => LocaldbWatchState
  saveWatchDbStateFn?: (path: string, state: LocaldbWatchState) => boolean
  /** focus 的 target_ref 输入验证（缺省真实 verifyTargetRef；测试注入 fake/真实密钥目录） */
  verifyRefFn?: (ref: string) => TargetRefIdentity
}

/**
 * target_ref 形态（payload 与签名均为 base64url 串，HMAC-SHA256 签名恒 43 字符、payload
 * 为 JSON 的 base64url 均 ≥16 字符）：focus 输入据此区分 ref 与会话名。普通会话名即使含
 * 点号也几乎不可能两段均为 ≥16 位 base64url 字符；极端误判时 verify 失败进 failed（不
 * 错绑定、不静默改义）。
 */
const TARGET_REF_SHAPE = /^[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}$/

export async function runLocaldbWatch(
  args: LocaldbWatchArgs,
  ctx: OpContext,
  current: CurrentAccount,
  deps: LocaldbWatchDeps = {},
): Promise<OperationOutcome> {
  const log = deps.logFn ?? logLocaldbEvent
  const getState = deps.readStateFn ?? (() => readLocalState(deps.stateDir))
  const putState = deps.writeStateFn ?? (state => writeLocalState(state, deps.stateDir))
  const runPy = deps.runPyFn ?? runLocaldbPy
  const watchStatePathFn = deps.watchStatePathFn ?? localdbWatchStatePath
  const loadWatchDbState = deps.loadWatchDbStateFn ?? loadLocaldbWatchState
  const saveWatchDbState = deps.saveWatchDbStateFn ?? saveLocaldbWatchState
  const verifyRefFn = deps.verifyRefFn ?? verifyTargetRef
  const state = getState()
  checkCancelled(ctx.signal)
  log({ type: 'db_attempt', stage: 'watch' })
  const admission = await admitLocaldbVersion(ctx, current, state, { forced: args.forced }, deps)
  const paths = current.dataDir.db_paths!
  const prepared = await prepareDecryptedDbs(
    { message: paths.message, user: paths.user, session: paths.session },
    {
      ctx, current, version: admission.version, expected: admission.expected, algorithm: admission.algorithm,
      state, getState, putState, workPrefix: 'watch',
      // focus 解析需要 session（R 群名）；缺 session 时该 focus 项解析失败进 failed，不拖垮整轮
      tolerateSessionFailure: true, deps,
    },
  )
  const { work } = prepared
  let outcome: OperationOutcome | null = null
  let failure: unknown
  let failed = false

  try {
    const call = async (params: string[], input?: string): Promise<Record<string, unknown>> => {
      checkCancelled(ctx.signal)
      const result = await runPy(params, { signal: ctx.signal, ...(input === undefined ? {} : { stdin: input }) })
      checkCancelled(ctx.signal) // runLocaldbPy 已等待 close，随后才允许 finally 清理。
      if (!result.ok || !result.data) throw unavailable('本地库子进程未完成，请检查 Python 环境')
      return result.data
    }
    const requireQuery = (value: Record<string, unknown>): Record<string, unknown> => {
      if (value.ok === true) return value
      if (value.error === 'IDENTITY_UNCONFIRMED') {
        throw new CodedOperationError('IDENTITY_UNCONFIRMED', '账号目录与 user_table.id 的本人身份映射未确认')
      }
      if (value.error === 'SCHEMA_UNSUPPORTED') throw new CodedOperationError('VERSION_UNSUPPORTED', '本地库 schema 不支持当前读取')
      throw unavailable('本地库查询未完成')
    }
    const query = (params: string[]) => call(['query', '--workdir', work.dir, ...params]).then(requireQuery)

    // 方向比较基准：user_table.id 唯一映射本人（--account 校验，同 readSession/unread context 任务）
    const meta = await query(['--task', 'context', '--account', current.account])
    if (typeof meta.self_user_id !== 'string' || meta.self_user_id !== current.account || !Number.isSafeInteger(meta.schema_version)) {
      throw new CodedOperationError('IDENTITY_UNCONFIRMED', '本人 sender_id 域身份未确认')
    }
    const selfUserId = meta.self_user_id as string

    // 全部会话当前水位（基线比对 + 未传 focus 时的目标集来源）；last_anchor 供水位建立时
    // 一并存储（下轮 py 锚点有效性校验的比对基准，第九轮评审 P1）
    const conversations = parseConversationRows((await query(['--task', 'conversations'])).conversations)
    const currentMax = new Map<string, number>()
    const currentMaxAnchor = new Map<string, string>()
    for (const row of conversations) {
      currentMax.set(row.conversation_id, row.last_message_id)
      currentMaxAnchor.set(row.conversation_id, row.last_anchor)
    }

    // DB 水位存储（冻结点 4：独立文件，账号变化 → 全量重置基线）
    const wsPath = watchStatePathFn()
    if (!wsPath) throw unavailable('%LOCALAPPDATA% 未设置，无法定位 localdb-watch-state.json')
    const ws = loadWatchDbState(wsPath, current.account)

    // 源库代际比对（第八轮评审 P1）：generation = hash(源库 fileIdentity + schema_version)，
    // 与 read-session cursor generation 同源同构。存储有水位但代际缺失/不一致（同账号源库
    // 被替换/重建，新库 message_id 可能远小于旧水位）→ 旧水位不可信：清空重建基线并在
    // 本轮输出明确报告，不当「正常无消息轮」（否则新消息被旧水位永久压住）；无水位时代际
    // 差异无失效对象，仅更新锚值
    const generation = hash([prepared.sourceId, meta.schema_version])
    const generationReasons: string[] = []
    if (Object.keys(ws.conversations).length > 0 && ws.generation !== generation) {
      generationReasons.push(ws.generation === undefined ? 'generation-missing' : 'generation-mismatch')
      ws.conversations = {}
    }
    ws.generation = generation

    // focus 绑定（冻结点 4 / 决策 #34）：建立时一次性解析并持久绑定到
    // {conversation_id, display_name, type?}；后续轮次只按持久身份读数，不再 resolve
    // 名称——联系人改名/类型不重判，已有绑定不受影响。ref 输入的身份约束（第九轮评审
    // P1）：只接受账号绑定的 contact/group ref（legacy 无账号证据、跨账号 ref、类型不符
    // 的候选均进 failed，不静默绑错）。解析/校验失败该项进 failed（不自动解绑，报告原因）；
    // 取消不被吞
    const failedFocus: FocusFailure[] = []
    const targetIds = new Set<string>()
    const focusDisplay = new Map<string, string>()
    const bindings = ws.focus ?? []
    if (args.focus !== undefined) {
      for (const focus of args.focus) {
        checkCancelled(ctx.signal)
        try {
          const bound = bindings.find((b) => b.key === focus)
          if (bound !== undefined) {
            // 已绑定：按持久 conversation_id 读数（改名轮零 resolve）
            targetIds.add(bound.conversation_id)
            focusDisplay.set(bound.conversation_id, bound.display_name)
            continue
          }
          let conversationId: string
          let displayName: string
          let source: 'name' | 'ref'
          let bindingType: string | undefined
          if (TARGET_REF_SHAPE.test(focus)) {
            // ref 输入：verify（有效期/签名）→ 身份约束核对 → 从 ref 的 target 走一次
            // resolve 得 conv_id。本地库只接受账号绑定 ref（同 readSession 的 target 约束）：
            // legacy ref 无账号证据，不能静默绑定到当前账号的会话；账号不符同样拒绝。
            const identity = verifyRefFn(focus)
            if (identity.account === undefined) {
              failedFocus.push({ focus, reason: 'legacy-ref-unsupported' })
              continue
            }
            if (identity.account !== current.account) {
              throw new CodedOperationError('TARGET_REF_STALE', '目标句柄属于另一个账号，不能作为当前账号的关注')
            }
            // 类型约束（第九轮评审 P1）：contact 只认 S:、group 只认 R:——不能把群引用绑到
            // 同名联系人会话（反之亦然）；type=other 无类型信息，按无名目标同款来源要求
            bindingType = identity.type
            conversationId = await resolveFocusConversation(
              query, identity.name, selfUserId,
              identity.type === 'contact' || identity.type === 'group' ? identity.type : undefined,
            )
            displayName = identity.name
            source = 'ref'
          } else {
            // name 输入：按会话名 resolve（规则同 readSession/unread）
            conversationId = await resolveFocusConversation(query, focus, selfUserId)
            displayName = focus
            source = 'name'
          }
          bindings.push({
            key: focus,
            conversation_id: conversationId,
            display_name: displayName,
            source,
            ...(bindingType !== undefined ? { type: bindingType } : {}),
            bound_at: new Date().toISOString(),
          })
          ws.focus = bindings
          targetIds.add(conversationId)
          focusDisplay.set(conversationId, displayName)
        } catch (err) {
          if (err instanceof CancelledError || (err instanceof CodedOperationError && err.code === 'CANCELLED')) throw err
          checkCancelled(ctx.signal)
          failedFocus.push({
            focus,
            reason: err instanceof CodedOperationError ? err.code : 'LOCALDB_UNAVAILABLE',
          })
        }
      }
    } else {
      for (const id of currentMax.keys()) targetIds.add(id)
    }

    // 基线建立 / 候选筛选：无水位记录 → 基线 = 当前 max（只报之后，冻结点 4「新增关注
    // 基线 = 加入时刻水位」）；显式关注解析成功的空会话（message_table 无行）→ 水位存 0
    //（首批消息按正常增量全量上报——加入时刻无消息，「只报加入之后」= 全部；0 是合法
    // 水位，与「无记录」以 prev === undefined 区分）；未传 focus 的全量模式首见会话才按
    // 当前 max 建基线（不回放旧会话）。max < 存储水位（源库回退/重建后 message_id 变小）
    // → 按代际变更同处理：重建该会话基线 + 明确报告。
    // 基线条目带 anchor（第九轮评审 P1）：按 max 建基线取该行摘要、空会话取 ''——非零
    // 水位缺锚时 py 校验必然失效重建，方向 fail-safe（宁重报不漏报）。
    const watermarks: Record<string, { last_message_id: number; anchor: string }> = {}
    let baselineEstablished = 0
    const regressedConvs: string[] = []
    for (const conv of targetIds) {
      const max = currentMax.get(conv)
      const prev = ws.conversations[conv]
      if (max === undefined) {
        if (args.focus !== undefined && prev === undefined) {
          ws.conversations[conv] = { last_message_id: 0, anchor: '' }
          baselineEstablished++
        }
        // 其余（全量模式首见空会话 / 已有水位）：无可建立的水位基线（等首条消息落库后下轮处理）
        continue
      }
      if (prev === undefined) {
        ws.conversations[conv] = { last_message_id: max, anchor: currentMaxAnchor.get(conv) ?? '' }
        baselineEstablished++
        continue
      }
      if (max < prev.last_message_id) {
        regressedConvs.push(conv)
        ws.conversations[conv] = { last_message_id: max, anchor: currentMaxAnchor.get(conv) ?? '' }
        continue
      }
      if (max > prev.last_message_id) {
        watermarks[conv] = { last_message_id: prev.last_message_id, anchor: prev.anchor ?? '' }
      } else if (max > 0 && max === prev.last_message_id &&
                 (currentMaxAnchor.get(conv) ?? '') !== (prev.anchor ?? '')) {
        // max 未变的同文件重写（行内容已换、行数相同）：无增量轮 py 不会被调用，py 的锚点
        // 校验补位由 TS 借 conversations 任务的水位行锚（currentMaxAnchor，每轮已取）完成——
        // 失效 → 传「当前 max + 旧锚」让 py 按既有失效路径按 0 全量重报并标注
        // watermark_reset（第九轮评审 P1 同族；锚匹配 → 不进 watermarks，正常无消息轮
        // 行为不变，不增加任何每轮开销）
        watermarks[conv] = { last_message_id: max, anchor: prev.anchor ?? '' }
      }
    }
    if (regressedConvs.length > 0) generationReasons.push('watermark-regressed')

    // py watch 任务：watermarks 经 workdir 内临时 JSON 文件传入（桥协议：文件路径而非 argv）
    let per: WatchPerConversation[] = []
    if (Object.keys(watermarks).length > 0) {
      checkCancelled(ctx.signal)
      const marksFile = join(work.dir, 'watch-watermarks.json')
      writeFileSync(marksFile, JSON.stringify(watermarks), 'utf8')
      const limitParams = args.limit !== undefined ? ['--limit', String(args.limit)] : []
      const response = call(
        ['watch', '--workdir', work.dir, '--watermarks', marksFile,
          '--account', selfUserId, ...limitParams],
      ).then(value => {
        if (value.ok !== true) {
          if (value.error === 'SCHEMA_UNSUPPORTED') throw new CodedOperationError('VERSION_UNSUPPORTED', '本地库 schema 不支持当前读取')
          throw unavailable('本地库 watch 查询未完成')
        }
        return value
      })
      per = parseWatchResponse(await response).per
    }

    // 组事件（冻结点 4 形态）+ 水位推进（只到 next_watermarks = 已返回消息 + 新锚点）。
    // watermark_reset（存储水位经锚点校验失效，py 按 0 全量重报）→ 会话事件标注
    // watermark_rebuilt + 顶层 watermark_reset 明细，绝不静默（第九轮评审 P1）
    const newMessages: LocaldbWatchConversation[] = []
    const lastSender: Record<string, 'self' | 'peer' | null> = {}
    const resetConvs: string[] = []
    for (const conv of per) {
      const messages = normalizeMessages(conv.rows, selfUserId)
      newMessages.push({
        conversation_id: conv.conversation_id,
        // 显式 focus 绑定的会话附绑定时显示名（后续轮次不再 resolve 名称，显示名不随改名漂移）
        ...(focusDisplay.has(conv.conversation_id) ? { display_name: focusDisplay.get(conv.conversation_id) } : {}),
        messages,
        ...(conv.hasMore ? { has_more: true } : {}),
        ...(conv.watermarkReset ? { watermark_rebuilt: true as const } : {}),
      })
      if (conv.watermarkReset) resetConvs.push(conv.conversation_id)
      lastSender[conv.conversation_id] = conv.lastSenderState
      const existing = ws.conversations[conv.conversation_id]
      // 水位推进（第十轮评审 P1）：重建（watermark_reset）路径允许水位回退写入——同文件
      // 重写可原地重生更小的 message_id（如旧水位 4、重写后 ID 1–6），limit 截断时本批
      // 末端（如 2）低于旧水位，「不回退」约束会把该会话永久卡在旧水位反复重报第一页。
      // py 已按锚点失效从 0 全量重报，本批返回末端 + 新锚点即重建后语义正确的新基线，
      // 直接替换旧状态；正常增量路径保留不回退约束不变。防御：nextWatermark 非法（-1，
      // next_watermarks 契约漂移）一律不动，且锚仅在合法摘要（parseWatchResponse 已校验
      // 64-hex）时替换——缺锚保持旧值，下轮校验必然失效重建，方向 fail-safe（宁重报不漏报）
      if (existing !== undefined && conv.nextWatermark >= 0 &&
          (conv.watermarkReset || conv.nextWatermark >= existing.last_message_id)) {
        existing.last_message_id = conv.nextWatermark
        if (conv.nextAnchor !== undefined) existing.anchor = conv.nextAnchor
      }
    }
    if (!saveWatchDbState(wsPath, ws)) {
      // 水位保存失败：本轮事件照常返回（内存中已推进），下轮按盘上旧水位重复读取——
      // 归一化的 msg_id 供消费侧去重，不丢消息（fail-safe 方向）
      log({ type: 'db_fallback', stage: 'watch-state', reason: 'state_write_failed' })
    }
    const failedNote = failedFocus.length > 0 ? `；${failedFocus.length} 个 focus 解析失败` : ''
    const baselineNote = baselineEstablished > 0 ? `；新建关注基线 ${baselineEstablished} 个（只报之后的新消息）` : ''
    const generationNote = generationReasons.length > 0
      ? `；检测到源库代际变化（${generationReasons.join(',')}），基线已重建`
      : ''
    const resetNote = resetConvs.length > 0
      ? `；${resetConvs.length} 个会话水位锚点失效已全量重报（按 msg_id 去重）`
      : ''
    outcome = {
      message: `本地库 watch 轮：新消息会话 ${newMessages.length} 个${failedNote}${baselineNote}${generationNote}${resetNote}`,
      data: {
        channel: 'local-db' as const,
        new_messages: newMessages,
        last_sender: lastSender,
        // 消费侧规则（评审 v4 #38）：恢复 DB 后消费增量前，核对会话最新发言方与已回复状态
        resume_note: '恢复 DB 后消费增量前，消费方应核对会话最新发言方与已回复状态（watch 中断期间不回放历史）',
        ...(baselineEstablished > 0 ? { baseline_established: baselineEstablished } : {}),
        // 代际变化（缺失/不一致/水位回退）：明确报告，绝不冒充「正常无消息轮」
        ...(generationReasons.length > 0
          ? { generation_changed: true as const, generation_reason: generationReasons.join(',') }
          : {}),
        // 水位锚点失效（同文件重写/行删除/无锚，代际校验不可见）：全量重报的会话明细，
        // 与会话级 watermark_rebuilt 标注配套，供消费侧去重（宁重报不漏报）
        ...(resetConvs.length > 0 ? { watermark_reset: resetConvs } : {}),
        ...(failedFocus.length > 0 ? { failed: failedFocus } : {}),
      },
    }
    const latest = getState()
    if (latest?.scan_failure) putState({ ...latest, scan_failure: undefined })
    log({ type: 'db_success', stage: 'watch' })
  } catch (err) {
    failure = err
    failed = true
  } finally {
    if (!work.cleanup()) {
      const data = { cleanup_failed: [{ dir: basename(work.dir), reason: 'remove_failed' }] }
      log({ type: 'db_fallback', stage: 'cleanup', reason: 'remove_failed' })
      if (failed) {
        const code: ErrorCode = failure instanceof CodedOperationError ? failure.code : failure instanceof CancelledError ? 'CANCELLED' : 'LOCALDB_UNAVAILABLE'
        failure = new CodedOperationError(code, failure instanceof Error ? failure.message : '本地库 watch 轮失败', 'none', {
          ...(failure instanceof CodedOperationError ? failure.data : {}), ...data,
        })
      } else if (outcome) Object.assign(outcome.data!, data)
    }
  }
  if (failed) throw failure
  return outcome!
}
