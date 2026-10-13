import type { JobRequirements, JobRequirementOptions, JobVipFilters } from '@/api/recruitingOperator'

export const DEFAULT_REQUIREMENT_OPTIONS: JobRequirementOptions = {
  experience: ['在校/应届', '26年毕业', '27年毕业', '27年后毕业', '1年以内', '1-3年', '3-5年', '5-10年', '10年以上'],
  educations: ['初中及以下', '中专/中技', '高中', '大专', '本科', '硕士', '博士'],
  salary: ['3-5K', '5-10K', '10-15K', '15-25K', '25-50K', '50K以上'],
  job_statuses: ['离职-随时到岗', '在职-暂不考虑', '在职-考虑机会', '在职-月内到岗'],
  vip_filters: {
    activity: ['刚刚活跃', '今日活跃', '3日内活跃', '本周活跃', '本月活跃'],
    gender: ['男', '女'], not_viewed: ['近14天没有'], not_exchanged: ['近一个月没有'],
    schools: ['985', '211', '双一流院校', '留学', '公办本科', 'QS 100', 'QS 500'],
    job_hopping: ['5年少于3份', '平均每份工作大于1年'],
    boss_keywords: ['HTML', 'CSS', 'MYSQL', 'GIT', '性能优化', '数据库设计', 'REDIS', 'AJAX', '高并发'],
  },
}

export interface RequirementsFormState {
  experiences: string[]
  educations: string[]
  job_statuses: string[]
  salary_mode: 'range' | 'legacy'
  salary_legacy: string
  salary_min: string
  salary_max: string
  age_min: string
  age_max: string
  activity: string
  gender: string
  not_viewed: string
  not_exchanged: string
  schools: string[]
  first_degree_only: boolean
  job_hopping: string
  boss_keywords: string[]
  keywords: string
  requirements_notes: string
}

