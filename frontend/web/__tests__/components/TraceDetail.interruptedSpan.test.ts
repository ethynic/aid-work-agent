import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import TraceDetail from '@/components/saas/TraceDetail.vue'
import { getTraceDetail } from '@/api/monitor'

vi.mock('vue-router', () => ({ useRoute: () => ({ params: { trace_id: 'fixture' } }), useRouter: () => ({ back: vi.fn() }) }))
vi.mock('@/api/monitor', () => ({ getTraceDetail: vi.fn() }))

describe('Trace timeline status rendering', () => {
  it.each([
    ['interrupted', '已中断'], ['waiting', '等待中'], ['paused', '已暂停'], ['cancelled', '已取消'],
  ])('never shows an unfinished %s tool as a green successful span', async (status, label) => {
    vi.mocked(getTraceDetail).mockResolvedValue({ success: true,
      trace: { trace_id: 'fixture', status, tags: [], total_tokens: 0, duration_ms: 1 } as any,
      spans: [{ span_id: 'fixture-tool', name: 'boss_resume_next', span_type: 'tool', status, duration_ms: 1 } as any],
    })
    const wrapper = mount(TraceDetail, { global: { stubs: { FullscreenJsonViewer: true } } })
    await flushPromises()
    const dot = wrapper.get('.rounded-full.inline-block')
    expect(dot.attributes('title')).toBe(label)
    expect(dot.classes()).toContain('bg-warning-500')
    expect(wrapper.findAll('.bg-success-500')).toHaveLength(0)
    expect(wrapper.text()).toContain(label)
    wrapper.unmount()
  })
})
