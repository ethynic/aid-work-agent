/**
 * 推荐牛人页职位切换执行器（设计文档 §10.7，CLI list-jobs / select-job 命令）。
 *
 * 推荐牛人页（/web/chat/recommend）顶部有「职位框」：格式「<职位名> _ <城市> <薪资>」的文本，
 * 与「筛选」按钮同行、位于其左侧（真机 916,70 vs 筛选 1156,70）。点击职位框展开职位下拉，
 * 下拉中每个职位项同样是「职位名 _ 城市 薪资」格式。点职位项即切换当前招聘职位。
 *
 * 真机校准（2026-08-13，窗口 1249x1277）：
 * - 职位框：与「筛选」按钮同行（y 容差 JOB_BOX_Y_BAND=24）、在其左侧（x<筛选x）的
 *   「职位 _ 城市 薪资」文本；真机 (916,70) PHP开发工程师 _ 上海 8-12K，在 doc2 推荐列表 iframe。
 * - 职位项：点击职位框后下拉异步渲染，等 JOB_DROPDOWN_DELAY=2500ms 再 captureDomSnapshot，
 *   职位项才有 layout bounds（点击坐标）。snapshot 太早（<2000ms）项无 bounds → 误判盲区（实为等不够）。
 * - DOM 模型（真机 2026-08-13 实证）：下拉关闭时视口内仅 1 个职位格式节点（= 职位框，显示当前职位）；
 *   下拉打开时有 N+1 个职位格式节点——职位框 trigger **仍带 bounds** 显示当前职位，N 个下拉职位项在下方，
 *   当前职位在下拉项里也（高亮）出现，故与框同名。真机 N=4，打开态共 5 个节点。
 *   因此 selectJob 必须排除职位框节点本身、只在下拉项里精确匹配（否则当前职位会「框+项」命中 2 个）。
 *
 * 关键解析（踩坑）：遍历 documents[].nodes.nodeValue（所有节点，含无 layout bounds 的文本节点），
 * 不只 layout.nodeIndex——异步/未渲染的项文本节点有 nodeValue 但暂无 bounds。对每个匹配「职位 _ 城市 薪资」
 * 格式的节点，再用 layout.nodeIndex.indexOf 取 bounds（无 bounds = 未渲染/等待不足，跳过）。
 *
 * 不做模糊匹配：用户口述的职位名可能不精确（如「PHP」想切「PHP开发工程师」），匹配判断交给调用方 AI。
 * 本执行器只做精确相等（name === job_name），匹配 0/多个都 fail-loud。
 *
 * 安全设计（fail-loud）：职位框 0/多个、职位项 0/多个、点击后职位框文本未变更 → 抛 JobSwitchError，绝不盲点。
 * 写动作（点职位框/职位项）走 Win32（deps.click）；浏览走 CDP（DOMSnapshot）。永不 Runtime.* 与 Playwright 合成事件。
 */
import {
  type DomSnapshot,
  type ClickPoint,
  findNodesByString,
  accumulateOwnerOffset,
  boundsCenter,
  indexedValues,
} from './domSnapshot.js'
import { viewportOf } from './FilterSetter.js'
import { CancelledError } from '../operations/types.js'

export class JobSwitchError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'JobSwitchError'
  }
}

export interface JobSwitchDeps {
  /** 采集 fresh DOMSnapshot（每次定位前重新采集，禁止复用旧坐标） */
  snapshot(): Promise<DomSnapshot>
  /** Win32 真实鼠标点击（viewport 为页面截图尺寸，device px） */
  click(point: ClickPoint, viewport: { width: number; height: number }): Promise<void>
  /** 协作式取消信号：入口检查一次，触发即抛 CancelledError */
  signal?: AbortSignal
  /** 可注入 sleep（测试） */
  sleep?(ms: number): Promise<void>
}

export interface JobItem {
  /** 职位名（"_" 前部分） */
  name: string
  /** 城市 */
  city: string
  /** 薪资 */
  salary: string
  /** 屏幕点击坐标（device px） */
  point: ClickPoint
  /** 是否「待开放」（未发布）：职位项右侧有「待」徽章。真机（2026-08-13）：切到待开放职位会致页面异常，应避免 */
  pending: boolean
  /** 节点 nodeIndex（内部定位用：selectJob 据此排除职位框 trigger 节点；list-jobs 输出不映射此字段） */
  nodeIndex?: number
}

