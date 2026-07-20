import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import HumanAssistanceCard from '@/components/browser/HumanAssistanceCard.vue'

vi.mock('@/api/agent', async () => {
  const original = await vi.importActual<any>('@/api/agent')
  return {
    ...original,
    browserApi: {
      takeControl: vi.fn().mockResolvedValue({ success: true }),
      complete: vi.fn().mockResolvedValue({ success: true, continuation_id: 'bac_1' }),
      extend: vi.fn().mockResolvedValue({ success: true, expires_at: '2099-01-01T00:00:00Z' }),
      cancel: vi.fn().mockResolvedValue({ success: true }),
      viewTicket: vi.fn().mockRejectedValue(new Error('offline')),
      continuationEvents: vi.fn().mockResolvedValue({ events: [], last_seq: 0 }),
      viewWebSocketUrl: vi.fn().mockReturnValue('ws://localhost/view'),
    },
  }
})

describe('HumanAssistanceCard', () => {
  const assistance = {
    assistance_id: 'bha_1', run_id: `br_${'a'.repeat(32)}`, continuation_id: 'bac_1',
    reason_code: 'CAPTCHA_REQUIRED', surface: 'server_web' as const,
    title: '请完成页面验证',
    steps: ['点击“开始接管”', '直接在浏览器画面中完成验证码', '完成后继续'],
    completion_mode: 'auto_or_confirm' as const, completion_status: 'waiting',
    expires_at: '2099-01-01T00:00:00Z', state: 'pending' as const,
  }

  it('renders structured safe instructions and lease', () => {
    const wrapper = mount(HumanAssistanceCard, { props: { assistance, authHeaders: {} } })
    expect(wrapper.text()).toContain('浏览器任务已暂停')
    expect(wrapper.text()).toContain('请完成页面验证')
    expect(wrapper.text()).toContain('开始接管')
    expect(wrapper.text()).toContain('画面不会保存到服务器')
  })

  it('switches to controlling after take control', async () => {
    const wrapper = mount(HumanAssistanceCard, { props: { assistance, authHeaders: {} } })
    await wrapper.get('button').trigger('click')
    expect(wrapper.emitted('updated')?.[0]?.[0]).toMatchObject({ state: 'controlling' })
  })
})
