/**
 * watch_poll 本地库通道的 DB 水位存储（P2 任务 3.2；设计冻结点 4 存储契约）。
 *
 * 文件：%LOCALAPPDATA%\AidWorkAgent\wecom-cli\localdb\localdb-watch-state.json（dir 可注入，
 * 与 localdb-state.json 同目录）。内容：{account, generation?, process_started_at?, focus?,
 * conversations: {"<conv_id>": {last_message_id, anchor?}}}。
 *
 * - 与 UI 通道的 watch-state.json（文本水位）完全分离——跨通道结果无法去重，绝不混用
 *   （设计 §4.2：UI 消息无 msg_id）；
 * - 键 = {account, conversation_id}：**账号变化 → 全量重置基线**（conversations 清空，
 *   旧账号水位不再有意义；加载时以调用方传入的当前账号为准）；
 * - generation（第八轮评审 P1）：源库代际锚 = hash(源库 fileIdentity + schema_version)，
 *   与 read-session cursor 的 generation 同源同构。同账号下源库被替换/重建（代际变化）、
 *   状态缺代际、或水位回退时，旧水位不再可信——watch 轮据此重建基线而不是当「正常无
 *   消息轮」压住新消息；文件由旧版本写入（无 generation）同样触发重建；
 * - focus（第八轮评审 P1）：显式关注的持久绑定（key=调用方原始输入 → conversation_id +
 *   绑定时 display_name + source + bound_at）。建立时一次解析，后续轮次只按 conversation_id
 *   读数、不再 resolve 名称——联系人改名不影响跟踪（冻结点 4 / 决策 #34「一次解析为
 *   {account, conversation_id}，后续按持久身份轮询」）；绑定随账号全量重置；
 * - 容错：文件缺失/损坏/单条水位非法/绑定条目非法 → 该条丢弃（退化为该会话重建基线/
 *   重新解析），绝不因状态文件崩溃（对齐 watchState.ts / matrix.ts 容错语义）；
 * - 写入走 tmp + rename 原子替换（对齐 matrix.ts writeLocalState），失败不抛出返回 false。
 *
 * 消费侧规则（评审 v4 #38 归属交付）：watch 中断（DB 不可用轮/进程退出）后恢复 DB 消费
 * 增量前，消费方应核对会话最新发言方与已回复状态——中断期间的新消息水位仍连续推进，
 * 但「是否需要回复」的判定应结合 aid-wecom unread 快照复核，不回放历史。
 */
