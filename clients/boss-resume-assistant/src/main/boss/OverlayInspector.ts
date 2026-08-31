/**
 * 弹层自愈原语一：覆盖层候选清单提取（只读，单次快照，2026-08-31）。
 *
 * 背景：BOSS 页面上工具操作偶发触发不可预见的弹层（广告/功能引导/活动弹窗），弹层盖在
 * 最上层导致后续操作必然失败（典型症状：UI_CHANGED / BUSY）。云端自愈编排（proxy_tool
 * 失败分支）先调本原语拿「视口内全部文本节点清单」，再由启发式/LLM 从中挑选关闭控件。
 *
 * 设计约束：
 * - 只读主文档 doc[0]（read-chat 真机实证：弹层与页面内容都在主文档，一次快照免滚动）
 * - 只导出 text/bounds/class，不做任何「是不是弹层」的判断——判断在云端（可热修），
 *   本层永远 fail-open 返回清单，判定错误不会误导页面操作
 * - class 是弹层识别的最强信号（BOSS 常见 dialog/mask/modal/pop/layer/guide 类名）
 */
import { indexedValues, classOf, type DomSnapshot } from './domSnapshot.js'

export interface OverlayCandidate {
  /** 文本内容（trim，超长截断到 24 字符 + …） */
  text: string
  /** bounds（文档绝对坐标，主文档 scrollOffset 恒 0，即屏幕 device px） */
  x: number
  y: number
  w: number
  h: number
  /** class 属性（截断到 36 字符） */
  cls: string
}

export interface OverlayInspection {
  /** 主文档视口（根节点 bounds，即页面截图尺寸） */
  viewport: { width: number; height: number }
  /** 文本节点候选清单（按 y,x 升序；上限 MAX_CANDIDATES 条） */
  candidates: OverlayCandidate[]
  /** 无文字 icon 关闭控件（class 命中关闭语义；按 y,x 升序；上限 ICON_MAX 条）。
   *  2026-08-31 真机实证：广告弹窗的关闭 × 无文字，纯文本清单抓不到——dismiss 以
   *  icon:<icon_cls> 引用（class 本身含 close 语义，安全等价于文本白名单） */
  icon_candidates: OverlayIconCandidate[]
}

const TEXT_MAX = 24
const CLS_MAX = 36
const MAX_CANDIDATES = 150

/** icon 关闭控件 class 识别（无文字图标按钮的关闭语义白名单，2026-08-31 真机实证：
 *  广告/弹窗的关闭 × 几乎都是无文字图标，class 惯例 boss-popup__close / icon-close / ad-banner-close） */
export const ICON_CLOSE_HINT = /close|guanbi/i
const ICON_MAX = 20

export interface OverlayIconCandidate {
  /** 命中关闭语义的完整 class（dismiss 以 icon:<cls> 引用） */
  icon_cls: string
  x: number
  y: number
  w: number
  h: number
}

/** 关闭语义白名单（工程持有，dismiss 与云端 LLM 选择的双重约束）：
 *  LLM 只能从这个集合里挑控件，杜绝把「立即领取」类按钮误判为关闭 */
export const DISMISS_TEXT_WHITELIST: readonly string[] = [
  '关闭',
  '关闭弹窗',
  '关闭广告',
  '知道了',
  '我知道了',
  '我知道啦',
  '以后再说',
  '下次再说',
  '稍后再说',
  '暂不',
  '暂不需要',
  '暂不使用',
  '不再提醒',
  '不再提示',
  '残忍拒绝',
  '取消',
  '跳过',
  '忽略',
  '×',
  '✕',
  '✖',
  'X',
  'No thanks',
  'Close',
]

export function isDismissText(text: string): boolean {
  return DISMISS_TEXT_WHITELIST.includes(text.trim())
}

/** 主文档视口（根节点 nodeIndex=0 的 bounds）；读不到回退 1249x1277（2026-08-06 真机窗口） */
function viewportOf(snapshot: DomSnapshot): { width: number; height: number } {
  const doc = snapshot.documents[0]
  const layout = doc?.layout
  if (layout && layout.nodeIndex[0] === 0 && layout.bounds[0] && layout.bounds[0]!.length >= 4) {
    const b = layout.bounds[0]!
    const x = b[0] ?? 0
    const y = b[1] ?? 0
    const w = b[2] ?? 0
    const h = b[3] ?? 0
    if (w > 0 && h > 0) return { width: Math.round(x + w), height: Math.round(y + h) }
  }
  return { width: 1249, height: 1277 }
}

/** 从主文档提取视口内文本节点候选清单（弹层识别原料） */
export function collectOverlayCandidates(snapshot: DomSnapshot): OverlayInspection {
  const doc = snapshot.documents[0]
  if (!doc?.layout) return { viewport: viewportOf(snapshot), candidates: [], icon_candidates: [] }

  // nodeValue 稀疏/稠密两种序列化 → nodeIdx → strings 下标
  const valueByNode = new Map<number, number>()
  for (const [nodeIdx, strIdx] of indexedValues(doc.nodes.nodeValue, 'nodeValue')) {
    valueByNode.set(nodeIdx, strIdx)
  }

  const vp = viewportOf(snapshot)
  const candidates: OverlayCandidate[] = []
  const iconCandidates: OverlayIconCandidate[] = []
  for (let i = 0; i < layoutLen(doc); i++) {
    const nodeIdx = doc.layout.nodeIndex[i]!
    const b = doc.layout.bounds[i]
    if (!b || b.length < 4) continue
    const x = b[0]!
    const y = b[1]!
    const w = b[2]!
    const h = b[3]!
    // 视口外/零面积：弹层控件必然在视口内且可点
    if (w <= 0 || h <= 0) continue
    if (x < 0 || y < 0 || x > vp.width || y > vp.height) continue
    const cls = (classOf(snapshot, 0, nodeIdx) ?? '').slice(0, CLS_MAX)
    // icon 关闭控件：class 命中关闭语义（无论有无文字——boss-popup__close 类通常无文字）
    if (ICON_CLOSE_HINT.test(cls) && iconCandidates.length < ICON_MAX) {
      iconCandidates.push({ icon_cls: cls, x: Math.round(x), y: Math.round(y), w: Math.round(w), h: Math.round(h) })
    }
    const strIdx = valueByNode.get(nodeIdx)
    if (strIdx === undefined) continue
    const raw = (snapshot.strings[strIdx] ?? '').trim()
    if (!raw) continue
    const text = raw.length > TEXT_MAX ? `${raw.slice(0, TEXT_MAX)}…` : raw
    candidates.push({ text, x: Math.round(x), y: Math.round(y), w: Math.round(w), h: Math.round(h), cls })
    if (candidates.length >= MAX_CANDIDATES) break
  }
  candidates.sort((a, b) => a.y - b.y || a.x - b.x)
  iconCandidates.sort((a, b) => a.y - b.y || a.x - b.x)
  return { viewport: vp, candidates, icon_candidates: iconCandidates }
}

function layoutLen(doc: DomSnapshot['documents'][number]): number {
  return Math.min(doc.layout.nodeIndex.length, doc.layout.bounds.length)
}
