/**
 * 「智能分发」共享编排（M7 从 messageSend.ts 无侵入抽取；wecom_message_send 与
 * wecom_send_image 共用，两 operation 的措辞差异全部经 texts 参数化——
 * messageSend 的既有测试对文案断言逐一兼容，切换共享实现不改任何行为）。
 * M9 起 wecom_read_session / wecom_watch_poll 的读取阶段驱动也共用本编排（readonly
 * 模式：阶段超时/取消不套写动作 unknown 语义，原样透传由上层按 readonly 归 effect=none）。
 *
 * 编排（原 messageSend.ts 内联实现，语义不变）：阶段驱动（发送文本/发送图片）返回
 * navigate_required=true（当前会话不是目标/无法判定）→ 内部编排：直接调用 chatSearch
 * operation 工厂（注入同一 runDriverFn，Jev 选 best）→ 取 best（须与目标 name+section+subtitle
 * 消歧键一致，否则按 items 里 name/subtitle/section 规则取唯一匹配项）→ 调用 chatSelect
 * operation（点击进会话并校验标题）→ 再次调用阶段驱动完成发送。两轮都不在目标会话 →
 * TARGET_NOT_FOUND；编排失败透传对应错误码（此时未发生任何发送，effect=none），不重试。
 *
 * 阶段驱动的超时/取消在本编排统一翻译为写动作语义：RESULT_TIMEOUT → EXECUTION_UNKNOWN
 * （写可能已落地，无法确认）；CancelledError → CANCELLED/effect=unknown；零自动重试。
 * readonly=true 的阶段（read-session/watch）无写副作用可处于 unknown：超时/取消原样
 * 透传（RESULT_TIMEOUT / CANCELLED，上层 readonly operation 归 effect=none）。
 */
import { mkdirSync } from 'node:fs'
import { join } from 'node:path'
import {
  CancelledError,
  CodedOperationError,
  type ErrorCode,
  type OpContext,
} from './types.js'
import type { RunPowerShellDriverFn } from '../platform/powershell.js'
import {
  type CreateTargetRefFn,
  type TargetRefIdentity,
  type VerifyTargetRefFn,
} from '../platform/targetRef.js'
import { createWecomChatSearchOperation } from './chatSearch.js'
import { createWecomChatSelectOperation } from './chatSelect.js'

/** 定位阶段选中条目（来自内部 chatSearch 结果的 best 或规则匹配唯一项） */
export interface LocatedTarget {
  name: string
  target_ref: string
}

/** 轻量名称归一化：去空白 + 剥 @微信 后缀 + 小写（TS 侧比对；驱动侧仍按全量归一化严格校验标题） */
export function normNameForMatch(s: string): string {
  return s.replace(/\s+/g, '').replace(/@微信$/, '').toLowerCase()
}

/** 目标分区 → 搜索结果分区（contact=联系人 / group=群聊 / other 等不限定） */
function sectionMatchesTarget(section: string, type: string): boolean {
  if (type === 'contact') return section === '联系人'
  if (type === 'group') return section === '群聊'
  return true
}

function identityMatches(item: { name: string; section: string }, target: TargetRefIdentity): boolean {
  return normNameForMatch(item.name) === normNameForMatch(target.name) && sectionMatchesTarget(item.section, target.type)
}

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

/**
 * 从 chatSearch 结果中选定位目标：先按 name+section（subtitle 优先收紧，落空放宽）算
 * 候选集；best（Jev/驱动规则选出）仅在身份相符**且落在候选集内**（即 subtitle 消歧键
 * 也一致）时才可信——Jev 看不到 target_ref 的 subtitle，同名同分区多条时它的 best
 * 无从消歧，直接信任会把消息发给错误的同名联系人（M2 的 subtitle 精确匹配语义）。
 * 无匹配 → null（TARGET_NOT_FOUND），多项 → 'ambiguous'（TARGET_AMBIGUOUS）。
 */
export function pickLocatedTarget(
  data: Record<string, unknown>,
  target: TargetRefIdentity,
): LocatedTarget | null | 'ambiguous' {
  const items = parseCandidates(data.items)
  const byNameSection = items.filter((it) => identityMatches(it, target))
  const withSubtitle =
    target.subtitle.length > 0 ? byNameSection.filter((it) => it.subtitle === target.subtitle) : byNameSection
  const candidates = withSubtitle.length > 0 ? withSubtitle : byNameSection
  const b = (data.best ?? null) as Record<string, unknown> | null
  if (
    b !== null &&
    typeof b === 'object' &&
    typeof b.target_ref === 'string' &&
    b.target_ref.length > 0 &&
    identityMatches({ name: String(b.name ?? ''), section: String(b.section ?? '') }, target) &&
    candidates.some((it) => it.target_ref === b.target_ref)
  ) {
    return { name: String(b.name ?? target.name), target_ref: b.target_ref }
  }
  if (candidates.length === 0) return null
  if (candidates.length > 1) return 'ambiguous'
  return { name: candidates[0]!.name, target_ref: candidates[0]!.target_ref }
}

