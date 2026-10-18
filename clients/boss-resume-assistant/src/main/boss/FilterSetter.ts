/**
 * 筛选面板设置器（设计文档 §10.3：筛选类控件被风控拦截 CDP 合成点击，全部走 Win32 通道）。
 *
 * 流程：定位「筛选」按钮开面板 → 行锚定定位选项逐个点击 → 「确定」→ 徽章计数校验。
 *
 * 行锚定消歧（关键）：选项文本（如「本科」）在候选人卡片中大量重名（真机 41 处命中），
 * 必须先定位行标签（「学历要求」），再在同一 y 行带、标签右侧的命中里找唯一选项。
 *
 * 所有无法唯一定位 / 校验失败的情况 fail-loud 抛 FilterSetError，绝不盲点。
 */
import {
  type DomSnapshot,
  type ClickPoint,
  findNodesByString,
  accumulateOwnerOffset,
  boundsCenter,
  lowestCommonAncestor,
  isDescendantOf,
  parentMapOf,
  indexedValues,
  classOf,
} from './domSnapshot.js'
import { CancelledError, CodedOperationError } from '../operations/types.js'
import { effectiveMembership, filterGroup, inspectMembership, membershipAccountKey, type MembershipHint, type MembershipObservation } from './MembershipInspector.js'
import { closeFilterPaywall, inspectFilterPaywall } from './FilterPaywall.js'

export class FilterSetError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'FilterSetError'
  }
}

/** 只表示输入正常返回后，真实筛选按钮的最终计数明确不符；直接 I/O 错误不属于此类。 */
export class FilterPostconditionError extends FilterSetError {
  constructor(message: string) {
    super(message)
    this.name = 'FilterPostconditionError'
  }
}

/**
 * 面板滚动适配失败（目标在弹窗可视区外且无滚动能力 / 滚 3 次仍不可见）：
 * 属页面状态问题而非档位缺失，不可由 apply 的保底档位映射恢复——直接 fail-loud。
 */
export class PanelScrollError extends FilterSetError {
  constructor(message: string) {
    super(message)
    this.name = 'PanelScrollError'
  }
}

export interface FilterSpec {
  /** 经验要求行选项，如 '5-10年' */
  experience?: string
  /** 学历要求行选项（多选），如 ['本科','硕士','博士']；「本科及以上」语义的展开由调用方负责 */
  educations?: string[]
  /** 薪资待遇行选项（单选），如 '10-20K' */
  salary?: string
  /** 页面实际行标签与精确选项；支持职位库未配置的当轮条件。 */
  filters?: Array<{ label: string; options: string[] }>
  age?: { min?: number; max?: number | null }
  membership_hint?: MembershipHint
  membership_account_ref?: string
  first_degree_only?: boolean
}

export interface FilterSetterDeps {
  /** 采集 fresh DOMSnapshot（页面动态变化，每次点击前重新采集，禁止复用旧坐标） */
  snapshot(): Promise<DomSnapshot>
  /** Win32 真实鼠标点击（viewport 为页面截图尺寸，device px） */
  click(point: ClickPoint, viewport: { width: number; height: number }): Promise<void>
  /** 协作式取消信号：入口检查一次，触发即抛 CancelledError */
  signal?: AbortSignal
  /** 可注入 sleep（测试） */
  sleep?(ms: number): Promise<void>
  /** CDP 浏览类滚动（VIP 面板内滚动，不占真实鼠标，真实接线由 operation 传 session.mouseWheel）。
   *  目标本就在可视区内时零滚动不触发；目标越界且未注入 → fail-loud 拒绝盲点（保护旧调用方） */
  mouseWheel?(x: number, y: number, deltaY: number): Promise<void>
  drag?(from: ClickPoint, to: ClickPoint, viewport: { width: number; height: number }): Promise<void>
  /** 在首次改变筛选前通知operation，后续失败不得误报effect=none并自动重放。 */
  onMutation?(): void
  /** 阶段计时埋点（panel:open / panel:clear / panel:option / panel:confirm / panel:verify /
   *  probe:describe，只记名称与毫秒，不记页面内容）；缺省恒等透传 */
  step?<T>(name: string, fn: () => Promise<T>): Promise<T>
}

interface VisibleHit {
  documentIndex: number
  nodeIndex: number
  bounds: [number, number, number, number]
  text: string
}

/** 探针输出：一个选项的文本与全局坐标 */
export interface PanelOptionInfo {
  text: string
  point: ClickPoint
}

/** 探针输出：一行筛选条件（行标签 + 全部可见选项） */
export interface PanelRowInfo {
  label: string
  options: PanelOptionInfo[]
  /** 单选标记来自页面；未标记的通用行保守只允许一个选项。 */
  selection?: 'single' | 'multiple' | 'unknown'
  control?: 'options' | 'range'
  range?: { min: number; max: number; unlimited: boolean }
  group?: 'ordinary' | 'vip'
}

export interface UnreadablePanelRow {
  label: string
  reason: string
}

/** 与真实取证 fixture 相同顺序，不引入 Runtime/evaluate。 */
export const FILTER_SNAPSHOT_OPTIONS = {
  includeDOMRects: true,
  computedStyles: ['overflow-x', 'overflow-y', 'display', 'position', 'height', 'visibility', 'opacity'],
}

interface StructuralRow {
  label: VisibleHit
  root: number
  options: VisibleHit[]
}

export interface SkippedFilter {
  label: string
  options?: string[]
  range?: { min?: number; max?: number | null }
  reason: 'non_vip' | 'paywall'
}

export interface FilterApplyResult {
  filterCount: number
  substitutions: Array<{ row: string; requested: string; matched: string }>
  appliedRows: Array<{ label: string; options: string[] }>
  age?: { min: number; max: number | null }
  firstDegreeOnly: boolean
  membership: MembershipObservation
  skippedFilters: SkippedFilter[]
  applied: boolean
}

export interface FilterPostcondition {
  status: 'met' | 'unmet' | 'inconclusive'
  expectedCount: number | null
  observedCount: number | null
  panelOpen: boolean
  /** 已完成面板选值、年龄与第一学历校验的原结果；仅 met 时可作为成功结果消费。 */
  result?: FilterApplyResult
}

class VipUnavailable extends Error {
  constructor(readonly membership: MembershipObservation) { super('当前筛选VIP权益不可用') }
}

/** 行配置：条件字段 → 行标签匹配（前缀匹配，兼容「薪资待遇[单选]」等后缀变体） */
const ROW_DEFS: Array<{ key: 'experience' | 'educations' | 'salary'; labelPrefix: string; multi: boolean }> = [
  { key: 'experience', labelPrefix: '经验要求', multi: true },
  { key: 'educations', labelPrefix: '学历要求', multi: true },
  { key: 'salary', labelPrefix: '薪资待遇', multi: false },
]

/** 推荐牛人列表页特征：「筛选」/「筛选·N」按钮（与 bossGreet/JobSwitcher 同源判定；operation 前置校验复用） */
export const FILTER_BUTTON_PATTERN = /^筛选(·\d+)?$/
const FILTER_BADGE_PATTERN = /^筛选·(\d+)$/

/** 选项文本归一化（精确匹配前）：去空白 + 去 K/k 单位字母 + 大写（兼容「15k-20k」≡「15-20K」≡「15-20k」） */
export function normalizeOptionText(s: string): string {
  return s.replace(/[\sKk]/g, '').toUpperCase()
}

/** 解析「数值区间」档位：5-10年 / 15-30K / 15k-30k → {min,max}；「5年以上」→ {min, max:∞哨兵}；非数值（本科/应届生）→ null */
function parseNumericRange(text: string): { min: number; max: number } | null {
  const t = text.replace(/\s+/g, '').replace(/[Kk]/g, '')
  let m = /^(\d+(?:\.\d+)?)[-~](\d+(?:\.\d+)?)/.exec(t)
  if (m) return { min: Number(m[1]), max: Number(m[2]) }
  m = /^(\d+(?:\.\d+)?)年?以上/.exec(t) // 「5年以上」：数字与「以上」之间带「年」
  if (m) return { min: Number(m[1]), max: 99999 }
  m = /^(\d+(?:\.\d+)?)(?:年|岁)?(?:以下|以内)$/.exec(t)
  if (m) return { min: 0, max: Number(m[1]) }
  return null
}

/**
 * 保底档位映射（2026-08-17 用户定调：**必须映射到页面上真实存在的选项**，不能让 LLM 瞎编）：
 * LLM 未先查 boss_filter_options 时可能传页面不存在的档位（如 15-30K，页面只有 10-20K/20-50K）。
 * 只在双方都能解析出数值区间时生效（经验/薪资；学历等非数值仍报错并列出可选值）。
 * 规则 = 保下限：薪资/经验下限是常见硬约束——在「下限 ≥ 用户下限」的档位里取下限最近者
 * （并列取重叠更大者）；全部低于用户下限时取重叠最大者（并列取下限更近者）。
 */
