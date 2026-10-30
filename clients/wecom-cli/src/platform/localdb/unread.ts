/**
 * P2 任务 3.1：unread_list 本地库执行器（待回复快照；设计 §4.2 待回复检测为主要用途）。
 *
 * 复用共享访问段（access.ts：版本准入 + 密钥/快照解密，与 read-session 完全同语义），业务段：
 *   context（user_table.id 唯一映射本人 sender 域）→ last-per-conv（py 侧按排除表
 *   {1017,38,31} 回退 last_participating）→ focus 逐名 resolve（规则同 readSession）→ 归一。
 *
 * 待回复判定（§4.2 冻结语义）：每会话取 last_participating——sender_id === 本人 → 非待回复；
 * !== 本人（含 sender 非字符串，保守按 peer）→ 待回复；null（全会话皆系统类）→ 非待回复且
 * 标注（system_only_conversations 计数）。输出 conversations 仅含待回复会话（unreplied:true），
 * 自会话/本人最后发言的会话不出现。系统消息既不制造（last_participating 跳过系统类，本人
 * 发言后的系统提示不重新触发）也不清除（对方发言后的系统提示不顶掉判定）。
 *
 * focus（冻结点 4）：未传 = 全部待回复会话；单名解析失败进 failed:[{focus, reason}] 部分返回，
 * 不影响其他；给出 focus 时只输出解析成功的关注会话。name 过滤与 UI 通道同语义（display_name
 * 子串包含；显示名来源 = focus 命中的 focus 原名 + 指定 name 时 py 反向取名的实际名称——
 * 第九轮评审 P2：不传 focus 的快照不再把全部会话当「无显示名」排除；无显示名（反向取名
 * null）的会话在指定 name 时排除）。
 *
 * 命名（第十轮评审 P2）：每轮请求 --with-names（反向取名）。① S: 联系人的名称/别名/备注
 * 与 resolve 同语义参与命名（行附 match_names = 该行全部非空名称字段）；过滤按 name 或
 * match_names 任意一项子串命中即算命中——备注/别名命中不再漏。② 存在 R: 会话但 session
 * 命名来源不可用 → py 顶层 naming_incomplete:true：指定 name 过滤时明确失败
 * （LOCALDB_UNAVAILABLE，不把「取不到群名」冒充「确实无匹配」的空结果）；未指定 name 的
 * 普通快照保持 session 可选语义不变，naming_incomplete 只标注到输出 data 不报错。
 *
 * 快照语义（一次性）：读后不复核账号/源文件可接受；无游标/水位；transient→db 运行时提升
 * 属 read-session 行为，本执行器不做。安全语义全部继承：identity_confirmed、keyscan 负缓存、
 * KEY_VERIFY_FAILED 重扫一次、finally 清理产物。
 */
import { basename } from 'node:path'
import { CancelledError, CodedOperationError, type ErrorCode, type OpContext } from '../../operations/types.js'
import type { OperationOutcome } from '../../operations/context.js'
import { admitLocaldbVersion, prepareDecryptedDbs, unavailable } from './access.js'
import { readLocalState, writeLocalState, type LocalDbState } from './matrix.js'
import { runLocaldbPy } from './driver.js'
import { logLocaldbEvent } from './log.js'
import { checkCancelled, type CurrentAccount } from './identity.js'
import { formatTimestamp, NON_TEXT_PLACEHOLDER, readableMessageText } from './messages.js'
import type { LocaldbReadDeps } from './readSession.js'
import { hasExternalWechatHint } from './contactScope.js'

