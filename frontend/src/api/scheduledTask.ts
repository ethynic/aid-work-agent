/**
 * 定时任务管理 API
 */

import { getAuthHeader } from './auth'

const API_BASE = `${import.meta.env.VITE_API_BASE_URL || ''}/api/scheduled-tasks`

export interface ScheduledTask {
  task_id: string
  user_id: string
  name: string
  description: string
  task_prompt: string
  schedule_type: string
  cron_expression: string
  interval_seconds: number
  session_id: string
  status: string
  max_retries: number
  retry_count: number
  last_run_at: string
  next_run_at: string
  total_runs: number
  success_count: number
  fail_count: number
  created_at: string
  updated_at: string
  stats?: TaskStats
}

export interface TaskStats {
  total_runs: number
  success_count: number
  fail_count: number
  avg_duration_ms: number
  success_rate: number
}

export interface TaskLog {
  log_id: string
  task_id: string
  user_id: string
  session_id: string
  status: string
  trigger_type: string
  result_summary: string
  result_detail: string
  error_message: string
  error_trace: string
  duration_ms: number
  token_usage: number
  started_at: string
  completed_at: string
  created_at: string
}

export interface TaskUserStats {
  active_tasks: number
  paused_tasks: number
  total_tasks: number
  total_runs: number
  total_success: number
  total_fail: number
  success_rate: number
}

interface ApiResponse<T = any> {
  success: boolean
  data?: T
  error?: string
  debug?: string
  message?: string
}

export async function listScheduledTasks(status?: string): Promise<ApiResponse<{ tasks: ScheduledTask[]; total: number }>> {
  const params = new URLSearchParams()
  if (status) params.append('status', status)
  const query = params.toString() ? `?${params}` : ''
  const res = await fetch(`${API_BASE}${query}`, { headers: { ...getAuthHeader() } })
  if (!res.ok) throw new Error('Failed to fetch scheduled tasks')
  return res.json()
}

export async function getTaskStats(): Promise<ApiResponse<TaskUserStats>> {
  const res = await fetch(`${API_BASE}/stats`, { headers: { ...getAuthHeader() } })
  if (!res.ok) throw new Error('Failed to fetch task stats')
  return res.json()
}

export async function getTask(taskId: string): Promise<ApiResponse<ScheduledTask>> {
  const res = await fetch(`${API_BASE}/${taskId}`, { headers: { ...getAuthHeader() } })
  if (!res.ok) throw new Error('Failed to fetch task')
  return res.json()
}

export async function pauseTask(taskId: string): Promise<ApiResponse> {
  const res = await fetch(`${API_BASE}/${taskId}/pause`, {
    method: 'POST',
    headers: { ...getAuthHeader() }
  })
  if (!res.ok) throw new Error('Failed to pause task')
  return res.json()
}

export async function resumeTask(taskId: string): Promise<ApiResponse> {
  const res = await fetch(`${API_BASE}/${taskId}/resume`, {
    method: 'POST',
    headers: { ...getAuthHeader() }
  })
  if (!res.ok) throw new Error('Failed to resume task')
  return res.json()
}

export async function cancelTask(taskId: string): Promise<ApiResponse> {
  const res = await fetch(`${API_BASE}/${taskId}`, {
    method: 'DELETE',
    headers: { ...getAuthHeader() }
  })
  if (!res.ok) throw new Error('Failed to cancel task')
  return res.json()
}

export async function triggerTask(taskId: string): Promise<ApiResponse> {
  const res = await fetch(`${API_BASE}/${taskId}/run`, {
    method: 'POST',
    headers: { ...getAuthHeader() }
  })
  if (!res.ok) throw new Error('Failed to trigger task')
  return res.json()
}

export async function updateTaskSchedule(taskId: string, scheduleType: string, timeConfig: Record<string, any>): Promise<ApiResponse> {
  const res = await fetch(`${API_BASE}/${taskId}/schedule`, {
    method: 'PUT',
    headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
    body: JSON.stringify({ schedule_type: scheduleType, time_config: timeConfig })
  })
  if (!res.ok) throw new Error('Failed to update task schedule')
  return res.json()
}

export async function getTaskLogs(taskId: string, limit: number = 20): Promise<ApiResponse<{ logs: TaskLog[]; total: number }>> {
  const res = await fetch(`${API_BASE}/${taskId}/logs?limit=${limit}`, { headers: { ...getAuthHeader() } })
  if (!res.ok) throw new Error('Failed to fetch task logs')
  return res.json()
}

export async function getAllLogs(limit: number = 50): Promise<ApiResponse<{ logs: TaskLog[]; total: number }>> {
  const res = await fetch(`${API_BASE}/logs/all?limit=${limit}`, { headers: { ...getAuthHeader() } })
  if (!res.ok) throw new Error('Failed to fetch all logs')
  return res.json()
}