export function pickClosestOption(requested: string, available: string[]): string | null {
  const rq = parseNumericRange(requested)
  if (!rq) return null
  const cands = available
    .map((opt) => ({ opt, r: parseNumericRange(opt) }))
    .filter((c): c is { opt: string; r: { min: number; max: number } } => c.r !== null)
  if (cands.length === 0) return null
  const overlap = (c: { min: number; max: number }) =>
    Math.max(0, Math.min(rq.max, c.max) - Math.max(rq.min, c.min))
  if (/^\d+(?:\.\d+)?(?:年|岁)?(?:以下|以内)$/.test(requested.replace(/[\sKk]/g, ''))) {
    // 单端上限不能沿用保下限排序或扩大范围；只能挑最大值不超过请求上限的实际档位。
    const upperEligible = cands.filter((c) => c.r.max <= rq.max + 1e-9)
    upperEligible.sort((a, b) => b.r.max - a.r.max || overlap(b.r) - overlap(a.r))
    return upperEligible[0]?.opt ?? null
  }
  const eligible = cands.filter((c) => c.r.min >= rq.min - 1e-9)
  if (eligible.length > 0) {
    eligible.sort((a, b) => a.r.min - b.r.min || overlap(b.r) - overlap(a.r))
    return eligible[0]!.opt
  }
  cands.sort((a, b) => overlap(b.r) - overlap(a.r) || Math.abs(rq.min - a.r.min) - Math.abs(rq.min - b.r.min))
  return cands[0]!.opt
}

/** 面板中其他常见行标签（真机 2026-08-05 面板实拍）：只参与行带收紧，不支持点击设置 */
const EXTRA_ROW_LABEL_PREFIXES = ['年龄', '活跃度', '性别', '近期没有看过', '求职意向', '求职状态']

export class FilterSetter {
  private readonly sleep: (ms: number) => Promise<void>
  private readonly step: <T>(name: string, fn: () => Promise<T>) => Promise<T>
  private observedMembership: MembershipObservation = { status: 'unknown', source: 'unknown', evidence: ['尚未探查头像'] }
  private membershipOverride?: MembershipObservation
  private accountKey?: string | null
  private unreadablePanelRows: UnreadablePanelRow[] = []
  private toolbarAnchor?: { documentIndex: number; bounds: [number, number, number, number] }
  private expectedCount: number | null = null
  private pendingResult?: FilterApplyResult

  get membership(): MembershipObservation { return this.observedMembership }
  get accountRef(): string | null { return this.accountKey ?? null }
  get unreadableRows(): UnreadablePanelRow[] { return this.unreadablePanelRows.map((row) => ({ ...row })) }

  constructor(private readonly deps: FilterSetterDeps) {
    this.sleep = deps.sleep ?? ((ms) => new Promise((r) => setTimeout(r, ms)))
    this.step = deps.step ?? ((_name, fn) => fn())
  }

  /**
   * 清除全部筛选：开面板（已开跳过）→ 清除 → 确定 → 校验徽章无计数。
   * 任一步失败抛 FilterSetError。
   */
  async clear(): Promise<void> {
    this.expectedCount = null
    this.pendingResult = undefined
    this.toolbarAnchor = undefined
    if (this.deps.signal?.aborted) throw new CancelledError()
    let snap = await this.ensurePanelOpen()
    // VIP 适配：目标可能在弹窗滚动区下方，点击前先确保可见
    snap = await this.ensureButtonVisible(snap, '清除')
    await this.step('panel:clear', async () => {
      const clearBtn = this.locateUniqueText(snap, (s) => s === '清除', '清除按钮')
      this.deps.onMutation?.()
      await this.deps.click(clearBtn, viewportOf(snap))
      await this.sleep(500)
    })

    snap = await this.deps.snapshot()
    snap = await this.ensureButtonVisible(snap, '确定')
    this.expectedCount = 0
    await this.step('panel:confirm', async () => {
      const confirm = this.locateUniqueText(snap, (s) => s === '确定', '确定按钮')
      await this.deps.click(confirm, viewportOf(snap))
      await this.sleep(2000)
    })

    // 校验：徽章应为无计数的「筛选」
    const verified = await this.step('panel:verify', () => this.verifyLastPostcondition())
    if (verified.status === 'inconclusive') throw new FilterSetError('清除筛选结果无法确认：未唯一读到可见筛选按钮，不能将不可读当作0')
    if (verified.status === 'unmet') throw new FilterPostconditionError(`清除筛选校验失败：${verified.panelOpen ? '筛选面板仍打开，确定未生效' : `徽章仍为「筛选·${verified.observedCount}」（确定可能未生效）`}`)
  }

  /**
   * 应用筛选条件（替换语义）。返回生效的筛选条件数（与「筛选·N」徽章比对通过）。
   *
   * 档位匹配策略（2026-08-17 用户定调：判断交 AI，CLI 只精确执行）：先按原文定位，0 命中时按
   * 归一化精确匹配兜底（15k-20k ≡ 15-20K）；仍不中则报错并列出该行全部可选档位——调用方
   * （LLM）应先用 boss_filter_options/probeOptions 读可选档位，只提交已确认且可匹配的子集，
   * 未匹配条件跳过并告知，全部未匹配时不调用空筛选或清除。
   *
   * 进面板后先点「清除」再选选项：选项是切换式控件，上次运行残留的已选状态下
   * 直接点同名选项会变成反选（2026-08-06 chat 演示实测：筛选·5 残留 + 同 spec
   * 再 apply → 5 项全被点掉，徽章校验失败）。先清除保证从空白态开始，幂等。
   * 任一步失败抛 FilterSetError。
   */
  async apply(spec: FilterSpec): Promise<FilterApplyResult> {
    this.expectedCount = null
    this.pendingResult = undefined
    this.toolbarAnchor = undefined
    this.membershipOverride = undefined
    this.accountKey = undefined
    try { return await this.applyOnce(spec) } catch (error) {
      if (!(error instanceof VipUnavailable)) throw error
      if (error.membership.source === 'paywall') await closeFilterPaywall(this.deps)
      this.membershipOverride = error.membership
      // 仅确定的付费/会员负证据允许本次丢弃VIP后重设普通条件；第二次失败直接停止，不无限重放。
      return this.applyOnce(spec, true)
    }
  }

