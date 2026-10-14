import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import JobFormModal from '@/components/recruiting/JobFormModal.vue'
import { buildRequirements, requirementsToForm, requirementGroups } from '@/components/recruiting/jobRequirementsForm'
import type { JobDetail, JobRequirements } from '@/api/recruitingOperator'

const mocks = vi.hoisted(() => ({ create: vi.fn(), update: vi.fn(), error: vi.fn() }))
vi.mock('@/api/recruitingOperator', () => ({
  createJob: (...args: unknown[]) => mocks.create(...args),
  updateJob: (...args: unknown[]) => mocks.update(...args),
  getRequirementOptions: vi.fn().mockResolvedValue({ success: false }),
}))
vi.mock('vue-toastification', () => ({ useToast: () => ({ success: vi.fn(), error: mocks.error }) }))

function job(requirements: JobRequirements = {}): JobDetail {
  return { id: 'job-1', job_name: '工程师', status: 'active', match_threshold: 70, script_count: 0, scripts: [], script_groups: [], job_requirements: requirements }
}
let wrapper: VueWrapper
function open(requirements?: JobRequirements) {
  wrapper = mount(JobFormModal, { props: { modelValue: true, job: requirements ? job(requirements) : null }, global: { stubs: { Teleport: true } } })
  return wrapper
}
function save() { return wrapper.findAll('button').find(button => button.text() === '保存')!.trigger('click') }
function option(value: string) { return wrapper.get(`input[type="checkbox"][value="${value}"]`) }
function firstDegree() { return wrapper.findAll('label').find(label => label.text().includes('只看第一学历'))!.get('input') }

beforeEach(() => {
  mocks.create.mockReset().mockResolvedValue({ success: true, data: job() })
  mocks.update.mockReset().mockResolvedValue({ success: true, data: job() })
  mocks.error.mockReset()
})
afterEach(() => wrapper?.unmount())