import { existsSync, mkdirSync, readFileSync, renameSync, unlinkSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'

/** 单会话水位（基元 = message_id，入库序）。anchor = 水位行（last_message_id 那行）的消息
 * 稳定字段摘要（py `_message_anchor` 同源，排除 server_id/sequence；第九轮评审 P1）：水位
 * 建立/推进时由 watch 轮一并写入，下轮经 py watch 的锚点有效性校验捕获「同 message_id 不
 * 再指向同一行」的库重写（fileIdentity/schema_version 不变时代际校验不可见）。
 * 空串 = 水位 0（显式关注的空会话）或暂无锚（旧文件/上游缺字段）——非零水位缺锚时 py 校验
 * 必然失效重建，方向 fail-safe（宁重报不漏报）。 */
export interface LocaldbWatchConversationState {
  last_message_id: number
  anchor?: string
}

/** focus 持久绑定（一次解析为 {account, conversation_id}，后续按持久身份轮询） */
export interface LocaldbWatchFocusBinding {
  /** 调用方原始输入（会话名或 target_ref 字符串） */
  key: string
  /** 建立时解析出的持久会话身份（后续轮次只按它读数） */
  conversation_id: string
  /** 绑定时的显示名（name 输入 = 输入名；ref 输入 = ref payload 名），输出显示名用 */
  display_name: string
  /** 绑定来源 */
  source: 'name' | 'ref'
  /** ref 绑定的目标类型（ref payload 的 contact/group/other；name 输入无类型信息不写。
   *  第九轮评审 P1：绑定建立时已按类型约束 resolve，此字段为绑定事实的存证，后续轮次
   *  不按它重判——已有绑定按 conversation_id 读数，改名/类型变化不影响跟踪） */
  type?: string
  /** 绑定时刻（ISO UTC） */
  bound_at: string
}

/** DB 水位存储文件内容（键 = {account, conversation_id}；账号切换整体失效重建基线） */
export interface LocaldbWatchState {
  account: string
  /** 源库代际锚 = hash(源库 fileIdentity + schema_version)；缺失（旧版本文件）→ watch 轮重建基线 */
  generation?: string
  /** 建立基线时观察到的 WXWork 进程启动时间（可选诊断字段，v1 不参与失效判定） */
  process_started_at?: string
  /** 显式关注的持久绑定（P2 任务 3.2 冻结点 4；第八轮评审 P1 落地持久身份） */
  focus?: LocaldbWatchFocusBinding[]
  conversations: Record<string, LocaldbWatchConversationState>
}

/** localdb-watch-state.json 路径（与 localdb-state.json 同目录）；参数语义对齐 matrix.ts
 * localDbStateDir：缺省读环境变量，显式 null → 模拟 LOCALAPPDATA 缺失 → null */
export function localdbWatchStatePath(localAppData?: string | null): string | null {
  const base = localAppData === undefined ? process.env.LOCALAPPDATA : localAppData
  if (!base) return null
  return join(base, 'AidWorkAgent', 'wecom-cli', 'localdb', 'localdb-watch-state.json')
}

function isValidConversationState(v: unknown): v is LocaldbWatchConversationState {
  if (v === null || typeof v !== 'object') return false
  const s = v as Record<string, unknown>
  if (typeof s.last_message_id !== 'number' || !Number.isSafeInteger(s.last_message_id) || s.last_message_id < 0) {
    return false
  }
  // anchor 允许空串（水位 0 的合法形态，见接口注释）；非字符串视为非法整条丢弃（退化为重建基线）
  return s.anchor === undefined || typeof s.anchor === 'string'
}

/** focus 绑定条目校验（字段非法 → 该条丢弃，退化为下轮重新解析，不拖垮整个状态） */
function isValidFocusBinding(v: unknown): v is LocaldbWatchFocusBinding {
  if (v === null || typeof v !== 'object') return false
  const b = v as Record<string, unknown>
  return typeof b.key === 'string' && b.key.length > 0 &&
    typeof b.conversation_id === 'string' && b.conversation_id.length > 0 &&
    typeof b.display_name === 'string' && b.display_name.length > 0 &&
    (b.source === 'name' || b.source === 'ref') &&
    (b.type === undefined || typeof b.type === 'string') &&
    typeof b.bound_at === 'string' && b.bound_at.length > 0
}

/**
 * 读取账号作用域的 DB 水位：文件缺失/损坏/账号不符 → 空基线（全量重建，conversations 清空）；
 * 单条水位非法/绑定条目非法 → 只丢该条（退化为该会话重建基线），不拖垮整个状态。
 * generation/focus 原样透出（缺失不在此定夺——代际比对与重建决策归 watch 轮）。
 */
export function loadLocaldbWatchState(path: string, account: string): LocaldbWatchState {
  const empty = (): LocaldbWatchState => ({ account, conversations: {} })
  try {
    if (!existsSync(path)) return empty()
    const parsed = JSON.parse(readFileSync(path, 'utf8')) as unknown
    if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) return empty()
    const obj = parsed as Record<string, unknown>
    if (obj.account !== account) return empty() // 账号变化 → 全量重置基线（冻结点 4）
    const raw = obj.conversations
    if (raw === null || typeof raw !== 'object' || Array.isArray(raw)) return empty()
    const conversations: Record<string, LocaldbWatchConversationState> = {}
    for (const [conv, v] of Object.entries(raw as Record<string, unknown>)) {
      if (isValidConversationState(v)) conversations[conv] = v
    }
    const state: LocaldbWatchState = { account, conversations }
    if (typeof obj.generation === 'string' && obj.generation.length > 0) state.generation = obj.generation
    if (typeof obj.process_started_at === 'string') state.process_started_at = obj.process_started_at
    if (Array.isArray(obj.focus)) {
      const focus = (obj.focus as unknown[]).filter(isValidFocusBinding)
      if (focus.length > 0) state.focus = focus
    }
    return state
  } catch {
    return empty()
  }
}

/** 原子写入（同目录 tmp + rename，对齐 matrix.ts writeLocalState）；目录不可写 → false 不抛 */
export function saveLocaldbWatchState(path: string, state: LocaldbWatchState): boolean {
  const tmp = `${path}.${process.pid}.${Date.now()}.tmp`
  try {
    mkdirSync(dirname(path), { recursive: true })
    writeFileSync(tmp, `${JSON.stringify(state, null, 2)}\n`, 'utf8')
    renameSync(tmp, path)
    return true
  } catch {
    try {
      unlinkSync(tmp)
    } catch {
      // 临时文件清理失败忽略（对齐 writeLocalState：不因清扫掩盖主结果）
    }
    return false
  }
}