  private async applyOnce(spec: FilterSpec, resetAfterVipFailure = false): Promise<FilterApplyResult> {
    if (this.deps.signal?.aborted) throw new CancelledError()
    const rows: Array<{ labelPrefix: string; options: string[] }> = []
    for (const def of ROW_DEFS) {
      const value = spec[def.key]
      const options = Array.isArray(value) ? value : value ? [value] : []
      if (options.length > 0) {
        if (!def.multi && options.length > 1) {
          throw new FilterSetError(`${def.labelPrefix} 行为单选，收到 ${options.length} 个选项`)
        }
        rows.push({ labelPrefix: def.labelPrefix, options })
      }
    }
    for (const row of spec.filters ?? []) {
      const canonical = (label: string) => label.replace(/\[单选\]$/, '').trim()
      if (rows.some((r) => canonical(r.labelPrefix) === canonical(row.label))) {
        throw new FilterSetError(`筛选行「${row.label}」重复，请将同一维度合并后只提供一次`)
      }
      rows.push({ labelPrefix: row.label.trim(), options: row.options.map((o) => o.trim()) })
    }
    if (rows.length === 0 && !spec.age) {
      throw new FilterSetError('未提供任何筛选条件')
    }

    const initial = await this.deps.snapshot()
    this.readToolbarCount(initial)
    const account = membershipAccountKey(initial)
    if (this.accountKey === undefined) this.accountKey = account
    else this.requireSameAccount(initial)
    if ((spec.membership_hint || spec.membership_account_ref) && (!account || spec.membership_account_ref !== account)) throw new CodedOperationError('MEMBERSHIP_REQUIRED', '会员提示或账号绑定缺少当前账号的有效membership_account_ref，请重新查询boss_filter_options并核对账号，原筛选未清除')
    const paywall = inspectFilterPaywall(initial)
    if (paywall) throw new VipUnavailable({ status: 'non_vip', source: 'paywall', evidence: ['已确认当前筛选的会员购买/开通弹窗'] })
    const observed = this.membershipOverride ?? inspectMembership(initial)
    const effective = effectiveMembership(observed, spec.membership_hint)
    this.observedMembership = observed.status === 'unknown' && effective !== 'unknown'
      ? { status: effective, source: 'user_hint', observed_status: 'unknown', evidence: ['本次用户明确提供会员状态提示；未以推广徽标或标题证明会员'] }
      : observed
    const vipRequested = rows.some((r) => filterGroup(r.labelPrefix) === 'vip') || spec.age !== undefined || spec.first_degree_only === true
    if (vipRequested && effective === 'unknown') throw new CodedOperationError('MEMBERSHIP_REQUIRED', '未可靠识别当前BOSS账号是否为VIP，请先询问用户会员状态并传membership_hint；原筛选未清除')
    if (vipRequested && effective === 'vip' && !this.accountKey) throw new CodedOperationError('MEMBERSHIP_REQUIRED', '无法把本次会员状态绑定到可核验的导航头像账号，原筛选未清除，请恢复账号页面后确认')
    const skippedFilters: SkippedFilter[] = []
    if (effective === 'non_vip') {
      const reason = observed.source === 'paywall' ? 'paywall' as const : 'non_vip' as const
      const vipRows = rows.filter((r) => filterGroup(r.labelPrefix) === 'vip')
      skippedFilters.push(...vipRows.map((r) => ({ label: r.labelPrefix, options: r.options, reason })))
      rows.splice(0, rows.length, ...rows.filter((r) => filterGroup(r.labelPrefix) === 'ordinary'))
      if (spec.age) skippedFilters.push({ label: '年龄', range: spec.age, reason })
      if (spec.first_degree_only) skippedFilters.push({ label: '只看第一学历', options: ['true'], reason })
      spec = { ...spec, age: undefined, first_degree_only: spec.first_degree_only === undefined ? undefined : false }
      if (rows.length === 0 && !resetAfterVipFailure) {
        const badge = initial.strings.map((s) => FILTER_BADGE_PATTERN.exec(s.trim())).find((m) => m !== null)
        return { filterCount: badge ? Number(badge[1]) : 0, substitutions: [], appliedRows: [], firstDegreeOnly: false, membership: this.observedMembership, skippedFilters, applied: false }
      }
    }

    // 1. 开面板（已打开则跳过——面板开着时再点「筛选」会把它关掉）
    let snap = await this.ensurePanelOpen(initial)

    // 所有条件先验证再清除：不支持的新增维度不能把已有筛选清空后才失败。
    const substitutions: Array<{ row: string; requested: string; matched: string }> = []
    const discovered = this.describePanel(snap)
    const requiresStructure = this.structuralRows(snap) !== null
    if (requiresStructure) this.cssWheel(snap, 0, 0, 0) // 完整几何门禁在清除前。
    if (spec.age) {
      const min = spec.age.min ?? 16, max = spec.age.max ?? null
      if (!Number.isInteger(min) || min < 16 || min > 45 || (max !== null && (!Number.isInteger(max) || max < min || max > 45))) throw new FilterSetError('当前实测年龄控件只可精确表示16至45岁，最大不限用null；无法精确表达请求，原筛选尚未清除')
      if (!this.deps.drag) throw new FilterSetError('未提供真实年龄拖动能力，原筛选尚未清除')
      this.ageSlider(snap)
      if (rows.some((r) => r.labelPrefix === '年龄')) throw new FilterSetError('年龄范围不能同时作为离散行设置')
    }
    for (const row of rows) {
      const info = discovered.find((r) => r.label === row.labelPrefix || r.label.startsWith(`${row.labelPrefix}[`))
      const generic = (spec.filters ?? []).some((r) => r.label.trim() === row.labelPrefix)
      if (generic && (!info || info.control === 'range')) throw new FilterSetError(`筛选行「${row.labelPrefix}」${info ? '为范围控件，不能按离散选项设置' : '未在当前面板完整解析到'}，原筛选尚未清除`)
      if (generic && row.options.length > 1 && info?.selection !== 'multiple') {
        throw new FilterSetError(`筛选行「${row.labelPrefix}」未确认支持多选，拒绝设置多个选项`)
      }
      if (new Set(row.options).size !== row.options.length) throw new FilterSetError(`筛选行「${row.labelPrefix}」选项重复`)
      row.options = row.options.map((option) => {
        try { this.locateRowOptionHit(snap, row.labelPrefix, option); return option } catch (err) {
          if (!(err instanceof FilterSetError) || !err.message.includes('实际 0 个')) throw err
          const available = this.rowOptionTexts(snap, row.labelPrefix)
          const exact = available.find((t) => generic ? t === option : normalizeOptionText(t) === normalizeOptionText(option))
          const closest = exact ?? (generic ? null : pickClosestOption(option, available))
          if (!closest) throw new FilterSetError(`${row.labelPrefix} 没有匹配的选项「${option}」，该行可选：${available.join('、') || '（未解析到选项）'}；请先用 boss_filter_options 查询，原筛选尚未清除（原始定位错误：${err.message}）`)
          this.locateRowOptionHit(snap, row.labelPrefix, closest)
          if (!exact) substitutions.push({ row: row.labelPrefix, requested: option, matched: closest })
          return closest
        }
      }).filter((option) => option !== '不限')
      if (new Set(row.options).size !== row.options.length) throw new FilterSetError(`筛选行「${row.labelPrefix}」多个请求映射到同一个实际选项，请合并后再试，原筛选尚未清除`)
    }

    if (this.deps.signal?.aborted) throw new CancelledError()

    // 2. 先清除残留选择（反选防护，见方法注释）
    snap = await this.ensureButtonVisible(snap, '清除')
    this.requireSameAccount(snap)
    if (rows.some((r) => filterGroup(r.labelPrefix) === 'vip') || spec.age || spec.first_degree_only) this.requireVip(snap, spec.membership_hint)
    await this.step('panel:clear', async () => {
      const clearBtn = this.locateUniqueText(snap, (s) => s === '清除', '清除按钮')
      this.deps.onMutation?.()
      await this.deps.click(clearBtn, viewportOf(snap))
      await this.sleep(500)
    })

    let appliedAge: { min: number; max: number | null } | undefined
    if (spec.age) appliedAge = await this.setAge(spec.age, spec.membership_hint)

    // 3. 逐项行锚定点击（每项 fresh snapshot，页面可能重排；VIP 面板目标行可能在滚动区
    //    下方，点击前先 ensureRowVisible 滚入可视区）。
    // 档位保底在清除前完成；执行时歧义或页面变化必须失败，不替换成另一个值。
    for (const row of rows) {
      for (const option of row.options) {
        if (this.deps.signal?.aborted) throw new CancelledError()
        snap = await this.deps.snapshot()
        if (inspectFilterPaywall(snap)) throw new VipUnavailable({ status: 'non_vip', source: 'paywall', evidence: ['筛选过程中出现已确认的会员购买/开通弹窗'] })
        if (filterGroup(row.labelPrefix) === 'vip') this.requireVip(snap, spec.membership_hint)
        snap = await this.ensureRowVisible(snap, row.labelPrefix, option)
        if (filterGroup(row.labelPrefix) === 'vip') this.requireVip(snap, spec.membership_hint)
        const point = this.locateRowOption(snap, row.labelPrefix, option)
        const clickSnap = snap
        await this.step('panel:option', async () => {
          await this.deps.click(point, viewportOf(clickSnap))
          await this.sleep(500)
        })
      }
    }

    const firstDegreeOnly = spec.first_degree_only === undefined ? false : await this.setFirstDegree(spec.first_degree_only, spec.membership_hint)
    // 4. 确定
    snap = await this.deps.snapshot()
    this.requireSameAccount(snap)
    if (inspectFilterPaywall(snap)) throw new VipUnavailable({ status: 'non_vip', source: 'paywall', evidence: ['提交前出现已确认的会员购买/开通弹窗'] })
    if (rows.some((r) => filterGroup(r.labelPrefix) === 'vip' && r.options.length > 0) || appliedAge || firstDegreeOnly) this.requireVip(snap, spec.membership_hint)
    this.verifySelectedRows(snap, rows, requiresStructure)
    if (appliedAge) {
      const current = this.ageSlider(snap).handles
      if (current[0]!.value !== appliedAge.min || current[1]!.value !== appliedAge.max) throw new FilterPostconditionError('提交前年龄范围已变化，拒绝确认')
    }
    if ((this.firstDegreeControl(snap)?.checked ?? false) !== firstDegreeOnly) throw new FilterPostconditionError('提交前第一学历勾选状态已变化，拒绝确认')
    snap = await this.ensureButtonVisible(snap, '确定')
    this.requireSameAccount(snap)
    if (rows.some((r) => filterGroup(r.labelPrefix) === 'vip' && r.options.length > 0) || appliedAge || firstDegreeOnly) this.requireVip(snap, spec.membership_hint)
    // 保存已验证面板事实，提交响应丢失时可由只读后验确认，无需重建筛选结果。
    const expected = rows.reduce((n, r) => n + r.options.length, 0) + (appliedAge && (appliedAge.min !== 16 || appliedAge.max !== null) ? 1 : 0) + (firstDegreeOnly ? 1 : 0)
    this.expectedCount = expected
    this.pendingResult = { filterCount: expected, substitutions, appliedRows: rows.map((r) => ({ label: r.labelPrefix, options: [...r.options] })), ...(appliedAge ? { age: appliedAge } : {}), firstDegreeOnly, membership: this.observedMembership, skippedFilters, applied: true }
    await this.step('panel:confirm', async () => {
      const confirm = this.locateUniqueText(snap, (s) => s === '确定', '确定按钮')
      await this.deps.click(confirm, viewportOf(snap))
      await this.sleep(2000)
    })

    // 5. 徽章计数校验（筛选·N）
    const verified = await this.step('panel:verify', () => this.verifyLastPostcondition())
    if (verified.status === 'inconclusive') throw new FilterSetError(`筛选结果无法确认：期望「筛选·${expected}」，未唯一读到可见筛选按钮`)
    if (verified.status === 'unmet') throw new FilterPostconditionError(`筛选结果校验失败：期望「筛选·${expected}」，实际「筛选·${verified.observedCount}」${verified.panelOpen ? '，筛选面板仍打开' : ''}`)
    return verified.result!
  }

