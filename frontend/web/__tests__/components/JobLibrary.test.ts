import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import JobLibrary from '@/components/recruiting/JobLibrary.vue'

vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }), useRoute: () => ({ path: '/recruiting-operator/jobs', params: {} }) }))
vi.mock('vue-toastification', () => ({ useToast: () => ({ success: vi.fn(), error: vi.fn() }) }))
vi.mock('@/api/recruitingOperator', () => ({
  MATCH_STATUS_LABELS: {}, createJob: vi.fn(), updateJob: vi.fn(),
  getRequirementOptions: vi.fn().mockResolvedValue({ success: false }),
  listJobs: vi.fn().mockResolvedValue({ success: true, data: { items: [{
    id: 'job-1', job_name: '工程师', status: 'active', match_threshold: 70, script_count: 0, categories: [], resume_count: 0, matched_count: 0,
    job_requirements: { experiences: ['26年毕业', '1年以内'], salary_range: { min: 15, max: 25 }, vip_filters: { gender: '男' }, keywords: ['评分词'] },
  }] } }),
}))

describe('JobLibrary 筛选摘要', () => {
  it('展示新普通范围、多选和 VIP 分组，不把评分字段当筛选条件', async () => {
    const wrapper = mount(JobLibrary)
    await flushPromises()
    expect(wrapper.text()).toContain('普通筛选：经验 26年毕业、1年以内、薪资 15–25K')
    expect(wrapper.text()).toContain('VIP 专享筛选：性别 男')
    expect(wrapper.text()).not.toContain('评分词')
    wrapper.unmount()
  })
})
