/**
 * 推荐牛人卡片「按钮定位 + 姓名配对」（共享模块）。
 *
 * 模块内容：findButtonsByExactText（视口内按文案找卡片按钮）+ pairCardName（按钮→同排
 * 姓名配对）。两者同域：都产出/消费 GreetButtonRef，共同构成「卡片识别」能力——
 * doctor 配对自检与业务执行器共用同一实现（2026-09-01 上移共享），保证「测的就是
 * 真实跑的那份代码」，规则变更不会出现自检与执行漂移。
 *
 * 配对规则（2026-09-01 相对化修订：旧规则姓名列绝对像素 x<360 按开发机 1278 宽窗口校准，
 * 自动拉起的调试 Chrome 窗口尺寸不同时姓名列 x 右移全部配不上——定向打招呼滚遍全列表
 * 也「找不到」目标；改为「按钮同排窄带（|dy|<16）内、按钮左侧 60% 区域、排除状态词、
 * 姓名模式匹配取 x 最小」，不依赖窗口宽度/DPI）：
 * 按钮同排窄带的姓名区内，取符合姓名模式的节点（x 最小优先）。
 * 真机锚定（1278 宽参考）：姓名 x/按钮x≈0.29、状态「刚刚活跃」≈0.34（状态词排除）、
 * 年龄含数字（不匹配模式）、公司/职位列≈0.70+（相对上限外）——多重排除不会误配；
 * 找不到合格节点返回 null（fail-safe）。
 *
 * 使用方（同一套锚定规则，保证「读到的人」与「打招呼的人」是同一个人）：
 * - ResumeBatchReader：批量读简历时给卡片配姓名（配对失败用 OCR 首行启发式兜底）
 * - GreetExecutor：定向打招呼时按姓名匹配卡片（配对失败的按钮一律跳过，宁可不打不能打错）
 * - doctor 配对自检：只读报告当前页配对率（0 配对=布局失配，定向打招呼必然找不到人）
 */
import {
  type DomSnapshot,
  type ClickPoint,
  accumulateOwnerOffset,
  boundsCenter,
  findNodesByString,
  indexedValues,
} from './domSnapshot.js'
import { viewportOf } from './FilterSetter.js'

/** 「打招呼」按钮文本（DOM 抓取的原始文本 trim 后精确相等） */
export const GREET_TEXT = '打招呼'

/** 「继续沟通」按钮文本（打招呼成功后按钮原地翻转的文案，GreetExecutor 点击后位置校验用） */
export const CONTINUE_TEXT = '继续沟通'

/** 按钮同排窄带（|dy|）：只覆盖姓名/状态所在排（真机姓名 dy≈8），学历年龄在 dy≈19-27 排外 */
const NAME_ROW_MAX_DY = 16
/**
 * 姓名区相对上限：候选 x 必须 < 按钮x × 0.6（真机数据：姓名 x/按钮x≈0.29，状态≈0.34，
 * 公司/职位列≈0.70+）。2026-09-01 真机事故：旧规则用绝对像素 x<360（按开发机 1278 宽窗口
 * 校准），自动拉起的调试 Chrome 窗口尺寸不同时姓名列整体右移 → 全部卡片配不上 →
 * 定向打招呼滚遍全列表也找不到目标。相对比例不依赖窗口宽度/DPI。
 */
const NAME_COL_RELATIVE_MAX = 0.6
/** 状态词排除：以这些结尾的 2-4 字中文是活跃状态而非姓名（「刚刚活跃」会被姓名模式误命中） */
const STATUS_WORD_SUFFIX = /(活跃|在线|看过我)$/
/** 姓名文本模式：2-4 字中文（含·），年龄含数字、状态/学历等长文本不匹配 */
const NAME_PATTERN = /^[\u4e00-\u9fa5·]{2,4}$/

/** 「打招呼」按钮引用：点击点（device px 屏幕坐标）+ 所在文档序号（姓名配对需回同文档找文本节点） */
export interface GreetButtonRef {
  point: ClickPoint
  documentIndex: number
}

