/**
 * 约面试表单填充演示执行器（CLI interview 子命令，设计文档 §10.5）。
 *
 * 沟通页当前会话 → 点「约面试」打开表单浮窗 → 备注事项逐字输入（CDP char 事件，拟人逐字）→
 * 面试时间选择明天日期 → 点「取消」关闭。
 *
 * ⚠️ 演示用途：只填充不发送。本执行器没有任何点击「发送」的代码路径。
 *
 * 真机校准（2026-08-06）：
 * - 「选择日期」placeholder 和备注 placeholder 都是无布局节点的文本，不能按文本定位；
 *   备注用右下角「/140」字数计数器锚定（点其左上 300/40 处聚焦 textarea），
 *   日期下拉用「面试时间」标签锚定（点其右侧 230px 处）
 * - 备注输入走 Win32 clickAndType（点击聚焦 + SendInput 真实键盘逐字，2026-08-26 决策）
 * - 日历日期数字在日历区域内唯一；选中后日期框显示 YYYY-MM-DD 文本（可校验）
 * - fail-loud：每步都有结果校验，任何一步不符合预期立即停止请人工查看
 */
import {
  type DomSnapshot,
  type ClickPoint,
  findNodesByString,
  accumulateOwnerOffset,
  boundsCenter,
  indexedValues, type SparseOrDense,
} from './domSnapshot.js'
import { viewportOf } from './FilterSetter.js'
import { listMaxX } from './ResumeConsentExecutor.js'
import { CancelledError } from '../operations/types.js'
import { BusinessOperationState } from './BusinessOperationState.js'
import { PagePostconditionError } from '../operations/pageOperation.js'

export class InterviewDemoError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'InterviewDemoError'
  }
}

export interface InterviewDemoDeps {
  state?: BusinessOperationState
  snapshot(): Promise<DomSnapshot>
  /** Win32 真实鼠标点击 */
  click(point: ClickPoint, viewport: { width: number; height: number }): Promise<void>
  /** Win32 原子「真实鼠标点击聚焦 + 真实键盘逐字输入」（2026-08-26 决策：输入类第一优先 Win32） */
  clickAndType(point: ClickPoint, viewport: { width: number; height: number }, text: string): Promise<void>
  /** 协作式取消信号：入口检查一次，触发即抛 CancelledError */
  signal?: AbortSignal
  sleep?(ms: number): Promise<void>
  /** 当前时间（测试注入跨月边界用），默认 Date.now */
  now?(): number
}

const INVITE_BUTTON = '约面试'
const FORM_TITLES = ['线下面试邀请', '线上面试邀请']
const REMARK_COUNTER = '/140'
const TIME_LABEL = '面试时间'
const CANCEL_TEXT = '取消'
const DEFAULT_REMARK = '请带好身份证和简历准时面试'

export class InterviewDemoExecutor {
  private readonly sleep: (ms: number) => Promise<void>
  readonly state: BusinessOperationState

  constructor(private readonly deps: InterviewDemoDeps) {
    this.state = deps.state ?? new BusinessOperationState()
    this.sleep = deps.sleep ?? ((ms) => new Promise((r) => setTimeout(r, ms)))
  }

  /** 填表单演示；返回填入的备注与日期。任何一步异常抛 InterviewDemoError（表单状态需人工确认） */
  async run(opts: { remark?: string; expectedContactRef?: string } = {}): Promise<{ remark: string; date: string }> {
    for (;;) {
      try { return await this.runOnce(opts) } catch (error) {
        if (!(error instanceof PagePostconditionError) || error.verification.status !== 'unmet' || !this.state.retry()) throw error
        const fresh = await this.deps.snapshot()
        if (this.formOpen(fresh)) {
          const cancel = this.uniqueText(fresh, CANCEL_TEXT)
          if (!cancel) throw new InterviewDemoError('重试前无法安全定位取消按钮，表单未发送')
          await this.state.input(() => this.deps.click(cancel, viewportOf(fresh)))
          await this.sleep(800)
          if (this.formOpen(await this.deps.snapshot())) throw new InterviewDemoError('重试前表单未关闭，表单未发送')
        }
      }
    }
  }

