import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import DurableTaskStatus from '@/components/chat/DurableTaskStatus.vue'
import { runnerView } from '../mocks/runnerView'

const timer = { kind:'timer', wake_at:'2026-10-22T12:00:10Z', seconds:10, reason:'等待新消息', wait_id:'wait-1', target_execution_id:'leaf-1' }
describe('Durable task controls use server state', () => {
  it('shows deadline and actual settled credits with unknown usage marked pending', async () => {
    const wrapper = mount(DurableTaskStatus, { props: { busy:false, row:runnerView({ status:'waiting', execution_mode:'continuous',
      snapshot:{ waiting:{ kind:'child_wait', child_wait:timer } },
      usage:{ task_credits:1.25, session_credits:8, pending:true, updated_at:'2026-10-22T12:00:00Z' } }) } })
    expect(wrapper.text()).toContain('任务休眠中')
    expect(wrapper.get('time').attributes('datetime')).toBe(timer.wake_at)
    expect(wrapper.text()).toContain('1.25 积分')
    expect(wrapper.text()).toContain('8 积分')
    expect(wrapper.text()).toContain('部分用量待结算')
    await wrapper.findAll('button').find(button => button.text() === '立即继续')!.trigger('click')
    expect(wrapper.emitted('control')).toEqual([['resume']])
    wrapper.unmount()
  })
  it('retains a paused deadline without advertising automatic wake and allows explicit continuation', async () => {
    const wrapper = mount(DurableTaskStatus, { props: { busy:false, row:runnerView({ status:'paused', snapshot:{ waiting:timer } }) } })
    expect(wrapper.text()).toContain('任务已暂停')
    expect(wrapper.find('time').exists()).toBe(false)
    await wrapper.findAll('button').find(button => button.text() === '继续任务')!.trigger('click')
    expect(wrapper.emitted('control')).toEqual([['resume']])
    wrapper.unmount()
  })
  it('makes busy loop protection a user choice and disables repeated operations', () => {
    const wrapper = mount(DurableTaskStatus, { props: { busy:true, row:runnerView({ status:'paused', execution_mode:'continuous',
      snapshot:{ waiting:{ kind:'continuous_guard', wait_id:'guard-wait', target_execution_id:'root' } } }) } })
    expect(wrapper.text()).toContain('本段保护上限')
    expect(wrapper.findAll('button').every(button => button.attributes('disabled') !== undefined)).toBe(true)
    wrapper.unmount()
  })
  it('allows an explicit continuation of the identified child guard without promising an automatic wake', async () => {
    const wrapper = mount(DurableTaskStatus, { props: { busy:false, row:runnerView({ status:'waiting', execution_mode:'continuous',
      snapshot:{ waiting:{ kind:'child_wait', child_wait:{ kind:'continuous_guard', wait_id:'child-guard', target_execution_id:'leaf' } } } }) } })
    expect(wrapper.text()).toContain('连续执行已暂停')
    expect(wrapper.find('time').exists()).toBe(false)
    await wrapper.findAll('button').find(button => button.text() === '立即继续')!.trigger('click')
    expect(wrapper.emitted('control')).toEqual([['resume']])
    wrapper.unmount()
  })
  it('does not offer execution controls on terminal tasks or resume an unidentified timer', () => {
    const wrapper = mount(DurableTaskStatus, { props: { busy:false, row:runnerView({ status:'completed', execution_mode:'continuous' }) } })
    expect(wrapper.findAll('button')).toHaveLength(0)
    wrapper.unmount()
    const unidentified = mount(DurableTaskStatus, { props: { busy:false, row:runnerView({ status:'waiting', snapshot:{ waiting:{ kind:'timer' } } }) } })
    expect(unidentified.text()).not.toContain('立即继续')
    unidentified.unmount()
  })
})
