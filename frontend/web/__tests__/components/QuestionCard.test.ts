import { beforeEach, describe, expect, it, vi } from 'vitest'
import { computed, reactive, shallowReactive } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import QuestionCard from '@/components/chat/QuestionCard.vue'
import type { AskForm } from '@/types'
import type { AskSubmissionState } from '@/composables/useAgent'

/** useAgent mock：提交记录 + 响应式提交状态（模拟 askSubmissions 注册表与 controlViews 观察） */
const holder = vi.hoisted(() => ({
  map: undefined as unknown as Map<string, AskSubmissionState>,
  controls: undefined as unknown as Map<string, { status: string; error_code?: string | null }>,
  submit: undefined as unknown as ReturnType<typeof vi.fn>,
}))
vi.mock('@/composables/useAgent', () => ({
  useAgent: () => ({
    submitAskAnswers: holder.submit,
    askSubmissionFor: (runnerId: string, waitId: string) => computed(() => holder.map?.get(runnerId + ':' + waitId)),
    askSubmissionForControl: (controlId: string) => computed(() => {
      for (const item of holder.map?.values() || []) if (item.controlId === controlId) return item
      return undefined
    }),
    askControlView: (controlId: string) => computed(() => holder.controls?.get(controlId)),
  }),
}))

const form: AskForm = {
  form_id: 'frm_1', wait_id: 'wait-1', title: '归档范围', intro: '除成本治理外，以下开发已完成',
  allow_skip: true, expires_at: null,
  questions: [
    { id: 'q1', tag: '归档范围', prompt: '以下哪些任务移入 ideas_finished.md？', type: 'multi', required: true,
      allow_custom_text: true, custom_placeholder: '输入自定义内容…', max_select: 2,
      options: [
        { key: 'q1o1', label: 'queue_order 序列自愈', description: '代码+测试+CR 完成' },
        { key: 'q1o2', label: 'wecom_kf 限频修复' },
        { key: 'q1o3', label: 'file_assets 迁移' },
      ] },
    { id: 'q2', tag: '后续独立事项', prompt: '哪些事项单独立任务？', type: 'single', required: false,
      options: [{ key: 'q2o1', label: '飞书钉钉接入' }, { key: 'q2o2', label: '暂缓' }] },
  ],
}

const binding = {
  runnerId: 'runner-A', waitId: 'wait-1', targetExecutionId: 'exec-1', form, terminal: false,
}

function mountCard(overrides: Record<string, unknown> = {}) {
  return mount(QuestionCard, { props: { card: { ...binding }, authHeaders: {}, ...overrides } })
}

beforeEach(() => {
  holder.map = reactive(new Map())
  // 与生产一致：controlViews 为 shallowReactive（回执 set() 触发 computed 重算）
  holder.controls = shallowReactive(new Map())
  holder.submit = vi.fn().mockResolvedValue('accepted')
})

