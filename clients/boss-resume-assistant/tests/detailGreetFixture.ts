/**
 * 详情页打招呼三件套测试共用 fixture（detail-greet-executor.test.ts / boss-detail-greet-ops.test.ts）。
 *
 * 快照构造按 2026-09-28 真机探查（docs/plans/desktop-automation/plan-boss-detail-greet.md）：
 * - 视口 1249x1277；详情 canvas [168,40,760,1264]（与视口求交后 h=1237）。
 * - canvas 右侧操作列为纯 DOM：收藏/举报/不合适一行 + 「打招呼」按钮在其正下方，
 *   四节点挂同一容器父节点（parents 数组）；列表卡片「打招呼」挂容器外（诱饵）。
 * - 详情头部候选人姓名 DOM 文本在 canvas 顶区 (318,129)。
 * 列表卡片行结构按 cardName 真机锚定（姓名(342,y-8) + 状态(400,y-8) 同行 + 按钮(1162,y)）。
 */
import type { DomSnapshot } from '../src/main/boss/domSnapshot.js'

export const VIEWPORT = { width: 1249, height: 1277 }
/** 真机：详情 iframe canvas（locateResumeCanvas 与视口求交后 → {x:168,y:40,w:760,h:1237}） */
export const CANVAS_BOUNDS: [number, number, number, number] = [168, 40, 760, 1264]
/** 操作列容器（真机 DOM 链实证 2026-09-28：LCA(三图标) 只是图标行容器，按钮挂兄弟子树；
 *  locateActionBar 上爬一层到父节点 = 本容器，同时包住图标行与按钮） */
export const BAR_CONTAINER_BOUNDS: [number, number, number, number] = [1040, 80, 200, 140]
/** 图标行容器（LCA(收藏,举报,不合适)，真机 4283 同构） */
export const BAR_TRIO_BOUNDS: [number, number, number, number] = [1044, 95, 156, 28]
/** 真机：按钮文本是字符串形态 "\n                  打招呼"（trim 后 === '打招呼'） */
export const GREET_STRING = '\n                  打招呼'
export const CONTINUE_STRING = '\n                  继续沟通'
/** 操作列「打招呼」按钮中心（真机探查 center≈(1085,159)） */
export const BAR_GREET_CENTER = { x: 1085, y: 156 }

/** 快照节点定义：text=文本节点 / canvas=CANVAS 元素 / 两者都无=纯结构容器节点 */
export interface SnapItem {
  text?: string
  canvas?: boolean
  bounds: [number, number, number, number]
  parent: number
}

/**
 * 构造单 document 快照（filter-setter.test.ts 的 buildSnap 范式扩展：支持 parents 与 CANVAS
 * nodeName）。node 0 为根（视口 1249x1277），其余节点 parent 指向任意已存在节点。
 */
export function buildSnap(items: SnapItem[]): DomSnapshot {
  const strings: string[] = ['']
  const nvIndex: number[] = [0]
  const nvValue: number[] = [0]
  const nameIndex: number[] = [0]
  const nameValue: number[] = [0]
  const parentIndex: number[] = [0]
  const layoutNodeIndex: number[] = [0]
  const layoutBounds: Array<[number, number, number, number]> = [[0, 0, VIEWPORT.width, VIEWPORT.height]]
  const intern = (s: string): number => {
    let i = strings.indexOf(s)
    if (i < 0) {
      strings.push(s)
      i = strings.length - 1
    }
    return i
  }
  let nextNi = 1
  for (const item of items) {
    const ni = nextNi++
    nvIndex.push(ni)
    nvValue.push(item.text !== undefined ? intern(item.text) : 0)
    nameIndex.push(ni)
    nameValue.push(item.canvas ? intern('CANVAS') : 0)
    parentIndex.push(item.parent)
    layoutNodeIndex.push(ni)
    layoutBounds.push(item.bounds)
  }
  return {
    strings,
    documents: [
      {
        nodes: {
          nodeValue: { index: nvIndex, value: nvValue },
          nodeName: { index: nameIndex, value: nameValue },
          contentDocumentIndex: { index: [], value: [] },
          parentIndex,
        },
        layout: { nodeIndex: layoutNodeIndex, bounds: layoutBounds },
        scrollOffsetY: 0,
      },
    ],
  }
}

/** 列表卡片行定义：姓名（null = DOM 配对失败）+ 打招呼按钮中心 y */
export interface ListRow {
  name: string | null
  buttonY: number
}

