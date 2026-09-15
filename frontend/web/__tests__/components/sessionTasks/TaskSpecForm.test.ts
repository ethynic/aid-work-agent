import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import TaskSpecForm from '@/components/sessionTasks/TaskSpecForm.vue'
import { phaseLabel } from '@/components/sessionTasks/presentation'
import type { SessionTask } from '@/api/sessionTasks'

function setup() {
  const wrapper = mount(TaskSpecForm)
  const vm = wrapper.vm as unknown as { form: Record<string, any>; getSpec: () => any }
  Object.assign(vm.form, { goal: '确认需求', expires: '2099-01-01T12:00' })
  return { wrapper, vm }
}
describe('会话任务授权表单', () => {
  it('三种完成模式生成互斥契约且始终携带预算与带时区截止时间', () => {
    const { vm } = setup()
    expect(vm.getSpec().completion_rule).toEqual({ mode: 'rounds', rounds_target: 3 })
    vm.form.mode = 'judged'; vm.form.criteria = '客户明确需求\n客户确认时间'
    expect(vm.getSpec().completion_rule).toEqual({ mode: 'judged', criteria: ['客户明确需求', '客户确认时间'] })
    vm.form.mode = 'peer_confirmed'; vm.form.fields[0].question = '是否确认'
    expect(vm.getSpec().completion_rule).toEqual({ mode: 'peer_confirmed', require_all: true, fields: [{ key: 'confirmed', question: '是否确认', allowed_values: ['是', '否'], accepted_values: ['是'] }] })
    expect(vm.getSpec().limits.expires_at).toMatch(/Z$/)
    expect(vm.getSpec().limits.max_cost_units).toBe(100)
  })
  it('拒绝超出发送预算、错误接受值和已过期截止时间', () => {
    const { vm } = setup()
    vm.form.replies = '3'; vm.form.opening = '你好'
    expect(() => vm.getSpec()).toThrow('不能超过')
    vm.form.mode = 'peer_confirmed'; vm.form.fields[0].question = '确认？'; vm.form.fields[0].accepted = '不在枚举'
    expect(() => vm.getSpec()).toThrow('不合法')
    vm.form.mode = 'rounds'; vm.form.replies = '5'; vm.form.expires = '2020-01-01T00:00'
    expect(() => vm.getSpec()).toThrow('截止时间')
  })
  it('区分客户、模型、桌面、同步和异常，预算停止不显示成功', () => {
    const task = { status: 'active' } as SessionTask
    for (const [phase, label] of Object.entries({ waiting_peer: '等客户', decision_pending: '等模型', send_ready: '等桌面', sync_pending: '同步中', unknown_send: '发送结果不明 · 需人工核对' })) expect(phaseLabel({ ...task, phase })).toBe(label)
    expect(phaseLabel({ ...task, blocked_reason: 'coverage_gap' })).toContain('观察缺口')
    expect(phaseLabel({ ...task, status: 'stopped', completion_reason: 'budget_exhausted' })).toContain('上限耗尽')
  })
})
