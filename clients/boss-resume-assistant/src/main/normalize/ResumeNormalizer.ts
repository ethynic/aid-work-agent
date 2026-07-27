/**
 * 简历归一化器（设计文档 §8.3）。
 * - 将列表摘要（DOMSnapshot）和 OCR 内容归一为统一 ResumeDocument
 * - 公司、职位、日期等字段优先使用 DOMSnapshot（列表已有）
 * - 来源冲突 → 保留两个值并进 reviewIssues，不静默覆盖
 * - 生成供筛选使用的 Markdown 和可审计 JSON
 */

/** ViewedResumeSummary（设计文档 §8.1） */
export interface ViewedResumeSummary {
  candidateFingerprint: string
  jobFingerprint: string
  viewedAt: string
  source: 'OCR_NORMALIZED' | 'LIST_DOM_FALLBACK'
  completeness: 'COMPLETE_SUMMARY' | 'PARTIAL_SUMMARY'
  baseInfo: {
    name?: string
    gender?: string
    activeTime?: string
  }
  workExperiences: Array<{
    company?: string
    position?: string
    start?: string
    end?: string
    duration?: string
  }>
  projectExperiences: Array<{
    name?: string
    role?: string
    start?: string
    end?: string
    duration?: string
  }>
  educationExperiences: Array<{
    school?: string
    degree?: string
    major?: string
    start?: string
    end?: string
  }>
}

export type FieldSource = 'LIST_DOM' | 'OCR'

export interface ResumeDocument {
  summary: ViewedResumeSummary
  /** Markdown 正文（供筛选 LLM） */
  markdown: string
  /** 来源冲突项（需人工复核） */
  reviewIssues: ReviewIssue[]
}

export interface ReviewIssue {
  field: string
  listValue?: string
  ocrValue?: string
  reason: string
}

export interface ListSummarySource {
  /** 列表 DOMSnapshot 已有的字段（优先级高） */
  fields: Partial<{
    name: string
    age: string
    degree: string
    workYears: string
    city: string
    expectedPosition: string
    expectedSalary: string
    recentCompany: string
    recentPosition: string
  }>
}

/** 归一化：合并列表摘要与 OCR，来源冲突进 reviewIssues */
export function normalize(
  ocrSummary: Partial<Omit<ViewedResumeSummary, 'source' | 'completeness'>>,
  listSummary: ListSummarySource,
  fingerprints: { candidate: string; job: string },
): ResumeDocument {
  const reviewIssues: ReviewIssue[] = []

  // baseInfo.name 冲突检测
  const ocrName = ocrSummary.baseInfo?.name
  const listName = listSummary.fields.name
  let resolvedName = ocrName ?? listName
  if (ocrName && listName && ocrName !== listName) {
    reviewIssues.push({
      field: 'baseInfo.name',
      listValue: listName,
      ocrValue: ocrName,
      reason: 'name mismatch between list DOM and OCR',
    })
  } else if (listName && !ocrName) {
    resolvedName = listName // 列表优先
  }

  const summary: ViewedResumeSummary = {
    candidateFingerprint: fingerprints.candidate,
    jobFingerprint: fingerprints.job,
    viewedAt: new Date().toISOString(),
    source: 'OCR_NORMALIZED',
    completeness: reviewIssues.length > 0 ? 'PARTIAL_SUMMARY' : 'COMPLETE_SUMMARY',
    baseInfo: {
      name: resolvedName,
      ...(ocrSummary.baseInfo?.gender ? { gender: ocrSummary.baseInfo.gender } : {}),
      ...(ocrSummary.baseInfo?.activeTime ? { activeTime: ocrSummary.baseInfo.activeTime } : {}),
    },
    workExperiences: mergeExperiences(ocrSummary.workExperiences ?? [], listSummary, reviewIssues),
    projectExperiences: ocrSummary.projectExperiences ?? [],
    educationExperiences: ocrSummary.educationExperiences ?? [],
  }

  const markdown = toMarkdown(summary, listSummary)
  return { summary, markdown, reviewIssues }
}

function mergeExperiences(
  ocrWork: ViewedResumeSummary['workExperiences'],
  listSummary: ListSummarySource,
  issues: ReviewIssue[],
): ViewedResumeSummary['workExperiences'] {
  // 列表的 recentCompany/recentPosition 优先填到第一条
  const recentCompany = listSummary.fields.recentCompany
  const recentPosition = listSummary.fields.recentPosition
  if (ocrWork.length === 0 && (recentCompany || recentPosition)) {
    return [{ company: recentCompany, position: recentPosition }]
  }
  if (ocrWork.length > 0 && recentCompany && ocrWork[0]!.company && ocrWork[0]!.company !== recentCompany) {
    issues.push({
      field: 'workExperiences[0].company',
      listValue: recentCompany,
      ocrValue: ocrWork[0]!.company,
      reason: 'recent company mismatch',
    })
  }
  return ocrWork
}

function toMarkdown(summary: ViewedResumeSummary, list: ListSummarySource): string {
  const lines: string[] = []
  lines.push(`# ${summary.baseInfo.name ?? '（未识别姓名）'}`)
  const f = list.fields
  const meta = [f.age, f.degree, f.workYears && `${f.workYears}年`, f.city].filter(Boolean).join(' / ')
  if (meta) lines.push(meta)
  if (f.expectedPosition || f.expectedSalary) {
    lines.push(`期望：${[f.expectedPosition, f.expectedSalary].filter(Boolean).join(' · ')}`)
  }
  if (summary.workExperiences.length > 0) {
    lines.push('', '## 工作经历')
    for (const w of summary.workExperiences) {
      const period = [w.start, w.end].filter(Boolean).join(' - ')
      lines.push(`- **${w.company ?? ''}** ${w.position ?? ''} ${period}`)
    }
  }
  if (summary.projectExperiences.length > 0) {
    lines.push('', '## 项目经历')
    for (const p of summary.projectExperiences) {
      lines.push(`- **${p.name ?? ''}** ${p.role ?? ''}`)
    }
  }
  if (summary.educationExperiences.length > 0) {
    lines.push('', '## 教育经历')
    for (const e of summary.educationExperiences) {
      lines.push(`- **${e.school ?? ''}** ${e.degree ?? ''} ${e.major ?? ''}`)
    }
  }
  return lines.join('\n')
}
