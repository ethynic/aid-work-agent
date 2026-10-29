/**
 * 定时任务 API（v2 Runner 化重建，设计 §10 / Phase 5 前端）
 *
 * 后端事实（src/api/scheduled_task.py）：
 * - 全部端点要求 Authorization Bearer + X-Tenant-Id（getAuthHeader 按路径自动附带）；
 * - 任务行公开投影不含 pending_snapshot；旧字段 description/max_retries/retry_count 已删除；
 * - 执行历史不在本 API——任务视图经 Runner 网关（api/runner.ts，source='scheduled'）读取；
 * - 409 语义：pause 非 active / resume 非 paused / trigger 非 active 或 pending 占用 /
 *   delete 有活跃 runner；resume once 过宽限返回 {success, expired:true, message}。
 */

import { ref } from 'vue'
import { getAuthHeader } from './auth'

const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/scheduled-tasks`

// ==================== 任务列表变更信号（侧栏分组刷新，评审 P2-5） ====================
// 领域级信号：任务视图内任何变更操作（创建/暂停/恢复/编辑/删除/触发）成功后 bump，
// MenuSidebar watch 本版本号轻量刷新任务计划分组。不引入通用事件总线。

/** 任务列表版本号：递增即表示后端任务行集合已发生变化 */
export const scheduledTasksVersion = ref(0)

/** 变更操作成功后调用，通知所有订阅任务列表的视图（侧栏）刷新 */
export function notifyScheduledTasksChanged(): void {
  scheduledTasksVersion.value += 1
}

export type ScheduledTaskStatus = 'active' | 'paused' | 'completed' | 'expired'
export type ScheduledTaskScheduleType = 'daily' | 'weekly' | 'monthly' | 'interval' | 'once'

/** 附件受权资源引用（创建时固化；AttachmentRef 契约白名单） */
export interface ScheduledTaskAttachment {
  file_id: string
  name: string
  type: 'file' | 'image'
  mime_type?: string
  size?: number
}

export interface ScheduledTask {
  task_id: string
  tenant_id?: string | null
  user_id: string
  name: string
  task_prompt: string
  attachments: ScheduledTaskAttachment[]
  profile_id: string
  instance_id?: string | null
  schedule_type: ScheduledTaskScheduleType
  cron_expression?: string | null
  interval_seconds?: number | null
  run_at?: string | null
  /** 任务专属会话（执行结果存储容器，metadata.scheduled_task 标记） */
  session_id: string
  status: ScheduledTaskStatus
  pause_reason?: string | null
  notification_target_id?: string | null
  next_due_at?: string | null
  last_run_at?: string | null
  last_status?: string | null
  total_runs: number
  success_count: number
  fail_count: number
  created_at: string
  updated_at: string
}

export interface ScheduledTaskUserStats {
  total_tasks: number
  active_tasks: number
  paused_tasks: number
  total_runs: number
  total_success: number
  total_fail: number
  success_rate: number
}

export interface UpdateSchedulePayload {
  schedule_type: ScheduledTaskScheduleType
  /** once 必填 run_at（YYYY-MM-DD HH:MM:SS，未来时间）；daily/weekly/monthly 用
   *  hour/minute（weekly 另需 day_of_week: mon..sun，monthly 另需 day: 1-31）；
   *  interval 用 interval_hours（正数，支持小数） */
  time_config: Record<string, unknown>
}

export interface UpdateDefinitionPayload {
  name?: string
  task_prompt?: string
  attachments?: ScheduledTaskAttachment[]
  notification_target_id?: string
  /** 显式清除通知目标（恢复默认：站内信 + 个人偏好渠道）；与 notification_target_id 互斥 */
  clear_notification_target?: boolean
}

export class ScheduledTaskRequestError extends Error {
  constructor(public status: number, message: string, public detail?: string) {
    super(detail || message)
  }
}

async function request<T>(path: string, init: RequestInit = {}, headers?: Record<string, string>): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { ...(headers || getAuthHeader()), ...(init.headers || {}) },
  })
  const data = await res.json().catch(() => null)
  if (!res.ok) {
    const detail = typeof data?.detail === 'string' ? data.detail
      : typeof data?.error === 'string' ? data.error : undefined
    throw new ScheduledTaskRequestError(res.status, `HTTP_${res.status}`, detail)
  }
  return data as T
}

interface ListResponse { success: boolean; data: { tasks: ScheduledTask[]; total: number } }
interface TaskResponse { success: boolean; data: ScheduledTask; message?: string }
interface StatsResponse { success: boolean; data: ScheduledTaskUserStats }
interface ResumeResponse { success: boolean; expired?: boolean; message?: string; data?: { next_due_at?: string | null } }

/** 任务列表（可按状态过滤）。headers 可显式传入（/notification 入口等非 /t/ 路径下使用） */
export async function listScheduledTasks(status?: string, headers?: Record<string, string>): Promise<ScheduledTask[]> {
  const query = status ? `?status=${encodeURIComponent(status)}` : ''
  const value = await request<ListResponse>(`${query}`, { headers })
  return value?.data?.tasks || []
}

export async function getScheduledTaskStats(): Promise<ScheduledTaskUserStats> {
  const value = await request<StatsResponse>('/stats')
  return value.data
}

export async function getScheduledTask(taskId: string): Promise<ScheduledTask> {
  const value = await request<TaskResponse>(`/${encodeURIComponent(taskId)}`)
  return value.data
}

/** 暂停（仅 active；已受理的执行继续到终态）。非 active 时后端 409 */
export async function pauseScheduledTask(taskId: string): Promise<void> {
  await request<{ success: boolean }>(`/${encodeURIComponent(taskId)}/pause`, { method: 'POST' })
}

/** 恢复（仅 paused）。once 过宽限返回 expired:true（任务已转 expired，需展示回执） */
export async function resumeScheduledTask(taskId: string): Promise<ResumeResponse> {
  return request<ResumeResponse>(`/${encodeURIComponent(taskId)}/resume`, { method: 'POST' })
}

/** 删除任务（含任务会话与消息）。有活跃 runner 时后端 409「任务正在执行，稍后再试」 */
export async function deleteScheduledTask(taskId: string): Promise<void> {
  await request<{ success: boolean }>(`/${encodeURIComponent(taskId)}`, { method: 'DELETE' })
}

/** 立即执行一次（manual pending 槽，不改 next_due_at；非 active/pending 占用时 409） */
export async function triggerScheduledTask(taskId: string): Promise<void> {
  await request<{ success: boolean }>(`/${encodeURIComponent(taskId)}/trigger`, { method: 'POST' })
}

/** 改期（仅调度字段变更，重算 next_due_at；非法时间配置 422 带 detail） */
export async function updateScheduledTaskSchedule(taskId: string, payload: UpdateSchedulePayload): Promise<void> {
  await request<{ success: boolean }>(`/${encodeURIComponent(taskId)}/schedule`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

/** 编辑任务内容/附件/通知目标（不动调度字段；空白 name/prompt 422） */
export async function updateScheduledTaskDefinition(taskId: string, payload: UpdateDefinitionPayload): Promise<ScheduledTask> {
  const value = await request<TaskResponse>(`/${encodeURIComponent(taskId)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  return value.data
}

