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
} from './domSnapshot.js'

export class FilterSetError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'FilterSetError'
  }
}

export interface FilterSpec {
  /** 经验要求行选项，如 '5-10年' */
  experience?: string
  /** 学历要求行选项（多选），如 ['本科','硕士','博士']；「本科及以上」语义的展开由调用方负责 */
  educations?: string[]
  /** 薪资待遇行选项（单选），如 '10-20K' */
  salary?: string
}

export interface FilterSetterDeps {
  /** 采集 fresh DOMSnapshot（页面动态变化，每次点击前重新采集，禁止复用旧坐标） */
  snapshot(): Promise<DomSnapshot>
  /** Win32 真实鼠标点击（viewport 为页面截图尺寸，device px） */
  click(point: ClickPoint, viewport: { width: number; height: number }): Promise<void>
  /** 可注入 sleep（测试） */
  sleep?(ms: number): Promise<void>
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
}

/** 行配置：条件字段 → 行标签匹配（前缀匹配，兼容「薪资待遇[单选]」等后缀变体） */
const ROW_DEFS: Array<{ key: 'experience' | 'educations' | 'salary'; labelPrefix: string; multi: boolean }> = [
  { key: 'experience', labelPrefix: '经验要求', multi: false },
  { key: 'educations', labelPrefix: '学历要求', multi: true },
  { key: 'salary', labelPrefix: '薪资待遇', multi: false },
]

const FILTER_BUTTON_PATTERN = /^筛选(·\d+)?$/
const FILTER_BADGE_PATTERN = /^筛选·(\d+)$/

/** 面板中其他常见行标签（真机 2026-08-05 面板实拍）：只参与行带收紧，不支持点击设置 */
const EXTRA_ROW_LABEL_PREFIXES = ['年龄', '活跃度', '性别', '近期没有看过', '求职意向']

export class FilterSetter {
  private readonly sleep: (ms: number) => Promise<void>

  constructor(private readonly deps: FilterSetterDeps) {
    this.sleep = deps.sleep ?? ((ms) => new Promise((r) => setTimeout(r, ms)))
  }

  /**
   * 清除全部筛选：开面板（已开跳过）→ 清除 → 确定 → 校验徽章无计数。
   * 任一步失败抛 FilterSetError。
   */
  async clear(): Promise<void> {
    let snap = await this.ensurePanelOpen()
    const clearBtn = this.locateUniqueText(snap, (s) => s === '清除', '清除按钮')
    await this.deps.click(clearBtn, viewportOf(snap))
    await this.sleep(500)

    snap = await this.deps.snapshot()
    const confirm = this.locateUniqueText(snap, (s) => s === '确定', '确定按钮')
    await this.deps.click(confirm, viewportOf(snap))
    await this.sleep(2000)

    // 校验：徽章应为无计数的「筛选」
    snap = await this.deps.snapshot()
    const badge = snap.strings.map((s) => FILTER_BADGE_PATTERN.exec(s.trim())).find((m) => m !== null)
    if (badge) {
      throw new FilterSetError(`清除筛选校验失败：徽章仍为「筛选·${badge[1]}」（确定可能未生效）`)
    }
  }

  /**
   * 应用筛选条件。返回生效的筛选条件数（与「筛选·N」徽章比对通过）。
   * 任一步失败抛 FilterSetError。
   */
  async apply(spec: FilterSpec): Promise<{ filterCount: number }> {
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
    if (rows.length === 0) {
      throw new FilterSetError('未提供任何筛选条件')
    }

    // 1. 开面板（已打开则跳过——面板开着时再点「筛选」会把它关掉）
    let snap = await this.ensurePanelOpen()

    // 2. 逐项行锚定点击（每项 fresh snapshot，页面可能重排）
    for (const row of rows) {
      for (const option of row.options) {
        snap = await this.deps.snapshot()
        const point = this.locateRowOption(snap, row.labelPrefix, option)
        await this.deps.click(point, viewportOf(snap))
        await this.sleep(500)
      }
    }

    // 3. 确定
    snap = await this.deps.snapshot()
    const confirm = this.locateUniqueText(snap, (s) => s === '确定', '确定按钮')
    await this.deps.click(confirm, viewportOf(snap))
    await this.sleep(2000)

    // 4. 徽章计数校验（筛选·N）
    snap = await this.deps.snapshot()
    const expected = rows.reduce((n, r) => n + r.options.length, 0)
    const badge = snap.strings.map((s) => FILTER_BADGE_PATTERN.exec(s.trim())).find((m) => m !== null)
    const count = badge ? Number(badge[1]) : null
    if (count !== expected) {
      throw new FilterSetError(
        `筛选结果校验失败：期望「筛选·${expected}」，实际 ${count === null ? '未找到徽章（面板可能未提交）' : `筛选·${count}`}`,
      )
    }
    return { filterCount: count }
  }