  /** 只读 fresh 核验，不打开面板、不点击；快照 I/O 错误原样传播。 */
  async verifyLastPostcondition(): Promise<FilterPostcondition> {
    const snap = await this.deps.snapshot()
    this.requireSameAccount(snap)
    const observedCount = this.readToolbarCount(snap)
    const panelOpen = this.visibleHits(snap, (text) => text === '经验要求' || text === '筛选条件').some((hit) => this.hitInViewport(snap, hit))
    const status = this.expectedCount === null || observedCount === null ? 'inconclusive'
      : !panelOpen && observedCount === this.expectedCount ? 'met' : 'unmet'
    return { status, expectedCount: this.expectedCount, observedCount, panelOpen, ...(status === 'met' && this.pendingResult ? { result: structuredClone(this.pendingResult) } : {}) }
  }

  /** Probe cleanup uses the same visible toolbar/panel rules without inventing a filter count. */
  async verifyProbeClosed(): Promise<{ status: 'met' | 'unmet' | 'inconclusive'; evidence: string[] }> {
    const snap = await this.deps.snapshot()
    return this.probeClosure(snap)
  }

  private probeClosure(snap: DomSnapshot): { status: 'met' | 'unmet' | 'inconclusive'; evidence: string[] } {
    this.requireSameAccount(snap)
    const count = this.readToolbarCount(snap)
    const panelOpen = this.visibleHits(snap, (text) => text === '经验要求' || text === '筛选条件')
      .some((hit) => this.hitInViewport(snap, hit))
    return { status: panelOpen ? 'unmet' : count === null ? 'inconclusive' : 'met',
      evidence: [panelOpen ? '筛选档位已读，但可见筛选面板仍展开' : count === null ? '面板标志不见且筛选工具栏不可读，不能确认已收起' : '原筛选工具栏可读且筛选面板标志已消失'] }
  }

  async closeProbePanel(): Promise<void> {
    const snap = await this.deps.snapshot()
    const before = this.probeClosure(snap)
    if (before.status === 'met') return
    if (before.status === 'inconclusive') throw new CodedOperationError('UI_CHANGED', before.evidence.join('；'))
    const button = this.locateUniqueText(snap, (s) => FILTER_BUTTON_PATTERN.test(s), '筛选按钮')
    await this.deps.click(button, viewportOf(snap))
    await this.sleep(800)
  }

  private hitInViewport(snap: DomSnapshot, hit: VisibleHit): boolean {
    const viewport = viewportOf(snap)
    const b = this.nodeGlobalBounds(snap, hit.documentIndex, hit.nodeIndex)
    if (!b || b[0] + b[2] / 2 < 0 || b[1] + b[3] / 2 < 0 || b[0] + b[2] / 2 >= viewport.width || b[1] + b[3] / 2 >= viewport.height) return false
    let documentIndex = hit.documentIndex
    let firstNode = hit.nodeIndex
    while (true) {
      const doc = snap.documents[documentIndex]!
      const parents = doc.nodes.parentIndex ? parentMapOf(doc) : null
      let node: number | undefined = firstNode
      const seen = new Set<number>()
      while (node !== undefined && !seen.has(node)) {
        seen.add(node)
        const styles = doc.layout.styles?.[doc.layout.nodeIndex.indexOf(node)]
        if (styles && (snap.strings[styles[2] ?? -1] === 'none' || ['hidden', 'collapse'].includes(snap.strings[styles[5] ?? -1] ?? '') || snap.strings[styles[6] ?? -1] === '0')) return false
        node = parents?.get(node)
      }
      if (documentIndex === 0) return true
      let owner: { documentIndex: number; nodeIndex: number } | undefined
      snap.documents.forEach((candidate, candidateIndex) => {
        for (const [nodeIndex, childIndex] of indexedValues(candidate.nodes.contentDocumentIndex, 'contentDocumentIndex')) {
          if (childIndex === documentIndex) owner = { documentIndex: candidateIndex, nodeIndex }
        }
      })
      if (!owner) return false
      const bounds = this.nodeGlobalBounds(snap, owner.documentIndex, owner.nodeIndex)
      if (!bounds || b[0] + b[2] / 2 < bounds[0] || b[1] + b[3] / 2 < bounds[1] || b[0] + b[2] / 2 >= bounds[0] + bounds[2] || b[1] + b[3] / 2 >= bounds[1] + bounds[3]) return false
      documentIndex = owner.documentIndex
      firstNode = owner.nodeIndex
    }
  }

  /** 徽章必须来自同一可见按钮，strings 中其他位置/隐藏 iframe 的「筛选·N」不是证据。 */
  private readToolbarCount(snap: DomSnapshot): number | null {
    const hits = this.visibleHits(snap, (text) => FILTER_BUTTON_PATTERN.test(text)).filter((hit) => {
      if (!this.hitInViewport(snap, hit)) return false
      const b = this.nodeGlobalBounds(snap, hit.documentIndex, hit.nodeIndex)!
      const anchor = this.toolbarAnchor
      return !anchor || (hit.documentIndex === anchor.documentIndex && Math.abs(b[0] - anchor.bounds[0]) <= Math.max(b[2], anchor.bounds[2]) && Math.abs(b[1] - anchor.bounds[1]) <= Math.max(b[3], anchor.bounds[3]))
    })
    if (hits.length !== 1) return null
    const hit = hits[0]!
    this.toolbarAnchor ??= { documentIndex: hit.documentIndex, bounds: this.nodeGlobalBounds(snap, hit.documentIndex, hit.nodeIndex)! }
    const badge = FILTER_BADGE_PATTERN.exec(hit.text)
    return badge ? Number(badge[1]) : 0
  }

  private requireVip(snap: DomSnapshot, hint?: MembershipHint): void {
    if (inspectFilterPaywall(snap)) throw new VipUnavailable({ status: 'non_vip', source: 'paywall', evidence: ['已确认会员购买/开通弹窗，用户提示不能覆盖付费证据'] })
    this.requireSameAccount(snap)
    const observed = inspectMembership(snap)
    if (observed.status === 'non_vip') throw new VipUnavailable(observed)
    if (effectiveMembership(observed, hint) !== 'vip') throw new CodedOperationError('MEMBERSHIP_REQUIRED', '执行VIP条件前无法继续确认会员状态，请重新核对账号；禁止猜测继续点击')
    this.observedMembership = observed.status === 'unknown'
      ? { status: 'vip', source: 'user_hint', observed_status: 'unknown', evidence: ['本次用户明确提供会员状态提示；账号已复核一致'] }
      : observed
  }

  private requireSameAccount(snap: DomSnapshot): void {
    if (this.accountKey && membershipAccountKey(snap) !== this.accountKey) throw new CodedOperationError('MEMBERSHIP_REQUIRED', '本次BOSS导航账号已变化或不可核验，不能将原账号会员提示沿用到新账号，请重新确认')
  }

  /**
   * 确保面板打开并返回打开后的 fresh snapshot（已打开则跳过，不做任何点击）。
   * 供 apply() 与探针模式共用。
   */
  async ensurePanelOpen(initial?: DomSnapshot): Promise<DomSnapshot> {
    return this.step('panel:open', () => this.openPanel(initial))
  }

  private async openPanel(initial?: DomSnapshot): Promise<DomSnapshot> {
    let snap = initial ?? await this.deps.snapshot()
    this.readToolbarCount(snap)
    if (this.visibleHits(snap, (s) => s === '经验要求').length === 1) return snap
    const button = this.locateUniqueText(snap, (s) => FILTER_BUTTON_PATTERN.test(s), '筛选按钮')
    await this.deps.click(button, viewportOf(snap))
    await this.sleep(1200)
    snap = await this.deps.snapshot()
    this.locateUniqueText(snap, (s) => s === '经验要求', '筛选面板行标签（面板未打开？）')
    return snap
  }

  /**
   * 探查筛选面板可确认的档位（boss_filter_options 工具，判断交 AI）：
   * 开面板 → describePanel 读各行选项 → 点「筛选」把面板收起还原页面。
   * 返回的选项交给调用方（LLM），由它把用户口语化要求（如 15k-20k / 5年以上 / 本科及以上）
   * 匹配到真实档位后再调 apply，不可读行单独报告且不阻塞其余行。0 行可读 → FilterSetError。
   */
  async probeOptions(options: { close?: boolean } = {}): Promise<PanelRowInfo[]> {
    if (this.deps.signal?.aborted) throw new CancelledError()
    const snap = await this.ensurePanelOpen()
    this.accountKey = membershipAccountKey(snap)
    this.observedMembership = inspectMembership(snap)
    // describePanel 只读结构（全量行都要读，不点击）——无需滚动适配
    const rows = await this.step('probe:describe', async () => this.describePanel(snap))
    const unreadable = this.unreadableRows
    const readable = rows.filter((row) => {
      if (row.control === 'range') {
        try { this.ageSlider(snap); return true } catch (err) {
          if (!(err instanceof FilterSetError)) throw err
          unreadable.push({ label: row.label, reason: err.message })
          return false
        }
      }
      if (row.options.length > 0) return true
      unreadable.push({ label: row.label, reason: '未解析到可选档位' })
      return false
    })
    this.unreadablePanelRows = unreadable
    if (readable.length === 0) {
      throw new FilterSetError('筛选面板未解析到任何可选档位（面板可能未打开或页面结构已变），请人工查看')
    }
    // 收起面板还原页面（面板开着时点「筛选」= 关闭；不触发任何筛选变更）
    if (options.close !== false) await this.closeProbePanel()
    return readable
  }

