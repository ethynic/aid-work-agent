/**
 * wecom_unread_list operation（M3 UI 通道；P2 任务 3.1 增本地库通道，对齐 readSession 范本）：
 * 读取会话快照（只读，effect=none）。
 *
 * - UI 通道（channel='ui' / 缺省按 doctor 状态分派）：PrintWindow 截主窗口（窗口被遮挡/非前台
 *   也能出图）→ drivers/py/chat_ocr.py unread 模式（红色角标像素检测 + 裁切放大 OCR 读数）
 *   → 聚合 {name, preview, unread_count, x, y}（旧字段原样保留）；不打开会话、不清角标。
 * - 本地库通道（db / ui(transient) 运行时探测 / 强制 local-db）：platform/localdb/unread.ts
 *   待回复快照（judgment_basis='last_sender'，覆盖"已读未回复"）；unread 是快照语义，允许
 *   兜底 UI——白名单同 readSession（VERSION_UNSUPPORTED/KEY_SCAN_FAILED/SNAPSHOT_UNSTABLE/
 *   LOCALDB_UNAVAILABLE），取消/身份（IDENTITY_UNCONFIRMED）/focus 解析类失败不兜底；兜底附
 *   原因 + capability_difference（角标无法覆盖已读未回复）+ judgment_basis='badge'。
 * - focus（冻结点 4）：字符串数组；未传 = 全部；空数组 = INVALID_ARGUMENT；本地库逐名 resolve
 *   失败进 failed 部分返回；UI 通道忽略 focus 并注明（对齐冻结点 4「UI 模式忽略并提示」）。
 * - name（第八轮评审 P2）：两通道同语义——会话显示名子串包含过滤；无显示名的会话在指定
 *   name 时因无法匹配而排除（本地库仅 focus 命中的会话携带显示名）。
 */
import { fileURLToPath } from 'node:url'
import { runWecomOperation, type OperationOutcome } from './context.js'
import { CancelledError, CodedOperationError, type OperationResult, type OpContext, WecomOperation } from './types.js'
import { runPowerShellDriver, type RunPowerShellDriverFn } from '../platform/powershell.js'
import { probeCurrentAccount, checkCancelled, type CurrentAccount } from '../platform/localdb/identity.js'
import { readLocaldbUnread } from '../platform/localdb/unread.js'
import { LOCALDB_FALLBACK_CODES, type LocaldbReadDeps } from '../platform/localdb/readSession.js'
import { readLocalState, writeLocalState } from '../platform/localdb/matrix.js'
import { logLocaldbEvent } from '../platform/localdb/log.js'

/** dist/src/operations → 包根 drivers/ps1 */
const DRIVER_PATH = fileURLToPath(new URL('../../../drivers/ps1/unread-list.ps1', import.meta.url))

export interface WecomUnreadListArgs {
  name?: string
  /** 显式通道；缺省按 doctor 状态分派（unset→UI+hint；ui(compat)→UI；db/ui(transient)→本地库优先） */
  channel?: 'local-db' | 'ui'
  /** 关注会话名列表（冻结点 4）：未传=全部；空数组=INVALID_ARGUMENT；本地库逐名解析，失败进 failed */
  focus?: string[]
}

export interface UnreadEntry {
  name: string
  preview: string
  unread_count: number
  /** 名称行中心（图像坐标系，原点 = 主窗口左上角）；watch 直点会话行用 */
  x: number
  y: number
}

/** 驱动 data.unread 防御性归一（PS 5.1 ConvertTo-Json 单元素数组解包怪癖） */
export function parseUnreadEntries(data: Record<string, unknown>): UnreadEntry[] {
  const raw = Array.isArray(data.unread) ? data.unread : data.unread != null ? [data.unread] : []
  return raw
    .map((it) => ({
      name: String((it as { name?: unknown })?.name ?? ''),
      preview: String((it as { preview?: unknown })?.preview ?? ''),
      unread_count: Number((it as { unread_count?: unknown })?.unread_count ?? 0),
      x: Number((it as { x?: unknown })?.x ?? -1),
      y: Number((it as { y?: unknown })?.y ?? -1),
    }))
    .filter((e) => e.name.length > 0 && e.unread_count > 0)
}

/** focus 入参校验（冻结点 4：字符串数组；空数组 = INVALID_ARGUMENT；项须非空字符串） */
export function validateFocus(focus: unknown): string | null {
  if (focus === undefined) return null
  if (!Array.isArray(focus)) return 'focus 必须是字符串数组'
  if (focus.length === 0) return 'focus 不能为空数组（未传 = 全部会话；如无关注目标请不要传 focus）'
  for (const item of focus) {
    if (typeof item !== 'string' || item.trim().length === 0) return 'focus 每项必须是非空字符串（会话名）'
  }
  return null
}