/** 列表页快照：卡片行（姓名/活跃状态/噪音/打招呼按钮，全部挂根节点=容器外诱饵） */
export function listSnap(rows: ListRow[]): DomSnapshot {
  const items: SnapItem[] = []
  for (const row of rows) {
    if (row.name !== null) items.push({ text: row.name, bounds: [317, row.buttonY - 18, 50, 20], parent: 0 }) // 中心 (342, y-8)
    items.push({ text: '刚刚活跃', bounds: [370, row.buttonY - 18, 60, 20], parent: 0 }) // 中心 (400, y-8)
    items.push({ text: '本科', bounds: [317, row.buttonY + 30, 40, 20], parent: 0 }) // 噪音：同行带外
    items.push({ text: GREET_STRING, bounds: [1130, row.buttonY - 16, 64, 32], parent: 0 }) // 中心 (1162, y)
  }
  return buildSnap(items)
}

/** 诱饵行默认组（详情打开态下列表仍可见——容器外「打招呼」必须被结构级排除） */
export const DECOY_ROWS: ListRow[] = [
  { name: '张三丰', buttonY: 146 },
  { name: '李建国', buttonY: 330 },
]

/**
 * 详情打开态快照：canvas + 头部候选人姓名（canvas 顶区）+ 操作列（真机 DOM 同构：
 * 图标行容器与按钮容器是**兄弟子树**，同挂操作列容器下——locateActionBar 需上爬一层）+ 容器外的列表诱饵行。
 * state='greet' → 按钮容器内是「打招呼」；'continue' → 是「继续沟通」（已打过）。
 */
export function detailSnap(opts: {
  name: string
  state: 'greet' | 'continue'
  decoyRows?: ListRow[]
  /** 额外节点（如顶部区同名第二命中，构造姓名歧义用例） */
  extraItems?: SnapItem[]
}): DomSnapshot {
  const items: SnapItem[] = [
    { canvas: true, bounds: CANVAS_BOUNDS, parent: 0 },
    { text: opts.name, bounds: [300, 119, 36, 20], parent: 0 }, // 中心 (318,129)，canvas 顶区
    { bounds: BAR_CONTAINER_BOUNDS, parent: 0 }, // 操作列容器（nodeIndex = 3）
    { bounds: BAR_TRIO_BOUNDS, parent: 3 }, // 图标行容器（LCA(三图标)，nodeIndex = 4）
    { bounds: [1060, 140, 60, 30], parent: 3 }, // 按钮子树容器（与图标行兄弟，nodeIndex = 5）
  ]
  const trioWrapper = 4
  const buttonWrapper = 5
  items.push(
    { text: '收藏', bounds: [1044, 99, 32, 20], parent: trioWrapper }, // 中心 (1060,109)
    { text: '举报', bounds: [1080, 99, 32, 20], parent: trioWrapper }, // 中心 (1096,109)
    { text: '不合适', bounds: [1112, 99, 44, 20], parent: trioWrapper }, // 中心 (1134,109)
  )
  if (opts.state === 'greet') {
    items.push({ text: GREET_STRING, bounds: [1064, 148, 42, 16], parent: buttonWrapper }) // 中心 (1085,156)
  } else {
    items.push({ text: CONTINUE_STRING, bounds: [1058, 148, 56, 16], parent: buttonWrapper }) // 中心 (1086,156)
  }
  items.push(...(opts.extraItems ?? []))
  // 诱饵行挂根节点（容器外）：结构级排除；真机几何上列表按钮与操作列同区带交叉，
  // 靠结构消歧而非几何（诱饵中心 x=1162 落在操作列 x 区间内，必须被 parentIndex 排除）
  const rows = opts.decoyRows ?? DECOY_ROWS
  for (const row of rows) {
    if (row.name !== null) items.push({ text: row.name, bounds: [317, row.buttonY - 18, 50, 20], parent: 0 })
    items.push({ text: '刚刚活跃', bounds: [370, row.buttonY - 18, 60, 20], parent: 0 })
    items.push({ text: '本科', bounds: [317, row.buttonY + 30, 40, 20], parent: 0 })
    items.push({ text: GREET_STRING, bounds: [1130, row.buttonY - 16, 64, 32], parent: 0 })
  }
  return buildSnap(items)
}

/** 详情关闭后的列表快照（默认诱饵行，无 canvas/操作列/头部姓名） */
export function closedListSnap(): DomSnapshot {
  return listSnap(DECOY_ROWS)
}