  /**
   * 面板结构探针：一次性输出各支持行的全部可见选项及坐标（只读，不点击）。
   *
   * 两道消歧：
   * 1. 容器级（结构）：≥2 个行标签的 LCA = 面板容器，候选人卡片文本在容器外直接排除
   *   （真机实测：卡片技能标签/公司名会和选项落进同一行带同一 x 区域，纯几何无法排除）；
   * 2. 几何级：与 locateRowOption 同一套行带/x 规则。
   * 宽松模式：行标签缺失/多命中的行直接跳过（探针用于人工核对，不 fail-loud）。
   */
  /** 某行的全部可见选项文本（describePanel 同源几何：行带 + 容器消歧），供档位就近匹配/错误提示 */
  private rowOptionTexts(snap: DomSnapshot, labelPrefix: string): string[] {
    const row = this.describePanel(snap).find((r) => r.label.startsWith(labelPrefix))
    return row ? row.options.map((o) => o.text) : []
  }

  /** 以真实标签列发现行，再取只包含该标签与选项的最近祖先，兼容会员新增行与折行。 */
  private structuralRows(snap: DomSnapshot): StructuralRow[] | null {
    this.unreadablePanelRows = []
    const anchors = ROW_DEFS.flatMap((d) => this.visibleHits(snap, (s) => s.startsWith(d.labelPrefix)))
    if (anchors.length < 2 || !snap.documents[anchors[0]!.documentIndex]?.layout.styles?.length) return null
    const di = anchors[0]!.documentIndex
    if (anchors.some((a) => a.documentIndex !== di)) throw new FilterSetError('筛选行标签跨文档，无法确定面板')
    const doc = snap.documents[di]!
    const parents = parentMapOf(doc)
    if (!parents) throw new FilterSetError('筛选面板缺少节点父关系')
    const footer = this.visibleHits(snap, (s) => s === '清除' || s === '确定').filter((h) => h.documentIndex === di)
    const panel = lowestCommonAncestor(doc, [...anchors, ...footer].map((h) => h.nodeIndex))
    if (panel === null || parents.get(panel) === panel || (parents.get(panel) ?? -1) < 0) {
      throw new FilterSetError('无法唯一确定筛选面板结构')
    }
    const all = this.visibleHits(snap, (s) => s.length > 0).filter((h) => h.documentIndex === di && isDescendantOf(doc, h.nodeIndex, panel))
    const labelX = anchors[0]!.bounds[0]
    const footerNodes = footer.map((h) => h.nodeIndex)
    const footerRoot = footerNodes.length > 1 ? lowestCommonAncestor(doc, footerNodes) : null
    const labels = all.filter((h) => Math.abs(h.bounds[0] - labelX) <= 2
      && h.text !== '筛选条件' && h.text !== '清除' && h.text !== '确定'
      && (footerRoot === null || !isDescendantOf(doc, h.nodeIndex, footerRoot)))
    const rows: StructuralRow[] = []
    for (const label of labels) {
      let parsed = false
      let current = parents.get(label.nodeIndex)
      const visited = new Set<number>()
      while (current !== undefined && current !== panel && !visited.has(current)) {
        visited.add(current)
        if (labels.some((other) => other !== label && isDescendantOf(doc, other.nodeIndex, current!))) break
        const options = all.filter((h) => h.nodeIndex !== label.nodeIndex
          && h.bounds[0] >= label.bounds[0] + label.bounds[2]
          && isDescendantOf(doc, h.nodeIndex, current!))
        const usableOptions = options.filter((o) => o.text !== '修改筛选专业' && !o.text.startsWith('只看第一学历'))
        if (usableOptions.length > 0) {
          rows.push({ label, root: current, options: usableOptions })
          parsed = true
          break
        }
        current = parents.get(current)
      }
      if (!parsed) this.unreadablePanelRows.push({ label: label.text, reason: '未完整解析出该行控件或可选档位' })
    }
    return rows
  }

  private selectionOf(label: string): 'single' | 'multiple' | 'unknown' {
    if (label.includes('[单选]') || ['性别', '近期没有看过', '是否与同事交换简历'].includes(label)) return 'single'
    if (['经验要求', '学历要求', '求职状态', '院校', '牛人关键词'].includes(label)) return 'multiple'
    return 'unknown'
  }

  /** 真机：选院校后才出现DIV.first-degree-wrap / SPAN.check-box及checked token。 */
  private firstDegreeControl(snap: DomSnapshot): { hit: VisibleHit; checked: boolean } | null {
    const labels = this.visibleHits(snap, (s) => s === '只看第一学历（第一学历为全日制本科）')
    if (labels.length === 0) return null
    if (labels.length !== 1) throw new FilterSetError('第一学历控件标签不唯一，拒绝勾选')
    const label = labels[0]!, doc = snap.documents[label.documentIndex]!, parents = parentMapOf(doc)
    let cur: number | undefined = label.nodeIndex
    const seen = new Set<number>()
    let wrap: number | undefined
    while (cur !== undefined && !seen.has(cur)) {
      seen.add(cur)
      if ((classOf(snap, label.documentIndex, cur) ?? '').split(/\s+/).includes('first-degree-wrap')) { wrap = cur; break }
      cur = parents?.get(cur)
    }
    const school = this.structuralRows(snap)?.find((r) => r.label.text === '院校')
    if (wrap === undefined || !school || isDescendantOf(doc, wrap, school.root) !== true) throw new FilterSetError('第一学历控件不在已确认的院校行结构内')
    const controls = doc.layout.nodeIndex.filter((node) => parents?.get(node) === wrap
      && (classOf(snap, label.documentIndex, node) ?? '').split(/\s+/).includes('check-box'))
    if (controls.length !== 1) throw new FilterSetError('第一学历勾选控件不唯一')
    const node = controls[0]!, b = doc.layout.bounds[doc.layout.nodeIndex.indexOf(node)]
    if (!b || b.length !== 4 || b[2]! <= 0 || b[3]! <= 0) throw new FilterSetError('第一学历勾选控件不可见')
    return { hit: { documentIndex: label.documentIndex, nodeIndex: node, bounds: b as [number, number, number, number], text: label.text }, checked: (classOf(snap, label.documentIndex, node) ?? '').split(/\s+/).includes('checked') }
  }

  private async setFirstDegree(target: boolean, hint?: MembershipHint): Promise<boolean> {
    let snap = await this.deps.snapshot()
    if (target) this.requireVip(snap, hint)
    else if (inspectFilterPaywall(snap)) throw new VipUnavailable({ status: 'non_vip', source: 'paywall', evidence: ['第一学历校验时出现会员付费弹窗'] })
    const initial = this.firstDegreeControl(snap)
    if (!initial) {
      if (target) throw new FilterSetError('选择院校后未出现第一学历控件，拒绝声称已勾选')
      return false
    }
    if (initial.checked === target) return target
    snap = await this.ensureHitVisible(snap, (s) => {
      const control = this.firstDegreeControl(s)
      if (!control) throw new FilterSetError('第一学历控件在点击前消失')
      return control.hit
    }, (s, h) => this.toGlobalPoint(s, h).x, '只看第一学历', 'panel', initial.hit.bounds[3] / 2)
    if (target) this.requireVip(snap, hint)
    const control = this.firstDegreeControl(snap)!
    await this.deps.click(this.toGlobalPoint(snap, control.hit), viewportOf(snap))
    await this.sleep(500)
    const afterSnap = await this.deps.snapshot()
    if (inspectFilterPaywall(afterSnap)) throw new VipUnavailable({ status: 'non_vip', source: 'paywall', evidence: ['第一学历操作后出现已确认会员付费弹窗'] })
    this.requireSameAccount(afterSnap)
    const after = this.firstDegreeControl(afterSnap)
    if (!after) throw new FilterSetError('第一学历勾选后控件不可读，无法确认状态')
    if (after.checked !== target) throw new FilterPostconditionError('第一学历勾选后状态未达到请求值，拒绝确认')
    return target
  }

  private verifySelectedRows(snap: DomSnapshot, requested: Array<{ labelPrefix: string; options: string[] }>, requiresStructure: boolean): void {
    const structural = this.structuralRows(snap)
    if (!structural) {
      if (requiresStructure) throw new FilterSetError('筛选面板结构在校验前丢失，拒绝确认')
      return // 从始至终采用旧精简快照的兼容路径。
    }
    for (const req of requested) {
      const row = structural.find((r) => r.label.text === req.labelPrefix || r.label.text.startsWith(`${req.labelPrefix}[`))
      if (!row) throw new FilterSetError(`校验时筛选行「${req.labelPrefix}」消失`)
      const doc = snap.documents[row.label.documentIndex]!, parents = parentMapOf(doc)!
      let observedOptionClass = false
      const selected = row.options.filter((option) => {
        let cur: number | undefined = option.nodeIndex
        const seen = new Set<number>()
        while (cur !== undefined && cur !== row.root && !seen.has(cur)) {
          seen.add(cur)
          const tokens = (classOf(snap, option.documentIndex, cur) ?? '').split(/\s+/)
          if (tokens.includes('option')) { observedOptionClass = true; return tokens.includes('active') }
          cur = parents.get(cur)
        }
        return false
      }).filter((o) => o.text !== '不限').map((o) => o.text)
      if (!observedOptionClass) throw new FilterSetError(`筛选行「${req.labelPrefix}」选中状态不可读`)
      if (selected.length !== req.options.length || req.options.some((o) => !selected.includes(o))) {
        throw new FilterPostconditionError(`筛选行「${req.labelPrefix}」实际已选值校验失败：期望${req.options.join('、') || '不限'}，实际${selected.join('、') || '未选中'}`)
      }
    }
  }

