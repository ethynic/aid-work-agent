import { describe, expect, it } from 'vitest'
import { ASK_SKIP_TEXT, ASK_UNANSWERED_TEXT, buildAskAnswers, clientCanonicalText, requiredUnmet } from '@/utils/askForm'
import type { AskForm } from '@/types'

const form: AskForm = {
  form_id: 'frm_1', wait_id: 'wait-1', title: '归档范围', intro: '除成本治理外，以下开发已完成',
  allow_skip: true, expires_at: null,
  questions: [
    { id: 'q1', tag: '归档范围', prompt: '以下哪些任务移入 ideas_finished.md？', type: 'multi', required: true,
      allow_custom_text: true, custom_placeholder: '输入自定义内容…', max_select: 5,
      options: [
        { key: 'q1o1', label: 'queue_order 序列自愈', description: '代码+测试+CR 完成' },
        { key: 'q1o2', label: 'wecom_kf 限频修复', description: '修复限频问题' },
      ] },
    { id: 'q2', tag: '后续独立事项', prompt: '哪些事项单独立任务？', type: 'single', required: false,
      options: [{ key: 'q2o1', label: '飞书钉钉接入 AgentRunner' }, { key: 'q2o2', label: '暂缓' }] },
    { id: 'q3', tag: '补充说明', prompt: '还有什么要补充？', type: 'text', required: false, placeholder: '输入你的回答…' },
  ],
}

describe('client canonical fallback generator (灰度/回滚窗口专用)', () => {
  it('skip renders the server-agreed skip text verbatim', () => {
    expect(clientCanonicalText(form, [], true)).toBe(ASK_SKIP_TEXT)
  })

  it('renders every question in order with labels, custom prefix and unanswered placeholder', () => {
    const text = clientCanonicalText(form, [
      { question_id: 'q1', selected_keys: ['q1o1', 'q1o2'], text: 'file_assets 建议观察一周再归档' },
      { question_id: 'q2', selected_keys: ['q2o1'], text: null },
    ])
    expect(text).toBe([
      'Q1【归档范围】queue_order 序列自愈、wecom_kf 限频修复；自定义：file_assets 建议观察一周再归档',
      'Q2【后续独立事项】飞书钉钉接入 AgentRunner',
      'Q3【补充说明】' + ASK_UNANSWERED_TEXT,
    ].join('\n'))
  })

  it('text answers render bare text and unknown keys fall back to the raw key', () => {
    const text = clientCanonicalText(form, [
      { question_id: 'q3', selected_keys: [], text: '没有了' },
      { question_id: 'q2', selected_keys: ['q2oX'], text: null },
    ])
    expect(text).toBe([
      'Q1【归档范围】' + ASK_UNANSWERED_TEXT,
      'Q2【后续独立事项】q2oX',
      'Q3【补充说明】没有了',
    ].join('\n'))
  })
})

describe('buildAskAnswers', () => {
  it('omits unanswered questions, keeps selection and custom text coexisting, strips text-question keys', () => {
    const answers = buildAskAnswers(form, { q1: ['q1o2'], q2: [] }, { q1: ' 自定义说明 ', q3: '  ' })
    expect(answers).toEqual([
      { question_id: 'q1', selected_keys: ['q1o2'], text: '自定义说明' },
    ])
  })

  it('explicit empty answers is a legitimate all-unanswered submission', () => {
    expect(buildAskAnswers(form, {}, {})).toEqual([])
  })
})

describe('requiredUnmet (前端预检，仅即时反馈)', () => {
  it('requires a selection or custom text on required choice questions', () => {
    const q1 = form.questions[0]
    expect(requiredUnmet(q1, [], '')).toBe('至少选一项或填自定义内容')
    expect(requiredUnmet(q1, ['q1o1'], '')).toBeNull()
    expect(requiredUnmet(q1, [], '自定义')).toBeNull()
    expect(requiredUnmet(q1, ['q1o1'], '自定义')).toBeNull()
  })

  it('requires body text on required text questions and ignores optional questions', () => {
    expect(requiredUnmet({ ...form.questions[2], required: true }, [], ' ')).toBe('至少选一项或填自定义内容')
    expect(requiredUnmet({ ...form.questions[2], required: true }, [], '说明')).toBeNull()
    expect(requiredUnmet(form.questions[1], [], '')).toBeNull()
  })
})