export function createWecomUnreadListOperation(
  deps: {
    runDriverFn?: RunPowerShellDriverFn
    localdb?: LocaldbReadDeps
    /** 本地库执行器（默认 readLocaldbUnread；测试注入 mock） */
    readDbFn?: typeof readLocaldbUnread
    probeAccountFn?: (ctx: OpContext) => Promise<CurrentAccount>
  } = {},
): WecomOperation<WecomUnreadListArgs> {
  const runDriverFn = deps.runDriverFn ?? runPowerShellDriver
  const local = deps.localdb ?? {}
  const getState = local.readStateFn ?? (() => readLocalState(local.stateDir))
  return {
    name: 'wecom_unread_list',
    execute(args: WecomUnreadListArgs, ctx: OpContext): Promise<OperationResult> {
      return runWecomOperation(
        'readonly',
        ctx,
        () => {
          if (args === null || typeof args !== 'object' || Array.isArray(args)) {
            return '参数必须是对象（当前仅支持可选字段 name: string；channel: local-db/ui；focus: string[]）'
          }
          if (args.name !== undefined && typeof args.name !== 'string') {
            return 'name 必须是字符串'
          }
          if (args.channel !== undefined && args.channel !== 'local-db' && args.channel !== 'ui') {
            return 'channel 必须为 local-db/ui'
          }
          return validateFocus(args.focus)
        },
        async (opCtx) => {
          // —— UI 通道（既有链路）：快照 + judgment_basis='badge' + 能力差异说明 —— //
          const runUi = async (fallbackReason?: string, hint?: string): Promise<OperationOutcome> => {
            checkCancelled(opCtx.signal)
            opCtx.progress({ stage: 'execute', message: '截取主窗口并 OCR 会话列表' })
            const data = await runDriverFn({ script: DRIVER_PATH, signal: opCtx.signal })
            let unread = parseUnreadEntries(data)
            const nameFilter = args.name?.trim()
            if (nameFilter) unread = unread.filter((u) => u.name.includes(nameFilter))
            const message =
              (nameFilter ? `未读会话（匹配「${nameFilter}」）${unread.length} 个` : `未读会话 ${unread.length} 个`) +
              (args.focus !== undefined ? '；UI 通道不支持 focus 解析，已忽略' : '') +
              (fallbackReason ? `（本地库不可用，已走 UI 兜底：${fallbackReason}）` : '') +
              (hint ? `；${hint}` : '')
            return {
              message,
              data: {
                unread,
                channel: 'ui' as const,
                judgment_basis: 'badge' as const,
                capability_difference: 'UI 基于角标快照，无法覆盖已读未回复',
                ...(args.focus !== undefined ? { focus_ignored: true } : {}),
                ...(fallbackReason ? { localdb_fallback_reason: fallbackReason, fallback_reason: fallbackReason } : {}),
                ...(hint ? { hint } : {}),
              },
            }
          }

          if (args.channel === 'ui') return runUi()

          // —— 通道分派（对齐 readSession 范本：状态 unset/compat → UI，不阻断）—— //
          const state = getState()
          if (args.channel !== 'local-db' && (!state || state.mode === 'unset')) {
            return runUi(undefined, '运行 aid-wecom doctor --localdb 可启用本地库通道（待回复判定，覆盖已读未回复）')
          }
          if (args.channel !== 'local-db' && state?.mode === 'ui' && state.kind === 'compat') {
            return runUi('mode_ui_compat', '当前状态为 UI 兼容模式，可重跑 doctor --localdb 核对')
          }

          const getAccount = async (): Promise<CurrentAccount> => {
            try {
              const account = await (deps.probeAccountFn ?? local.probeAccountFn ?? probeCurrentAccount)(opCtx)
              checkCancelled(opCtx.signal)
              return account
            } catch (err) {
              // 已观察到登录/身份变化，旧扫描失败上下文不再有效；不改原模式（同 readSession）。
              if (state?.scan_failure && err instanceof CodedOperationError &&
                  ['NOT_LOGGED_IN', 'WECOM_NOT_FOUND', 'IDENTITY_UNCONFIRMED'].includes(err.code)) {
                const latest = getState()
                if (latest) (local.writeStateFn ?? (value => writeLocalState(value, local.stateDir)))({ ...latest, scan_failure: undefined })
              }
              throw err
            }
          }
          const current = await getAccount()
          try {
            return await (deps.readDbFn ?? readLocaldbUnread)(
              {
                // name 透传本地库执行器（第八轮评审 P2：与 UI 通道同语义过滤，不在 DB
                // 分支静默忽略）
                ...(args.name !== undefined ? { name: args.name } : {}),
                ...(args.focus !== undefined ? { focus: args.focus } : {}),
                forced: args.channel === 'local-db',
              },
              opCtx,
              current,
              local,
            )
          } catch (err) {
            if (err instanceof CancelledError || (err instanceof CodedOperationError && err.code === 'CANCELLED')) throw err
            checkCancelled(opCtx.signal)
            // 白名单外（取消/身份/focus 解析类/目标类失败）不兜底；强制 local-db 保留具体错误
            if (!(err instanceof CodedOperationError) || !LOCALDB_FALLBACK_CODES.has(err.code) || args.channel === 'local-db') throw err
            ;(local.logFn ?? logLocaldbEvent)({ type: 'db_fallback', stage: 'unread', reason: err.code })
            return runUi(err.code, '可运行 aid-wecom doctor --localdb 核对本地库状态')
          }
        },
      )
    },
  }
}