export function sanitizeTiming(raw: unknown): Record<string, number> {
  const out: Record<string, number> = {}
  if (raw === null || typeof raw !== 'object') return out
  for (const [k, v] of Object.entries(raw as Record<string, unknown>)) {
    if (typeof v === 'number' && Number.isFinite(v)) out[k] = Math.round(v)
  }
  return out
}

/** 两 operation 的措辞差异（保持 messageSend 原文案逐字兼容） */
export interface StageNavigationTexts {
  /** 超时/取消文案中的写对象（messageSend「消息」/ sendImage「图片消息」） */
  writeDesc: string
  /** 定位失败/中止文案中的未发生说明（「未发送消息」/「未发送图片」） */
  notSentNote: string
  /** 发送前会话检查描述（「发送前检查」/「粘贴图片前检查」，用于两轮失败文案） */
  checkDesc: string
  /** 进入会话后的进度动作（「执行发送」/「执行图片发送」） */
  afterEnter: string
  /**
   * TARGET_AMBIGUOUS 拒绝动作文案：写动作缺省「已拒绝发送」；readonly 阶段
   * （read-session/watch）传「已中止读取」——只读链路触达该分支时说「已拒绝发送」
   * 与实际动作（读取）不符（M9 参数化，缺省值保持写路径文案逐字不变）。
   */
  refuseDesc?: string
}

export interface StageNavigationConfig {
  runDriverFn: RunPowerShellDriverFn
  verifyRefFn: VerifyTargetRefFn
  /** 内部 chatSearch 签发 target_ref 用（与 verifyRefFn 配套注入，测试可替换） */
  createRefFn: CreateTargetRefFn
  opCtx: OpContext
  /** artifact 根目录（已存在；阶段目录在本编排内创建） */
  root: string
  target: TargetRefIdentity
  /** 阶段驱动 artifact 目录前缀（send / send-image / read-session），时间戳冲突加序号后缀 */
  artifactPrefix: string
  /** 阶段驱动调用（不含超时/取消翻译——本编排统一包装为写动作 unknown 语义） */
  spawnStage: (dir: string) => Promise<Record<string, unknown>>
  /**
   * 阶段是否只读（M9 read-session / watch 共用）：true 时阶段超时/取消**原样透传**
   * （无写副作用可处于 unknown，上层 readonly operation 归 effect=none），不套用
   * 「写可能已发出」的 EXECUTION_UNKNOWN/unknown 翻译；缺省 false = 写动作语义。
   */
  readonly?: boolean
  texts: StageNavigationTexts
}

export interface StageNavigationOutcome {
  /** 阶段驱动终轮 data（navigate_required=false = 已在目标会话完成动作） */
  data: Record<string, unknown>
  /** 是否走了 search+select 分发路径 */
  navigated: boolean
}

/**
 * 智能分发编排主体：首次调用阶段驱动（当前会话对则直接完成）→ navigate_required 时
 * 内部 chatSearch + chatSelect → 二次调用阶段驱动。返回终轮 data 与 navigated。
 */