describe('职位筛选模板表单', () => {
  it('新增保存普通多选、薪资范围及 VIP 选项，评分关键词独立', async () => {
    open()
    await wrapper.get('#job-name').setValue('开发工程师')
    await option('在校/应届').setValue(true)
    await option('26年毕业').setValue(true)
    await option('高中').setValue(true)
    await option('本科').setValue(true)
    await option('离职-随时到岗').setValue(true)
    await wrapper.get('#job-salary-min').setValue('15')
    await wrapper.get('#job-salary-max').setValue('25')
    await wrapper.get('#job-age-min').setValue('25')
    await wrapper.get('#job-vip-activity').setValue('今日活跃')
    await wrapper.get('#job-vip-gender').setValue('男')
    await wrapper.get('#job-vip-not_viewed').setValue('近14天没有')
    await wrapper.get('#job-vip-not_exchanged').setValue('近一个月没有')
    await wrapper.get('#job-vip-job_hopping').setValue('5年少于3份')
    await option('985').setValue(true)
    await option('QS 100').setValue(true)
    await firstDegree().setValue(true)
    await option('HTML').setValue(true)
    await wrapper.get('#job-keywords').setValue('Linux, MySQL')
    await save()
    await flushPromises()
    expect(mocks.create).toHaveBeenCalledWith(expect.objectContaining({ job_requirements: {
      experiences: ['在校/应届', '26年毕业'], educations: ['高中', '本科'], job_statuses: ['离职-随时到岗'],
      salary_range: { min: 15, max: 25 }, keywords: ['Linux', 'MySQL'],
      vip_filters: { age: { min: 25, max: null }, activity: '今日活跃', gender: '男', not_viewed: '近14天没有',
        not_exchanged: '近一个月没有', job_hopping: '5年少于3份', schools: ['985', 'QS 100'], first_degree_only: true, boss_keywords: ['HTML'] },
    } }))
    expect(wrapper.emitted('saved')).toHaveLength(1)
    expect(wrapper.text()).toContain('执行取决于当前登录的 BOSS 账号会员权益')
  })

  it('编辑回填新字段，清空全部要求以 {} 更新', async () => {
    open({ experiences: ['1年以内'], educations: ['本科'], job_statuses: ['在职-月内到岗'], salary_range: { max: 25 }, vip_filters: { schools: ['985'], first_degree_only: true, gender: '女', age: { min: 20, max: 35 }, boss_keywords: ['CSS'] } })
    expect((option('1年以内').element as HTMLInputElement).checked).toBe(true)
    expect((firstDegree().element as HTMLInputElement).checked).toBe(true)
    expect((wrapper.get('#job-age-max').element as HTMLInputElement).value).toBe('35')
    for (const value of ['1年以内', '本科', '在职-月内到岗', '985', 'CSS']) await option(value).setValue(false)
    await wrapper.get('#job-vip-gender').setValue('')
    for (const id of ['job-age-min', 'job-age-max', 'job-salary-max']) await wrapper.get(`#${id}`).setValue('')
    expect(firstDegree().attributes('disabled')).toBeDefined()
    await save(); await flushPromises()
    expect(mocks.update).toHaveBeenCalledWith('job-1', expect.objectContaining({ job_requirements: {} }))
  })

  it('兼容旧经验和无法转换的旧薪资，改其它字段不丢旧薪资', async () => {
    open({ experience: '2-4年', salary: '按项目薪酬' })
    expect((option('2-4年').element as HTMLInputElement).checked).toBe(true)
    expect((wrapper.get('#job-legacy-salary').element as HTMLInputElement).value).toBe('按项目薪酬')
    await wrapper.get('#job-name').setValue('新职位名')
    await save(); await flushPromises()
    expect(mocks.update).toHaveBeenCalledWith('job-1', expect.objectContaining({ job_requirements: { experiences: ['2-4年'], salary: '按项目薪酬' } }))
  })

  it('旧合法薪资回填手填范围，并可主动清空兼容薪资', async () => {
    open({ experience: '1年以内', salary: '15-25K' })
    expect((wrapper.get('#job-salary-min').element as HTMLInputElement).value).toBe('15')
    expect((wrapper.get('#job-salary-max').element as HTMLInputElement).value).toBe('25')
    await save(); await flushPromises()
    expect(mocks.update.mock.calls[0][1].job_requirements.salary_range).toEqual({ min: 15, max: 25 })
    wrapper.unmount()
    open({ salary: '按项目薪酬' })
    await wrapper.get('#job-legacy-salary').setValue('')
    await save(); await flushPromises()
    expect(mocks.update.mock.calls[1][1].job_requirements).toEqual({})
  })

  it('非法范围阻止提交并保留输入，修正后保存', async () => {
    open({})
    await wrapper.get('#job-salary-min').setValue('30')
    await wrapper.get('#job-salary-max').setValue('20')
    await save(); await flushPromises()
    expect(mocks.update).not.toHaveBeenCalled()
    expect(wrapper.get('[role="alert"]').text()).toContain('下限不能大于上限')
    expect((wrapper.get('#job-salary-min').element as HTMLInputElement).value).toBe('30')
    await wrapper.get('#job-salary-max').setValue('40')
    await wrapper.get('#job-age-min').setValue('46')
    await save(); await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('16–45')
    await wrapper.get('#job-age-min').setValue('25')
    await save(); await flushPromises()
    expect(mocks.update).toHaveBeenCalledOnce()
  })

  it('保存失败保留输入和弹框，避免重复提交', async () => {
    mocks.create.mockResolvedValue({ success: false, error: '服务不可用' })
    open()
    await wrapper.get('#job-name').setValue('开发工程师')
    await option('GIT').setValue(true)
    await save(); await flushPromises()
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
    expect((option('GIT').element as HTMLInputElement).checked).toBe(true)
    expect(mocks.error).toHaveBeenCalledWith('服务不可用')
    expect(wrapper.findAll('button').find(button => button.text() === '保存')!.attributes('disabled')).toBeUndefined()
  })

  it('编辑未保存修改有关闭确认，标签和对话框语义可访问', async () => {
    open({})
    expect(wrapper.get('[role="dialog"]').attributes('aria-modal')).toBe('true')
    expect(wrapper.get('label[for="job-age-min"]').text()).toContain('年龄下限')
    await wrapper.get('#job-name').setValue('修改后')
    await wrapper.get('button[aria-label="关闭对话框"]').trigger('click')
    expect(wrapper.text()).toContain('未保存的修改')
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
  })
})

describe('筛选模板 helper', () => {
  it.each([{ min: 20 }, { max: 40 }, { min: 10.5, max: 25 }])('单边/小数薪资回填不丢失 %j', range => {
    expect(buildRequirements(requirementsToForm({ salary_range: range }))).toEqual({ salary_range: range })
  })
  it('第一学历不能孤立提交', () => {
    const form = requirementsToForm(); form.first_degree_only = true
    expect(() => buildRequirements(form)).toThrow('先选择院校')
  })
  it('详情分普通、VIP、评分组并展示全部配置', () => {
    const groups = requirementGroups({ experiences: ['26年毕业', '1年以内'], job_statuses: ['离职-随时到岗'], salary_range: { min: 15 }, vip_filters: { age: { min: 25, max: null }, activity: '今日活跃', gender: '男', not_viewed: '近14天没有', not_exchanged: '近一个月没有', schools: ['985'], first_degree_only: true, job_hopping: '5年少于3份', boss_keywords: ['GIT'] }, keywords: ['评分词'], notes: '要求备注' })
    expect(groups.map(group => group.title)).toEqual(['普通筛选', 'VIP 专享筛选', '评分补充'])
    expect(groups[1].entries).toHaveLength(9)
    expect(groups[1].entries).toContainEqual(['牛人关键词', 'GIT'])
    expect(groups[2].entries).toContainEqual(['加分关键词', '评分词'])
  })
})