  /** 从年龄行的实际轨道及两个含tooltip的直接子节点识别滑块，不依赖混淆class。 */
  private ageSlider(snap: DomSnapshot): { label: VisibleHit; rail: [number, number, number, number]; handles: Array<{ point: ClickPoint; value: number | null; hit: VisibleHit }> } {
    const rows = this.structuralRows(snap)?.filter((r) => r.label.text === '年龄')
    if (rows?.length !== 1) throw new FilterSetError('当前面板未唯一解析到年龄范围控件')
    const row = rows[0]!, di = row.label.documentIndex, doc = snap.documents[di]!, parents = parentMapOf(doc)!
    const candidates: Array<{ label: VisibleHit; rail: [number, number, number, number]; handles: Array<{ point: ClickPoint; value: number | null; hit: VisibleHit }> }> = []
    for (const node of doc.layout.nodeIndex) {
      if (!isDescendantOf(doc, node, row.root)) continue
      const b = this.nodeGlobalBounds(snap, di, node)
      if (!b || b[2] < 100 || b[3] <= 0 || b[3] > 16) continue
      const handles = doc.layout.nodeIndex.filter((n) => parents.get(n) === node).flatMap((n) => {
        const hb = this.nodeGlobalBounds(snap, di, n)
        if (!hb || hb[2] < 16 || hb[2] > 80 || Math.abs(hb[2] - hb[3]) > 1) return []
        const values = row.options.filter((o) => isDescendantOf(doc, o.nodeIndex, n) && (/^\d+$/.test(o.text) || o.text === '不限'))
        if (values.length !== 1) return []
        const local = doc.layout.bounds[doc.layout.nodeIndex.indexOf(n)]! as [number, number, number, number]
        return [{ point: boundsCenter(hb), value: values[0]!.text === '不限' ? null : Number(values[0]!.text), hit: { documentIndex: di, nodeIndex: n, bounds: local, text: values[0]!.text } }]
      }).sort((a, b) => a.point.x - b.point.x)
      if (handles.length === 2) candidates.push({ label: row.label, rail: b, handles })
    }
    if (candidates.length !== 1) throw new FilterSetError('年龄轨道/双端句柄结构不唯一，拒绝拖动')
    return candidates[0]!
  }

  private async setAge(age: { min?: number; max?: number | null }, hint?: MembershipHint): Promise<{ min: number; max: number | null }> {
    const requested = { min: age.min ?? 16, max: age.max ?? null }
    let snap = await this.deps.snapshot()
    this.requireVip(snap, hint)
    for (const side of [0, 1] as const) {
      snap = await this.ensureHitVisible(snap, (s) => this.ageSlider(s).handles[side]!.hit, (s) => this.toGlobalPoint(s, this.ageSlider(s).label).x, '年龄句柄', 'panel', 24)
      const target = side === 0 ? requested.min : requested.max
      const observations: Array<{ step: number; x: number }> = []
      for (let attempt = 0; ; attempt++) {
        if (this.deps.signal?.aborted) throw new CancelledError()
        this.requireVip(snap, hint)
        const slider = this.ageSlider(snap), handle = slider.handles[side]!
        if (handle.value === target) break
        if (attempt >= 7) throw new FilterSetError(`年龄${side === 0 ? '下限' : '上限'}拖动后未达到精确值，实际${handle.value ?? '不限'}`)
        // 2026-10-13 实测16..45及末端不限共30步；此处仅作为初始移动量，最终必须读tooltip精确闭环。
        const currentStep = handle.value === null ? 30 : handle.value - 16
        const desiredStep = target === null ? 30 : target - 16
        if (observations.length === 0) observations.push({ step: currentStep, x: handle.point.x })
        let x = target === null ? slider.rail[0] + slider.rail[2]
          : Math.max(slider.rail[0], Math.min(slider.rail[0] + slider.rail[2], handle.point.x + (desiredStep - currentStep) * slider.rail[2] / 30))
        const lower = observations.filter((o) => o.step < desiredStep).sort((a, b) => b.step - a.step)[0]
        const upper = observations.filter((o) => o.step > desiredStep).sort((a, b) => a.step - b.step)[0]
        if (target !== null && lower && upper && upper.x > lower.x) {
          // 使用实际发出的x与随后tooltip反馈建立边界，避免重复固定步长在26岁档位振荡。
          x = lower.x + (upper.x - lower.x) * (desiredStep - lower.step) / (upper.step - lower.step)
          if (observations.some((o) => Math.abs(o.x - x) < 1)) x = (lower.x + upper.x) / 2
        } else if (target !== null && observations.length > 1) {
          const last = observations[observations.length - 1]!, prior = observations.find((o) => o.step !== last.step)
          if (prior) x = last.x + (desiredStep - last.step) * (last.x - prior.x) / (last.step - prior.step)
        }
        x = Math.max(slider.rail[0], Math.min(slider.rail[0] + slider.rail[2], x))
        await this.step('panel:age:drag', () => this.deps.drag!(handle.point, { x, y: handle.point.y }, viewportOf(snap)))
        await this.sleep(650)
        snap = await this.deps.snapshot()
        this.requireVip(snap, hint)
        const observed = this.ageSlider(snap).handles[side]!.value
        observations.push({ step: observed === null ? 30 : observed - 16, x })
      }
    }
    const values = this.ageSlider(snap).handles
    if (values[0]!.value !== requested.min || values[1]!.value !== requested.max) throw new FilterPostconditionError('年龄范围最终校验未通过')
    return requested
  }

  describePanel(snap: DomSnapshot): PanelRowInfo[] {
    const structural = this.structuralRows(snap)
    if (structural) return structural.map((row) => ({
      label: row.label.text,
      control: row.label.text === '年龄' ? 'range' : 'options',
      selection: this.selectionOf(row.label.text),
      group: filterGroup(row.label.text),
      ...(row.label.text === '年龄' ? { range: { min: 16, max: 45, unlimited: true } } : {}),
      options: row.label.text === '年龄' ? [] : row.options.map((h) => ({ text: h.text, point: this.toGlobalPoint(snap, h) })),
    }))
    // 1. 行标签命中（每行恰好 1 个才收）
    const labelHits = new Map<string, VisibleHit>()
    for (const def of ROW_DEFS) {
      const hits = this.visibleHits(snap, (s) => s.startsWith(def.labelPrefix))
      if (hits.length === 1) labelHits.set(def.labelPrefix, hits[0]!)
    }

    // 2. 面板容器 = 同一 document 内行标签节点的 LCA
    const containerByDoc = this.panelContainerByDoc(snap, [...labelHits.values()])

    // 3. 逐行收集容器内、行带内、标签右侧的文本
    const rows: PanelRowInfo[] = []
    for (const def of ROW_DEFS) {
      const label = labelHits.get(def.labelPrefix)
      if (!label) continue
      const labelOffset = accumulateOwnerOffset(snap, label.documentIndex)
      const rowCy = labelOffset.y + label.bounds[1] + label.bounds[3] / 2
      const rowRight = labelOffset.x + label.bounds[0] + label.bounds[2]
      const band = this.rowBand(snap, def.labelPrefix, rowCy)
      const options = this.visibleHits(snap, (s) => s.length > 0)
        .filter((h) => {
          const container = containerByDoc.get(h.documentIndex)
          if (container !== undefined) {
            const inside = isDescendantOf(snap.documents[h.documentIndex]!, h.nodeIndex, container)
            if (inside === false) return false
            // inside === null（快照缺 parentIndex）时回退纯几何
          }
          const offset = accumulateOwnerOffset(snap, h.documentIndex)
          const c = boundsCenter(h.bounds)
          return Math.abs(offset.y + c.y - rowCy) <= band && offset.x + c.x > rowRight
        })
        .map((h) => ({ text: h.text, point: this.toGlobalPoint(snap, h) }))
        .sort((a, b) => a.point.x - b.point.x)
      rows.push({ label: label.text, options })
    }
    return rows
  }

  /** 面板容器定位：≥2 个行标签节点的 LCA（describePanel 与 locateRowOption 共用的结构级消歧） */
  private panelContainerByDoc(snap: DomSnapshot, labelHits: VisibleHit[]): Map<number, number> {
    const labelNodesByDoc = new Map<number, number[]>()
    for (const hit of labelHits) {
      const arr = labelNodesByDoc.get(hit.documentIndex) ?? []
      arr.push(hit.nodeIndex)
      labelNodesByDoc.set(hit.documentIndex, arr)
    }
    const containerByDoc = new Map<number, number>()
    for (const [docIdx, nodeIdxs] of labelNodesByDoc) {
      if (nodeIdxs.length < 2) continue
      const lca = lowestCommonAncestor(snap.documents[docIdx]!, nodeIdxs)
      if (lca !== null) containerByDoc.set(docIdx, lca)
    }
    return containerByDoc
  }

