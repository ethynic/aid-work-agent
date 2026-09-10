/**
 * 微信营销自动化工作台 API 客户端（P3-A2，R54②）
 *
 * 契约快照来源（后端 schema 为权威，微信计划 §8）：
 * - 请求/响应模型：src/weixin_marketing/models.py（TriggerConfig / ContentBlockSpec
 *   判别联合、各 Input 模型）——字段与校验规则手写快照，修改须前后端同步；
 * - 路由与错误码：src/weixin_marketing/api.py（23 条路由；稳定码矩阵
 *   NOT_FOUND / CONFLICT / TENANT_NOT_ALLOWED / RETRY_EVIDENCE_REQUIRED /
 *   VALIDATION_FAILED / QUOTA_EXCEEDED / ADAPTER_UNREGISTERED / VERIFY_FAILED /
 *   PREFLIGHT_FAILED / IDEMPOTENCY_*）；
 * - 响应字段：src/weixin_marketing/service.py（automations/runs/deliveries）
 *   与 src/weixin_marketing/workbench.py（test-send/group-searches/
 *   group-bindings/devices）的返回 dict；
 * - 状态枚举：src/weixin_marketing/constants.py（AUTOMATION_STATUSES 等）与
 *   src/desktop_automation/constants.py（RUN_TERMINAL_STATES / delivery
 *   effect / phase）。
 *
 * 与既有 api/* 文件的差异：失败时抛 WeixinApiError（含 code/status/field_errors）
 * 而非返回 envelope 对象——编辑器需要区分 409 版本冲突并保留用户草稿（R55）。
 * Idempotency-Key 由前端生成（api.py Header(alias="Idempotency-Key")，长度 8-200；
 * 同 key 异 payload 409）：每次逻辑动作生成新 key，配合按钮提交中禁用防双击。
 */

import { getAuthHeader } from './auth'

