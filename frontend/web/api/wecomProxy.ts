/**
 * 企微代理任务 API（/api/wecom-proxy-tasks 前缀，代理任务契约 §2/§3/§4）。
 *
 * 字段以后端投影实际返回为准（proxy_api.py / proxy_tasks.py）：
 * - 列表 = 任务行 + active_target_count + 运行信息聚合（last_checked_at/
 *   latest_seen_msg_id/idle_read_count/credit_cost/last_error，P7 评审补充）；
 * - 详情 = 配置 + targets + batches（批次分页 batch_offset/batch_limit，
 *   batches_has_more 按取到条数==limit 判定）；
 * - stop 响应额外带 stop_settled（本次是否立即落 stopped）。
 * 失败抛 WecomProxyApiError（含 code/status/field_errors），页面按码映射可读文案。
 */

const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/wecom-proxy-tasks`

import { getTenantScopedKey } from './tenantStorage'
import { credentialGet } from '@/platform/credentialStore'

function getAuthHeaders(): Record<string, string> {
  const path = window.location.pathname
  let tokenKey: string
  if (path.startsWith('/t/')) {
    tokenKey = getTenantScopedKey('saas_token')
  } else {
    tokenKey = 'saas_token'
  }
  const token = credentialGet(tokenKey)
  const headers: Record<string, string> = {}
  if (token) headers['Authorization'] = `Bearer ${token}`
  const match = path.match(/^\/t\/([^/]+)/)
  if (match) headers['X-Tenant-Id'] = match[1]
  headers['Content-Type'] = 'application/json'
  return headers
}

/** 失败错误（沿 WeixinApiError 模式：页面需要按 code 区分提示文案）。 */
export class WecomProxyApiError extends Error {
  code: string
  status: number
  fieldErrors: string[]

  constructor(message: string, code: string, status: number, fieldErrors: string[] = []) {
    super(message)
    this.name = 'WecomProxyApiError'
    this.code = code
    this.status = status
    this.fieldErrors = fieldErrors
  }
}

/** 后端统一返回 {success:true, data:{...}}；此处解包 data，失败抛带码错误。 */
async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method,
    headers: getAuthHeaders(),
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const value = await response.json().catch(() => ({}))
  if (!response.ok || value.success === false) {
    throw new WecomProxyApiError(
      value.error || `请求失败（${response.status}）`,
      value.code || '',
      response.status,
      Array.isArray(value.field_errors) ? value.field_errors : [],
    )
  }
  return (value.data ?? {}) as T
}

// --- 类型定义（与 proxy_tasks.py 投影一致） ---

export type ProxyTaskStatus = 'stopped' | 'running' | 'deleted'
export type ProxyTargetStatus = 'active' | 'halted' | 'removed'

export interface WecomProxyTask {
  id: number
  tenant_id: string
  user_id: string
  profile_id: string
  instance_id: string | null
  device_id: string
  wecom_account_id: string
  self_user_id: string
  title: string
  agent_id: string
  instruction: string
  opening_message?: string | null
  has_started?: boolean
  status: ProxyTaskStatus
  stop_requested: boolean
  revision: number
  created_at: string
  updated_at: string
  deleted_at: string | null
  /** 仅列表投影返回：active 状态目标数 */
  active_target_count?: number
  /** 仅列表投影返回：active 目标最近一次监控读落库时间 */
  last_checked_at?: string | null
  /** 仅列表投影返回：active 目标观测的最新消息位置 */
  latest_seen_msg_id?: number | null
  /** 仅列表投影返回：空闲读取轮次合计（监控无候选推游标累加） */
  idle_read_count?: number
  /** 仅列表投影返回：批次回答积分合计 */
  credit_cost?: number
  /** 仅列表投影返回：最近错误（目标 halted_reason 或最近批次 cli_error，取时间最新） */
  last_error?: string | null
  /** 仅详情投影返回 */
  device_name?: string | null
  /** 已归档的本账号本人显示名称，仅详情投影返回 */
  wecom_account_name?: string | null
  /** 仅详情投影返回 */
  targets?: WecomProxyTarget[]
  /** 仅详情投影返回（按 batch_offset/batch_limit 分页） */
  batches?: WecomProxyBatch[]
  /** 仅详情投影返回：是否还有更多批次 */
  batches_has_more?: boolean
  /** 仅停止响应返回：本次是否立即落 stopped */
  stop_settled?: boolean
}

export interface WecomProxyTarget {
  id: number
  tenant_id: string
  user_id: string
  task_id: number
  session: string
  conversation_name?: string | null
  web_session_id: string | null
  source_generation: string | null
  /** wb1 游标；null = 尚未建基线（首次启动时建立） */
  wb1_cursor: string | null
  /** initial = 创建初选；edit_join_read = 停止后编辑加入（已记水位） */
  baseline_origin: string | null
  processed_position: number | null
  latest_seen_msg_id: number | null
  /** 空闲读取轮次（监控无候选推游标时累加；P7 评审补充） */
  idle_read_count: number
  status: ProxyTargetStatus
  opening_send_status?: 'pending' | 'sending' | 'succeeded' | 'failed' | 'unknown' | 'unconfirmed' | 'cancelled' | null
  opening_send_error?: string | null
  opening_send_invocation_id?: string | null
  halted_reason: string | null
  halted_at: string | null
  created_at: string
  updated_at: string
}

export interface WecomProxyBatch {
  id: number
  task_id: number
  session: string
  conversation_name?: string | null
  batch_end_msg_id: number | null
  agent_id: string | null
  web_session_id: string | null
  runner_id: string | null
  status: string
  end_json: ProxyBatchEndJson | null
  /** 仅投影该批回复已引用的原消息，不包含其他运行快照内容 */
  inbound_messages?: Array<{ msg_id: number; sender_name: string | null; text: string | null }>
  cli_error: string | null
  retry_count: number
  /** 本批回答积分累计（核定落库跨重试累加；P7 评审补充） */
  credit_cost: number
  created_at: string
  updated_at: string
  started_at: string | null
  finished_at: string | null
}

/** 批次结束 JSON v1（契约 §5；页面据此展示，不虚构字段）。 */
export interface ProxyBatchEndJson {
  schema_version?: number
  batch_id?: string
  task_id?: string
  session?: string
  runner_id?: string
  batch_end_msg_id?: number
  needs_reply?: boolean
  replies?: ProxyBatchReply[]
  unanswered?: ProxyBatchUnanswered[]
  [key: string]: unknown
}

export interface ProxyBatchReply {
  inbound_msg_ids?: number[]
  text?: string
  receipt_id?: string
  verification?: string
  verification_detail?: { method?: string; message_id?: number; source_generation?: string }
  [key: string]: unknown
}

export interface ProxyBatchUnanswered {
  msg_id?: number
  reason?: string
  cli_error?: { code?: string; invocation_id?: string }
  [key: string]: unknown
}

// --- 错误码 → 可读文案（沿后端错误码清单；服务端 message 缺省时兜底） ---

export const PROXY_ERROR_LABELS: Record<string, string> = {
  PROXY_CLIENT_UPGRADE: '客户端需升级：当前设备缺少配置检查能力，请安装最新版 Runtime 和 CLI',
  PROXY_CAPABILITY_MISSING: '任务固定的设备缺少所需能力，请安装最新版 Runtime 和 CLI',
  DEVICE_UNAVAILABLE: '当前设备不可用，请确认设备在线及授权',
  DEVICE_OFFLINE: '任务固定的设备当前离线，请保持本机 Runtime 在线后再试',
  PROXY_DEVICE_MISMATCH: '读取来源设备与任务固定设备不一致，未启动',
  AGENT_NOT_ALLOWED: '所选智能体不存在或当前用户无权使用',
  PROXY_NOT_READY: '本机配置检查未通过，请按提示处理设备环境后重试',
  CONFIG_CHECK_FAILED: '本机配置检查执行失败，请稍后重试',
  PROXY_SESSION_OWNED: '同一企微账号的会话已由运行中的任务监控，请先停止该任务或调整会话',
  PROXY_ACCOUNT_MISMATCH: '设备当前企微账号与任务固定账号不一致，未执行',
  PROXY_STATE_CONFLICT: '任务当前状态不允许该操作',
  PROXY_TASK_DELETED: '任务已删除',
  PROXY_ALREADY_DELETED: '任务已删除',
  PROXY_NOT_FOUND: '代理任务不存在或无权访问',
  PROXY_SESSION_INVALID: '所选会话无效，请重新从本地目录选择已登记会话',
  PROXY_NO_TARGETS: '任务没有启用的会话目标，请先编辑添加',
  PROXY_BASELINE_MISSING: '会话基线读取结果缺失，未启动',
  PROXY_BATCH_OCCUPIED: '该会话已有未决批次，不重复建批',
  PROXY_RECOVERY_UNAVAILABLE: '该会话当前无法恢复重试，请查看批次记录',
  PROXY_BATCH_STATE_INVALID: '批次状态不允许该迁移',
  PROXY_BACKLOG_TOO_LARGE: '会话积压超过单次读取上限（20 页×50 条），请先消化积压后重试',
  PROXY_CURSOR_MISSING: '本地读取结果缺少游标，无法记录基线',
  PROXY_READ_INVALID: '本地读取结果不完整，请升级企业微信插件',
  PROXY_READ_FAILED: '本地会话读取失败，请稍后重试',
  VALIDATION_FAILED: '输入校验失败，请检查填写内容',
  UNAUTHORIZED: '登录状态已失效，请重新登录',
}

/** 页面统一错误文案：服务端 message 优先（更具体），缺省时按码映射。 */
export function proxyErrorText(error: unknown): string {
  if (error instanceof WecomProxyApiError) {
    if (error.message) return error.message
    if (error.code && PROXY_ERROR_LABELS[error.code]) return PROXY_ERROR_LABELS[error.code]
    return `操作失败（${error.code || error.status || '未知错误'}）`
  }
  return error instanceof Error ? error.message : String(error)
}

// --- API 封装（七路由） ---

export interface ProxyTaskCreateInput {
  title: string
  instruction: string
  agent_id: string
  sessions: string[]
  profile_id?: string
  instance_id?: string
  opening_message?: string
}

export interface ProxyTaskEditInput {
  title?: string
  instruction?: string
  add_sessions?: string[]
  remove_sessions?: string[]
  opening_message?: string | null
}

export interface ProxyTaskListOptions {
  includeDeleted?: boolean
  limit?: number
  offset?: number
}

export function listProxyTasks(options: ProxyTaskListOptions = {}): Promise<{ tasks: WecomProxyTask[] }> {
  const query = new URLSearchParams()
  if (options.includeDeleted) query.set('include_deleted', 'true')
  if (options.limit) query.set('limit', String(options.limit))
  if (options.offset) query.set('offset', String(options.offset))
  return request('GET', query.size ? `?${query}` : '')
}

export function getProxyTask(taskId: number, options: { batchOffset?: number; batchLimit?: number } = {}): Promise<{ task: WecomProxyTask }> {
  const query = new URLSearchParams()
  if (options.batchLimit) query.set('batch_limit', String(options.batchLimit))
  if (options.batchOffset) query.set('batch_offset', String(options.batchOffset))
  return request('GET', `/${taskId}${query.size ? `?${query}` : ''}`)
}

export function createProxyTask(input: ProxyTaskCreateInput): Promise<{ task: WecomProxyTask }> {
  return request('POST', '', input)
}

export function editProxyTask(taskId: number, input: ProxyTaskEditInput): Promise<{ task: WecomProxyTask }> {
  return request('PUT', `/${taskId}`, input)
}

export function deleteProxyTask(taskId: number): Promise<{ task: WecomProxyTask }> {
  return request('DELETE', `/${taskId}`)
}

export function startProxyTask(taskId: number): Promise<{ task: WecomProxyTask }> {
  return request('POST', `/${taskId}/start`)
}

export function recoverProxyTarget(taskId: number, targetId: number): Promise<{ target: WecomProxyTarget }> {
  return request('POST', `/${taskId}/targets/${targetId}/recover`)
}

export function stopProxyTask(taskId: number): Promise<{ task: WecomProxyTask }> {
  return request('POST', `/${taskId}/stop`)
}
