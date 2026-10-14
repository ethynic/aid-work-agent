import type { AskForm, AskFormQuestion, RunnerAnswerItem } from '@/types'

/** 服务端跳过文案（与 src/services/agent_runner/ask_form.py SKIP_TEXT 同源）：
 *  仅在 capabilities 未声明 ask_structured_answers 的降级窗口由客户端生成。 */
export const ASK_SKIP_TEXT = '（用户跳过了本次提问，请按已知信息继续）'
/** 未答题占位（与服务端 UNANSWERED_TEXT 同源）。 */
export const ASK_UNANSWERED_TEXT = '（未回答）'

/**
 * 客户端 canonical 文本生成器（灰度/回滚窗口专用，设计 §8）：仅当会话启动的
 * capabilities 未声明 `ask_structured_answers` 时，把提问卡片答案降级为普通
 * 文本 reply 提交；服务端支持结构化时一律提交 answers，canonical 由服务端
 * 生成（设计 §4.2），本函数不会被调用。格式与服务端 ask_form.canonical_text
 * 对齐：按题序输出全部题，选项写 label 多选「、」，自定义以「；自定义：」前缀。
 */
export function clientCanonicalText(form: AskForm, answers: RunnerAnswerItem[], skip = false): string {
  if (skip) return ASK_SKIP_TEXT
  const byId = new Map(answers.map(item => [item.question_id.toLowerCase(), item]))
  return form.questions.map((question, index) => {
    const answer = byId.get(question.id.toLowerCase())
    const label = question.tag || question.prompt || `问题${index + 1}`
    const parts: string[] = []
    if (question.type !== 'text') {
      for (const key of answer?.selected_keys || []) {
        const option = question.options?.find(item => item.key === key)
        parts.push(option?.label || key)
      }
    }
    const text = answer?.text?.trim() || ''
    const head = `Q${index + 1}【${label}】`
    if (!parts.length && !text) return head + ASK_UNANSWERED_TEXT
    return head + parts.join('、') + (text ? (parts.length ? '；自定义：' + text : text) : '')
  }).join('\n')
}

/**
 * required 前端预检（设计 §7.1：仅即时反馈，服务端权威校验一次完成）：
 * 必答题至少选一项或填自定义内容（text 题即正文）。
 */
export function requiredUnmet(question: AskFormQuestion, selected: string[], text: string): string | null {
  if (!question.required) return null
  const hasSelection = question.type !== 'text' && selected.length > 0
  return hasSelection || !!text.trim() ? null : '至少选一项或填自定义内容'
}

/**
 * 组装结构化 answers（设计 §4.2）：未答题省略（省略=未答，判定表遍历原 form 全部题）；
 * text 题不携带选项 key；允许只选、只输入或两者并存（UI 不做互斥清空）。
 */
export function buildAskAnswers(form: AskForm, selections: Record<string, string[]>, drafts: Record<string, string>): RunnerAnswerItem[] {
  const answers: RunnerAnswerItem[] = []
  for (const question of form.questions) {
    const selected = question.type === 'text' ? [] : [...(selections[question.id] || [])]
    const text = (drafts[question.id] || '').trim()
    if (!selected.length && !text) continue
    answers.push({ question_id: question.id, selected_keys: selected, text: text || null })
  }
  return answers
}