// ==================== 展示辅助（与后端 PAUSE_REASON_LABELS 同口径） ====================

/** pause_reason → 用户可见文案（src/scheduler/dispatch.py PAUSE_REASON_LABELS） */
export const SCHEDULED_PAUSE_REASON_LABELS: Record<string, string> = {
  credit_blocked: '积分额度不足',
  profile_forbidden: '数字员工不可用',
  tenant_unavailable: '租户不可用或已过期',
  definition_invalid: '任务定义校验未通过',
  submit_conflict: '提交冲突（幂等键命中不同内容）',
  submit_rejected: '提交被拒绝',
  interrupted: '执行中断且自动恢复失败',
}

export const SCHEDULED_STATUS_LABELS: Record<string, string> = {
  active: '执行中',
  paused: '已暂停',
  completed: '已完成',
  expired: '已过期',
}

/** 调度规则人类可读描述（设计 §6 头部展示） */
export function describeSchedule(task: Pick<ScheduledTask, 'schedule_type' | 'cron_expression' | 'interval_seconds' | 'run_at'>): string {
  const weekNames: Record<string, string> = { mon: '周一', tue: '周二', wed: '周三', thu: '周四', fri: '周五', sat: '周六', sun: '周日' }
  // APScheduler 编号（后端 _WEEKDAY_CRON_MAP）：0=周一 … 6=周日
  const cronNumbers: Record<string, string> = { '0': '周一', '1': '周二', '2': '周三', '3': '周四', '4': '周五', '5': '周六', '6': '周日' }
  const parts = (task.cron_expression || '').split(' ').filter(Boolean)
  const time = parts.length >= 2 ? `${parts[1].padStart(2, '0')}:${parts[0].padStart(2, '0')}` : ''
  switch (task.schedule_type) {
    case 'daily': return parts.length >= 2 ? `每天 ${time}` : '每天'
    case 'weekly': {
      // cron 顺序：分 时 日 月 周 —— 周字段是 parts[4]（APScheduler 编号 0=周一→sun 映射兜底）
      const day = weekNames[parts[4] || ''] || cronNumbers[parts[4] || ''] || ''
      return parts.length >= 5 ? `每${day} ${time}` : '每周'
    }
    case 'monthly': return parts.length >= 3 ? `每月 ${parts[2]} 日 ${time}` : '每月'
    case 'interval': {
      const seconds = task.interval_seconds || 0
      if (seconds > 0 && seconds % 86400 === 0) return `每隔 ${seconds / 86400} 天`
      if (seconds > 0 && seconds % 3600 === 0) return `每隔 ${seconds / 3600} 小时`
      if (seconds > 0 && seconds % 60 === 0) return `每隔 ${seconds / 60} 分钟`
      return seconds > 0 ? `每隔 ${seconds} 秒` : '间隔执行'
    }
    case 'once': return task.run_at ? `一次性 ${task.run_at.replace('T', ' ')}` : '一次性'
    default: return task.schedule_type
  }
}

