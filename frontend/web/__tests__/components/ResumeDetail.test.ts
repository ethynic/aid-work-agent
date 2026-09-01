/**
 * ResumeDetail 组件挂载冒烟测试
 *
 * 简历详情路由化详情页，验证：
 * - 按路由 resumeId 加载详情并渲染候选人名
 * - 四个 tab（简历详情/匹配评估/沟通记录/邀约信息，第④期扩为四 tab）
 * - 图片画廊走 /api/files/{file_id}
 * - 已关联职位显示「查看职位」，未关联显示「未关联」
 * - 新 tab（沟通记录/邀约信息）渲染冒烟：首次激活懒挂载并拉取数据、空态文案
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn(), back: vi.fn() }),
  useRoute: () => ({ path: '/recruiting-operator/resumes/12', params: { resumeId: '12' } }),
}))

vi.mock('vue-toastification', () => ({ useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn() }) }))

const getResumeMock = vi.fn()
const listCommLogsMock = vi.fn()
const listInvitationsMock = vi.fn()
const listResumeNotifyLogsMock = vi.fn()

vi.mock('@/api/recruitingOperator', () => ({
  // recruitingDisplay（共享展示工具）运行时会用到
  MATCH_STATUS_LABELS: { matched: '匹配', unmatched: '接近', rejected: '不匹配' },
  getResume: (...args: unknown[]) => getResumeMock(...args),
  // CRUD 桩（冒烟测试不触发）
  updateResume: vi.fn(),
  reEvaluateResume: vi.fn(),
  // 简历时间线（第④期）：两个新 tab 子组件的列表拉取
  listCommLogs: (...args: unknown[]) => listCommLogsMock(...args),
  createCommLog: vi.fn(),
  deleteCommLog: vi.fn(),
  listInvitations: (...args: unknown[]) => listInvitationsMock(...args),
  createInvitation: vi.fn(),
  updateInvitation: vi.fn(),
  deleteInvitation: vi.fn(),
  // 企微通知留痕（2026-09-01，邀约信息 tab 底部留痕区）
  listResumeNotifyLogs: (...args: unknown[]) => listResumeNotifyLogsMock(...args),
  // 中文标签常量（新 tab 子组件模板直接引用）
  COMM_DIRECTIONS: { out: '发出', in: '收到' },
  COMM_CHANNELS: { boss: 'BOSS 直聘', wecom: '企业微信', phone: '电话', other: '其他' },
  INVITATION_STATUSES: { pending: '待确认', confirmed: '已确认', done: '已到面', noshow: '未到面', cancelled: '已取消' },
}))

import ResumeDetail from '@/components/recruiting/ResumeDetail.vue'

const RESUME_FIXTURE = {
  id: 12,
  candidate_name: '李四',
  job_id: 'job-9',
  job_name: 'Golang 工程师',
  candidate_info: { 学历: '本科' },
  images: [{ file_id: 'file_abc', name: '简历.png' }],
  source: 'boss',
  status: 'new',
  match_score: 82,
  match_status: 'matched',
  match_summary: '技术栈匹配',
  key_info: { education: '本科', core_skills: ['Go'] },
  ocr_text: 'OCR 全文',
  remark: '',
  fetched_at: '2026-08-30T10:00:00',
}

function findTabButton(wrapper: ReturnType<typeof mount>, label: string) {
  return wrapper.findAll('button').filter(b => b.text() === label)[0]
}

describe('ResumeDetail 挂载冒烟', () => {
  beforeEach(() => {
    getResumeMock.mockReset()
    listCommLogsMock.mockReset()
    listInvitationsMock.mockReset()
    listResumeNotifyLogsMock.mockReset()
    listCommLogsMock.mockResolvedValue({ success: true, data: { items: [] } })
    listInvitationsMock.mockResolvedValue({ success: true, data: { items: [] } })
    listResumeNotifyLogsMock.mockResolvedValue({ success: true, data: { items: [] } })
  })

  it('加载详情并渲染简历详情 tab（图片画廊 + 基本信息）', async () => {
    getResumeMock.mockResolvedValue({ success: true, data: { ...RESUME_FIXTURE } })
    const wrapper = mount(ResumeDetail)
    await flushPromises()

    expect(getResumeMock).toHaveBeenCalledWith(12)
    expect(wrapper.text()).toContain('李四')
    expect(wrapper.text()).toContain('· Golang 工程师')
    // 已关联职位：显示查看职位入口
    expect(wrapper.text()).toContain('查看职位')
    // 图片走文件服务
    expect(wrapper.html()).toContain('/api/files/file_abc')
    // 默认简历详情 tab
    expect(wrapper.text()).toContain('OCR 全文')
  })

  it('渲染四个 tab（简历详情/匹配评估/沟通记录/邀约信息）', async () => {
    getResumeMock.mockResolvedValue({ success: true, data: { ...RESUME_FIXTURE } })
    const wrapper = mount(ResumeDetail)
    await flushPromises()

    const labels = wrapper.findAll('button').map(b => b.text())
    expect(labels).toContain('简历详情')
    expect(labels).toContain('匹配评估')
    expect(labels).toContain('沟通记录')
    expect(labels).toContain('邀约信息')
  })

  it('未关联职位时显示「未关联」；匹配评估 tab 渲染关键信息', async () => {
    getResumeMock.mockResolvedValue({
      success: true,
      data: { ...RESUME_FIXTURE, job_id: null, job_name: '历史文本职位' },
    })
    const wrapper = mount(ResumeDetail)
    await flushPromises()

    expect(wrapper.text()).toContain('未关联')
    // 切到匹配评估 tab
    await findTabButton(wrapper, '匹配评估').trigger('click')
    expect(wrapper.text()).toContain('82 ✓')
    expect(wrapper.text()).toContain('技术栈匹配')
    expect(wrapper.text()).toContain('核心技能')
  })

  it('沟通记录 tab：首次激活懒挂载并拉取列表，空态提示', async () => {
    getResumeMock.mockResolvedValue({ success: true, data: { ...RESUME_FIXTURE } })
    const wrapper = mount(ResumeDetail)
    await flushPromises()

    // 未激活时不拉取
    expect(listCommLogsMock).not.toHaveBeenCalled()

    await findTabButton(wrapper, '沟通记录').trigger('click')
    await flushPromises()

    // 以简历 id 拉取 + 空态文案 + 补录入口
    expect(listCommLogsMock).toHaveBeenCalledWith(12)
    expect(wrapper.text()).toContain('暂无沟通记录')
    expect(wrapper.text()).toContain('补录沟通')
  })

  it('邀约信息 tab：首次激活懒挂载并拉取列表，空态提示', async () => {
    getResumeMock.mockResolvedValue({ success: true, data: { ...RESUME_FIXTURE } })
    const wrapper = mount(ResumeDetail)
    await flushPromises()

    expect(listInvitationsMock).not.toHaveBeenCalled()

    await findTabButton(wrapper, '邀约信息').trigger('click')
    await flushPromises()

    expect(listInvitationsMock).toHaveBeenCalledWith(12)
    expect(wrapper.text()).toContain('尚未发起邀约')
    expect(wrapper.text()).toContain('新增邀约')
  })

  it('沟通记录 tab 渲染时间线条目（方向/渠道徽标 + 内容）', async () => {
    getResumeMock.mockResolvedValue({ success: true, data: { ...RESUME_FIXTURE } })
    listCommLogsMock.mockResolvedValue({
      success: true,
      data: {
        items: [
          {
            id: 1,
            resume_id: 12,
            direction: 'out',
            channel: 'wecom',
            content: '已加候选人企业微信，约定明天电话沟通',
            user_id: 'op_1',
            created_at: '2026-08-31T10:00:00+00:00',
          },
        ],
      },
    })
    const wrapper = mount(ResumeDetail)
    await flushPromises()

    await findTabButton(wrapper, '沟通记录').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('发出')
    expect(wrapper.text()).toContain('企业微信')
    expect(wrapper.text()).toContain('已加候选人企业微信，约定明天电话沟通')
    expect(wrapper.text()).toContain('op_1')
  })

  it('邀约信息 tab 渲染邀约卡片（状态徽标 + 面试时间/面试官/方式）', async () => {
    getResumeMock.mockResolvedValue({ success: true, data: { ...RESUME_FIXTURE } })
    listInvitationsMock.mockResolvedValue({
      success: true,
      data: {
        items: [
          {
            id: 5,
            resume_id: 12,
            interview_at: '2026-09-01T14:00:00+00:00',
            interviewer: '王经理',
            method: '视频面试',
            status: 'confirmed',
            notes: '候选人已确认',
            created_at: '2026-08-31T09:00:00+00:00',
          },
        ],
      },
    })
    const wrapper = mount(ResumeDetail)
    await flushPromises()

    await findTabButton(wrapper, '邀约信息').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('已确认')
    expect(wrapper.text()).toContain('王经理')
    expect(wrapper.text()).toContain('视频面试')
    expect(wrapper.text()).toContain('候选人已确认')
    // 状态流转下拉（卡片上直接改状态）
    expect(wrapper.text()).toContain('流转状态')
  })

  it('邀约信息 tab：邀约卡片带删除按钮 + 底部企微通知留痕区（kind 徽标 + 状态 + 内容）', async () => {
    getResumeMock.mockResolvedValue({ success: true, data: { ...RESUME_FIXTURE } })
    listInvitationsMock.mockResolvedValue({
      success: true,
      data: {
        items: [{ id: 5, resume_id: 12, status: 'pending', created_at: '2026-08-31T09:00:00+00:00' }],
      },
    })
    listResumeNotifyLogsMock.mockResolvedValue({
      success: true,
      data: {
        items: [
          {
            id: 71,
            kind: 'pre',
            status: 'sent',
            content: '【面试邀约知会】PHP开发工程师 拟邀约候选人面试',
            created_at: '2026-08-31T10:00:00+00:00',
          },
        ],
      },
    })
    const wrapper = mount(ResumeDetail)
    await flushPromises()

    await findTabButton(wrapper, '邀约信息').trigger('click')
    await flushPromises()

    // 企微通知留痕：以简历 id 拉取 + kind 徽标 + 状态徽标 + 内容摘要
    expect(listResumeNotifyLogsMock).toHaveBeenCalledWith(12)
    expect(wrapper.text()).toContain('企微通知留痕')
    expect(wrapper.text()).toContain('事前知会')
    expect(wrapper.text()).toContain('已发送')
    expect(wrapper.text()).toContain('【面试邀约知会】PHP开发工程师 拟邀约候选人面试')
    // 邀约卡片带删除入口（删除走原生 confirm，冒烟只断言按钮存在）
    expect(wrapper.text()).toContain('删除')
  })
})
