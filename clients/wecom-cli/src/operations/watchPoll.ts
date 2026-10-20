/**
 * wecom_watch_poll operation（M3；M9 起读取阶段换用 read-session 驱动 + 智能分发；P2 任务 3.2
 * 增本地库通道，分派矩阵对齐 unreadList 范本）：新消息跟踪单轮。
 *
 * - UI 通道（channel='ui' / 缺省按 doctor 状态分派——unset→UI+hint、ui(compat)→UI 不阻断）：
 *   单轮流程（TS 编排，组合 unread-list / read-session 两个驱动）：
 *     1) unread_list 快照（不开会话不清角标）；
 *     2) 与 watch-state.json 水位 diff：unread_count 增大或新出现 → 候选
 *        （读取成功后 last_unread 归零：进会话已清角标，之后任何角标都是新增；
 *        会话从快照消失时 last_unread 归零，否则「读完再来 1 条」会被旧水位压住漏报）；
 *     3) 每候选：经共享智能分发编排（navigate.ts，readonly 模式）调 read-session 驱动——
 *        首轮带 unread OCR 的行坐标走 row 快路径直点会话列表行，驱动内会话判定不过
 *        （列表已滚动/坐标漂移/非目标）→ navigate_required → 内部 chatSearch+chatSelect
 *        切换会话 → 二次调用（不再点行，已在目标会话）；读取失败不重试 → 读当前屏消息 →
 *        与 last_seen_text_norm 比对取增量 → 推进水位。读取失败的候选不推进水位
 *        （下轮仍候选，不丢消息）；
 *     4) 产出 NDJSON 事件（data.events）：{type:"new_messages",session,unread_count,messages}
 *        或（本轮无任何新消息时）{type:"tick",unread_total}。
 *   UI 通道既有事件字段/行为零改动（watchState 文本水位照旧）；focus 在 UI 模式被忽略并注明
 *   （对齐冻结点 4「UI 模式忽略并提示」）。
 *
 * - 本地库通道（db / ui(transient) 运行时探测 / 强制 local-db）：platform/localdb/watch.ts
 *   多会话增量执行器（冻结点 4 事件形态：data = {channel:'local-db', new_messages,
 *   last_sender, failed?}，增量独立于待回复判定，消息含 msg_id/system 标注）。**DB 不可用
 *   不自动落 UI**（设计 §4.2 v1 决策 + §2 兜底适用范围限定：UI 消息无 msg_id，跨通道结果
 *   无法去重且会污染水位语义）——失败返回结构化 {ok:false, code, db_unavailable:true}
 *   （水位天然保留：失败轮不推进），取消/身份失败（IDENTITY_UNCONFIRMED）照常抛不吞；
 *   强制 local-db 同样不落 UI。
 *
 * 副作用注意：UI 通道读取候选会话会清除其未读角标（企微客户端固有行为），并更新本机
 * watch-state.json 水位文件；本地库通道零 UI 副作用，推进 localdb-watch-state.json DB 水位；
 * 无对外写副作用（effect=none）。长循环不走本 operation，由 CLI watch 动词循环调用
 * （MCP 每轮一次 tool call）。
 */
import { mkdirSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { runWecomOperation, type OperationOutcome } from './context.js'
import { CancelledError, CodedOperationError, type OperationResult, type OpContext, type WecomOperation } from './types.js'
import { runPowerShellDriver, type RunPowerShellDriverFn } from '../platform/powershell.js'
import { artifactDir } from '../platform/environment.js'
import {
  createTargetRef,
  verifyTargetRef,
  type CreateTargetRefFn,
  type VerifyTargetRefFn,
} from '../platform/targetRef.js'
import { loadWatchState, saveWatchState, watchStatePath, type WatchState } from '../platform/watchState.js'
import { parseUnreadEntries, validateFocus, type UnreadEntry } from './unreadList.js'
import { parseSessionMessages, type SessionMessage } from './readSession.js'
import { runStageWithNavigation } from './navigate.js'
import { probeCurrentAccount, checkCancelled, type CurrentAccount } from '../platform/localdb/identity.js'
import { runLocaldbWatch, type LocaldbWatchDeps } from '../platform/localdb/watch.js'
import { readLocalState, writeLocalState } from '../platform/localdb/matrix.js'
import { logLocaldbEvent } from '../platform/localdb/log.js'

/** dist/src/operations → 包根 drivers/ps1 */
const UNREAD_DRIVER_PATH = fileURLToPath(new URL('../../../drivers/ps1/unread-list.ps1', import.meta.url))
const READ_SESSION_DRIVER_PATH = fileURLToPath(new URL('../../../drivers/ps1/read-session.ps1', import.meta.url))

/** 历史抓取按屏滚动 + OCR 较慢，单候选预算 600s */
const READ_SESSION_TIMEOUT_MS = 600_000

export interface WecomWatchPollArgs {
  /** 显式通道；缺省按 doctor 状态分派（unset→UI+hint；ui(compat)→UI；db/ui(transient)→本地库优先） */
  channel?: 'local-db' | 'ui'
  /** 关注会话名列表（冻结点 4）：未传=全部；空数组=INVALID_ARGUMENT；本地库逐名解析，失败进 failed；UI 通道忽略 */
  focus?: string[]
}

/** 本地库通道单轮事件（冻结点 4 形态；runLocaldbWatch data 的消费面） */
export interface LocaldbWatchEvent {
  channel: 'local-db'
  new_messages: Array<{
    conversation_id: string
    /** 显式 focus 绑定的会话附绑定时显示名（改名不影响跟踪） */
    display_name?: string
    messages: unknown[]
    has_more?: boolean
  }>
  last_sender: Record<string, 'self' | 'peer' | null>
  failed?: Array<{ focus: string; reason: string }>
}

export interface WatchEventNewMessages {
  type: 'new_messages'
  session: string
  unread_count: number
  messages: SessionMessage[]
}

export interface WatchEventTick {
  type: 'tick'
  unread_total: number
}

export type WatchEvent = WatchEventNewMessages | WatchEventTick

/**
 * 归一化（与 chat_ocr.py normalize_text / _common.ps1 ConvertTo-WeComNormalized 同规则）：
 * 去全部空白、全角 ASCII（U+FF01–FF5E）转半角、常见全角标点（。、～）转半角、小写折叠。
 */
export function normalizeChatText(s: string): string {
  let out = ''
  for (const ch of s) {
    const o = ch.codePointAt(0)!
    if (/\s/.test(ch)) continue
    if (o >= 0xff01 && o <= 0xff5e) {
      out += String.fromCodePoint(o - 0xfee0)
      continue
    }
    out += ch === '。' ? '.' : ch === '、' ? ',' : ch === '～' ? '~' : ch
  }
  return out.toLowerCase()
}

/** 最后一行非时间线消息的归一化文本（水位）；无消息返回 '' */
export function lastMessageNorm(messages: SessionMessage[]): string {
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i]!
    if (m.side !== 'timeline' && m.text.length > 0) return normalizeChatText(m.text)
  }
  return ''
}

/**
 * 增量提取：watermark 为空（新会话首轮）→ 当前屏全部非时间线消息；
 * 找到水位（取最后一次出现，容忍连续重复消息）→ 其后的非时间线消息；
 * 水位未找到（历史上滚/会话重建）→ 当前屏全部非时间线消息（保守全量，
 * 可能重复一轮；同内容不重发由 unread_count diff 兜底）。
 */
export function computeDelta(messages: SessionMessage[], watermark: string): SessionMessage[] {
  const chat = messages.filter((m) => m.side !== 'timeline')
  if (chat.length === 0) return []
  if (!watermark) return chat
  let idx = -1
  for (let i = chat.length - 1; i >= 0; i--) {
    if (normalizeChatText(chat[i]!.text) === watermark) {
      idx = i
      break
    }
  }
  return idx < 0 ? chat : chat.slice(idx + 1)
}

