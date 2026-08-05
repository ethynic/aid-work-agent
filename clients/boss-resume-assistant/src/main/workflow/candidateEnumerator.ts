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
 * 禁用子串（真机推荐页实际语料校准，2026-08-04 scripts/probe-snapshot-strings.mjs）：
 * 命中任何一个即非姓名。中文姓名不会包含这些 bigram（活跃/牛人/管理/沟通…），
 * 子串匹配比精确黑名单更能覆盖 UI 文案变体（今日活跃/刚刚活跃/新牛人/精选牛人/牛人管理…）。
 */
const BANNED_SUBSTRINGS = [
  '活跃', '牛人', '权益', '设置', '优惠', '邀请', '道具', '客服', '中心', '规范',
  '数据', '管理', '沟通', '精选', '推荐', '搜索', '筛选', '面试', '面议', '至今',
  '互动', '优势', '期望', '意向', '保存', '取消', '更多', '最新', '关注', '调整',
  '退出', '账号', '直豆', '工具', '要求', '任务', '限时', '职位', '消息', '首页',
  '全部', '在线', '刚刚', '今天', '昨天', '查看', '收起', '展开', '已读', '未读',
  '回复', '发送', '确认', '确定', '关闭', '加载', '换一', '招呼', '合适', '继续',
  '学历', '毕业', '离职', '在职', '本科', '硕士', '博士', '大专', '高中', '中专',
  '在校', '应届', '实习', '不限', '个人', '公司', '清除', '发布', '基础', '经验',
]

/**
 * 候选人卡片结构常量（真机行结构 dump 校准，2026-08-04 scripts/probe-card-rows.mjs）：
 * - 卡片列表在左侧栏：姓名 x≈225（左导航 x=72、右侧详情面板 x≥900 均排除）
 * - 姓名行的下一行（约 +40px）同列必有年龄文本「\d+岁」——这是候选人卡片的独有特征，
 *   城市（上海）、行业标签（动画/网络安全）等字段值下方没有年龄行，可精确区分
 */
const LIST_COL_MIN_X = 80 // fixture x=100 与真机 x=225 均通过；左导航 x=72 被此排除
const LIST_COL_MAX_X = 700
const AGE_SIGNAL = /^\d+岁$/
const AGE_WINDOW_DOWN = 65 // 年龄行在姓名行下方约 40px，留余量
const AGE_COL_TOLERANCE = 30 // 年龄与姓名同列（真机同为 x=225）

/**
 * 候选人卡片行带信号（已被下方年龄行规则取代，仅保留作兼容注释）：
 * 真机事故（2026-08-04）：±30px 行带 + 学历/年限信号过宽，「上海」等城市字段
 * 的行带里混入右侧详情面板的「本科」导致误枚举。改用下方年龄行规则。
 */

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
  if (BANNED_SUBSTRINGS.some((b) => t.includes(b))) return false
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
      // 卡片列表左列校验：排除左导航（x=72）与右侧详情面板（x≥900）里的同形态文本
      if (vx < LIST_COL_MIN_X || vx > LIST_COL_MAX_X) continue

      // 结构信号：姓名下方窗口内同列必须有年龄行「\d+岁」（候选人卡片独有）
      const nameTop = node.bounds[1]
      const hasAgeRow = nodes.some(
        (n) =>
          AGE_SIGNAL.test(n.text) &&
          n.bounds[1] > nameTop &&
          n.bounds[1] <= nameTop + AGE_WINDOW_DOWN &&
          Math.abs(n.bounds[0] - node.bounds[0]) <= AGE_COL_TOLERANCE,
      )
      if (!hasAgeRow) continue

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