const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/weixin-marketing`

// ==================== 契约快照：枚举 ====================

/** automation.status（constants.AUTOMATION_STATUSES） */
export type AutomationStatus = 'draft' | 'active' | 'paused' | 'archived'

/** revision.status（constants.REVISION_STATUSES） */
export type RevisionStatus = 'draft' | 'published' | 'superseded'

/** run.state（RUN_STATES = pending/running/waiting_device + RUN_TERMINAL_STATES） */
export type RunState =
  | 'pending'
  | 'running'
  | 'waiting_device'
  | 'succeeded'
  | 'failed'
  | 'cancelled'
  | 'partial'
  | 'unknown'
  | 'expired'

/** run 终态（desktop_automation.constants.RUN_TERMINAL_STATES）——轮询停止条件 */
export const RUN_TERMINAL_STATES: readonly RunState[] = [
  'succeeded',
  'failed',
  'cancelled',
  'partial',
  'unknown',
  'expired',
]

export function isRunTerminal(state: string): boolean {
  return (RUN_TERMINAL_STATES as readonly string[]).includes(state)
}

/** delivery.state（desktop_automation.constants） */
export type DeliveryState =
  | 'pending'
  | 'dispatched'
  | 'succeeded'
  | 'failed'
  | 'unknown'
  | 'expired'
  | 'skipped'

/** delivery 聚合 effect（R10） */
export type DeliveryEffect = 'none' | 'applied' | 'unknown'

/** attempt/operation phase（R10） */
export type OperationPhase = 'prepared' | 'may_have_started' | 'verified' | 'unknown'

/** group_bindings.state（P3-A1 增 rejected） */
export type GroupBindingState = 'pending' | 'complete' | 'rejected' | 'disabled'

/** 群搜索/预检对外状态（workbench._map_search_status） */
export type AsyncSearchStatus = 'pending' | 'running' | 'succeeded' | 'failed'

/** 内容块约束（constants：BLOCK_TEXT_MAX_LENGTH / BLOCK_TEXT_FORBIDDEN_CHARS / DEFAULT_MAX_BLOCKS） */
export const BLOCK_TEXT_MAX_LENGTH = 500
export const BLOCK_TEXT_FORBIDDEN_CHARS = ['\n', '\r', '\x00']
export const MAX_BLOCKS = 20

// ==================== 契约快照：TriggerConfig 判别联合（models.py） ====================

export interface OnceTriggerConfig {
  type: 'once'
  /** ISO 时间（后端 tz-aware：naive 按 UTC 解释，前端提交一律带时区） */
  run_at: string
  timezone: string
  grace_seconds: number
}

export interface IntervalTriggerConfig {
  type: 'interval'
  start_at: string
  interval_seconds: number
  timezone: string
  grace_seconds: number
  miss_policy: 'skip_overlap' | 'catch_up_latest'
  ends_at?: string | null
  max_count?: number | null
}

export interface CalendarTriggerConfig {
  type: 'calendar'
  timezone: string
  cron_expr?: string | null
  year?: string | null
  month?: string | null
  day?: string | null
  week?: string | null
  day_of_week?: string | null
  hour?: string | null
  minute?: string | null
  second?: string | null
  grace_seconds: number
  miss_policy: 'skip_overlap' | 'catch_up_latest'
  ends_at?: string | null
  max_count?: number | null
}

export interface EventTriggerConfig {
  type: 'event'
  source_ref: string
  event_type: string
  delay_seconds: number
  timezone: string
}

export type TriggerConfig =
  | OnceTriggerConfig
  | IntervalTriggerConfig
  | CalendarTriggerConfig
  | EventTriggerConfig

export type TriggerType = TriggerConfig['type']

// ==================== 契约快照：ContentBlock 判别联合（models.py） ====================

export interface TextBlockSpec {
  type: 'text'
  text_content: string
}

export interface LinkBlockSpec {
  type: 'link'
  url: string
}

export interface ImageBlockSpec {
  type: 'image'
  asset_id: string
}

export type ContentBlockSpec = TextBlockSpec | LinkBlockSpec | ImageBlockSpec

// ==================== 契约快照：行/详情结构（service.py 返回 dict） ====================

export interface AutomationRow {
  id: string
  tenant_id?: string
  user_id?: string
  name: string
  status: AutomationStatus
  active_revision_id?: string | null
  draft_revision_id?: string | null
  owner_scope?: string
  version: number
  pause_reason?: string | null
  created_at?: string
  updated_at?: string
}

export interface RevisionRow {
  id: string
  tenant_id?: string
  automation_id?: string
  user_id?: string
  revision_no: number
  executor_type?: string
  status: RevisionStatus
  /** service 详情投影剔除 trigger_json/policy_json（正文不进列表，草稿触发另有 draft_trigger） */
  group_binding_id?: string | null
  content_hash?: string | null
  authorized_by?: string | null
  authorization_source?: string | null
  source_message_id?: string | null
  published_at?: string | null
  created_at?: string
  updated_at?: string
}

export interface RunSummaryRow {
  id: string
  state: RunState
  created_at?: string
  finished_at?: string | null
}

/** GET /automations/{id} data（service._automation_detail_on；P3-A1 增 draft_blocks，复审 P1-1 增 active_*） */
export interface AutomationDetail {
  automation: AutomationRow
  revisions: RevisionRow[]
  draft_trigger: TriggerConfig | null
  recent_runs: RunSummaryRow[]
  /** draft revision 有序块（无草稿时空列表；旧后端未上线时可能缺省/null） */
  draft_blocks?: DraftBlockRow[] | null
  /** active revision 有序块（结构与 draft_blocks 一致；无已发布版本时空列表/缺省） */
  active_blocks?: DraftBlockRow[] | null
  /** active revision 触发配置（结构化，不回退默认值；无已发布版本 null/缺省） */
  active_trigger?: TriggerConfig | null
  /** active revision 的 group_binding_id（无已发布版本 null/缺省） */
  active_group_binding_id?: string | null
}

/** GET /runs items（service.list_runs，desktop_automation_runs 列投影） */
export interface RunListItem {
  id: string
  occurrence_id?: string | null
  scenario_key?: string
  task_ref: string
  revision_ref?: string | null
  user_id?: string
  state: RunState
  device_id?: string | null
  due_at?: string | null
  expires_at?: string | null
  result_json?: Record<string, unknown> | null
  created_at?: string
  finished_at?: string | null
}

/** run 详情内 delivery 摘要（service.get_run_detail + api._enrich_latest_attempts） */
export interface DeliveryDetail {
  id: string
  position?: number
  operation?: string | null
  target_ref?: string | null
  payload_ref?: string | null
  payload_hash?: string | null
  state: DeliveryState
  effect?: DeliveryEffect | null
  phase?: OperationPhase | null
  created_at?: string
  updated_at?: string
  finished_at?: string | null
  /** 末次 attempt 摘要；safe_to_retry 仅 true 表示可重试（None/False 均不可） */
  latest_attempt?: {
    attempt_id: string
    attempt_no?: number | null
    effect?: DeliveryEffect | null
    phase?: OperationPhase | null
    safe_to_retry?: boolean | null
    evidence_ref?: string | null
  } | null
}

/**
 * 草稿内容块行（P3-A1 并行契约：GET /automations/{id} 响应增 draft_blocks——
 * draft revision 的有序块 [{position,kind,text_content,url,asset_id}]，无草稿时 null）。
 * kind 对应 BLOCK_KIND_*；与业务字段互斥（text→text_content，link→url，image→asset_id）。
 */
export interface DraftBlockRow {
  position: number
  kind: 'text' | 'link' | 'image'
  text_content?: string | null
  url?: string | null
  asset_id?: string | null
}

export interface RunDetailData {
  run: RunListItem
  deliveries: DeliveryDetail[]
}

// ==================== 契约快照：工作台结构（workbench.py 返回 dict） ====================

/** GET /devices items（workbench.list_devices） */
export interface DeviceItem {
  device_id: string
  name?: string | null
  platform?: string | null
  runtime_version?: string | null
  selected?: boolean
  status?: string | null
  online: boolean
  last_seen_at?: string | null
  created_at?: string | null
  weixin: {
    available: boolean
    provider_key?: string | null
    protocol_version?: number | string | null
  }
}

/** 群搜索候选（设备回传透传；target_ref 为绑定依据，title 用于展示与精确核验） */
export interface GroupSearchCandidate {
  target_ref: string
  title: string
  [key: string]: unknown
}

/** GET /group-searches/{id} data（workbench.get_group_search） */
export interface GroupSearchDetail {
  search_id: string
  device_id: string
  status: AsyncSearchStatus
  created_at?: string | null
  finished_at?: string | null
  items?: GroupSearchCandidate[]
  truncated?: boolean
  error_code?: string
  error_message?: string | null
}

/** GET /group-bindings items（workbench.list_group_bindings 列投影） */
export interface GroupBindingItem {
  id: string
  device_id: string
  account_binding_id?: string | null
  label: string
  identity_evidence_ref?: string | null
  identity_version?: number | null
  state: GroupBindingState
  verified_at?: string | null
  created_at?: string
  updated_at?: string
}

// ==================== 请求输入（models.py Input 快照） ====================

export interface AutomationCreateInput {
  name: string
  trigger: TriggerConfig
  blocks: ContentBlockSpec[]
  group_binding_id: string
  policy?: Record<string, unknown>
}

export interface DraftUpdateInput {
  expected_version: number
  name?: string
  trigger?: TriggerConfig
  blocks?: ContentBlockSpec[]
  group_binding_id?: string
  policy?: Record<string, unknown>
}

export interface PublishInput {
  expected_version: number
  revision_id?: string | null
  authorization_source?: string
}

export interface VersionedActionInput {
  expected_version: number
  reason?: string | null
}

export interface DeliveryResolveInput {
  verdict: 'delivered' | 'not_delivered' | 'uncertain'
  decision?: 'confirmed_not_sent' | null
  /** decision 非 null 时必填（max 2000） */
  note?: string | null
}

export interface TestSendInput {
  group_binding_id: string
  block_position: number
}

export interface GroupSearchCreateInput {
  device_id: string
  keyword: string
}

export interface GroupBindingCreateInput {
  device_id: string
  label: string
  target_ref: string
  search_id: string
  account_binding_id?: string | null
}

// ==================== 动作结果（service/workbench 返回 dict 快照） ====================

export interface ManualRunResult {
  occurrence_id: string | null
  run_id: string | null
  created: boolean
}

export interface TestSendResult {
  automation_id: string
  run_id: string
  occurrence_id: string | null
  delivery_id: string
  invocation_id: string
  attempt_id: string
  request_id: string
  block_position: number
  group_binding_id: string
}

export interface PublishResult {
  automation_id: string
  revision_id: string
  revision_no: number
  authorization_epoch: number | null
  version: number
}

export interface TransitionResult {
  automation_id: string
  status: AutomationStatus
  version: number
  authorization_epoch: number | null
  cancelled_runs: number
}

export interface CancelRunResult {
  run_id: string
  state: RunState
  changed: boolean
}

export interface ResolveDeliveryResult {
  delivery_id: string
  verdict: string
  decision: string | null
  machine_state: string | null
  machine_effect: string | null
}

export interface RetryDeliveryResult {
  delivery_id: string
  attempt_id: string
  attempt_no: number
  predecessor_attempt_id: string
  invocation_id: string
  request_id: string
}

export interface ValidateResult {
  ok: boolean
  errors: { field: string; message: string }[]
  warnings: { field: string; message: string }[]
  next_fires: string[]
  revision_id: string
}

export interface GroupSearchCreateResult {
  search_id: string
  device_id: string
  status: AsyncSearchStatus
}

export interface GroupBindingCreateResult {
  binding_id: string
  state: GroupBindingState
  device_id: string
  account_binding_id: string | null
  label: string
  target_ref: string
  search_id: string
  created_at?: string
}

export interface VerifyBindingResult {
  binding_id: string
  state: GroupBindingState
  result: 'verified' | 'rejected'
  reason: string
  verify_search_id: string
  identity_evidence_ref: string
  exact_matches: number
  verified_at?: string
}

export interface PreflightResult {
  device_id: string
  invocation_id: string
  environment: Record<string, unknown>
  finished_at?: string | null
}

// ==================== 列表响应 ====================

export interface ListResult<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

// ==================== 错误处理 ====================

/** 后端稳定错误码（api.py CODE_*） */
export const WX_ERROR_CODES = {
  NOT_FOUND: 'NOT_FOUND',
  CONFLICT: 'CONFLICT',
  TENANT_NOT_ALLOWED: 'TENANT_NOT_ALLOWED',
  RETRY_EVIDENCE_REQUIRED: 'RETRY_EVIDENCE_REQUIRED',
  VALIDATION_FAILED: 'VALIDATION_FAILED',
  QUOTA_EXCEEDED: 'QUOTA_EXCEEDED',
  ADAPTER_UNREGISTERED: 'ADAPTER_UNREGISTERED',
  INTERNAL_ERROR: 'INTERNAL_ERROR',
  IDEMPOTENCY_PAYLOAD_CONFLICT: 'IDEMPOTENCY_PAYLOAD_CONFLICT',
  IDEMPOTENCY_IN_PROGRESS: 'IDEMPOTENCY_IN_PROGRESS',
  IDEMPOTENCY_KEY_INVALID: 'IDEMPOTENCY_KEY_INVALID',
  VERIFY_FAILED: 'VERIFY_FAILED',
  PREFLIGHT_FAILED: 'PREFLIGHT_FAILED',
  NETWORK_ERROR: 'NETWORK_ERROR',
} as const

export type WxErrorCode = (typeof WX_ERROR_CODES)[keyof typeof WX_ERROR_CODES]

/** 统一 API 错误：携带 HTTP 状态与稳定码；409 CONFLICT = 版本/状态机冲突信号 */
export class WeixinApiError extends Error {
  readonly status: number
  readonly code: string
  readonly fieldErrors: { field: string; message: string }[]

  constructor(
    message: string,
    status: number,
    code: string,
    fieldErrors: { field: string; message: string }[] = [],
  ) {
    super(message)
    this.name = 'WeixinApiError'
    this.status = status
    this.code = code
    this.fieldErrors = fieldErrors
  }
}

/** 版本 CAS/状态机冲突（409 CONFLICT）——编辑器保留草稿并提示差异 */
export function isVersionConflict(err: unknown): err is WeixinApiError {
  return err instanceof WeixinApiError && err.status === 409 && err.code === WX_ERROR_CODES.CONFLICT
}

/** 未知效果重试缺人工证据（409 RETRY_EVIDENCE_REQUIRED）——引导先 resolve */
export function isRetryEvidenceRequired(err: unknown): err is WeixinApiError {
  return (
    err instanceof WeixinApiError && err.code === WX_ERROR_CODES.RETRY_EVIDENCE_REQUIRED
  )
}

function newIdempotencyKey(prefix: string): string {
  const uuid =
    typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(16).slice(2)}`
  return `${prefix}-${uuid}`
}