/** 推荐牛人列表页特征：「筛选」/「筛选·N」按钮存在（与 bossGreet 同源判定） */
export const FILTER_BUTTON_PATTERN = /^筛选(·\d+)?$/

/** 点职位框后等下拉异步渲染（ms）。真机校准（2026-08-13）：项的 layout bounds 需 ~2500ms 出现，
 *  太早（<2000ms）项无 bounds 会误判"抓不到/盲区"，实为等待不足 */
const JOB_DROPDOWN_DELAY = 2500
/** 点职位项后校验切换的总超时（ms）：切换触发推荐列表 iframe 重载，职位框会暂时消失，需等待重现 */
const VERIFY_TIMEOUT = 8000
/** 点职位项后校验切换的轮询间隔（ms） */
const VERIFY_POLL_INTERVAL = 800
/** 职位格式：<职位名> _ <城市> <薪资>，如 "PHP开发工程师 _ 上海 8-12K" */
const JOB_TEXT_PATTERN = /^(.+?) _ (\S+) (.+)$/
/** 职位框与「筛选」按钮同行的 y 容差（device px） */
const JOB_BOX_Y_BAND = 24
/** 职位框最小宽度，排除过窄误命中（device px） */
const JOB_BOX_MIN_WIDTH = 60
/** 「待开放」徽章文本（未发布职位项右侧的小标记；真机 2026-08-13 实证为文本节点，DOMSnapshot 可抓） */
const PENDING_TEXT = '待'
/** 「待」徽章与职位项判定同行的 y 容差（device px）；真机 dy≈1 */
const PENDING_SAME_Y_BAND = 16
/** 「待」徽章在职位项右侧的最大 x 距离（device px）；真机 dx≈120-136 */
const PENDING_MAX_DX = 200

export class JobSwitcher {
  private readonly sleep: (ms: number) => Promise<void>

  constructor(private readonly deps: JobSwitchDeps) {
    this.sleep = deps.sleep ?? ((ms) => new Promise((r) => setTimeout(r, ms)))
  }

  /**
   * 列出当前招聘者的全部职位：打开职位下拉 → 解析职位项。
   * 返回去重后的职位列表（按 name，保留首个有 bounds 的）。任何歧义抛 JobSwitchError（fail-loud）。
   */
  async listJobs(): Promise<JobItem[]> {
    if (this.deps.signal?.aborted) throw new CancelledError()
    const snap = await this.openJobDropdown()
    const all = this.parseJobItems(snap)
    // 按 name 去重（同名保留首个有 bounds 的）
    const seen = new Set<string>()
    const items = all.filter((it) => {
      if (seen.has(it.name)) return false
      seen.add(it.name)
      return true
    })
    if (items.length === 0) {
      throw new JobSwitchError(
        '打开职位下拉后未解析到任何职位项（项无 layout bounds 可能是等待不足，或下拉未打开/页面结构已变），请人工查看',
      )
    }
    return items
  }

