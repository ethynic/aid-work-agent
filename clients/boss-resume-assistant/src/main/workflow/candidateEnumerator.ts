/**
 * 推荐列表候选人枚举器（Phase 8 编排层启发式）。
 *
 * 背景：Phase 3 的 ListSnapshotParser 只提供"按姓名定位"（locateUniqueCandidate），
 * 批量循环需要一个"当前列表上有哪些候选人"的枚举入口。本模块是编排层的保守启发式：
 *
 * - 候选人姓名 = 有可见布局的独立文本节点，trim 后为 2~4 个中文/间隔号字符
 * - 排除招聘写动作文案（WRITE_ACTION_TEXT）和常见 UI 词（UI_WORDS）
 * - 只保留点击点落在视口安全区内的节点（复用 ListSnapshotParser 的安全区 inset）
 * - 同一姓名去重（同名歧义交给 locateUniqueCandidate → UNLOCATABLE → PAUSED，不猜）
 *
 * 误判处理原则（fail-loud）：枚举只是候选集，真正点击前仍走 locateUniqueCandidate
 * 的唯一性+安全区校验；任何无法唯一定位的候选人都会让会话 PAUSED 等人工处理，
 * 绝不因启发式误枚举而盲点。
 */
import {
  type DomSnapshot,
  type DomDocument,
  indexedValues,
  accumulateOwnerOffset,
  boundsCenter,
} from '../boss/domSnapshot.js'
import { WRITE_ACTION_TEXT, SAFE_INSET_TOP, SAFE_INSET_BOTTOM } from '../boss/ListSnapshotParser.js'

/** 中文姓名模式：2~4 个汉字，允许间隔号（少数民族/外文译名） */
const NAME_PATTERN = /^[一-龥·]{2,4}$/

/**
 * 常见 UI 词黑名单：长度落在 2~4 汉字的非姓名界面文案。
 * 宁多勿少：误枚举的非姓名词若唯一可见，点击会落在该文本中心（可能误点标签），
 * 因此这里尽量列全；真机门禁阶段需人工核对首批枚举结果。
 */
const UI_WORDS = new Set([
  '推荐', '精选', '牛人', '搜索', '筛选', '消息', '职位', '公司', '我的', '首页',
  '全部', '最新', '活跃', '在线', '刚刚', '今天', '昨天', '查看', '更多', '收起',
  '展开', '关注', '已读', '未读', '回复', '发送', '取消', '确认', '确定', '关闭',
  '加载中', '下一页', '上一页', '换一批', '打招呼', '不合适', '继续沟通',
  '期望薪资', '工作年限', '学历要求', '应届毕业生', '离职', '在职',
])

export interface EnumeratedCandidate {
  name: string
  /** 同一 document 内与姓名节点纵向相邻（卡片行带内）的其它文本，作列表摘要 */
  bandText: string[]
}

export interface EnumerateOptions {
  viewport: { width: number; height: number }
}

/** 文本节点是否疑似候选人姓名 */
export function looksLikeCandidateName(text: string): boolean {
  const t = text.trim()
  if (!NAME_PATTERN.test(t)) return false
  if (WRITE_ACTION_TEXT.test(t)) return false
  if (UI_WORDS.has(t)) return false
  return true
}

/** 收集某 document 内所有有布局的文本节点 */
function collectTextNodes(
  snapshot: DomSnapshot,
  document: DomDocument,
  documentIndex: number,
): Array<{ text: string; bounds: [number, number, number, number] }> {
  const out: Array<{ text: string; bounds: [number, number, number, number] }> = []
  for (const [nodeIndex, stringIndex] of indexedValues(document.nodes.nodeValue, 'nodeValue')) {
    const text = snapshot.strings[stringIndex]
    if (typeof text !== 'string' || !text.trim()) continue
    const layoutIndex = document.layout.nodeIndex.indexOf(nodeIndex)
    if (layoutIndex < 0) continue
    const b = document.layout.bounds[layoutIndex]
    if (!b || b.length !== 4) continue
    const bounds: [number, number, number, number] = [b[0]!, b[1]!, b[2]!, b[3]!]
    if (bounds[2] <= 0 || bounds[3] <= 0) continue
    out.push({ text: text.trim(), bounds })
  }
  return out
}

/**
 * 枚举当前 snapshot 中疑似候选人的姓名列表（按纵向位置排序，去重）。
 * bandText：与姓名节点同属一个纵向行带（上下各扩 30px）的同 document 文本。
 */
export function enumerateCandidates(snapshot: DomSnapshot, opts: EnumerateOptions): EnumeratedCandidate[] {
  const seen = new Set<string>()
  const result: EnumeratedCandidate[] = []

  snapshot.documents.forEach((document, documentIndex) => {
    // 根 document（0）通常是外壳；候选人内容在推荐 frame 内，但也不排除平铺结构，全部扫描
    let ownerOffset = { x: 0, y: 0 }
    if (documentIndex !== 0) {
      try {
        ownerOffset = accumulateOwnerOffset(snapshot, documentIndex)
      } catch {
        return // owner 不唯一/无布局：该 document 不可定位，跳过（点击阶段会 UNLOCATABLE）
      }
    }
    const nodes = collectTextNodes(snapshot, document, documentIndex)

    for (const node of nodes) {
      if (!looksLikeCandidateName(node.text)) continue
      if (seen.has(node.text)) continue

      // 视口安全区校验（与 ListSnapshotParser 同一 inset 口径）
      const center = boundsCenter(node.bounds)
      const vx = ownerOffset.x + center.x
      const vy = ownerOffset.y + center.y
      if (vy < SAFE_INSET_TOP || vy > opts.viewport.height - SAFE_INSET_BOTTOM) continue
      if (vx < 0 || vx > opts.viewport.width) continue

      seen.add(node.text)
      const bandTop = node.bounds[1] - 30
      const bandBottom = node.bounds[1] + node.bounds[3] + 30
      const bandText = nodes
        .filter((n) => n.text !== node.text && n.bounds[1] + n.bounds[3] >= bandTop && n.bounds[1] <= bandBottom)
        .map((n) => n.text)
      result.push({ name: node.text, bandText })
    }
  })

  return result
}
