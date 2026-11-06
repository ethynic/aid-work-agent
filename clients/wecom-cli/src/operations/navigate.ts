/** Shared target-name resolution and selector checks for current local-database paths. */
import {
  CodedOperationError,
  type ErrorCode,
  type OpContext,
} from './types.js'
import type { RunPowerShellDriverFn } from '../platform/powershell.js'
import {
  type CreateTargetRefFn,
} from '../platform/targetRef.js'
import { createWecomChatSearchOperation } from './chatSearch.js'
import type { DirectoryArgs } from './sessionDirectory.js'
import type { OperationOutcome } from './context.js'

type ReadCatalogFn = (args: DirectoryArgs, ctx: OpContext) => Promise<OperationOutcome>

/** chatSearch 返回的候选（TS 防御性解析形态） */
interface SearchCandidate {
  name: string
  subtitle: string
  section: string
  target_ref: string
}

function parseCandidates(raw: unknown): SearchCandidate[] {
  const arr = Array.isArray(raw) ? raw : raw != null ? [raw] : []
  const out: SearchCandidate[] = []
  for (const r of arr) {
    const it = (r ?? {}) as Record<string, unknown>
    if (typeof it.target_ref !== 'string' || it.target_ref.length === 0) continue
    out.push({
      name: String(it.name ?? ''),
      subtitle: String(it.subtitle ?? ''),
      section: String(it.section ?? ''),
      target_ref: it.target_ref,
    })
  }
  return out
}

/** M11a 直达模式 target_name 长度上限（与 chatSearch 的 query 上限一致） */
const MAX_TARGET_NAME_LENGTH = 100

/**
 * M11a：target_ref / target_name 二选一校验（send / send-image / send-file /
 * read-session 四命令共用）。两个都传或都不传 → 错误文案（INVALID_ARGUMENT）；
 * 只传其一且合法 → null。
 */
export function validateTargetSelector(args: { target_ref?: unknown; target_name?: unknown }): string | null {
  if (args.target_ref !== undefined && args.target_name !== undefined) {
    return 'target_ref 与 target_name 互斥，只能传其一（ref 模式用 target_ref，直达模式用 target_name）'
  }
  if (args.target_ref !== undefined) {
    if (typeof args.target_ref !== 'string' || args.target_ref.length === 0) {
      return 'target_ref 必填且必须是非空字符串（先经 wecom_chat_search 获取）'
    }
    return null
  }
  if (args.target_name === undefined) {
    return 'target_ref 与 target_name 必须传其一（target_ref 来自 wecom_chat_search；target_name 为直达模式，内部自动搜索定位）'
  }
  if (typeof args.target_name !== 'string' || args.target_name.trim().length === 0) {
    return 'target_name 必须是非空字符串（直达模式：内部自动搜索定位，可含 @微信 后缀）'
  }
  if (args.target_name.length > MAX_TARGET_NAME_LENGTH) {
    return `target_name 长度不能超过 ${MAX_TARGET_NAME_LENGTH} 字`
  }
  return null
}

/** M11a 直达模式定位结果：resolved_target 透传用（name/subtitle/section 来自命中条目） */
export interface ResolvedByNameTarget {
  name: string
  subtitle: string
  section: string
  target_ref: string
}

/** 直达模式定位失败文案（与各 operation 的分发文案保持同款：notSentNote / refuseDesc） */
export interface ResolveByNameTexts {
  notSentNote: string
  refuseDesc?: string
}

/**
 * M11a target-name 直达模式身份定位：内部调 chatSearch operation（注入依赖同 send
 * 编排）→ 从结果挑唯一目标 → 返回其 target_ref 与身份三元组，调用方用 ref 走既有链路
 * （后续零新逻辑）。
 *
 * 本地库 search 只在唯一命中时签发 ref；无命中或歧义直接透传错误码，
 * 此时没有 UI 或发送副作用。
 */
export async function resolveTargetByName(
  cfg: {
    runDriverFn: RunPowerShellDriverFn
    createRefFn: CreateTargetRefFn
    opCtx: OpContext
    root: string
    readCatalogFn?: ReadCatalogFn
  },
  targetName: string,
  subtitle: string,
  texts: ResolveByNameTexts,
): Promise<ResolvedByNameTarget> {
  const query = targetName.replace(/@微信$/, '').trim()
  cfg.opCtx.progress({ stage: 'execute', message: `按名称「${targetName}」直达：内部搜索定位目标` })
  const searchOp = createWecomChatSearchOperation({
    createRefFn: cfg.createRefFn,
    readCatalogFn: cfg.readCatalogFn,
  })
  const searchRes = await searchOp.execute({ query, type: 'any' }, cfg.opCtx)
  if (!searchRes.success) {
    // search 本身失败（面板未出现/驱动错等）：透传错误码，未发生任何写动作
    throw new CodedOperationError(
      searchRes.code as ErrorCode,
      `按名称定位「${targetName}」失败（搜索阶段，${searchRes.code === 'TARGET_AMBIGUOUS' ? (texts.refuseDesc ?? '已拒绝发送') + '，' : ''}${texts.notSentNote}）：${searchRes.message}`,
      'none',
      searchRes.data,
    )
  }
  // Search returns a single identity bound to this account and full target name.
  const unique = searchRes.data.best as { name?: string; target_ref?: string } | undefined
  const picked = unique?.name && unique.target_ref
    ? { name: unique.name, target_ref: unique.target_ref } : null
  if (picked === null) {
    throw new CodedOperationError(
      'TARGET_NOT_FOUND',
      `按名称「${targetName}」搜索结果中没有匹配候选（${texts.notSentNote}）`,
      'none',
      searchRes.data,
    )
  }
  // subtitle/section 按 target_ref 回查命中条目（best 也派生自 items，必命中；防御性兜底空串）
  const hit = parseCandidates(searchRes.data.items).find((it) => it.target_ref === picked.target_ref)
  return {
    name: picked.name,
    subtitle: hit?.subtitle ?? '',
    section: hit?.section ?? '',
    target_ref: picked.target_ref,
  }
}

export function sanitizeTiming(raw: unknown): Record<string, number> {
  const out: Record<string, number> = {}
  if (raw === null || typeof raw !== 'object') return out
  for (const [k, v] of Object.entries(raw as Record<string, unknown>)) {
    if (typeof v === 'number' && Number.isFinite(v)) out[k] = Math.round(v)
  }
  return out
}