  /**
   * 切换到指定职位名（精确匹配）：打开职位下拉 → 唯一定位目标项 → 点击 → 校验职位框文本已变更。
   * 成功返回 { switched: true }；匹配 0/多个、或点击后未生效都抛 JobSwitchError（绝不盲点）。
   */
  async selectJob(opts: { jobName: string }): Promise<{ switched: boolean }> {
    if (this.deps.signal?.aborted) throw new CancelledError()
    const jobName = opts.jobName
    if (!jobName) throw new JobSwitchError('职位名不能为空')

    const snap = await this.openJobDropdown()
    // 打开态职位框 trigger 仍带 bounds 显示当前职位（与下拉项里的当前职位高亮项同名），
    // 必须排除职位框节点本身、只在下拉项里精确匹配，否则切到当前职位会「框+项」命中 2 个。
    const openBox = this.locateJobBox(snap)
    const items = this.parseJobItems(snap).filter((i) => i.nodeIndex !== openBox.nodeIndex)
    const matches = items.filter((i) => i.name === jobName)
    if (matches.length === 0) {
      throw new JobSwitchError(
        `职位「${jobName}」不在当前招聘者的职位列表中（可用职位：${items.map((i) => i.name).join('、') || '无'}），请人工查看`,
      )
    }
    if (matches.length > 1) {
      throw new JobSwitchError(`职位「${jobName}」在下拉中匹配到 ${matches.length} 个项，无法唯一定位，已停止，请人工查看`)
    }
    // 点击前拦截待开放职位：切到未发布职位会导致页面异常（筛选消失），绝不切。这是比"切过去再校验失败"更友好的前置防护。
    if (matches[0]!.pending) {
      const openNames = items.filter((i) => !i.pending).map((i) => i.name)
      throw new JobSwitchError(
        `职位「${jobName}」当前待开放（未发布），无法切换。请从以下已开放职位中选择：${openNames.join('、') || '无'}`,
      )
    }

    // 点击目标职位项（Win32 写动作）
    await this.deps.click(matches[0]!.point, viewportOf(snap))

    // 校验切换成功：点击后下拉收起、推荐列表 iframe 重新加载（新职位牛人），
    // 职位框/筛选会从 doc2 暂时消失——轮询等待 iframe 重载完（职位框重新出现且文本=目标）再判定。
    await this.verifySwitched(jobName)
    return { switched: true }
  }

  /**
   * 点击职位项后校验切换是否生效：轮询 snapshot，等推荐列表 iframe 重载完、职位框重新出现且
   * 文本 === jobName 即成功返回。真机（2026-08-13）：切换职位触发 iframe 重载，职位框（在 doc2）
   * 会暂时消失，固定延时校验会误判「未知」；轮询等待重载完更稳。超时（职位框未重现 / 文本不符）→ fail-loud。
   *
   * 用「最大尝试次数 = ceil(VERIFY_TIMEOUT/VERIFY_POLL_INTERVAL)」控制轮询（而非 Date.now 实时时钟），
   * 这样测试注入 noop sleep 时循环快进不阻塞；生产环境每次 sleep(POLL_INTERVAL)，总时长≈VERIFY_TIMEOUT。
   */
  private async verifySwitched(jobName: string): Promise<void> {
    const maxAttempts = Math.max(1, Math.ceil(VERIFY_TIMEOUT / VERIFY_POLL_INTERVAL))
    let lastPoint: ClickPoint | null = null
    let lastName: string | null = null
    for (let attempt = 0; attempt < maxAttempts; attempt++) {
      if (attempt > 0) await this.sleep(VERIFY_POLL_INTERVAL) // 首次立即查（切自身时 iframe 不重载，秒过），之后每次先等
      if (this.deps.signal?.aborted) throw new CancelledError('已取消：校验切换结果时中止')
      const snap = await this.deps.snapshot()
      const box = this.locateJobBox(snap)
      lastPoint = box.point
      lastName = box.name
      if (box.point && box.name === jobName) return // iframe 重载完，职位框已显示目标职位
    }
    // 超时 fail-loud：消息含「未生效」或「无法确认」→ errorMapping 判 EXECUTION_UNKNOWN（写动作已发出）
    throw new JobSwitchError(
      lastPoint
        ? `点击职位项后切换未生效（职位框仍为「${lastName ?? '未知'}」，期望「${jobName}」），请人工查看页面`
        : `点击职位项后职位框在推荐列表重载后未重新出现（等待约 ${VERIFY_TIMEOUT}ms），切换结果无法确认，请人工查看页面`,
    )
  }

  /**
   * 打开职位下拉并返回打开后的 fresh snapshot（已打开则跳过，不做任何点击）。
   * 幂等处理已开状态：MCP 同会话连调时上一次 list-jobs 可能未关下拉，再点职位框会把它关掉。
   *
   * 判据：parseJobItems >= 2 = 下拉已打开（关闭态仅职位框 1 个）；关闭态则定位职位框点击。
   */
  private async openJobDropdown(): Promise<DomSnapshot> {
    const snap0 = await this.deps.snapshot()
    const items0 = this.parseJobItems(snap0)
    if (items0.length >= 2) {
      // 下拉已打开 → 项已渲染，不重复点（避免把已开的下拉点关）
      return await this.deps.snapshot()
    }
    // 关闭态（0 或 1 = 仅职位框）→ 定位职位框 → 点击 → 等异步渲染
    const box = this.locateJobBox(snap0)
    if (!box.point) {
      throw new JobSwitchError(
        box.count === 0
          ? '未找到职位框（参考「筛选」按钮同行左侧的「职位 _ 城市 薪资」文本 0 命中）：当前可能不在推荐牛人页或页面结构已变，请人工查看'
          : `找到 ${box.count} 个候选职位框，无法唯一定位，已停止，请人工查看`,
      )
    }
    await this.deps.click(box.point, viewportOf(snap0))
    await this.sleep(JOB_DROPDOWN_DELAY)
    return await this.deps.snapshot()
  }