  /**
   * 确保面板打开并返回打开后的 fresh snapshot（已打开则跳过，不做任何点击）。
   * 供 apply() 与探针模式共用。
   */
  async ensurePanelOpen(): Promise<DomSnapshot> {
    let snap = await this.deps.snapshot()
    if (this.visibleHits(snap, (s) => s === '经验要求').length === 1) return snap
    const button = this.locateUniqueText(snap, (s) => FILTER_BUTTON_PATTERN.test(s), '筛选按钮')
    await this.deps.click(button, viewportOf(snap))
    await this.sleep(1200)
    snap = await this.deps.snapshot()
    this.locateUniqueText(snap, (s) => s === '经验要求', '筛选面板行标签（面板未打开？）')
    return snap
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
  describePanel(snap: DomSnapshot): PanelRowInfo[] {
    // 1. 行标签命中（每行恰好 1 个才收）
    const labelHits = new Map<string, VisibleHit>()
    for (const def of ROW_DEFS) {
      const hits = this.visibleHits(snap, (s) => s.startsWith(def.labelPrefix))
      if (hits.length === 1) labelHits.set(def.labelPrefix, hits[0]!)
    }

    // 2. 面板容器 = 同一 document 内行标签节点的 LCA
    const labelNodesByDoc = new Map<number, number[]>()
    for (const hit of labelHits.values()) {
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

  /** 跨 document 收集所有可见文本命中（bounds w/h > 0） */
  private visibleHits(snap: DomSnapshot, pred: (s: string) => boolean): VisibleHit[] {
    const hits: VisibleHit[] = []
    snap.strings.forEach((s, stringIndex) => {
      const text = s.trim()
      if (!pred(text)) return
      snap.documents.forEach((document, documentIndex) => {
        for (const { bounds, nodeIndex } of findNodesByString(document, stringIndex)) {
          if (bounds[2] > 0 && bounds[3] > 0) hits.push({ documentIndex, nodeIndex, bounds, text })
        }
      })
    })
    return hits
  }

  /** 唯一文本定位（含 iframe owner 偏移），多命中/零命中 fail-loud */
  private locateUniqueText(snap: DomSnapshot, pred: (s: string) => boolean, what: string): ClickPoint {
    const hits = this.visibleHits(snap, pred)
    if (hits.length !== 1) {
      throw new FilterSetError(`${what}必须恰好 1 个可见匹配，实际 ${hits.length} 个`)
    }
    return this.toGlobalPoint(snap, hits[0]!)
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

    const candidates = this.visibleHits(snap, (s) => s === option).filter((h) => {
      const offset = accumulateOwnerOffset(snap, h.documentIndex)
      const c = boundsCenter(h.bounds)
      return Math.abs(offset.y + c.y - rowCy) <= band && offset.x + c.x > rowRight
    })
    if (candidates.length !== 1) {
      throw new FilterSetError(
        `选项「${option}」在「${labelPrefix}」行内必须恰好 1 个可见匹配，实际 ${candidates.length} 个`,
      )
    }
    return this.toGlobalPoint(snap, candidates[0]!)
  }

  /** 行带半径：最近其他行标签垂直距离的一半；无其他行标签时给足余量兜底 100 */
  private rowBand(snap: DomSnapshot, labelPrefix: string, rowCy: number): number {
    let band = 100
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
    return { x: offset.x + c.x, y: offset.y + c.y }
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