  private async runOnce(opts: { remark?: string; expectedContactRef?: string }): Promise<{ remark: string; date: string }> {
    if (this.deps.signal?.aborted) throw new CancelledError()
    const remark = opts.remark ?? DEFAULT_REMARK
    if (!remark.trim()) throw new InterviewDemoError('备注内容不能为空')

    // 0. 前置：表单不能已打开（已打开说明上次异常残留，人工确认后再来）
    const snap0 = await this.deps.snapshot()
    this.state.bindContact(snap0, opts.expectedContactRef)
    if (this.formOpen(snap0)) {
      throw new InterviewDemoError('面试邀请表单已处于打开状态，请先人工关闭（避免在残留表单上操作）')
    }

    // 1. 定位并点击「约面试」（右侧面板唯一）
    const btn = this.uniqueText(snap0, INVITE_BUTTON, (x) => x > listMaxX(viewportOf(snap0).width))
    if (!btn) throw new InterviewDemoError('未找到唯一的「约面试」按钮：请先在沟通页打开一个会话')
    await this.state.input(() => this.deps.click(btn, viewportOf(snap0)))
    await this.sleep(1500)

    // 2. 校验表单打开 + 定位备注计数器
    const snap1 = await this.deps.snapshot()
    if (!this.formOpen(snap1)) {
      throw new PagePostconditionError('点击约面试后没有打开表单', { status: this.uniqueText(snap1, INVITE_BUTTON) ? 'unmet' : 'inconclusive', evidence: ['fresh表单标题未出现，同时核对原入口是否仍可读'] })
    }
    const counter = this.uniqueText(snap1, REMARK_COUNTER)
    if (!counter) throw new InterviewDemoError('表单已打开但未找到备注「/140」计数器，表单结构可能已变')

    // 3. 聚焦备注 textarea 并逐字输入（Win32 原子：点击聚焦后紧接真实键盘逐字）
    if (this.deps.signal?.aborted) throw new CancelledError()
    this.state.bindContact(snap1)
    const remarkPoint = { x: counter.x - 300, y: counter.y - 40 }
    await this.state.input(() => this.deps.clickAndType(remarkPoint, viewportOf(snap1), remark))

    // 4. textarea 当前值在 DOMSnapshot textValue；全文和计数都要符合原意图。
    const snap2 = await this.deps.snapshot()
    const remarkValue = this.fieldValueAt(snap2, 'TEXTAREA', remarkPoint)
    if (!this.countMatches(snap2, remark.length) || remarkValue !== remark) {
      throw new PagePostconditionError('备注文本或计数未满足原意图，表单未发送', {
        status: remarkValue === null ? 'inconclusive' : 'unmet', evidence: ['仅在表单备注字段读取原值，不以同长度其他文本当成功'] })
    }

    // 5. 面试时间：打开日期面板选明天（跨月先点下月箭头）
    const label = this.uniqueText(snap2, TIME_LABEL)
    if (!label) throw new InterviewDemoError('未找到「面试时间」标签，表单结构可能已变')
    const tomorrow = new Date((this.deps.now ?? Date.now)() + 86400000)
    const dateStr = formatDate(tomorrow)
    const datePoint = { x: label.x + 230, y: label.y }
    await this.state.input(() => this.deps.click(datePoint, viewportOf(snap2)))
    await this.sleep(1200)
    if (tomorrow.getDate() === 1) {
      // 跨月：当前面板是本月，先点右上角下月箭头（锚定：标签右 455 / 下 85）
      const monthSnap = await this.deps.snapshot()
      const freshLabel = this.uniqueText(monthSnap, TIME_LABEL)
      if (!freshLabel) throw new InterviewDemoError('换月前面试时间标签无法定位')
      await this.state.input(() => this.deps.click({ x: freshLabel.x + 455, y: freshLabel.y + 85 }, viewportOf(monthSnap)))
      await this.sleep(1000)
    }
    const snap3 = await this.deps.snapshot()
    const day = this.uniqueText(snap3, String(tomorrow.getDate()), (_x, y) => this.inCalendarRegion(_x, y, label))
    if (!day) {
      throw new InterviewDemoError(`日历面板中未找到唯一日期「${tomorrow.getDate()}」，已停止，请人工查看（表单未提交）`)
    }
    await this.state.input(() => this.deps.click(day, viewportOf(snap3)))
    await this.sleep(800)

    // 6. 校验日期填入 + 定位「取消」
    // 同排还有开始时间 INPUT，只核验本次日期点击点所在字段的 inputValue。
    const snap4 = await this.deps.snapshot()
    const dateValue = this.fieldValueAt(snap4, 'INPUT', datePoint)
    if (dateValue !== dateStr) {
      throw new PagePostconditionError('面试日期字段与原日期不符，表单未发送', {
        status: dateValue === null ? 'inconclusive' : 'unmet', evidence: ['读取原日期点击点所在唯一INPUT的真实值，不查全局strings或相邻开始时间'] })
    }
    const cancel = this.uniqueText(snap4, CANCEL_TEXT)
    if (!cancel) throw new InterviewDemoError('未找到唯一的「取消」按钮，请人工关闭表单（切勿点发送）')

    // 7. 取消关闭（本执行器没有任何点击「发送」的路径）
    this.state.bindContact(snap4)
    await this.state.input(() => this.deps.click(cancel, viewportOf(snap4)))
    await this.sleep(800)
    let snap5 = await this.deps.snapshot()
    if (this.formOpen(snap5) && this.state.retry()) {
      const freshCancel = this.uniqueText(snap5, CANCEL_TEXT)
      if (!freshCancel) throw new InterviewDemoError('取消重试前无法唯一定位取消按钮，表单未发送')
      await this.state.input(() => this.deps.click(freshCancel, viewportOf(snap5)))
      await this.sleep(800)
      snap5 = await this.deps.snapshot()
    }
    if (this.formOpen(snap5)) {
      throw new PagePostconditionError('取消后面试表单仍打开，未发送', { status: 'unmet', evidence: ['fresh表单标题仍可见'] })
    }
    this.state.execution.stage = 'postcheck'
    this.state.execution.verification = { status: 'met', evidence: ['备注字段全文、字数及日期字段已核验，取消后表单标题消失，未发送'] }
    return { remark, date: dateStr }
  }