  /**
   * 职位框定位（关闭态点击目标）：先找「筛选」按钮锚点，再在其同行左侧找唯一的「职位 _ 城市 薪资」文本。
   * 返回命中数与（唯一时的）屏幕坐标 + 职位名；无筛选按钮返回 count 0（调用方报错）。
   *
   * 真机（2026-08-13）：职位框与筛选按钮在推荐列表 iframe 同一行（y≈70），职位框在筛选左侧。
   * 「筛选」按钮是推荐牛人页信号（与 bossGreet 同），无筛选 = 不在该页。
   */
  private locateJobBox(
    snap: DomSnapshot,
  ): { point: ClickPoint | null; name: string | null; count: number; nodeIndex: number | null } {
    const viewport = viewportOf(snap)

    // 1. 找「筛选」按钮锚点：遍历 strings，trim 后匹配 FILTER_BUTTON_PATTERN，取视口内最上方可见的
    let filterPoint: ClickPoint | null = null
    snap.strings.forEach((s, stringIndex) => {
      if (!FILTER_BUTTON_PATTERN.test(s.trim())) return
      snap.documents.forEach((document, documentIndex) => {
        let offset: { x: number; y: number }
        try {
          offset = accumulateOwnerOffset(snap, documentIndex)
        } catch {
          return // 隐藏 iframe owner 无可见 bounds（后台标签页），跳过
        }
        for (const { bounds } of findNodesByString(document, stringIndex)) {
          if (bounds[2] <= 0 || bounds[3] <= 0) continue
          const c = boundsCenter(bounds)
          const x = offset.x + c.x - (document.scrollOffsetX ?? 0)
          const y = offset.y + c.y - (document.scrollOffsetY ?? 0)
          if (x <= 0 || x > viewport.width || y <= 0 || y > viewport.height) continue
          if (filterPoint === null || y < filterPoint.y) filterPoint = { x, y }
        }
      })
    })
    if (filterPoint === null) {
      return { point: null, name: null, count: 0, nodeIndex: null } // 无筛选按钮 = 不在推荐页/页面结构已变
    }

    // 2. 找职位框候选：遍历所有 document 的 nodeValue，匹配 JOB_TEXT_PATTERN 且有 bounds
    const hits: Array<{ point: ClickPoint; name: string; nodeIndex: number }> = []
    snap.documents.forEach((document, documentIndex) => {
      let offset: { x: number; y: number }
      try {
        offset = accumulateOwnerOffset(snap, documentIndex)
      } catch {
        return
      }
      for (const [nodeIndex, valueIndex] of indexedValues(document.nodes.nodeValue, 'nodeValue')) {
        const t = snap.strings[valueIndex]
        if (typeof t !== 'string') continue
        const m = JOB_TEXT_PATTERN.exec(t.trim())
        if (!m) continue
        const layoutIndex = document.layout.nodeIndex.indexOf(nodeIndex)
        if (layoutIndex < 0) continue // 文本节点无 layout bounds（未渲染），跳过
        const b = document.layout.bounds[layoutIndex]!
        if (!b || b[2]! <= 0 || b[3]! <= 0) continue
        if (b[2]! < JOB_BOX_MIN_WIDTH) continue // 排除过窄误命中
        const c = boundsCenter([b[0]!, b[1]!, b[2]!, b[3]!])
        const x = offset.x + c.x - (document.scrollOffsetX ?? 0)
        const y = offset.y + c.y - (document.scrollOffsetY ?? 0)
        if (x <= 0 || x > viewport.width || y <= 0 || y > viewport.height) continue // 视口外
        if (Math.abs(y - filterPoint!.y) > JOB_BOX_Y_BAND) continue // 不与筛选同行
        if (x >= filterPoint!.x) continue // 不在筛选左侧
        hits.push({ point: { x: Math.round(x), y: Math.round(y) }, name: m[1]!, nodeIndex })
      }
    })

    if (hits.length === 1) {
      return { point: hits[0]!.point, name: hits[0]!.name, count: 1, nodeIndex: hits[0]!.nodeIndex }
    }
    return { point: null, name: null, count: hits.length, nodeIndex: null }
  }