  /** 跨 document 收集所有可见文本命中（bounds w/h > 0） */
  private visibleHits(snap: DomSnapshot, pred: (s: string) => boolean): VisibleHit[] {
    const hits: VisibleHit[] = []
    snap.strings.forEach((s, stringIndex) => {
      const text = s.trim()
      if (!pred(text)) return
      snap.documents.forEach((document, documentIndex) => {
        for (const { bounds, nodeIndex } of findNodesByString(document, stringIndex)) {
          if (bounds.every(Number.isFinite) && bounds[2] > 0 && bounds[3] > 0) {
            try { accumulateOwnerOffset(snap, documentIndex) } catch { continue }
            hits.push({ documentIndex, nodeIndex, bounds, text })
          }
        }
      })
    })
    return hits
  }

  /** 唯一文本定位（含 iframe owner 偏移），多命中/零命中 fail-loud */
  private locateUniqueText(snap: DomSnapshot, pred: (s: string) => boolean, what: string): ClickPoint {
    return this.toGlobalPoint(snap, this.locateUniqueTextHit(snap, pred, what))
  }

  /** locateUniqueText 的 hit 版（VIP 可视性判定需要完整 bounds 而不只是中心点） */
  private locateUniqueTextHit(snap: DomSnapshot, pred: (s: string) => boolean, what: string): VisibleHit {
    const hits = this.visibleHits(snap, pred)
    if (hits.length !== 1) {
      throw new FilterSetError(`${what}必须恰好 1 个可见匹配，实际 ${hits.length} 个`)
    }
    return hits[0]!
  }

  // ---------- VIP 面板滚动适配（2026-09-28） ----------
  // VIP 账号筛选弹窗含大量 VIP 独享行，目标行（经验/学历/薪资）可能位于弹窗滚动区下方、
  // 弹窗可视 clip 之外（用户手动预滚过面板时也可能在上方越界）。DOMSnapshot 仍能取到其
  // bounds（布局存在），直接换算坐标点击会落点错误。点击前先把目标滚进可视区；
  // 滚不动（3 次）或无滚动能力 fail-loud。

  /** 某节点自身全局 bounds（文档 bounds + owner 偏移）；不在 layout / 无 bounds / owner 不可见时 null */
  private nodeGlobalBounds(snap: DomSnapshot, documentIndex: number, nodeIndex: number): [number, number, number, number] | null {
    const layout = snap.documents[documentIndex]?.layout
    const pos = layout ? layout.nodeIndex.indexOf(nodeIndex) : -1
    if (pos < 0) return null
    const b = layout!.bounds[pos]
    if (!b || b.length !== 4) return null
    let offset: { x: number; y: number }
    try {
      offset = accumulateOwnerOffset(snap, documentIndex)
    } catch {
      return null // 隐藏 iframe owner 无可见 bounds——按取不到容器处理
    }
    const doc = snap.documents[documentIndex]!
    return [b[0]! + offset.x - (doc.scrollOffsetX ?? 0), b[1]! + offset.y - (doc.scrollOffsetY ?? 0), b[2]!, b[3]!]
  }

  /**
   * 可视 clip（panel 模式 = 行标签 LCA 容器与视口的交集，容器取不到时退化为仅视口；
   * viewport 模式 = 仅视口，供「清除/确定」按钮用——按钮在弹窗 footer，不随内容区滚动，
   * 用行容器底做下界会把 footer 按钮误判为越界）。
   */
  private visibleClipOf(snap: DomSnapshot, mode: 'panel' | 'viewport', hit?: VisibleHit): { top: number; bottom: number } {
    const viewport = viewportOf(snap)
    let top = 0
    let bottom = viewport.height
    if (mode === 'panel') {
      if (hit && snap.documents[hit.documentIndex]?.layout.styles?.length) {
        const doc = snap.documents[hit.documentIndex]!
        const parents = parentMapOf(doc)
        let cur: number | undefined = hit.nodeIndex
        const seen = new Set<number>()
        while (cur !== undefined && !seen.has(cur)) {
          seen.add(cur)
          const index = doc.layout.nodeIndex.indexOf(cur)
          const overflow = snap.strings[doc.layout.styles?.[index]?.[1] ?? -1]
          if (overflow && ['auto', 'scroll', 'hidden', 'clip'].includes(overflow)) {
            const b = this.nodeGlobalBounds(snap, hit.documentIndex, cur)
            if (b) { top = Math.max(top, b[1]); bottom = Math.min(bottom, b[1] + b[3]) }
          }
          cur = parents?.get(cur)
        }
        if (bottom <= top) throw new PanelScrollError('筛选面板可视滚动区域为空，拒绝盲点')
        return { top, bottom }
      }
      const allLabelHits = ROW_DEFS.flatMap((d) => this.visibleHits(snap, (s) => s.startsWith(d.labelPrefix)))
      for (const [docIdx, nodeIdx] of this.panelContainerByDoc(snap, allLabelHits)) {
        const b = this.nodeGlobalBounds(snap, docIdx, nodeIdx)
        if (b) {
          bottom = Math.min(bottom, b[1] + b[3])
          top = Math.max(top, b[1])
        }
      }
    }
    return { top, bottom }
  }

  /** 仅筛选滚轮使用根 HTML 同节点的 bounds/clientRects，点击仍保留 device bounds 口径。 */
  private cssWheel(snap: DomSnapshot, x: number, y: number, deltaY: number): { x: number; y: number; deltaY: number } {
    const root = snap.documents[0]
    // 精简旧测试快照没有 CSS 几何；真实 operation 恒请求 includeDOMRects。
    if (!root?.layout.clientRects && !root?.layout.styles) return { x, y, deltaY }
    const invalid = (): never => { throw new PanelScrollError('缺少有效的根 HTML CSS 几何，未发送筛选滚动') }
    if (!root.nodes.nodeName || !root.layout.clientRects) return invalid()
    const html = indexedValues(root.nodes.nodeName, 'nodeName').filter(([, value]) => snap.strings[value] === 'HTML')
    if (html.length !== 1) return invalid()
    const i = root.layout.nodeIndex.indexOf(html[0]![0])
    const b = root.layout.bounds[i], c = root.layout.clientRects[i]
    if (!b || !c || b.length !== 4 || c.length !== 4 || ![...b, ...c].every(Number.isFinite)
      || b[2]! <= 0 || b[3]! <= 0 || c[2]! <= 0 || c[3]! <= 0
      || Math.abs(b[0]!) > 0.01 || Math.abs(b[1]!) > 0.01 || Math.abs(c[0]!) > 0.01 || Math.abs(c[1]!) > 0.01
      || (root.scrollOffsetX ?? 0) !== 0 || (root.scrollOffsetY ?? 0) !== 0) return invalid()
    const sx = b[2]! / c[2]!, sy = b[3]! / c[3]!
    if (Math.abs(sx - sy) > Math.max(sx, sy) * 0.001) return invalid()
    return { x: x / sx, y: y / sy, deltaY: deltaY / sy }
  }

  /**
   * 滚动核心：目标中心 y 超出可视区上下界即 CDP 滚动（下方越界正 deltaY 下滚；用户
   * 手动预滚过面板时目标可能在上方越界，负 deltaY 上滚），滚后 sleep 再 fresh snapshot
   * 重新定位（滚动内容 bounds 随滚动更新），最多滚 3 次。
   * 可视判定 = 严格的中心点在 clip 内（top ≤ cy ≤ bottom），不留边距：真机实证
   * （2026-09-28 非 VIP 首跑）经验要求行是面板第一行、中心距容器顶仅 ~39px，带 MARGIN
   * 的上界会把正常可见的首行误判为越界（滚 3 次失败报错）。中心在 clip 内时点击点
   * 几何上必在可视区内，无需额外安全边距；VIP 场景目标行真正超出 clip 时依然命中。
   * 目标本就可见 → 直接返回当前 snap（零开销路径，非 VIP 行为完全不变）。
   */
  private async ensureHitVisible(
    snap: DomSnapshot,
    locate: (s: DomSnapshot) => VisibleHit,
    scrollXOf: (s: DomSnapshot, hit: VisibleHit) => number,
    what: string,
    mode: 'panel' | 'viewport',
    padding = 0,
  ): Promise<DomSnapshot> {
    for (let attempt = 0; ; attempt++) {
      if (this.deps.signal?.aborted) throw new CancelledError()
      const hit = locate(snap)
      const cy = this.toGlobalPoint(snap, hit).y
      const { top, bottom } = this.visibleClipOf(snap, mode, hit)
      const range = `[${Math.round(top)}, ${Math.round(bottom)}]`
      if (cy <= bottom - padding && cy >= top + padding) return snap
      if (attempt >= 3) {
        throw new PanelScrollError(
          `目标「${what}」滚动 3 次后仍在筛选弹窗可视区外（y=${Math.round(cy)} 不在 ${range}），请人工查看`,
        )
      }
      if (!this.deps.mouseWheel) {
        throw new PanelScrollError(
          `目标「${what}」在筛选弹窗可视区外（y=${Math.round(cy)} 不在 ${range}），且未注入滚动能力，拒绝盲点`,
        )
      }
      // 滚动点在弹窗可视区内中部偏下（上滚时护住 clip 顶）；|deltaY| = 超出量 + 200 缓冲
      const deltaY = cy > bottom - padding
        ? Math.ceil(cy - bottom + padding) + 200
        : -(Math.ceil(top + padding - cy) + 200)
      const wheelY = Math.min(bottom - 1, Math.max(top + 1, bottom - 100))
      const wheel = this.cssWheel(snap, scrollXOf(snap, hit), wheelY, deltaY)
      await this.deps.mouseWheel(wheel.x, wheel.y, wheel.deltaY)
      await this.sleep(400)
      snap = await this.deps.snapshot()
    }
  }