interface Envelope<T> {
  success: boolean
  data?: T
  error?: string
  code?: string
  field_errors?: { field: string; message: string }[]
}

/** 统一请求：解析 envelope；失败抛 WeixinApiError（网络异常归一 NETWORK_ERROR） */
async function request<T>(
  path: string,
  init: RequestInit & { idempotencyKey?: string; extraHeaders?: Record<string, string> } = {},
  signal?: AbortSignal,
): Promise<T> {
  const headers: Record<string, string> = { ...getAuthHeader(), ...init.extraHeaders }
  if (init.body != null) headers['Content-Type'] = 'application/json'
  if (init.idempotencyKey) headers['Idempotency-Key'] = init.idempotencyKey
  let res: Response
  try {
    res = await fetch(`${API_BASE}${path}`, {
      method: init.method ?? 'GET',
      headers,
      body: init.body != null ? String(init.body) : undefined,
      signal: signal ?? init.signal,
    })
  } catch (e) {
    // 调用方主动取消（AbortController.abort）原样抛出，不归一为 NETWORK_ERROR——
    // 上层（轮询）据此跳过退避重排，避免卸载/切路由后轮询复活（V-P1-2）
    if (e instanceof DOMException && e.name === 'AbortError') throw e
    throw new WeixinApiError(
      '网络异常，请求未送达（可重试）',
      0,
      WX_ERROR_CODES.NETWORK_ERROR,
    )
  }
  let body: Envelope<T> | null = null
  try {
    body = (await res.json()) as Envelope<T>
  } catch {
    body = null
  }
  if (!res.ok || !body || body.success !== true) {
    throw new WeixinApiError(
      body?.error || `请求失败（HTTP ${res.status}）`,
      res.status,
      body?.code || WX_ERROR_CODES.INTERNAL_ERROR,
      body?.field_errors || [],
    )
  }
  return body.data as T
}

