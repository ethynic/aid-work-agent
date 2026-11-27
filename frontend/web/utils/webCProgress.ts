/**
 * webC 过程状态克制呈现（2026-11-25，官网访客小屏）。
 *
 * 数据来源：Runner 快照 snapshot.progress（durable_control 只保留最新一条
 * progress 事件文本，public_view 口径透出）。呈现原则：单行替代式更新、
 * 正文出现即让位、失败不外显（保持中性，细节进 console）。
 */

/** 过滤失败/澄清类的标记：失败保持中性不展示；澄清由 waitingText 呈现 */
const HIDDEN_MARKS = /❌|失败|澄清/

/** Runner 过程文案前缀的 emoji（🚀📋📦✅ 等）+ 变体选择符/零宽连接符 */
const LEADING_EMOJI = /^[\p{Extended_Pictographic}\uFE0F\u200D\s]+/u

/** 单行截断上限：防止工具结果 preview 把小屏单行状态撑爆 */
const MAX_LENGTH = 40

/** 过程状态克制化：只透出访客能理解的单行动词短语，非文本/空串/含隐藏标记返回空串 */
export function restrainedProgress(raw: unknown): string {
  if (typeof raw !== 'string') return ''
  const text = raw.trim()
  if (!text || HIDDEN_MARKS.test(text)) return ''
  const stripped = text.replace(LEADING_EMOJI, '')
  return stripped.length > MAX_LENGTH ? stripped.slice(0, MAX_LENGTH) + '…' : stripped
}
