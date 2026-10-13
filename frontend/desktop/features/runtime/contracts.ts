export type ManagementMethod = 'describe' | 'getState' | 'plugins.list' | 'operations.get' | 'start' | 'stop' | 'pair' | 'plugins.import' | 'plugins.enable' | 'plugins.disable' | 'plugins.uninstall'
export interface ManagementRequest { request_id: string; method: ManagementMethod; params: Record<string, string> }
/** H3 supplies this port from the trusted preload; no executable, path or business invocation. */
export interface RuntimeManagementPort {
  request(request: ManagementRequest): Promise<unknown>
  observe(listener: (event: unknown) => void): () => void
  onDisconnect?(listener: () => void): () => void
  choosePackage(input: { instance_id: string; request_key: string }): Promise<unknown>
  diagnoseConnection?(): Promise<unknown>
  registrationStatus?(): Promise<unknown>
  reconfigureConnection?(input: ConnectionRecoveryInput): Promise<unknown>
}
export interface ConnectionRecoveryInput { mode: 'preserve' | 'new_device' | 'register_new' | 'prepare_reboot' | 'continue_registration'; confirmed: true; server?: string; device_name?: string; pairing_code?: string }
export interface ConnectionDiagnosis {
  version: 1; category: string; summary: string; steps: string[]
  service_verified: boolean; can_preserve: boolean; can_reconfigure: boolean; requires_reboot: boolean
}
export interface ConnectionRecoveryResult { version: 1; status: 'complete' | 'action_required'; diagnosis: ConnectionDiagnosis; new_device: boolean }
export interface HostState {
  instance_id: string; revision: number; supervisor: 'cli' | 'desktop' | 'runtime_app'
  state: 'stopped' | 'starting' | 'running' | 'draining' | 'reconciling' | 'blocked'
  connection: 'unpaired' | 'connecting' | 'online' | 'offline' | 'revoked'
  device?: { device_id: string; name?: string }; blocked_reason?: string
}
export interface PluginSummary { installation_id: string; plugin_id: string; display_name: string; release_id: string; enabled: boolean; ready: boolean; reason?: string; version?: string }
export interface PluginList { instance_id: string; revision: number; plugins: PluginSummary[] }
export interface Description { api_major: 1; host_version: string; features: string[]; instance_id: string; revision: number }
export interface ManagementOperation { operation_id: string; operation: 'start' | 'stop' | 'pair' | 'import' | 'enable' | 'disable' | 'uninstall'; status: 'running' | 'succeeded' | 'failed' | 'reconciling'; code: number; error: string; message?: string }
export interface ManagementResponse { request_id: string; method: ManagementMethod; code: number; error: string; result: Description | HostState | PluginList | ManagementOperation | null }
export interface HostEvent { event: 'state_changed'; instance_id: string; revision: number }