export function createWecomWatchPollOperation(
  deps: {
    runDriverFn?: RunPowerShellDriverFn
    statePathFn?: () => string | null
    artifactDirFn?: () => string | null
    nowFn?: () => Date
    loadStateFn?: (path: string) => WatchState
    saveStateFn?: (path: string, state: WatchState) => void
    /** M9 智能分发依赖（内部 chatSearch 签发 / chatSelect 验证 target_ref），测试可替换 */
    verifyRefFn?: VerifyTargetRefFn
    createRefFn?: CreateTargetRefFn
    /** P2 任务 3.2 本地库依赖（LocaldbReadDeps 超集，测试注入 mock） */
    localdb?: LocaldbWatchDeps
    /** 本地库执行器（默认 runLocaldbWatch；测试注入 mock） */
    watchDbFn?: typeof runLocaldbWatch
    probeAccountFn?: (ctx: OpContext) => Promise<CurrentAccount>
  } = {},
): WecomOperation<WecomWatchPollArgs> {
  const runDriverFn = deps.runDriverFn ?? runPowerShellDriver
  const statePathFn = deps.statePathFn ?? (() => watchStatePath())
  const artifactDirFn = deps.artifactDirFn ?? (() => artifactDir())
  const nowFn = deps.nowFn ?? (() => new Date())
  const loadStateFn = deps.loadStateFn ?? loadWatchState
  const saveStateFn = deps.saveStateFn ?? saveWatchState
  const verifyRefFn = deps.verifyRefFn ?? verifyTargetRef
  // 与 chatSearch 的默认签发适配保持一致（坐标随条目写入 payload，select 消费）
  const createRefFn: CreateTargetRefFn =
    deps.createRefFn ?? ((name, type, subtitle, coords) => createTargetRef(name, type, subtitle, { coords }))
  const local = deps.localdb ?? {}
  const getLocalState = local.readStateFn ?? (() => readLocalState(local.stateDir))
  return {
    name: 'wecom_watch_poll',
    execute(args: WecomWatchPollArgs, ctx: OpContext): Promise<OperationResult> {
      return runWecomOperation(
        'readonly',
        ctx,
        () => {
          if (args === null || typeof args !== 'object' || Array.isArray(args)) {
            return '参数必须是对象（可选字段 channel: local-db/ui；focus: string[]）'
          }
          if (args.channel !== undefined && args.channel !== 'local-db' && args.channel !== 'ui') {
            return 'channel 必须为 local-db/ui'
          }
          return validateFocus(args.focus)
        },
        async (opCtx) => {
          // —— UI 通道（既有链路，行为零改动；focus 忽略并注明）—— //
          const runUi = async (compatReason?: string, hint?: string): Promise<OperationOutcome> => {
            const statePath = statePathFn()
            if (!statePath) {
              throw new CodedOperationError('INTERNAL_ERROR', '%LOCALAPPDATA% 未设置，无法定位 watch-state.json')
            }
            const root = artifactDirFn()
            if (!root) {
              throw new CodedOperationError('INTERNAL_ERROR', '%LOCALAPPDATA% 未设置，无法定位 artifact 目录')
            }

            // 1) unread 快照
            opCtx.progress({ stage: 'execute', message: '读取未读会话快照' })
            const unreadData = await runDriverFn({ script: UNREAD_DRIVER_PATH, signal: opCtx.signal })
            const entries = parseUnreadEntries(unreadData)
            const unreadTotal = entries.reduce((sum, e) => sum + e.unread_count, 0)
            const snapshotNames = new Set(entries.map((e) => e.name))

            // 2) diff → 候选
            const state = loadStateFn(statePath)
            const candidates = entries.filter((e) => {
              const prev = state.sessions[e.name]
              return !prev || e.unread_count > prev.last_unread
            })

            // 3) 每候选读当前屏取增量（M9：read-session 驱动 + 共享智能分发编排，
            //    旧「直点行 UI_CHANGED 降级 search 重试」由驱动的 navigate_required 分发取代）
            const roundDir = join(root, `watch-${nowFn().toISOString().replace(/[:.]/g, '-')}`)
            mkdirSync(roundDir, { recursive: true })
            const events: WatchEvent[] = []
            let readOk = 0
            for (const [i, cand] of candidates.entries()) {
              if (opCtx.signal.aborted) throw new CancelledError()
              const candDir = join(roundDir, `cand-${i}`)
              mkdirSync(candDir, { recursive: true })
              opCtx.progress({ stage: 'execute', current: i + 1, total: candidates.length, message: `读取候选会话「${cand.name}」（未读 ${cand.unread_count}）` })
              // row 快路径只在首轮使用：分发（search+select）后的二次调用已在目标会话，
              // 不再点行（行坐标可能已陈旧，重复点击反有串会话风险）
              let stageCalls = 0
              const spawnReadStage = (dir: string): Promise<Record<string, unknown>> => {
                stageCalls++
                const driverArgs = ['-TargetName', cand.name, '-MaxPages', '1', '-ArtifactDir', dir]
                if (stageCalls === 1 && cand.x > 0 && cand.y > 0) {
                  driverArgs.push('-RowX', String(cand.x), '-RowY', String(cand.y))
                }
                return runDriverFn({
                  script: READ_SESSION_DRIVER_PATH,
                  args: driverArgs,
                  timeoutMs: READ_SESSION_TIMEOUT_MS,
                  signal: opCtx.signal,
                })
              }
              let data: Record<string, unknown>
              try {
                const outcome = await runStageWithNavigation({
                  runDriverFn,
                  verifyRefFn,
                  createRefFn,
                  opCtx,
                  root: candDir,
                  // unread 候选只有会话名（无 type/subtitle 消歧键）：按名字唯一匹配定位，
                  // 同名多条无法消歧 → TARGET_AMBIGUOUS（旧降级搜索链同名语义）
                  target: { name: cand.name, type: 'other', subtitle: '' },
                  artifactPrefix: 'read-session',
                  spawnStage: spawnReadStage,
                  readonly: true,
                  texts: {
                    writeDesc: '读取消息',
                    notSentNote: '未读取消息',
                    checkDesc: '读取前检查',
                    afterEnter: '执行消息读取',
                    refuseDesc: '已中止读取',
                  },
                })
                data = outcome.data
              } catch (err) {
                if (err instanceof CancelledError) throw err
                // 读取失败（含分发链 search/select 失败）：不推进水位，下轮重试不丢消息
                opCtx.progress({ stage: 'execute', message: `「${cand.name}」读取失败（不推进水位，下轮重试）：${err instanceof Error ? err.message : String(err)}` })
                continue
              }
              const messages = parseSessionMessages(data)
              const prev = state.sessions[cand.name]
              const watermark = prev?.last_seen_text_norm ?? ''
              const delta = computeDelta(messages, watermark)
              state.sessions[cand.name] = {
                last_seen_text_norm: lastMessageNorm(messages) || watermark,
                // 进会话已清角标（企微固有行为）：last_unread 必须归零而非记 cand.unread_count。
                // 记旧值会把「读取后来 1 条新消息」（角标 1 < 旧水位）压住漏报；
                // 角标异常未清时下一轮会重复读取，但文本水位去重保证不重发事件。
                last_unread: 0,
                updated_at: nowFn().toISOString(),
              }
              readOk++
              if (delta.length > 0) {
                events.push({ type: 'new_messages', session: cand.name, unread_count: cand.unread_count, messages: delta })
              }
            }

            // 4) 非候选项只推进 last_unread；快照消失的会话 last_unread 归零（防水位压住漏报）
            const candidateNames = new Set(candidates.map((c: UnreadEntry) => c.name))
            for (const e of entries) {
              if (candidateNames.has(e.name)) continue
              const prev = state.sessions[e.name]
              if (prev) prev.last_unread = e.unread_count
            }
            for (const name of Object.keys(state.sessions)) {
              if (!snapshotNames.has(name)) state.sessions[name]!.last_unread = 0
            }
            saveStateFn(statePath, state)

            if (events.length === 0) events.push({ type: 'tick', unread_total: unreadTotal })
            return {
              message: `轮询完成：未读会话 ${entries.length} 个（共 ${unreadTotal} 条），候选 ${candidates.length} 个，读取成功 ${readOk} 个，新消息事件 ${events.filter((e) => e.type === 'new_messages').length} 个`,
              data: {
                events,
                channel: 'ui' as const,
                unread_total: unreadTotal,
                sessions_with_unread: entries.length,
                candidates: candidates.length,
                read_ok: readOk,
                ...(args.focus !== undefined ? { focus_ignored: true } : {}),
                ...(compatReason ? { localdb_fallback_reason: compatReason } : {}),
                ...(hint ? { hint } : {}),
              },
            }
          }

          if (args.channel === 'ui') return runUi()

          // —— 通道分派（对齐 unreadList 范本：状态 unset/compat → UI + hint，不阻断）—— //
          const state = getLocalState()
          if (args.channel !== 'local-db' && (!state || state.mode === 'unset')) {
            return runUi(undefined, '运行 aid-wecom doctor --localdb 可启用本地库通道（增量含消息 ID 与精确方向，且不切会话清角标）')
          }
          if (args.channel !== 'local-db' && state?.mode === 'ui' && state.kind === 'compat') {
            return runUi('mode_ui_compat', '当前状态为 UI 兼容模式，可重跑 doctor --localdb 核对')
          }

          // —— 本地库通道（冻结点 4）：DB 不可用不落 UI，结构化 db_unavailable 失败 —— //
          const getAccount = async (): Promise<CurrentAccount> => {
            const account = await (deps.probeAccountFn ?? local.probeAccountFn ?? probeCurrentAccount)(opCtx)
            checkCancelled(opCtx.signal)
            return account
          }
          const current = await getAccount()
          try {
            return await (deps.watchDbFn ?? runLocaldbWatch)(
              {
                ...(args.focus !== undefined ? { focus: args.focus } : {}),
                forced: args.channel === 'local-db',
              },
              opCtx,
              current,
              local,
            )
          } catch (err) {
            // 取消/身份失败照常抛（IDENTITY_UNCONFIRMED 不吞，§2 分流表）；其余 DB 不可用
            // 一律结构化失败（水位保留、日志留痕），**绝不自动落 UI**——含强制 local-db
            if (err instanceof CancelledError || (err instanceof CodedOperationError && err.code === 'CANCELLED')) throw err
            checkCancelled(opCtx.signal)
            const code = err instanceof CodedOperationError ? err.code : 'LOCALDB_UNAVAILABLE'
            ;(local.logFn ?? logLocaldbEvent)({ type: 'db_fallback', stage: 'watch', reason: code })
            throw new CodedOperationError(code,
              `本地库 watch 轮失败（水位已保留，本轮不落 UI）：${err instanceof Error ? err.message : String(err)}`,
              'none',
              { channel: 'local-db', db_unavailable: true, ...(err instanceof CodedOperationError ? err.data : {}) })
          }
        },
      )
    },
  }
}