  /**
   * 解析下拉中全部职位项：遍历所有 document 的 nodeValue，匹配「职位名 _ 城市 薪资」格式，
   * 取有 layout bounds 且视口内的。无 bounds（未渲染/等待不足）的文本节点跳过。
   *
   * 不去重（按出现顺序返回全部）——selectJob 靠 count 做 fail-loud（多个同名=异常）；
   * listJobs 在调用处按 name 去重。
   */
  private parseJobItems(snap: DomSnapshot): JobItem[] {
    const viewport = viewportOf(snap)

    // 先收集「待」徽章节点的屏幕坐标（判定职位项是否待开放用）。
    // 「待」是单独文本节点，遍历 strings 精确匹配 PENDING_TEXT；忽略自我介绍等长文本里的"待"（y 远离下拉项）。
    const pendingPoints: ClickPoint[] = []
    const pendingStringIndex = snap.strings.indexOf(PENDING_TEXT)
    if (pendingStringIndex >= 0) {
      snap.documents.forEach((document, documentIndex) => {
        let offset: { x: number; y: number }
        try {
          offset = accumulateOwnerOffset(snap, documentIndex)
        } catch {
          return
        }
        for (const { bounds } of findNodesByString(document, pendingStringIndex)) {
          if (bounds[2] <= 0 || bounds[3] <= 0) continue
          const c = boundsCenter(bounds)
          const x = offset.x + c.x - (document.scrollOffsetX ?? 0)
          const y = offset.y + c.y - (document.scrollOffsetY ?? 0)
          if (x <= 0 || x > viewport.width || y <= 0 || y > viewport.height) continue
          pendingPoints.push({ x: Math.round(x), y: Math.round(y) })
        }
      })
    }

    const items: JobItem[] = []
    snap.documents.forEach((document, documentIndex) => {
      let offset: { x: number; y: number }
      try {
        offset = accumulateOwnerOffset(snap, documentIndex)
      } catch {
        return // 隐藏 iframe owner 无可见 bounds（后台标签页），跳过
      }
      for (const [nodeIndex, valueIndex] of indexedValues(document.nodes.nodeValue, 'nodeValue')) {
        const t = snap.strings[valueIndex]
        if (typeof t !== 'string') continue
        const m = JOB_TEXT_PATTERN.exec(t.trim())
        if (!m) continue
        const layoutIndex = document.layout.nodeIndex.indexOf(nodeIndex)
        if (layoutIndex < 0) continue // 无 layout bounds（未渲染/等待不足），跳过
        const b = document.layout.bounds[layoutIndex]!
        if (!b || b[2]! <= 0 || b[3]! <= 0) continue
        const c = boundsCenter([b[0]!, b[1]!, b[2]!, b[3]!])
        const x = offset.x + c.x - (document.scrollOffsetX ?? 0)
        const y = offset.y + c.y - (document.scrollOffsetY ?? 0)
        if (x <= 0 || x > viewport.width || y <= 0 || y > viewport.height) continue // 视口外
        // 待开放判定：右侧（同 y，x+≤PENDING_MAX_DX）存在「待」徽章
        const pending = pendingPoints.some(
          (p) => Math.abs(p.y - y) <= PENDING_SAME_Y_BAND && p.x > x && p.x - x <= PENDING_MAX_DX,
        )
        items.push({
          name: m[1]!,
          city: m[2]!.trim(),
          salary: m[3]!.trim(),
          point: { x: Math.round(x), y: Math.round(y) },
          pending,
          nodeIndex,
        })
      }
    })
    return items
  }
}