  private fieldValueAt(snap: DomSnapshot, tag: 'TEXTAREA' | 'INPUT', point: ClickPoint): string | null {
    const values: Array<string | null> = []
    snap.documents.forEach((doc, di) => {
      if (!doc.nodes.nodeName) return
      let offset: ClickPoint
      try { offset = accumulateOwnerOffset(snap, di) } catch { return }
      const valueKey = tag === 'TEXTAREA' ? 'textValue' : 'inputValue'
      const fieldValues = (doc.nodes as typeof doc.nodes & { inputValue?: SparseOrDense<number>; textValue?: SparseOrDense<number> })[valueKey]
      const valueByNode = new Map(fieldValues ? indexedValues(fieldValues, valueKey) : [])
      for (const [ni, si] of indexedValues(doc.nodes.nodeName, 'nodeName')) {
        if (snap.strings[si] !== tag) continue
        const b = doc.layout.bounds[doc.layout.nodeIndex.indexOf(ni)]
        if (!b || b[2]! <= 0 || b[3]! <= 0) continue
        const x = offset.x + b[0]! - (doc.scrollOffsetX ?? 0)
        const y = offset.y + b[1]! - (doc.scrollOffsetY ?? 0)
        if (point.x < x || point.x > x + b[2]! || point.y < y || point.y > y + b[3]!) continue
        const value = valueByNode.get(ni)
        values.push(value === undefined ? null : snap.strings[value] ?? null)
      }
    })
    return values.length === 1 ? values[0]! : null
  }

  /** 表单是否打开：面试邀请标题有可见布局节点 */
  private formOpen(snap: DomSnapshot): boolean {
    return FORM_TITLES.some((t) => this.visibleTextExists(snap, t))
  }

  private visibleTextExists(snap: DomSnapshot, text: string): boolean {
    return this.visibleHits(snap, text).length > 0
  }

  /** 字数校验：「/140」计数器左侧同行（|Δy|<20，距离 <100）出现数字 N */
  private countMatches(snap: DomSnapshot, n: number): boolean {
    const counter = this.uniqueText(snap, REMARK_COUNTER)
    if (!counter) return false
    return this.visibleHits(snap, String(n)).some(
      (p) => Math.abs(p.y - counter.y) < 20 && counter.x - p.x > 0 && counter.x - p.x < 100,
    )
  }

  /** 日历区域（相对「面试时间」标签）：x +60~+540，y +40~+520 */
  private inCalendarRegion(x: number, y: number, label: ClickPoint): boolean {
    return x > label.x + 60 && x < label.x + 540 && y > label.y + 40 && y < label.y + 520
  }

  /** 唯一可见文本命中（可加 x/y 过滤）；0 或多个返回 null */
  private uniqueText(snap: DomSnapshot, text: string, filter?: (x: number, y: number) => boolean): ClickPoint | null {
    const hits = this.visibleHits(snap, text).filter((p) => !filter || filter(p.x, p.y))
    return hits.length === 1 ? hits[0]! : null
  }

  /** 全文档可见命中（同一文案遍历全部 string 下标，见 §17 坑：同文案多下标） */
  private visibleHits(snap: DomSnapshot, text: string): ClickPoint[] {
    const viewport = viewportOf(snap)
    const hits: ClickPoint[] = []
    snap.strings.forEach((s, i) => {
      if (s.trim() !== text) return
      snap.documents.forEach((document, documentIndex) => {
        let offset: { x: number; y: number }
        try {
          offset = accumulateOwnerOffset(snap, documentIndex)
        } catch {
          return // 隐藏 iframe owner 无可见 bounds（后台标签页），跳过
        }
        for (const { bounds } of findNodesByString(document, i)) {
          if (bounds[2] <= 0 || bounds[3] <= 0) continue
          const c = boundsCenter(bounds)
          const x = offset.x + c.x - (document.scrollOffsetX ?? 0)
          const y = offset.y + c.y - (document.scrollOffsetY ?? 0)
          if (x < 0 || y < 0 || x > viewport.width || y > viewport.height) continue
          hits.push({ x, y })
        }
      })
    })
    return hits
  }
}

function formatDate(d: Date): string {
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  return `${d.getFullYear()}-${mm}-${dd}`
}
