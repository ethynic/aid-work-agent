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
 * - CDP Input.dispatchKeyEvent(type=char, text=字) 逐字输入中文畅通（风控不拦键盘）
 * - 日历日期数字在日历区域内唯一；选中后日期框显示 YYYY-MM-DD 文本（可校验）
 * - fail-loud：每步都有结果校验，任何一步不符合预期立即停止请人工查看
 */
import {
  type DomSnapshot,
  type ClickPoint,
  findNodesByString,
  accumulateOwnerOffset,
  boundsCenter,
} from './domSnapshot.js'
import { viewportOf } from './FilterSetter.js'
import { LIST_MAX_X } from './ResumeConsentExecutor.js'
import { CancelledError } from '../operations/types.js'

export class InterviewDemoError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'InterviewDemoError'
  }
}

export interface InterviewDemoDeps {
  snapshot(): Promise<DomSnapshot>
  /** Win32 真实鼠标点击 */
  click(point: ClickPoint, viewport: { width: number; height: number }): Promise<void>
  /** CDP char 事件逐字输入（调用方保证焦点已在目标输入框） */
  typeChar(ch: string): Promise<void>
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
/** 逐字输入间隔（拟人节奏，demo 可视化效果） */
const TYPE_INTERVAL_MS = 150

export class InterviewDemoExecutor {
  private readonly sleep: (ms: number) => Promise<void>

  constructor(private readonly deps: InterviewDemoDeps) {
    this.sleep = deps.sleep ?? ((ms) => new Promise((r) => setTimeout(r, ms)))
  }

  /** 填表单演示；返回填入的备注与日期。任何一步异常抛 InterviewDemoError（表单状态需人工确认） */
  async run(opts: { remark?: string } = {}): Promise<{ remark: string; date: string }> {
    if (this.deps.signal?.aborted) throw new CancelledError()
    const remark = opts.remark ?? DEFAULT_REMARK
    if (!remark.trim()) throw new InterviewDemoError('备注内容不能为空')

    // 0. 前置：表单不能已打开（已打开说明上次异常残留，人工确认后再来）
    const snap0 = await this.deps.snapshot()
    if (this.formOpen(snap0)) {
      throw new InterviewDemoError('面试邀请表单已处于打开状态，请先人工关闭（避免在残留表单上操作）')
    }

    // 1. 定位并点击「约面试」（右侧面板唯一）
    const btn = this.uniqueText(snap0, INVITE_BUTTON, (x) => x > LIST_MAX_X)
    if (!btn) throw new InterviewDemoError('未找到唯一的「约面试」按钮：请先在沟通页打开一个会话')
    await this.deps.click(btn, viewportOf(snap0))
    await this.sleep(1500)

    // 2. 校验表单打开 + 定位备注计数器
    const snap1 = await this.deps.snapshot()
    if (!this.formOpen(snap1)) {
      throw new InterviewDemoError('点击「约面试」后表单未打开：点击可能被拦截，请人工查看页面')
    }
    const counter = this.uniqueText(snap1, REMARK_COUNTER)
    if (!counter) throw new InterviewDemoError('表单已打开但未找到备注「/140」计数器，表单结构可能已变')

    // 3. 聚焦备注 textarea 并逐字输入
    await this.deps.click({ x: counter.x - 300, y: counter.y - 40 }, viewportOf(snap1))
    await this.sleep(500)
    for (const ch of remark) {
      await this.deps.typeChar(ch)
      await this.sleep(TYPE_INTERVAL_MS)
    }

    // 4. 校验字数（textarea 内容不在 DOM 文本节点里，用计数器数字验证输入落地）
    const snap2 = await this.deps.snapshot()
    if (!this.countMatches(snap2, remark.length)) {
      throw new InterviewDemoError(
        `备注逐字输入后字数计数器未显示 ${remark.length}：输入未完整落地，已停止，请人工查看表单`,
      )
    }

    // 5. 面试时间：打开日期面板选明天（跨月先点下月箭头）
    const label = this.uniqueText(snap2, TIME_LABEL)
    if (!label) throw new InterviewDemoError('未找到「面试时间」标签，表单结构可能已变')
    const tomorrow = new Date((this.deps.now ?? Date.now)() + 86400000)
    const dateStr = formatDate(tomorrow)
    await this.deps.click({ x: label.x + 230, y: label.y }, viewportOf(snap2))
    await this.sleep(1200)
    if (tomorrow.getDate() === 1) {
      // 跨月：当前面板是本月，先点右上角下月箭头（锚定：标签右 455 / 下 85）
      await this.deps.click({ x: label.x + 455, y: label.y + 85 }, viewportOf(snap2))
      await this.sleep(1000)
    }
    const snap3 = await this.deps.snapshot()
    const day = this.uniqueText(snap3, String(tomorrow.getDate()), (_x, y) => this.inCalendarRegion(_x, y, label))
    if (!day) {
      throw new InterviewDemoError(`日历面板中未找到唯一日期「${tomorrow.getDate()}」，已停止，请人工查看（表单未提交）`)
    }
    await this.deps.click(day, viewportOf(snap3))
    await this.sleep(800)

    // 6. 校验日期填入 + 定位「取消」
    // 注意：日期框是 input，选中值进 strings 表但无布局文本节点（真机 2026-08-06 实证，
    // 与 placeholder 同机制），只能查 strings 存在性，不能查可见坐标
    const snap4 = await this.deps.snapshot()
    if (!snap4.strings.some((s) => s.trim() === dateStr)) {
      throw new InterviewDemoError(`点击日期后日期框未显示 ${dateStr}：日期未选中，已停止，请人工查看（表单未提交）`)
    }
    const cancel = this.uniqueText(snap4, CANCEL_TEXT)
    if (!cancel) throw new InterviewDemoError('未找到唯一的「取消」按钮，请人工关闭表单（切勿点发送）')

    // 7. 取消关闭（本执行器没有任何点击「发送」的路径）
    await this.deps.click(cancel, viewportOf(snap4))
    await this.sleep(800)
    const snap5 = await this.deps.snapshot()
    if (this.formOpen(snap5)) {
      throw new InterviewDemoError('点击「取消」后表单仍未关闭，请人工查看页面（表单未提交）')
    }
    return { remark, date: dateStr }
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