export interface LocaldbUnreadArgs {
  /** focus 会话名列表（operation 层已校验非空数组；逐个 resolve，失败进 failed） */
  focus?: string[]
  /** 会话名过滤（与 UI 通道同语义——display_name 子串包含。每轮请求 py 反向取名后按
   *  实际名称匹配（第九轮评审 P2）：R 群名认 conversation_table、S 联系人认 user_table
   *  的名称/别名/备注列（第十轮评审 P2①，与 resolve 同语义）；命中 = name 或
   *  match_names 任意一项子串包含。无命名来源的会话无法匹配而排除；命名来源不可用
   *  （session 缺失且存在 R: 会话）→ 明确失败 LOCALDB_UNAVAILABLE，不冒充空结果） */
  name?: string
  /** 强制通道覆盖落盘状态选择（channel='local-db' 时；不绕过实际版本矩阵） */
  forced?: boolean
}

/** 待回复会话行（两通道同形字段 + channel + judgment_basis；§4.2） */
export interface UnrepliedConversation {
  /** 会话名（focus 解析命中时附 focus 原名；指定 name 过滤时附反向取名的实际名称——
   *  未指定 name 且未传 focus 的快照不附） */
  name?: string
  conversation_id: string
  /** 最后一条可参与回复判定消息的时间（'YYYY-MM-DD HH:MM:SS' 本地时区；非法留空） */
  last_time: string
  /** 该消息正文预览（超 100 字符截断；非文本类型为占位符） */
  last_text_preview: string
  /** 恒 true：conversations 只含待回复会话（字段显式表达语义，供消费侧过滤） */
  unreplied: true
}

/** focus 单项解析失败（冻结点 4：部分返回，不推进/不影响其他项） */
export interface FocusFailure {
  focus: string
  reason: string
}

/** py last-per-conv 行的最小消费面（防御性逐字段归一，不信任 py 侧契约漂移） */
interface LastPerConvRow {
  conversation_id: string
  last_participating: Record<string, unknown> | null
  /** 反向取名的规范显示名（--with-names 才有：R 群名认 conversation_table、S 联系人认
   *  user_table 规范 name 列，无命中/来源缺失 → null）；未请求取名 → undefined */
  name?: string
  /** S: 会话该行全部非空名称字段（名称/别名/备注候选列，第十轮评审 P2①——与 resolve
   *  同语义参与 name 过滤）；R: 群无此字段 */
  matchNames?: string[]
}

const PREVIEW_MAX_CHARS = 100

/** 待回复行的正文预览：文本类型取 py 提取正文，其余类型占位（与 C9b 占位语义一致） */
function previewText(row: Record<string, unknown>): string {
  const text = readableMessageText(row) || NON_TEXT_PLACEHOLDER
  return text.length > PREVIEW_MAX_CHARS ? text.slice(0, PREVIEW_MAX_CHARS) + '…' : text
}

function parseLastPerConvRows(value: unknown): LastPerConvRow[] {
  const raw = Array.isArray(value) ? value : []
  const rows: LastPerConvRow[] = []
  for (const item of raw) {
    if (item === null || typeof item !== 'object') continue
    const rec = item as Record<string, unknown>
    if (typeof rec.conversation_id !== 'string' || rec.conversation_id.length === 0) continue
    const part = rec.last_participating
    // match_names：仅收非空字符串（第十轮评审 P2①；缺省/非法 → undefined，不参与匹配）
    const matchNames = Array.isArray(rec.match_names)
      ? rec.match_names.filter((n): n is string => typeof n === 'string' && n.length > 0)
      : []
    rows.push({
      conversation_id: rec.conversation_id,
      last_participating: part !== null && typeof part === 'object' && !Array.isArray(part)
        ? (part as Record<string, unknown>)
        : null,
      // name=null（无命名来源）与缺省（未请求取名）都归一为 undefined——两者都不参与匹配
      name: typeof rec.name === 'string' && rec.name.length > 0 ? rec.name : undefined,
      matchNames: matchNames.length > 0 ? matchNames : undefined,
    })
  }
  return rows
}