// ==================== 任务会话识别（设计 §6 单一标记来源） ====================

/**
 * 读取会话行的 metadata.scheduled_task 标记（任务专属会话识别）。
 * /api/sessions 列表行来自 SELECT *（含 metadata，JSONB）；兼容 JSON 字符串形态
 * （后端删除守卫同样做了字符串归一化）。非任务会话返回 null。
 */
export function scheduledTaskSessionMark(session: { metadata?: unknown } | null | undefined): string | null {
  if (!session) return null
  let metadata: unknown = session.metadata
  if (typeof metadata === 'string') {
    try { metadata = JSON.parse(metadata) } catch { return null }
  }
  const value = (metadata as Record<string, unknown> | null | undefined)?.scheduled_task
  return typeof value === 'string' && value ? value : null
}

/**
 * 会话 ID → 任务行解析（通知 runner 资源 / 旧聊天链接路由用）：
 * scheduled_tasks.session_id UNIQUE，按任务列表匹配。
 *
 * 三态语义（评审 P2-8，调用方必须区分）：
 * - ScheduledTask：命中任务行（任务专属会话，路由任务视图）；
 * - null：**确认**不是当前账号的任务会话（列表请求成功且未命中，或 403 确认不可见）；
 * - undefined：查询本身失败（网络异常/5xx/401 未授权），调用方不得据此回退到聊天页，
 *   应展示可重试错误。
 */
export async function findScheduledTaskBySessionId(sessionId: string, headers?: Record<string, string>): Promise<ScheduledTask | null | undefined> {
  try {
    const tasks = await listScheduledTasks(undefined, headers)
    return tasks.find(task => task.session_id === sessionId) || null
  } catch (e) {
    if (e instanceof ScheduledTaskRequestError) {
      // 403 = 列表请求成功但确认当前账号不可见该任务行 → 确认不是任务会话
      // 401 = 登录态失效 → 查询失败（调用方引导重登）；4xx/5xx 同样视为失败
      if (e.status === 403) return null
    }
    return undefined
  }
}