function jsonBody(value: unknown): string {
  return JSON.stringify(value)
}

function query(params: Record<string, string | number | undefined | null>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, String(value))
  }
  const s = search.toString()
  return s ? `?${s}` : ''
}

// ==================== automations ====================

export function listAutomations(
  params: {
    keyword?: string
    status?: AutomationStatus | ''
    trigger_type?: TriggerType | ''
    page?: number
    page_size?: number
  } = {},
  signal?: AbortSignal,
): Promise<ListResult<AutomationRow>> {
  return request(`/automations${query(params)}`, { signal })
}

export function createAutomation(input: AutomationCreateInput): Promise<AutomationDetail> {
  return request('/automations', {
    method: 'POST',
    body: jsonBody(input),
    idempotencyKey: newIdempotencyKey('wxm-create'),
  })
}

export function getAutomation(automationId: string, signal?: AbortSignal): Promise<AutomationDetail> {
  return request(`/automations/${encodeURIComponent(automationId)}`, { signal })
}

/**
 * 更新草稿（版本 CAS）：expected_version 为权威，同时携带 If-Match 头（等价别名，
 * 两信道等值不会触发后端 422）。409 CONFLICT 时直接抛错，由编辑器保留草稿并提示差异。
 */
export function updateDraft(
  automationId: string,
  input: DraftUpdateInput,
): Promise<AutomationDetail> {
  return request(`/automations/${encodeURIComponent(automationId)}/draft`, {
    method: 'PUT',
    body: jsonBody(input),
    extraHeaders: { 'If-Match': String(input.expected_version) },
  })
}