/**
 * focus 单名 → conversation_id（规则同 readSession 范本：strip @微信；命名候选仅 user_table
 * 精确匹配 + session 的 R 群名；user 命中须有实际 S 会话；多候选 → TARGET_AMBIGUOUS）。
 * focus 无目标类型信息，按 readSession 无名目标同款来源要求（user_table + conversation_table）。
 * targetType（第九轮评审 P1）：ref 输入携带的 identity.type 保留为类型约束——contact 只认
 * S:、group 只认 R:（类型不符/无命中 → 查无目标，不能把群引用绑到联系人）；缺省同无名目标。
 * P2 任务 3.2/3.4 起 watch 执行器与 send 本地库核验复用同一段（导出而非复制）。
 */
export async function resolveFocusConversation(
  query: (params: string[]) => Promise<Record<string, unknown>>,
  focus: string,
  selfUserId: string,
  targetType?: 'contact' | 'group',
  subtitle?: string,
): Promise<string> {
  const name = focus.trim().replace(/@微信$/, '').trim()
  if (!name) throw new CodedOperationError('TARGET_NOT_FOUND', '会话名去除 @微信 后缀后为空，无法定位')
  const externalOnly = hasExternalWechatHint(focus, targetType, subtitle)
  const resolved = await query(['--task', 'resolve', '--name', name, '--account', selfUserId,
    ...(externalOnly ? ['--external-only'] : [])])
  const sources = resolved.sources as Record<string, unknown> | undefined
  const required = targetType === 'contact' || externalOnly ? ['user_table']
    : targetType === 'group' ? ['conversation_table'] : ['user_table', 'conversation_table']
  if (!sources || required.some(source => sources[source] !== 'ok')) {
    if (required.some(source => sources?.[source] === 'schema-unknown')) {
      throw new CodedOperationError('VERSION_UNSUPPORTED', '会话定位所需 schema 尚不支持，不能完整消歧')
    }
    throw unavailable('会话定位所需来源不完整，不能确认查无目标或唯一目标')
  }
  const candidateIds: string[] = []
  const unresolvedUsers = new Set<string>()
  for (const raw of Array.isArray(resolved.candidates) ? resolved.candidates : []) {
    if (!raw || typeof raw !== 'object') continue
    const row = raw as Record<string, unknown>
    if (externalOnly && (row.source !== 'user_table' || row.contact_scope !== 'external')) {
      throw new CodedOperationError('VERSION_UNSUPPORTED', '外部联系人身份来源不完整，不能绑定同名目标')
    }
    const id = row.conversation_id
    if (row.source === 'user_table' && typeof row.user_id === 'string' && id === null && targetType !== 'group') {
      unresolvedUsers.add(row.user_id)
    }
    // session 的 S 标题不作命名来源；S 单聊须两端含本人，群只认 conversation_table 的 R:。
    if (typeof id !== 'string' ||
        !(row.source === 'user_table' && id.startsWith('S:')) &&
        !(row.source === 'conversation_table' && id.startsWith('R:'))) continue
    if (id.startsWith('S:') && !id.slice(2).split('_').includes(selfUserId)) continue
    // 类型约束（第九轮评审 P1）：contact 只认 S:、group 只认 R:
    if (targetType === 'contact' && !id.startsWith('S:')) continue
    if (targetType === 'group' && !id.startsWith('R:')) continue
    if (!candidateIds.includes(id)) candidateIds.push(id)
  }
  if (candidateIds.length === 0 && unresolvedUsers.size > 0 && sources.conversation_table !== 'ok') {
    if (sources.conversation_table === 'schema-unknown') {
      throw new CodedOperationError('VERSION_UNSUPPORTED', '没有消息会话，且 session schema 无法用于核实实际会话')
    }
    throw unavailable('没有消息会话，且 session 来源不可用，不能确认查无会话')
  }
  if (candidateIds.length + unresolvedUsers.size > 1) {
    throw new CodedOperationError('TARGET_AMBIGUOUS', '本地库存在多个同名目标，无法消歧')
  }
  const id = candidateIds[0]
  if (!id) throw new CodedOperationError('TARGET_NOT_FOUND', '完整检索后未找到已识别的目标会话')
  return id
}

