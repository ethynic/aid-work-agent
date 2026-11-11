/**
 * 系统健康监控 API Client（/portal/system-health，平台管理员）
 * 后端：src/api/admin_monitor.py；快照由 background 容器采集器每 15s 写 Redis
 */

import { getSaasAuthHeader } from './saasTenant'

const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/admin/monitor`

// ==================== 类型 ====================

export type HealthLevel = 'normal' | 'watch' | 'critical' | 'error'
export type CollectorStatus = 'ok' | 'collector_down' | 'storage_unavailable'

export interface RunnerSection {
  queued: number
  running: number
  finalizing: number
  waiting: number
  paused: number
  interrupted: number
  oldest_running_seconds: number
  oldest_queued_seconds: number
  wait_p50_seconds: number | null
  wait_p95_seconds: number | null
  runner_started_today: number
  runner_retried_today: number
  retried_share_today: number | null
  stalled_rounds: number
  runner_stalled: boolean
  active_alerts: number
  worker_slots?: number
  error?: string
}

export interface SystemSection {
  available: boolean
  cpu_percent?: number
  cpu_count?: number
  load_1?: number | null
  mem_used_percent?: number
  mem_used_gb?: number
  mem_total_gb?: number
  disk_read_mb_per_s?: number
  disk_write_mb_per_s?: number
  disk_used_percent_storage?: number | null
  disk_used_percent_log?: number | null
  net_recv_mb_per_s?: number
  net_sent_mb_per_s?: number
  net_bandwidth_mbps?: number | null
  error?: string
}

export interface BusinessSection {
  online_sessions_5m: number
  requests_today: number
  error_rate_today: number | null
  duration_p50_ms: number | null
  duration_p95_ms: number | null
  errors_last_hour: number
  error?: string
}

export interface MiddlewareSection {
  db_select1_ms: number
  db_connections: number
  db_max_connections: number | null
  redis_available: boolean
  key_pool_active: number | null
  key_pool_max: number | null
  error?: string
}

export interface HealthSnapshot {
  collected_at: string
  runner: RunnerSection
  system: SystemSection
  business: BusinessSection
  middleware: MiddlewareSection
}

export interface HealthAssessment {
  overall: HealthLevel
  summary: string
  items: Array<{ group: string; status: HealthLevel; note?: string }>
  suggestion: string
  source: 'llm' | 'rule'
  hash?: string
  assessed_at?: string
}

export interface MonitorOverview {
  success: boolean
  status: CollectorStatus
  snapshot: HealthSnapshot | null
  assessment: HealthAssessment | null
  heartbeat: { collected_at: string; age_seconds: number | null } | null
  server_time: string
  error?: string
  debug?: string
}

export interface RunnerTaskItem {
  runner_id: string
  tenant_id: string | null
  tenant_name: string | null
  profile_id: string
  status: string
  attempt: number
  elapsed_seconds: number
}

export interface MonitorRunners {
  success: boolean
  running: RunnerTaskItem[]
  queued: RunnerTaskItem[]
  error?: string
  debug?: string
}

// ==================== 接口 ====================

export async function getMonitorOverview(): Promise<MonitorOverview> {
  const res = await fetch(`${API_BASE}/overview`, { headers: getSaasAuthHeader() })
  return res.json()
}

export async function getMonitorRunners(): Promise<MonitorRunners> {
  const res = await fetch(`${API_BASE}/runners`, { headers: getSaasAuthHeader() })
  return res.json()
}