export function validateAutomation(automationId: string): Promise<ValidateResult> {
  return request(`/automations/${encodeURIComponent(automationId)}/validate`, { method: 'POST', body: jsonBody({}) })
}

export function publishAutomation(automationId: string, input: PublishInput): Promise<PublishResult> {
  return request(`/automations/${encodeURIComponent(automationId)}/publish`, {
    method: 'POST',
    body: jsonBody(input),
    idempotencyKey: newIdempotencyKey('wxm-publish'),
  })
}

function versionedTransition(
  automationId: string,
  action: 'pause' | 'resume' | 'archive',
  input: VersionedActionInput,
): Promise<TransitionResult> {
  return request(`/automations/${encodeURIComponent(automationId)}/${action}`, {
    method: 'POST',
    body: jsonBody(input),
  })
}

export function pauseAutomation(automationId: string, expectedVersion: number, reason?: string) {
  return versionedTransition(automationId, 'pause', { expected_version: expectedVersion, reason })
}

export function resumeAutomation(automationId: string, expectedVersion: number) {
  return versionedTransition(automationId, 'resume', { expected_version: expectedVersion })
}

export function archiveAutomation(automationId: string, expectedVersion: number) {
  return versionedTransition(automationId, 'archive', { expected_version: expectedVersion })
}

