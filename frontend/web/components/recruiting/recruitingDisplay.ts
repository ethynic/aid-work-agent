/**
 * 招聘模块共享展示工具（简历库 / 职位库 列表与详情页复用）
 *
 * 抽取自 ResumeLibrary / JobLibrary 原地实现，保证徽标配色、日期格式、
 * 详情页路径拼法在拆分后的列表页与详情页之间保持一致。
 */
import type { RouteLocationNormalizedLoaded } from 'vue-router'
import { MATCH_STATUS_LABELS, type ResumeListItem } from '@/api/recruitingOperator'

// ============== 状态徽标 ==============

/** 状态中文标签（顺序即下拉顺序） */
export const STATUS_LABELS: Record<string, string> = {
  new: '新简历',
  viewed: '已查看',
  shortlisted: '有意向',
  interviewed: '已约面',
  rejected: '不合适',
}

export function statusIntent(status?: string): 'info' | 'neutral' | 'success' | 'warning' | 'danger' {
  const map: Record<string, 'info' | 'neutral' | 'success' | 'warning' | 'danger'> = {
    new: 'info',
    viewed: 'neutral',
    shortlisted: 'success',
    interviewed: 'warning',
    rejected: 'danger',
  }
  return map[status || ''] || 'neutral'
}

export function sourceLabel(source?: string): string {
  const map: Record<string, string> = { boss: 'CLI 入库', manual: '页面补录' }
  return map[source || ''] || source || '-'
}

// 匹配度徽标（简历-职位匹配 Phase 5）：matched 绿（分数+✓）/ unmatched 黄（分数，含 50-69 接近）/
// rejected 灰（分数）/ null 灰「未评分」
export function matchIntent(status?: string | null): 'success' | 'warning' | 'neutral' {
  const map: Record<string, 'success' | 'warning' | 'neutral'> = {
    matched: 'success',
    unmatched: 'warning',
    rejected: 'neutral',
  }
  return map[status || ''] || 'neutral'
}

export function matchText(row: Pick<ResumeListItem, 'match_score' | 'match_status'>): string {
  if (!row.match_status) return '未评分'
  if (row.match_score == null) return MATCH_STATUS_LABELS[row.match_status] || row.match_status
  return row.match_status === 'matched' ? `${row.match_score} ✓` : String(row.match_score)
}

export function formatDate(t?: string): string {
  if (!t) return '-'
  return t.slice(0, 10)
}

/** 面试时间展示：带时分（区别于 formatDate 只到日期），空值显示 '-' */
export function formatDateTime(t?: string | null): string {
  if (!t) return '-'
  return t.slice(0, 16).replace('T', ' ')
}

/** 邀约状态徽标配色：pending 绿（待推进）/ confirmed info / done success / noshow danger / cancelled neutral */
export function invitationStatusIntent(status?: string): 'success' | 'info' | 'danger' | 'neutral' {
  const map: Record<string, 'success' | 'info' | 'danger' | 'neutral'> = {
    pending: 'success',
    confirmed: 'info',
    done: 'success',
    noshow: 'danger',
    cancelled: 'neutral',
  }
  return map[status || ''] || 'neutral'
}

// ============== 路由路径（兼容 demo 与租户前台两套路由） ==============

/** 招聘模块路由基路径：租户前台（/t/:tenant_id 前缀）带租户段，demo 直连 */
export function recruitingBasePath(route: RouteLocationNormalizedLoaded): string {
  return route.path.startsWith('/t/')
    ? `/t/${String(route.params.tenant_id)}/recruiting-operator`
    : '/recruiting-operator'
}

export function jobListPath(route: RouteLocationNormalizedLoaded): string {
  return `${recruitingBasePath(route)}/jobs`
}

export function resumeListPath(route: RouteLocationNormalizedLoaded): string {
  return `${recruitingBasePath(route)}/resumes`
}

export function jobDetailPath(route: RouteLocationNormalizedLoaded, jobId: string): string {
  return `${recruitingBasePath(route)}/jobs/${jobId}`
}

export function resumeDetailPath(route: RouteLocationNormalizedLoaded, resumeId: number | string): string {
  return `${recruitingBasePath(route)}/resumes/${resumeId}`
}