/**
 * 姓名配对：按钮同排窄带（|dy|<16）的姓名列区间（x<360）内取姓名模式节点（x 最小优先）。
 * 不依赖「活跃」等状态文本；配对失败返回 null。
 */
export function pairCardName(
  snap: DomSnapshot,
  btn: GreetButtonRef,
  viewport: { width: number; height: number },
): string | null {
  const doc = snap.documents[btn.documentIndex]!
  let offset: { x: number; y: number }
  try {
    offset = accumulateOwnerOffset(snap, btn.documentIndex)
  } catch {
    return null
  }
  // 该 document 内有 bounds 且视口可见的文本节点（屏幕坐标，取中心）
  const texts: Array<{ t: string; x: number; y: number }> = []
  for (const [nodeIndex, valueIndex] of indexedValues(doc.nodes.nodeValue, 'nodeValue')) {
    const t = snap.strings[valueIndex]
    if (typeof t !== 'string' || !t.trim()) continue
    const layoutIndex = doc.layout.nodeIndex.indexOf(nodeIndex)
    if (layoutIndex < 0) continue
    const b = doc.layout.bounds[layoutIndex]!
    if (!b || b[2]! <= 0 || b[3]! <= 0) continue
    const c = boundsCenter([b[0]!, b[1]!, b[2]!, b[3]!])
    const x = offset.x + c.x - (doc.scrollOffsetX ?? 0)
    const y = offset.y + c.y - (doc.scrollOffsetY ?? 0)
    if (x <= 0 || x > viewport.width || y <= 0 || y > viewport.height) continue
    texts.push({ t: t.trim(), x: Math.round(x), y: Math.round(y) })
  }
  // 按钮同排窄带的姓名区内取姓名模式节点（x 最小优先——姓名是该行最左元素）。
  // 不依赖任何状态文本；找不到返回 null（fail-safe，绝不拿学历/状态充当姓名）
  let best: { t: string; x: number } | null = null
  for (const n of texts) {
    if (!NAME_PATTERN.test(n.t)) continue
    if (STATUS_WORD_SUFFIX.test(n.t)) continue
    if (Math.abs(n.y - btn.point.y) >= NAME_ROW_MAX_DY) continue
    if (n.x >= btn.point.x * NAME_COL_RELATIVE_MAX || n.x >= btn.point.x) continue
    if (!best || n.x < best.x) best = { t: n.t, x: n.x }
  }
  return best?.t ?? null
}

/**
 * 视口内全部指定文案按钮（trim 后精确相等），按 y 从上到下排序（含所在文档序号，配对姓名用）。
 * 从 GreetExecutor 私有方法上移共享（2026-09-01）：doctor 配对自检与 greet 共用同一实现。
 * 坑修复（2026-08-27）：原实现 findIndex 只取第一个匹配的 string 下标——strings 表中同文案
 * 可出现多个下标（不同节点分别 intern），只认第一个会漏掉其余按钮；现遍历全部下标。
 */
export function findButtonsByExactText(snap: DomSnapshot, text: string): GreetButtonRef[] {
  const viewport = viewportOf(snap)
  const points: GreetButtonRef[] = []
  snap.strings.forEach((s, stringIndex) => {
    if (s.trim() !== text) return
    snap.documents.forEach((document, documentIndex) => {
      for (const { bounds } of findNodesByString(document, stringIndex)) {
        if (bounds[2] <= 0 || bounds[3] <= 0) continue
        // bounds 是文档绝对坐标（不随滚动变化，真机实测：滚动后 bounds 不动、scrollOffsetY 变），
        // 屏幕坐标 = owner 偏移 + bounds - 文档滚动偏移；只收视口内的：视口外的按钮 Win32 点不到
        const offset = accumulateOwnerOffset(snap, documentIndex)
        const c = boundsCenter(bounds)
        const x = offset.x + c.x - (document.scrollOffsetX ?? 0)
        const y = offset.y + c.y - (document.scrollOffsetY ?? 0)
        if (x < 0 || y < 0 || x > viewport.width || y > viewport.height) continue
        points.push({ point: { x, y }, documentIndex })
      }
    })
  })
  return points.sort((a, b) => a.point.y - b.point.y)
}
