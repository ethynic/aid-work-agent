/**
 * JobDetail 组件挂载冒烟测试
 *
 * 职位管理拆分为「列表页 + 路由化详情页」后，验证详情页：
 * - 按路由 jobId 加载职位详情并渲染职位名
 * - 三个 tab（基本信息/沟通话术/关联简历）按计数渲染，默认落在基本信息
 * - 关联简历 tab 用 listResumes({ job_id }) 服务端筛选（job_id 强关联契约）
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

// 路由桩：demo 段职位详情路径
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() }),
  useRoute: () => ({ path: '/recruiting-operator/jobs/job-1', params: { jobId: 'job-1' } }),
}))

vi.mock('vue-toastification', () => ({ useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn() }) }))

const getJobMock = vi.fn()
const listResumesMock = vi.fn()

vi.mock('@/api/recruitingOperator', () => ({
  // recruitingDisplay（共享展示工具）运行时会用到
  MATCH_STATUS_LABELS: { matched: '匹配', unmatched: '接近', rejected: '不匹配' },
  JOB_SCRIPT_CATEGORIES: ['初次开场', '了解摸底', '追问细节', '邀约推进'],
  getJob: (...args: unknown[]) => getJobMock(...args),
  listResumes: (...args: unknown[]) => listResumesMock(...args),
  // CRUD 桩（冒烟测试不触发）
  updateJob: vi.fn(),
  deleteJob: vi.fn(),
  createJobScript: vi.fn(),
  updateJobScript: vi.fn(),
  deleteJobScript: vi.fn(),
  getRequirementOptions: vi.fn().mockResolvedValue({ success: false }),
}))

import JobDetail from '@/components/recruiting/JobDetail.vue'

const JOB_FIXTURE = {
  id: 'job-1',
  job_name: 'Python 后端工程师',
  notes: '技术栈：FastAPI',
  status: 'active',
  match_threshold: 70,
  job_requirements: { experience: '3-5年', educations: ['本科'] },
  script_count: 1,
  scripts: [{ id: 's1', job_id: 'job-1', category: '初次开场', title: '开场', content: '您好', sort_order: 0 }],
  script_groups: [
    { category: '初次开场', scripts: [{ id: 's1', job_id: 'job-1', category: '初次开场', title: '开场', content: '您好', sort_order: 0 }] },
  ],
}

describe('JobDetail 挂载冒烟', () => {
  beforeEach(() => {
    getJobMock.mockReset().mockResolvedValue({ success: true, data: { ...JOB_FIXTURE } })
    listResumesMock.mockReset().mockResolvedValue({
      success: true,
      data: {
        items: [{ id: 7, candidate_name: '张三', status: 'new', source: 'boss', images: [] }],
        total: 1, page: 1, page_size: 20,
      },
    })
  })

  it('加载职位详情并渲染基本信息 tab', async () => {
    const wrapper = mount(JobDetail)
    await flushPromises()

    expect(getJobMock).toHaveBeenCalledWith('job-1')
    expect(wrapper.text()).toContain('Python 后端工程师')
    // tab 带计数
    expect(wrapper.text()).toContain('沟通话术(1)')
    expect(wrapper.text()).toContain('关联简历(1)')
    // 默认基本信息 tab：及格线与要求明细
    expect(wrapper.text()).toContain('匹配及格线')
    expect(wrapper.text()).toContain('3-5年')
  })

  it('关联简历 tab 用 job_id 筛选并渲染候选人行', async () => {
    const wrapper = mount(JobDetail)
    await flushPromises()

    // 挂载即按 job_id 服务端筛选
    expect(listResumesMock).toHaveBeenCalledWith(expect.objectContaining({ job_id: 'job-1' }))
    // 切到关联简历 tab 后渲染候选人行
    await wrapper.findAll('button').filter(b => b.text() === '关联简历(1)')[0].trigger('click')
    expect(wrapper.text()).toContain('张三')
    expect(wrapper.text()).not.toContain('该职位暂无关联简历')
  })
})