export async function readLocaldbUnread(
  args: LocaldbUnreadArgs,
  ctx: OpContext,
  current: CurrentAccount,
  deps: LocaldbReadDeps = {},
): Promise<OperationOutcome> {
  const log = deps.logFn ?? logLocaldbEvent
  const getState = deps.readStateFn ?? (() => readLocalState(deps.stateDir))
  const putState = deps.writeStateFn ?? (state => writeLocalState(state, deps.stateDir))
  const runPy = deps.runPyFn ?? runLocaldbPy
  const state = getState()
  checkCancelled(ctx.signal)
  log({ type: 'db_attempt', stage: 'unread' })
  const admission = await admitLocaldbVersion(ctx, current, state, { forced: args.forced }, deps)
  const paths = current.dataDir.db_paths!
  const prepared = await prepareDecryptedDbs(
    { message: paths.message, user: paths.user, session: paths.session },
    {
      ctx, current, version: admission.version, expected: admission.expected, algorithm: admission.algorithm,
      state, getState, putState, workPrefix: 'unread',
      // 快照语义：focus 单目标解析失败进 failed 部分返回，可选 session 失败不拖垮整个快照
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

    // 方向比较基准：user_table.id 唯一映射本人（--account 校验，同 readSession context 任务）
    const meta = await query(['--task', 'context', '--account', current.account])
    if (typeof meta.self_user_id !== 'string' || meta.self_user_id !== current.account || !Number.isSafeInteger(meta.schema_version)) {
      throw new CodedOperationError('IDENTITY_UNCONFIRMED', '本人 sender_id 域身份未确认')
    }

    // focus 逐名解析（冻结点 4）：单名失败进 failed 部分返回；取消不被吞
    const failedFocus: FocusFailure[] = []
    const focusNames = new Map<string, string>() // conversation_id → focus 原名（首个命中）
    for (const focus of args.focus ?? []) {
      checkCancelled(ctx.signal)
      try {
        const id = await resolveFocusConversation(query, focus, meta.self_user_id as string)
        if (!focusNames.has(id)) focusNames.set(id, focus)
      } catch (err) {
        if (err instanceof CancelledError || (err instanceof CodedOperationError && err.code === 'CANCELLED')) throw err
        checkCancelled(ctx.signal)
        failedFocus.push({
          focus,
          reason: err instanceof CodedOperationError ? err.code : 'LOCALDB_UNAVAILABLE',
        })
      }
    }
    // 全部 focus 解析失败时不抛错：返回空 conversations + failed 明细（不冒充「无待回复」
    // 的正常空快照，语义由 failed 表达——冻结点 4 部分返回的极端情形）

    // 反向取名（--with-names，每轮请求；第十轮评审 P2）：py 侧 R 群名认 conversation_table、
    // S 联系人认 user_table 的名称/别名/备注列（与 resolve 同一命名纪律），行附 name 与
    // S: 的 match_names；存在 R: 会话且 session 来源不可用 → 顶层 naming_incomplete:true。
    // 指定 name 过滤遇 naming_incomplete → 明确失败（LOCALDB_UNAVAILABLE）：群名取不到时
    // 「过滤」不可靠，绝不冒充「确实无匹配」的空结果；未指定 name 的普通快照保持 session
    // 可选语义不变（只把标注透传到 data，不报错）
    const nameFilter = args.name?.trim()
    const last = await query(['--task', 'last-per-conv', '--with-names', '--account', meta.self_user_id as string])
    const namingIncomplete = last.naming_incomplete === true
    if (nameFilter && namingIncomplete) {
      throw unavailable('群命名来源不可用，无法执行名称过滤')
    }
    const rows = parseLastPerConvRows(last.conversations)
    // match_names 参与匹配（第十轮评审 P2①）：name 子串命中 name 或 match_names 任意一项
    // 即算命中（与 resolve 的名称/别名/备注规则一致；备注/别名不再漏）
    const matchNamesByConv = new Map<string, string[]>()
    for (const row of rows) {
      if (row.matchNames !== undefined) matchNamesByConv.set(row.conversation_id, row.matchNames)
    }
    const focused = args.focus !== undefined && args.focus.length > 0
    const conversations: UnrepliedConversation[] = []
    let systemOnly = 0
    for (const row of rows) {
      if (focused && !focusNames.has(row.conversation_id)) continue
      const part = row.last_participating
      if (part === null) {
        systemOnly++ // 全会话皆系统类：非待回复且标注（计数），不进待回复列表
        continue
      }
      // 方向：sender_id 与本人账号严格字符串相等（非字符串保守按 peer → 待回复）
      const unreplied = part.sender_id !== meta.self_user_id
      if (!unreplied) continue // 本人最后发言（含自会话）不出现
      // 显示名：focus 命中用 focus 原名（既有语义）；指定 name 时附反向取名的规范名称
      //（输出形态与第九轮一致：未指定 name 且未传 focus 的快照不附）
      const displayName = focusNames.get(row.conversation_id) ?? (nameFilter ? row.name : undefined)
      conversations.push({
        ...(displayName !== undefined ? { name: displayName } : {}),
        conversation_id: row.conversation_id,
        last_time: formatTimestamp(part.send_time),
        last_text_preview: previewText(part),
        unreplied: true,
      })
    }
    // name 过滤（第八轮评审 P2 + 第九/十轮评审 P2 修正）：UI 通道 name 既有语义为「display_name
    // 子串包含」（unreadList.ts runUi: u.name.includes(nameFilter)），DB 通道应用同一语义，
    // 两通道一致。命中 = name 或 match_names 任意一项子串包含（与 resolve 的名称/别名/备注
    // 规则一致）；name=null（无命名来源）的会话在指定 name 时无法匹配而排除（不冒充「无待
    // 回复」——过滤只是展示收窄，system_only 计数与 failed 明细不受影响），未指定 name 时保留
    const visible = nameFilter
      ? conversations.filter((c) => (c.name !== undefined && c.name.includes(nameFilter)) ||
          (matchNamesByConv.get(c.conversation_id)?.some((n) => n.includes(nameFilter)) ?? false))
      : conversations
    const nameNote = nameFilter ? `（匹配「${nameFilter}」）` : ''
    const failedNote = failedFocus.length > 0 ? `；${failedFocus.length} 个 focus 解析失败` : ''
    outcome = {
      message: `本地库待回复快照：待回复会话 ${visible.length} 个${nameNote}${failedNote}`,
      data: {
        channel: 'local-db',
        judgment_basis: 'last_sender',
        conversations: visible,
        // 命名来源不可用标注（第十轮评审 P2②）：仅未指定 name 的快照带（指定 name 时已
        // 明确失败）——「R: 群名取不到」的事实照常标注，不冒充正常轮
        ...(namingIncomplete ? { naming_incomplete: true as const } : {}),
        ...(failedFocus.length > 0 ? { failed: failedFocus } : {}),
        ...(systemOnly > 0 ? { system_only_conversations: systemOnly } : {}),
      },
    }
    const latest = getState()
    if (latest?.scan_failure) putState({ ...latest, scan_failure: undefined })
    log({ type: 'db_success', stage: 'unread' })
  } catch (err) {
    failure = err
    failed = true
  } finally {
    if (!work.cleanup()) {
      const data = { cleanup_failed: [{ dir: basename(work.dir), reason: 'remove_failed' }] }
      log({ type: 'db_fallback', stage: 'cleanup', reason: 'remove_failed' })
      if (failed) {
        const code: ErrorCode = failure instanceof CodedOperationError ? failure.code : failure instanceof CancelledError ? 'CANCELLED' : 'LOCALDB_UNAVAILABLE'
        failure = new CodedOperationError(code, failure instanceof Error ? failure.message : '本地库待回复快照失败', 'none', {
          ...(failure instanceof CodedOperationError ? failure.data : {}), ...data,
        })
      } else if (outcome) Object.assign(outcome.data!, data)
    }
  }
  if (failed) throw failure
  return outcome!
}