const object = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value)
const text = (value: unknown): value is string => typeof value === 'string' && value.length > 0
const integer = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 0
const keys = (value: Record<string, unknown>, required: string[], optional: string[] = []) => required.every((key) => Object.prototype.hasOwnProperty.call(value, key)) && Object.keys(value).every((key) => required.includes(key) || optional.includes(key))
const member = (value: unknown, allowed: readonly string[]) => typeof value === 'string' && allowed.includes(value)
const revision = (value: Record<string, unknown>) => text(value.instance_id) && integer(value.revision)
export function decodeConnectionDiagnosis(value: unknown): ConnectionDiagnosis {
  if (!object(value) || !keys(value, ['version', 'category', 'summary', 'steps', 'service_verified', 'can_preserve', 'can_reconfigure', 'requires_reboot'])
    || value.version !== 1 || !text(value.category) || !/^[A-Z0-9_]{1,80}$/.test(value.category) || !text(value.summary) || value.summary.length > 2000
    || !Array.isArray(value.steps) || value.steps.length > 12 || !value.steps.every(step => text(step) && step.length <= 2000)
    || !['service_verified', 'can_preserve', 'can_reconfigure', 'requires_reboot'].every(key => typeof value[key] === 'boolean')) throw new Error('连接检查回复不兼容，请重新安装本批客户端')
  return value as unknown as ConnectionDiagnosis
}
export function decodeConnectionRecoveryResult(value: unknown): ConnectionRecoveryResult {
  if (!object(value) || !keys(value, ['version', 'status', 'diagnosis', 'new_device']) || value.version !== 1 || !member(value.status, ['complete', 'action_required']) || typeof value.new_device !== 'boolean') throw new Error('连接恢复回复不兼容，原请求未自动重复')
  return { version: 1, status: value.status as ConnectionRecoveryResult['status'], diagnosis: decodeConnectionDiagnosis(value.diagnosis), new_device: value.new_device }
}
export function isHostState(value: unknown): value is HostState {
  if (!object(value) || !keys(value, ['instance_id', 'revision', 'supervisor', 'state', 'connection'], ['device', 'blocked_reason']) || !revision(value)) return false
  if (!member(value.supervisor, ['cli', 'desktop', 'runtime_app']) || !member(value.state, ['stopped', 'starting', 'running', 'draining', 'reconciling', 'blocked']) || !member(value.connection, ['unpaired', 'connecting', 'online', 'offline', 'revoked'])) return false
  if (Object.prototype.hasOwnProperty.call(value, 'blocked_reason') && !text(value.blocked_reason)) return false
  return !Object.prototype.hasOwnProperty.call(value, 'device') || (object(value.device) && keys(value.device, ['device_id'], ['name']) && text(value.device.device_id) && (!Object.prototype.hasOwnProperty.call(value.device, 'name') || text(value.device.name)))
}
export function isPluginList(value: unknown): value is PluginList {
  return object(value) && keys(value, ['instance_id', 'revision', 'plugins']) && revision(value) && Array.isArray(value.plugins) && value.plugins.every((plugin) => object(plugin) && keys(plugin, ['installation_id', 'plugin_id', 'display_name', 'release_id', 'enabled', 'ready'], ['reason', 'version']) && ['installation_id', 'plugin_id', 'display_name', 'release_id'].every((key) => text(plugin[key])) && typeof plugin.enabled === 'boolean' && typeof plugin.ready === 'boolean' && (!Object.prototype.hasOwnProperty.call(plugin, 'version') || text(plugin.version)) && (!Object.prototype.hasOwnProperty.call(plugin, 'reason') || text(plugin.reason)))
}
export function isDescription(value: unknown): value is Description {
  return object(value) && keys(value, ['api_major', 'host_version', 'features', 'instance_id', 'revision']) && value.api_major === 1 && text(value.host_version) && revision(value) && Array.isArray(value.features) && value.features.every(text) && new Set(value.features).size === value.features.length
}
export function isOperation(value: unknown): value is ManagementOperation {
  if (!object(value) || !keys(value, ['operation_id', 'operation', 'status', 'code', 'error'], ['message']) || !text(value.operation_id) || !integer(value.code) || typeof value.error !== 'string' || !member(value.operation, ['start', 'stop', 'pair', 'import', 'enable', 'disable', 'uninstall']) || !member(value.status, ['running', 'succeeded', 'failed', 'reconciling'])) return false
  if (Object.prototype.hasOwnProperty.call(value, 'message') && !text(value.message)) return false
  return value.status === 'failed' ? value.code > 0 && text(value.error) : value.code === 0 && value.error === ''
}
export function isHostEvent(value: unknown): value is HostEvent { return object(value) && keys(value, ['event', 'instance_id', 'revision']) && value.event === 'state_changed' && revision(value) }
export function decodeResponse(value: unknown, request: ManagementRequest): ManagementResponse {
  if (!object(value) || !keys(value, ['request_id', 'method', 'code', 'error', 'result']) || value.request_id !== request.request_id || value.method !== request.method || !integer(value.code) || typeof value.error !== 'string') throw new Error('Runtime 管理回复格式不兼容')
  if (value.code > 0) {
    if (!text(value.error) || value.result !== null) throw new Error('Runtime 管理错误格式不兼容')
  } else {
    if (value.error !== '') throw new Error('Runtime 管理回复格式不兼容')
    const valid = request.method === 'describe' ? isDescription(value.result) : request.method === 'getState' ? isHostState(value.result) : request.method === 'plugins.list' ? isPluginList(value.result) : isOperation(value.result)
    if (!valid) throw new Error('Runtime 管理结果格式不兼容')
    if (isOperation(value.result) && request.method !== 'operations.get' && value.result.operation !== request.method.replace('plugins.', '')) throw new Error('Runtime 操作类型不匹配')
  }
  return value as unknown as ManagementResponse
}
export function decodeSelection(value: unknown): { selection_ref: string; label: string } | null {
  if (value === null) return null
  if (!object(value) || !keys(value, ['selection_ref', 'label']) || !text(value.selection_ref) || !text(value.label)) throw new Error('包选择回复格式不兼容')
  return { selection_ref: value.selection_ref, label: value.label }
}