  /** 确保某行选项在弹窗可视区内；返回已确认可见的最新 snap（调用方再定位最终点击点） */
  private async ensureRowVisible(snap: DomSnapshot, labelPrefix: string, option: string): Promise<DomSnapshot> {
    return this.ensureHitVisible(
      snap,
      (s) => this.locateRowOptionHit(s, labelPrefix, option),
      (s) => {
        // 滚动点 x 取行标签列（标签 x 恒在弹窗左列内，比选项 x 更稳）
        const hits = this.visibleHits(s, (t) => t.startsWith(labelPrefix))
        if (hits.length !== 1) {
          throw new FilterSetError(`行标签「${labelPrefix}」必须恰好 1 个可见匹配，实际 ${hits.length} 个`)
        }
        return this.toGlobalPoint(s, hits[0]!).x
      },
      `${labelPrefix}:${option}`,
      'panel',
    )
  }

  /**
   * 确保「清除」/「确定」按钮在可视区内。footer 不随弹窗内容区滚动，按纯视口判定：
   * 行容器底做下界会把按钮误判为越界（非 VIP 页按钮恒在行容器下方），滚内容也救不了
   * footer 越屏——真越屏直接 fail-loud。
   */
  private async ensureButtonVisible(snap: DomSnapshot, text: string): Promise<DomSnapshot> {
    return this.ensureHitVisible(
      snap,
      (s) => this.locateUniqueTextHit(s, (t) => t === text, `${text}按钮`),
      (s, hit) => this.toGlobalPoint(s, hit).x,
      text,
      'viewport',
    )
  }

  /**
   * 行锚定选项定位：先唯一锁定行标签，再在其 y 行带、x 右侧的同名文本中取唯一命中。
   * 用于避开候选人卡片中的重名文本（如「本科」）。
   *
   * 行带推导（真机踩坑）：标签与选项可能不在同一行——真机实测标签 y=571、选项 y=629
   * （相差 57.5px，标签独占一行、选项在下一行）。固定行高倍数会漏掉分行布局，
   * 故用「最近的其他行标签」垂直距离的一半作为行带半径，兼容同行/分行两种布局。
   */
  private locateRowOption(snap: DomSnapshot, labelPrefix: string, option: string): ClickPoint {
    return this.toGlobalPoint(snap, this.locateRowOptionHit(snap, labelPrefix, option))
  }

  /** locateRowOption 的 hit 版（VIP 可视性判定需要完整 bounds 而不只是中心点） */
  private locateRowOptionHit(snap: DomSnapshot, labelPrefix: string, option: string): VisibleHit {
    const structural = this.structuralRows(snap)
    if (structural) {
      const rows = structural.filter((r) => r.label.text === labelPrefix || r.label.text.startsWith(`${labelPrefix}[`))
      if (rows.length !== 1) throw new FilterSetError(`行标签「${labelPrefix}」必须恰好 1 个可见匹配，实际 ${rows.length} 个`)
      const hits = rows[0]!.options.filter((h) => h.text === option)
      if (hits.length !== 1) throw new FilterSetError(`选项「${option}」在「${labelPrefix}」行内必须恰好 1 个可见匹配，实际 ${hits.length} 个`)
      return hits[0]!
    }
    const labelHits = this.visibleHits(snap, (s) => s.startsWith(labelPrefix))
    if (labelHits.length !== 1) {
      throw new FilterSetError(`行标签「${labelPrefix}」必须恰好 1 个可见匹配，实际 ${labelHits.length} 个`)
    }
    const label = labelHits[0]!
    const labelOffset = accumulateOwnerOffset(snap, label.documentIndex)
    const rowCy = labelOffset.y + label.bounds[1] + label.bounds[3] / 2
    // 选项必须严格在行标签右侧（真机实测：弹层后候选人卡片的重名文本 x≈631 会落进行带，
    // 放松到标签左缘会引入诱饵；目标选项如「本科」cx=1369 恒在标签右缘 682.5 之右）
    const rowRight = labelOffset.x + label.bounds[0] + label.bounds[2]
    const band = this.rowBand(snap, labelPrefix, rowCy)

    // 结构级消歧（与 describePanel 同源）：弹层背后的候选人卡片文本仍带布局 bounds，
    // 可能恰好落进行带且在标签右侧（真机 2026-08-07：右列卡片「本科」cy=622.5 落进
    // 学历要求行带 ±34.75，与真选项 cy=643 形成 2 命中）。容器外命中一律排除，
    // isDescendantOf 返回 null（快照缺 parentIndex）时回退纯几何。
    const allLabelHits = ROW_DEFS.flatMap((d) => this.visibleHits(snap, (s) => s.startsWith(d.labelPrefix)))
    const containerByDoc = this.panelContainerByDoc(snap, allLabelHits)

    const inRow = (cyMin: number, cyMax: number) =>
      this.visibleHits(snap, (s) => s === option).filter((h) => {
        const container = containerByDoc.get(h.documentIndex)
        if (container !== undefined) {
          const inside = isDescendantOf(snap.documents[h.documentIndex]!, h.nodeIndex, container)
          if (inside === false) return false
        }
        const offset = accumulateOwnerOffset(snap, h.documentIndex)
        const c = boundsCenter(h.bounds)
        const hitCy = offset.y + c.y
        return hitCy >= cyMin && hitCy < cyMax && offset.x + c.x > rowRight
      })

    let candidates = inRow(rowCy - band, rowCy + band)
    if (candidates.length === 0) {
      // 折行兜底：行内选项过多时第二行选项落到行带外（真机 2026-08-07：5-10年 cy=770、
      // 标签 cy=712，行带仅 ±34.75）。放宽下界到「下一行标签上沿」，上沿仍收紧；
      // 容器/x 规则不变，仍要求唯一，多命中照样 fail-loud。
      const nextBelow = this.nextLabelBelowCy(snap, rowCy)
      if (nextBelow !== null) candidates = inRow(rowCy - band, nextBelow)
    }
    if (candidates.length !== 1) {
      throw new FilterSetError(
        `选项「${option}」在「${labelPrefix}」行内必须恰好 1 个可见匹配，实际 ${candidates.length} 个`,
      )
    }
    return candidates[0]!
  }

  /** 本行标签下方最近的其他行标签 cy（折行兜底的下界）；没有则返回 null */
  private nextLabelBelowCy(snap: DomSnapshot, rowCy: number): number | null {
    const prefixes = [...ROW_DEFS.map((d) => d.labelPrefix), ...EXTRA_ROW_LABEL_PREFIXES]
    let next: number | null = null
    for (const prefix of prefixes) {
      for (const other of this.visibleHits(snap, (s) => s.startsWith(prefix))) {
        const offset = accumulateOwnerOffset(snap, other.documentIndex)
        const cy = offset.y + other.bounds[1] + other.bounds[3] / 2
        if (cy > rowCy + 1 && (next === null || cy < next)) next = cy
      }
    }
    return next
  }

  /** 行带半径：最近其他行标签垂直距离的一半；无其他行标签时给足余量兜底 100 */
  private rowBand(snap: DomSnapshot, labelPrefix: string, rowCy: number): number {    let band = 100
    const otherPrefixes = [
      ...ROW_DEFS.filter((d) => d.labelPrefix !== labelPrefix).map((d) => d.labelPrefix),
      ...EXTRA_ROW_LABEL_PREFIXES,
    ]
    for (const prefix of otherPrefixes) {
      for (const other of this.visibleHits(snap, (s) => s.startsWith(prefix))) {
        const otherOffset = accumulateOwnerOffset(snap, other.documentIndex)
        const otherCy = otherOffset.y + other.bounds[1] + other.bounds[3] / 2
        const d = Math.abs(otherCy - rowCy)
        if (d > 1) band = Math.min(band, d / 2)
      }
    }
    return band
  }

  private toGlobalPoint(snap: DomSnapshot, hit: VisibleHit): ClickPoint {
    const offset = accumulateOwnerOffset(snap, hit.documentIndex)
    const c = boundsCenter(hit.bounds)
    const doc = snap.documents[hit.documentIndex]!
    return { x: offset.x + c.x - (doc.scrollOffsetX ?? 0), y: offset.y + c.y - (doc.scrollOffsetY ?? 0) }
  }
}

/** 根文档（document 0）视口尺寸 = 页面截图尺寸（device px，与 Page.captureScreenshot 同口径） */
export function viewportOf(snap: DomSnapshot): { width: number; height: number } {
  const b = snap.documents[0]?.layout.bounds[0]
  if (!b || b[2]! <= 0 || b[3]! <= 0) {
    throw new FilterSetError('根文档视口尺寸缺失，无法换算屏幕坐标')
  }
  return { width: b[2]!, height: b[3]! }
}