describe('QuestionCard rendering (设计 §7.1)', () => {
  it('renders title/intro, tag chip, option label+description, and strict input types per question', async () => {
    const wrapper = mountCard()
    expect(wrapper.find('[data-test="question-card"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('归档范围')
    expect(wrapper.text()).toContain('除成本治理外，以下开发已完成')
    expect(wrapper.text()).toContain('queue_order 序列自愈')
    expect(wrapper.text()).toContain('代码+测试+CR 完成')
    const checkboxes = wrapper.findAll('input[type="checkbox"]')
    expect(checkboxes.map(item => item.attributes('data-test'))).toEqual(['option-q1o1', 'option-q1o2', 'option-q1o3'])
    // 分页后第二题是 single → radio
    await wrapper.find('[data-test="next-page"]').trigger('click')
    expect(wrapper.findAll('input[type="radio"]').map(item => item.attributes('data-test'))).toEqual(['option-q2o1', 'option-q2o2'])
  })

  it('hides the skip button when allow_skip=false and shows it otherwise', () => {
    expect(mountCard().find('[data-test="skip-btn"]').exists()).toBe(true)
    const noSkip = mountCard({ card: { ...binding, form: { ...form, allow_skip: false } } })
    expect(noSkip.find('[data-test="skip-btn"]').exists()).toBe(false)
  })

  it('pages multi-question forms with ‹ 1/N › and disables out-of-range paging', async () => {
    const wrapper = mountCard()
    expect(wrapper.find('[data-test="page-indicator"]').text()).toBe('1 / 2')
    expect((wrapper.find('[data-test="prev-page"]').element as HTMLButtonElement).disabled).toBe(true)
    await wrapper.find('[data-test="next-page"]').trigger('click')
    expect(wrapper.find('[data-test="page-indicator"]').text()).toBe('2 / 2')
    expect((wrapper.find('[data-test="next-page"]').element as HTMLButtonElement).disabled).toBe(true)
  })
})

describe('QuestionCard interactions', () => {
  it('single selection replaces, multi caps at max_select, and custom input coexists with selections', async () => {
    const wrapper = mountCard()
    // 完全受控勾选（click.prevent）：视觉勾选由组件状态驱动
    await wrapper.find('[data-test="option-q1o1"]').trigger('click')
    await wrapper.find('[data-test="option-q1o2"]').trigger('click')
    await wrapper.vm.$nextTick()
    const checked = () => wrapper.findAll('input[type="checkbox"]:checked').map(item => item.attributes('data-test'))
    expect(checked()).toEqual(['option-q1o1', 'option-q1o2'])
    // max_select=2：第三项点击被忽略，且不会误显示为已选
    await wrapper.find('[data-test="option-q1o3"]').trigger('click')
    await wrapper.vm.$nextTick()
    expect(checked()).toEqual(['option-q1o1', 'option-q1o2'])
    // 取消勾选 q1o1 后腾出名额，可再次勾选
    await wrapper.find('[data-test="option-q1o1"]').trigger('click')
    await wrapper.vm.$nextTick()
    expect(checked()).toEqual(['option-q1o2'])
    await wrapper.find('[data-test="option-q1o1"]').trigger('click')
    await wrapper.vm.$nextTick()
    expect(checked()).toEqual(['option-q1o1', 'option-q1o2'])
    // 自定义输入不清空已选（并存不互斥）
    await wrapper.find('[data-test="custom-input"]').setValue('file_assets 建议观察一周')
    expect(checked()).toEqual(['option-q1o1', 'option-q1o2'])
    await wrapper.find('[data-test="submit-btn"]').trigger('click')
    await flushPromises()
    expect(holder.submit).toHaveBeenCalledWith(expect.objectContaining({
      runnerId: 'runner-A', waitId: 'wait-1', targetExecutionId: 'exec-1',
      answers: [
        { question_id: 'q1', selected_keys: ['q1o2', 'q1o1'], text: 'file_assets 建议观察一周' },
      ],
    }))
  })

  it('single question submits exactly one selected key and skip sends skip=true without answers', async () => {
    const wrapper = mountCard()
    // 先满足 q1（required）预检，再翻页验证 single radio 的替换语义
    await wrapper.find('[data-test="option-q1o1"]').trigger('click')
    await wrapper.find('[data-test="next-page"]').trigger('click')
    await wrapper.find('[data-test="option-q2o1"]').trigger('click')
    await wrapper.find('[data-test="option-q2o2"]').trigger('click')
    await wrapper.vm.$nextTick()
    const radios = () => wrapper.findAll('input[type="radio"]:checked').map(item => item.attributes('data-test'))
    expect(radios()).toEqual(['option-q2o2'])
    await wrapper.find('[data-test="submit-btn"]').trigger('click')
    await flushPromises()
    expect(holder.submit).toHaveBeenCalledWith(expect.objectContaining({
      answers: [
        { question_id: 'q1', selected_keys: ['q1o1'], text: null },
        { question_id: 'q2', selected_keys: ['q2o2'], text: null },
      ],
    }))
    holder.submit.mockClear()
    await wrapper.find('[data-test="skip-btn"]').trigger('click')
    await flushPromises()
    expect(holder.submit).toHaveBeenCalledTimes(1)
    const payload = holder.submit.mock.calls[0][0] as Record<string, unknown>
    expect(payload.skip).toBe(true)
    expect('answers' in payload).toBe(false)
    expect(payload.waitId).toBe('wait-1')
  })

  it('required pre-check blocks submit with an inline hint and jumps to the offending page, then clears once satisfied', async () => {
    const wrapper = mountCard()
    await wrapper.find('[data-test="next-page"]').trigger('click')
    await wrapper.find('[data-test="option-q2o1"]').setValue(true)
    await wrapper.find('[data-test="submit-btn"]').trigger('click')
    await wrapper.vm.$nextTick()
    // 预检失败：定位回首题并即时提示，不发起提交
    expect(wrapper.find('[data-test="page-indicator"]').text()).toBe('1 / 2')
    expect(wrapper.find('[data-test="question-error"]').text()).toBe('至少选一项或填自定义内容')
    expect(holder.submit).not.toHaveBeenCalled()
    await wrapper.find('[data-test="option-q1o1"]').trigger('click')
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-test="question-error"]').exists()).toBe(false)
  })
})

describe('QuestionCard state machine (设计 §5.7)', () => {
  it('disables buttons while submitting and shows accepted state with 已提交，等待应用', async () => {
    holder.map.set('runner-A:wait-1', { status: 'submitting' })
    const wrapper = mountCard()
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-test="card-status"]').text()).toBe('提交中…')
    expect((wrapper.find('[data-test="submit-btn"]').element as HTMLButtonElement).disabled).toBe(true)
    expect((wrapper.find('[data-test="skip-btn"]').element as HTMLButtonElement).disabled).toBe(true)
    // 受理：按钮保持禁用，状态徽标切换（快照仍带该 wait 时的过渡态）
    holder.map.set('runner-A:wait-1', { status: 'accepted', controlId: 'control-1' })
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-test="card-status"]').text()).toBe('已提交，等待应用')
    expect((wrapper.find('[data-test="submit-btn"]').element as HTMLButtonElement).disabled).toBe(true)
    expect((wrapper.find('[data-test="option-q1o1"]').element as HTMLInputElement).disabled).toBe(true)
  })

  it('highlights 422 errors per question and surfaces question_id=form errors as the card banner', async () => {
    const wrapper = mountCard()
    holder.map.set('runner-A:wait-1', { status: 'error', errorCode: 'FORM_ANSWER_INVALID', errors: [
      { question_id: 'q1', code: 'FORM_ANSWER_INVALID', message: '该题为必答' },
      { question_id: 'form', code: 'FORM_SKIP_NOT_ALLOWED', message: '该表单不允许整体跳过' },
    ] })
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-test="question-error"]').text()).toBe('该题为必答')
    expect(wrapper.find('[data-test="error-banner"]').text()).toContain('该表单不允许整体跳过')
    // 校验失败后仍可修正重提（无专门键管理）
    expect((wrapper.find('[data-test="submit-btn"]').element as HTMLButtonElement).disabled).toBe(false)
  })

  it('stale 409 shows a refresh notice without resubmitting automatically, closed codes lock the card', async () => {
    const wrapper = mountCard()
    holder.map.set('runner-A:wait-1', { status: 'error', errorCode: 'CONTROL_WAIT_STALE' })
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-test="error-banner"]').text()).toContain('状态已更新')
    expect(holder.submit).not.toHaveBeenCalled()
    expect((wrapper.find('[data-test="submit-btn"]').element as HTMLButtonElement).disabled).toBe(false)

    holder.map.set('runner-A:wait-1', { status: 'error', errorCode: 'CONTROL_EXECUTION_CLOSED' })
    await wrapper.vm.$nextTick()
    expect((wrapper.find('[data-test="submit-btn"]').element as HTMLButtonElement).disabled).toBe(true)
    expect(wrapper.find('[data-test="error-banner"]').text()).toContain('任务已关闭')
  })

  it('terminal runners lock the card with a read-only annotation', async () => {
    const wrapper = mountCard({ card: { ...binding, terminal: true } })
    expect(wrapper.find('[data-test="card-status"]').text()).toBe('任务已结束')
    expect(wrapper.find('[data-test="error-banner"]').text()).toContain('仅作回显')
    expect((wrapper.find('[data-test="submit-btn"]').element as HTMLButtonElement).disabled).toBe(true)
    expect(wrapper.find('[data-test="custom-input"]').attributes('disabled')).toBeDefined()
  })

  it('RUNNER_RESUME_PENDING blocks resubmission until the snapshot clears the conflict', async () => {
    const wrapper = mountCard()
    holder.map.set('runner-A:wait-1', { status: 'error', errorCode: 'RUNNER_RESUME_PENDING' })
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-test="error-banner"]').text()).toContain('已有提交正在等待应用')
    expect((wrapper.find('[data-test="submit-btn"]').element as HTMLButtonElement).disabled).toBe(true)
    // 快照事实解除冲突（评审 P2）：wait 仍有效且无在途受理 control → 恢复可编辑
    holder.map.set('runner-A:wait-1', { status: 'error', errorCode: 'RUNNER_RESUME_PENDING', conflictCleared: true })
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-test="error-banner"]').text()).toContain('另一端提交未生效')
    expect((wrapper.find('[data-test="submit-btn"]').element as HTMLButtonElement).disabled).toBe(false)
    expect((wrapper.find('[data-test="option-q1o1"]').element as HTMLInputElement).disabled).toBe(false)
  })
})