/** 手动触发（202）：返回 run_id 供跳转运行详情 */
export function runAutomation(automationId: string): Promise<ManualRunResult> {
  return request(`/automations/${encodeURIComponent(automationId)}/run`, {
    method: 'POST',
    body: jsonBody({}),
    idempotencyKey: newIdempotencyKey('wxm-run'),
  })
}

// ==================== runs / deliveries ====================

export function listRuns(
  params: { automation_id?: string; state?: RunState | ''; page?: number; page_size?: number } = {},
  signal?: AbortSignal,
): Promise<ListResult<RunListItem>> {
  return request(`/runs${query(params)}`, { signal })
}

export function getRunDetail(runId: string, signal?: AbortSignal): Promise<RunDetailData> {
  return request(`/runs/${encodeURIComponent(runId)}`, { signal })
}

export function cancelRun(runId: string): Promise<CancelRunResult> {
  return request(`/runs/${encodeURIComponent(runId)}/cancel`, { method: 'POST', body: jsonBody({}) })
}

export function resolveDelivery(
  deliveryId: string,
  input: DeliveryResolveInput,
): Promise<ResolveDeliveryResult> {
  return request(`/deliveries/${encodeURIComponent(deliveryId)}/resolve`, {
    method: 'POST',
    body: jsonBody(input),
  })
}

export function retryDelivery(deliveryId: string): Promise<RetryDeliveryResult> {
  return request(`/deliveries/${encodeURIComponent(deliveryId)}/retry`, {
    method: 'POST',
    body: jsonBody({ confirm: true }),
  })
}

// ==================== test-send / 群搜索 / 绑定 / 设备 ====================

export function testSend(
  automationId: string,
  input: TestSendInput,
): Promise<TestSendResult> {
  return request(`/automations/${encodeURIComponent(automationId)}/test-send`, {
    method: 'POST',
    body: jsonBody(input),
    idempotencyKey: newIdempotencyKey('wxm-test'),
  })
}

export function createGroupSearch(input: GroupSearchCreateInput): Promise<GroupSearchCreateResult> {
  return request('/group-searches', {
    method: 'POST',
    body: jsonBody(input),
    idempotencyKey: newIdempotencyKey('wxm-gs'),
  })
}

export function getGroupSearch(searchId: string, signal?: AbortSignal): Promise<GroupSearchDetail> {
  return request(`/group-searches/${encodeURIComponent(searchId)}`, { signal })
}

export function createGroupBinding(input: GroupBindingCreateInput): Promise<GroupBindingCreateResult> {
  return request('/group-bindings', {
    method: 'POST',
    body: jsonBody(input),
    idempotencyKey: newIdempotencyKey('wxm-gb'),
  })
}

export function listGroupBindings(
  params: { state?: GroupBindingState | ''; device_id?: string; page?: number; page_size?: number } = {},
  signal?: AbortSignal,
): Promise<ListResult<GroupBindingItem>> {
  return request(`/group-bindings${query(params)}`, { signal })
}

export function verifyGroupBinding(bindingId: string): Promise<VerifyBindingResult> {
  return request(`/group-bindings/${encodeURIComponent(bindingId)}/verify`, {
    method: 'POST',
    body: jsonBody({}),
    idempotencyKey: newIdempotencyKey('wxm-verify'),
  })
}

export function listDevices(signal?: AbortSignal): Promise<{ items: DeviceItem[]; total: number }> {
  return request('/devices', { signal })
}

export function preflightDevice(deviceId: string): Promise<PreflightResult> {
  return request(`/devices/${encodeURIComponent(deviceId)}/preflight`, {
    method: 'POST',
    body: jsonBody({}),
    idempotencyKey: newIdempotencyKey('wxm-pf'),
  })
}