export async function runStageWithNavigation(cfg: StageNavigationConfig): Promise<StageNavigationOutcome> {
  // 各阶段 artifact 目录独立留痕（可两次，时间戳冲突加序号后缀）；
  // search-<ts> / select-<ts> 由内部 operation 自建，同一 artifact 根
  const usedDirs = new Set<string>()
  const makeArtifactDir = (): string => {
    const ts = new Date().toISOString().replace(/[:.]/g, '-')
    let dir = join(cfg.root, `${cfg.artifactPrefix}-${ts}`)
    for (let i = 2; usedDirs.has(dir); i++) dir = join(cfg.root, `${cfg.artifactPrefix}-${ts}-${i}`)
    usedDirs.add(dir)
    mkdirSync(dir, { recursive: true })
    return dir
  }

  const spawnStageTranslated = async (): Promise<Record<string, unknown>> => {
    try {
      return await cfg.spawnStage(makeArtifactDir())
    } catch (err) {
      // 只读阶段（read-session / watch）：无写副作用可处于 unknown——超时/取消原样透传，
      // 由上层 readonly operation 归 effect=none，不得套用「写可能已发出」的 unknown 文案
      if (cfg.readonly === true) throw err
      // 驱动超时/被取消都无法确认写是否落地：按写动作 unknown 语义上报，绝不自动重试
      if (err instanceof CodedOperationError && err.code === 'RESULT_TIMEOUT') {
        throw new CodedOperationError(
          'EXECUTION_UNKNOWN',
          `发送链路超时：${cfg.texts.writeDesc}可能已发出但无法确认（不会自动重试，请人工核对）`,
        )
      }
      if (err instanceof CancelledError) {
        throw new CodedOperationError(
          'CANCELLED',
          `发送链路被取消：${cfg.texts.writeDesc}可能已发出但无法确认（不会自动重试，请人工核对）`,
          'unknown',
        )
      }
      throw err
    }
  }

  // 第一次调用阶段驱动：当前会话对则直接完成发送；不对则返回 navigate_required
  let data = await spawnStageTranslated()
  let navigated = false
  if (data.navigate_required === true) {
    navigated = true
    const firstReason = typeof data.reason === 'string' ? data.reason : '当前会话不是目标'
    cfg.opCtx.progress({ stage: 'execute', message: `当前会话非目标（${firstReason}），自动 search + select 切换会话` })
    // 定位阶段：直接调用 operation 工厂（不经 CLI），注入同一 runDriverFn 与签发/验证依赖
    const query = cfg.target.name.replace(/@微信$/, '').trim()
    const searchType = cfg.target.type === 'contact' || cfg.target.type === 'group' ? cfg.target.type : 'any'
    const searchOp = createWecomChatSearchOperation({
      runDriverFn: cfg.runDriverFn,
      createRefFn: cfg.createRefFn,
      artifactDirFn: () => cfg.root,
    })
    const searchRes = await searchOp.execute({ query, type: searchType }, cfg.opCtx)
    if (!searchRes.success) {
      // 编排失败透传错误码；此时未发生任何发送，effect=none
      throw new CodedOperationError(
        searchRes.code as ErrorCode,
        `定位「${cfg.target.name}」失败（搜索阶段，${cfg.texts.notSentNote}）：${searchRes.message}`,
        'none',
        searchRes.data,
      )
    }
    const picked = pickLocatedTarget(searchRes.data, cfg.target)
    if (picked === null) {
      throw new CodedOperationError(
        'TARGET_NOT_FOUND',
        `搜索「${query}」结果中没有与「${cfg.target.name}」匹配的候选（${cfg.texts.notSentNote}）`,
        'none',
        searchRes.data,
      )
    }
    if (picked === 'ambiguous') {
      throw new CodedOperationError(
        'TARGET_AMBIGUOUS',
        `搜索「${query}」有多个与「${cfg.target.name}」匹配的候选且无法消歧，${cfg.texts.refuseDesc ?? '已拒绝发送'}（${cfg.texts.notSentNote}）`,
        'none',
        searchRes.data,
      )
    }
    const selectOp = createWecomChatSelectOperation({
      runDriverFn: cfg.runDriverFn,
      verifyRefFn: cfg.verifyRefFn,
      artifactDirFn: () => cfg.root,
    })
    const selectRes = await selectOp.execute({ target_ref: picked.target_ref }, cfg.opCtx)
    if (!selectRes.success) {
      throw new CodedOperationError(
        selectRes.code as ErrorCode,
        `定位「${cfg.target.name}」失败（进入会话阶段，${cfg.texts.notSentNote}）：${selectRes.message}`,
        'none',
        selectRes.data,
      )
    }
    // 此时已在目标会话：第二次调用阶段驱动
    cfg.opCtx.progress({ stage: 'execute', message: `已进入「${cfg.target.name}」会话，${cfg.texts.afterEnter}` })
    data = await spawnStageTranslated()
    if (data.navigate_required === true) {
      const secondReason = typeof data.reason === 'string' ? data.reason : ''
      throw new CodedOperationError(
        'TARGET_NOT_FOUND',
        `两轮${cfg.texts.checkDesc}均判定当前会话不是目标「${cfg.target.name}」（第一轮：${firstReason}；search+select 进入后第二轮仍不符${secondReason ? `：${secondReason}` : ''}），已中止且${cfg.texts.notSentNote}`,
        'none',
      )
    }
  }
  return { data, navigated }
}