describe('QuestionCard read-only answered echo (设计 §7.1 历史投影)', () => {
  const answered = {
    controlId: 'control-1', clientRequestId: 'key-1', acceptedAt: '2026-10-14T10:01:00Z',
    form, answers: [{ question_id: 'q1', selected_keys: ['q1o1'], text: '自定义说明' }], terminal: true,
  }

  it('renders question texts, chosen answers, custom text and answer time without any submit controls', () => {
    const wrapper = mount(QuestionCard, { props: { answered } })
    expect(wrapper.find('[data-test="answered-card"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('Q1【归档范围】以下哪些任务移入 ideas_finished.md？')
    expect(wrapper.text()).toContain('自定义：自定义说明')
    expect(wrapper.text()).toContain('（未回答）') // q2 未答
    // 终态而无 control 事实不得推定已应用（评审 P2）
    expect(wrapper.find('[data-test="answered-status"]').text()).toBe('已提交（任务已结束）')
    expect(wrapper.text()).toMatch(/\d{2}-\d{2} \d{2}:\d{2}:\d{2}/) // 答复时间
    expect(wrapper.find('[data-test="submit-btn"]').exists()).toBe(false)
  })

  it('labels terminal cards from the queried control fact, updating in place when the receipt arrives', async () => {
    // 评审 P2：同一挂载实例下 control 回执到达必须即时更新（生产 controlViews
    // 为 shallowReactive；不得用"先写状态再重挂载"掩盖非响应式实现）。
    const wrapper = mount(QuestionCard, { props: { answered } })
    expect(wrapper.find('[data-test="answered-status"]').text()).toBe('已提交（任务已结束）')
    holder.controls.set('control-1', { status: 'consumed' })
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-test="answered-status"]').text()).toBe('已应用')
    holder.controls.set('control-1', { status: 'rejected', error_code: 'CONTROL_WAIT_STALE' })
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-test="answered-status"]').text()).toBe('已提交（未应用）')
    holder.controls.set('control-1', { status: 'accepted' })
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-test="answered-status"]').text()).toBe('已提交')
  })

  it('distinguishes submitted vs applied via the observed control fact', async () => {
    holder.map.set('runner-A:wait-1', { status: 'accepted', controlId: 'control-1' })
    const wrapper = mount(QuestionCard, { props: { answered: { ...answered, terminal: false } } })
    expect(wrapper.find('[data-test="answered-status"]').text()).toBe('已提交')
    holder.map.set('runner-A:wait-1', { status: 'accepted', controlId: 'control-1', applied: true })
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-test="answered-status"]').text()).toBe('已应用')
    holder.map.set('runner-A:wait-1', { status: 'error', controlId: 'control-1', errorCode: 'RECOVERY_PROFILE_CHANGED' })
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-test="answered-status"]').text()).toBe('已提交（未应用）')
  })
})
