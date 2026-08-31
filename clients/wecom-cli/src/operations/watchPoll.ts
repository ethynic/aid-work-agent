/**
 * wecom_watch_poll operation（M3）：新消息跟踪单轮（无会话归档时的核心能力）。
 *
 * 单轮流程（TS 编排，组合 unread-list / history-read 两个驱动）：
 *   1) unread_list 快照（不开会话不清角标）；
 *   2) 与 watch-state.json 水位 diff：unread_count 增大或新出现 → 候选
 *      （读取成功后 last_unread 归零：进会话已清角标，之后任何角标都是新增；
 *      会话从快照消失时 last_unread 归零，否则「读完再来 1 条」会被旧水位压住漏报）；
 *   3) 每候选：直点会话列表行（unread OCR 给的行坐标；标题校验不一致 UI_CHANGED 时
 *      降级搜索定位重试一次）→ 读当前屏消息 → 与 last_seen_text_norm 比对取增量 →
 *      推进水位。读取失败的候选不推进水位（下轮仍候选，不丢消息）；
 *   4) 产出 NDJSON 事件（data.events）：{type:"new_messages",session,unread_count,messages}
 *      或（本轮无任何新消息时）{type:"tick",unread_total}。
 *
 * 副作用注意：读取候选会话会清除其未读角标（企微客户端固有行为），并更新本机
 * watch-state.json 水位文件；无对外写副作用（effect=none）。长循环不走本 operation，
 * 由 CLI watch 动词循环调用（MCP 每轮一次 tool call）。
 */
import { mkdirSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { runWecomOperation } from './context.js'
import { CancelledError, CodedOperationError, type OperationResult, type OpContext, type WecomOperation } from './types.js'
import { runPowerShellDriver, type RunPowerShellDriverFn } from '../platform/powershell.js'
import { artifactDir } from '../platform/environment.js'
import { loadWatchState, saveWatchState, watchStatePath, type WatchState } from '../platform/watchState.js'
import { parseUnreadEntries, type UnreadEntry } from './unreadList.js'
import { parseHistoryMessages, type HistoryMessage } from './historyRead.js'

/** dist/src/operations → 包根 drivers/ps1 */
const UNREAD_DRIVER_PATH = fileURLToPath(new URL('../../../drivers/ps1/unread-list.ps1', import.meta.url))
const HISTORY_DRIVER_PATH = fileURLToPath(new URL('../../../drivers/ps1/history-read.ps1', import.meta.url))

/** 历史抓取按屏滚动 + OCR 较慢，单候选预算 600s */
const HISTORY_TIMEOUT_MS = 600_000

export interface WecomWatchPollArgs {
  // 当前无入参（占位对象，保持 operation 契约一致）
}

export interface WatchEventNewMessages {
  type: 'new_messages'
  session: string
  unread_count: number
  messages: HistoryMessage[]
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
export function lastMessageNorm(messages: HistoryMessage[]): string {
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
export function computeDelta(messages: HistoryMessage[], watermark: string): HistoryMessage[] {
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
  } = {},
): WecomOperation<WecomWatchPollArgs> {
  const runDriverFn = deps.runDriverFn ?? runPowerShellDriver
  const statePathFn = deps.statePathFn ?? (() => watchStatePath())
  const artifactDirFn = deps.artifactDirFn ?? (() => artifactDir())
  const nowFn = deps.nowFn ?? (() => new Date())
  const loadStateFn = deps.loadStateFn ?? loadWatchState
  const saveStateFn = deps.saveStateFn ?? saveWatchState
  return {
    name: 'wecom_watch_poll',
    execute(args: WecomWatchPollArgs, ctx: OpContext): Promise<OperationResult> {
      return runWecomOperation(
        'readonly',
        ctx,
        () => {
          if (args === null || typeof args !== 'object' || Array.isArray(args)) {
            return '参数必须是对象（当前无入参，传 {} 即可）'
          }
          return null
        },
        async (opCtx) => {
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

          // 3) 每候选读当前屏取增量
          const roundDir = join(root, `watch-${nowFn().toISOString().replace(/[:.]/g, '-')}`)
          mkdirSync(roundDir, { recursive: true })
          const events: WatchEvent[] = []
          let readOk = 0
          for (const [i, cand] of candidates.entries()) {
            if (opCtx.signal.aborted) throw new CancelledError()
            const candDir = join(roundDir, `cand-${i}`)
            mkdirSync(candDir, { recursive: true })
            opCtx.progress({ stage: 'execute', current: i + 1, total: candidates.length, message: `读取候选会话「${cand.name}」（未读 ${cand.unread_count}）` })
            let data: Record<string, unknown>
            try {
              data = await runDriverFn({
                script: HISTORY_DRIVER_PATH,
                args: [
                  '-TargetName', cand.name,
                  '-MaxPages', '1',
                  '-OpenMode', 'row',
                  '-RowX', String(cand.x),
                  '-RowY', String(cand.y),
                  '-ArtifactDir', candDir,
                ],
                timeoutMs: HISTORY_TIMEOUT_MS,
                signal: opCtx.signal,
              })
            } catch (err) {
              if (err instanceof CancelledError) throw err
              // 直点行标题校验不一致（列表已滚动/坐标漂移）→ 降级搜索定位重试一次
              if (err instanceof CodedOperationError && err.code === 'UI_CHANGED') {
                opCtx.progress({ stage: 'execute', message: `「${cand.name}」直点会话行未命中，降级搜索定位重试` })
                try {
                  data = await runDriverFn({
                    script: HISTORY_DRIVER_PATH,
                    args: [
                      '-TargetName', cand.name,
                      '-MaxPages', '1',
                      '-OpenMode', 'search',
                      '-ArtifactDir', candDir,
                    ],
                    timeoutMs: HISTORY_TIMEOUT_MS,
                    signal: opCtx.signal,
                  })
                } catch (err2) {
                  if (err2 instanceof CancelledError) throw err2
                  opCtx.progress({ stage: 'execute', message: `「${cand.name}」读取失败（不推进水位，下轮重试）：${err2 instanceof Error ? err2.message : String(err2)}` })
                  continue
                }
              } else {
                opCtx.progress({ stage: 'execute', message: `「${cand.name}」读取失败（不推进水位，下轮重试）：${err instanceof Error ? err.message : String(err)}` })
                continue
              }
            }
            const messages = parseHistoryMessages(data)
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
              unread_total: unreadTotal,
              sessions_with_unread: entries.length,
              candidates: candidates.length,
              read_ok: readOk,
            },
          }
        },
      )
    },
  }
}