export function parseLegacySalary(salary?: string | null): JobRequirements['salary_range'] {
  if (!salary) return undefined
  const text = salary.trim()
  const range = text.match(/^(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*K$/i)
  if (range) {
    const min = Number(range[1]), max = Number(range[2])
    if (min > 0 && max >= min) return { min, max }
  }
  const single = text.match(/^(\d+(?:\.\d+)?)\s*K\s*(以上|以下)$/i)
  if (single && Number(single[1]) > 0) return { [single[2] === '以上' ? 'min' : 'max']: Number(single[1]) }
  return undefined
}

export function requirementsToForm(reqs: JobRequirements = {}): RequirementsFormState {
  const vip = reqs.vip_filters || {}
  const salary = reqs.salary_range || parseLegacySalary(reqs.salary)
  return {
    experiences: [...(reqs.experiences || (reqs.experience && reqs.experience !== '不限' ? [reqs.experience] : []))],
    educations: [...(reqs.educations || [])], job_statuses: [...(reqs.job_statuses || [])],
    salary_mode: !salary && reqs.salary && reqs.salary !== '不限' ? 'legacy' : 'range',
    salary_legacy: reqs.salary || '', salary_min: salary?.min == null ? '' : String(salary.min),
    salary_max: salary?.max == null ? '' : String(salary.max),
    age_min: vip.age?.min == null ? '' : String(vip.age.min), age_max: vip.age?.max == null ? '' : String(vip.age.max),
    activity: vip.activity || '', gender: vip.gender || '', not_viewed: vip.not_viewed || '',
    not_exchanged: vip.not_exchanged || '', schools: [...(vip.schools || [])],
    first_degree_only: !!vip.first_degree_only, job_hopping: vip.job_hopping || '',
    boss_keywords: [...(vip.boss_keywords || [])], keywords: (reqs.keywords || []).join(', '),
    requirements_notes: reqs.notes || '',
  }
}

function readRange(minText: string, maxText: string, age = false): { min?: number; max?: number } | undefined {
  const minValue = String(minText).trim(), maxValue = String(maxText).trim()
  if (!minValue && !maxValue) return undefined
  const range: { min?: number; max?: number } = {}
  for (const [bound, value] of [['min', minValue], ['max', maxValue]] as const) {
    if (!value) continue
    const number = Number(value)
    if (!/^\d+(?:\.\d+)?$/.test(value) || !Number.isFinite(number) || number <= 0) throw new Error(`${age ? '年龄' : '薪资'}必须填写正数`)
    if (age && (!Number.isInteger(number) || number < 16 || number > 45)) throw new Error('年龄必须为 16–45 岁整数，上限可留空表示不限')
    range[bound] = number
  }
  if (range.min != null && range.max != null && range.min > range.max) throw new Error(`${age ? '年龄' : '薪资'}下限不能大于上限`)
  return range
}

export function buildRequirements(form: RequirementsFormState): JobRequirements {
  const reqs: JobRequirements = {}
  for (const key of ['experiences', 'educations', 'job_statuses'] as const) {
    if (form[key].length) reqs[key] = [...form[key]]
  }
  if (form.salary_mode === 'legacy') {
    if (form.salary_legacy.trim()) reqs.salary = form.salary_legacy.trim()
  } else {
    const range = readRange(form.salary_min, form.salary_max)
    if (range) reqs.salary_range = range
  }
  const vip: JobVipFilters = {}
  const age = readRange(form.age_min, form.age_max, true)
  if (age) vip.age = { ...age, ...(age.max == null ? { max: null } : {}) }
  for (const key of ['activity', 'gender', 'not_viewed', 'not_exchanged', 'job_hopping'] as const) {
    if (form[key] && form[key] !== '不限') vip[key] = form[key]
  }
  for (const key of ['schools', 'boss_keywords'] as const) if (form[key].length) vip[key] = [...form[key]]
  if (form.first_degree_only) {
    if (!form.schools.length) throw new Error('只看第一学历必须先选择院校条件')
    vip.first_degree_only = true
  }
  if (Object.keys(vip).length) reqs.vip_filters = vip
  const keywords = form.keywords.split(/[,，]/).map(s => s.trim()).filter(Boolean)
  if (keywords.length) reqs.keywords = keywords
  if (form.requirements_notes.trim()) reqs.notes = form.requirements_notes.trim()
  return reqs
}

export function requirementGroups(reqs: JobRequirements = {}): { title: string; entries: [string, string][] }[] {
  const ordinary: [string, string][] = [], vipEntries: [string, string][] = [], scoring: [string, string][] = []
  const experience = reqs.experiences?.join('、') || reqs.experience
  if (experience) ordinary.push(['经验', experience])
  if (reqs.educations?.length) ordinary.push(['学历', reqs.educations.join('、')])
  if (reqs.job_statuses?.length) ordinary.push(['求职状态', reqs.job_statuses.join('、')])
  const range = reqs.salary_range
  if (range) ordinary.push(['薪资', range.min != null && range.max != null ? `${range.min}–${range.max}K` : range.min != null ? `${range.min}K以上` : `${range.max}K以下`])
  else if (reqs.salary) ordinary.push(['薪资', reqs.salary])
  const vip = reqs.vip_filters || {}
  if (vip.age) vipEntries.push(['年龄', `${vip.age.min ?? 16}–${vip.age.max ?? '不限'}岁`])
  const labels = { activity: '活跃度', gender: '性别', not_viewed: '近期没有看过', not_exchanged: '同事交换简历', job_hopping: '跳槽频率' }
  for (const key of Object.keys(labels) as (keyof typeof labels)[]) if (vip[key]) vipEntries.push([labels[key], vip[key]!])
  if (vip.schools?.length) vipEntries.push(['院校', vip.schools.join('、')])
  if (vip.first_degree_only) vipEntries.push(['第一学历', '只看第一学历（全日制本科）'])
  if (vip.boss_keywords?.length) vipEntries.push(['牛人关键词', vip.boss_keywords.join('、')])
  if (reqs.keywords?.length) scoring.push(['加分关键词', reqs.keywords.join('、')])
  if (reqs.notes) scoring.push(['要求备注', reqs.notes])
  return [{ title: '普通筛选', entries: ordinary }, { title: 'VIP 专享筛选', entries: vipEntries }, { title: '评分补充', entries: scoring }].filter(group => group.entries.length)
}
