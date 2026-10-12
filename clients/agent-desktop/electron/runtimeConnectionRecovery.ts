import { randomUUID } from 'node:crypto'
import { dirname, join } from 'node:path'
import { pathToFileURL } from 'node:url'
import type { RuntimeSupervisor } from './runtimeSupervisor.js'

export interface ConnectionDiagnosis {
  version: 1; category: string; summary: string; steps: string[]
  service_verified: boolean; can_preserve: boolean; can_reconfigure: boolean; requires_reboot: boolean
}
export interface ConnectionRecoveryInput { mode: 'preserve' | 'new_device' | 'prepare_reboot'; confirmed: true; server?: string; device_name?: string; pairing_code?: string }
export interface ConnectionRecoveryResult { version: 1; status: 'complete' | 'action_required'; diagnosis: ConnectionDiagnosis; new_device: boolean }
export interface RecoveryInspection { diagnosis: ConnectionDiagnosis; resources_safe: boolean; identity_digest: string | null }
const object = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v)
export function validConnectionDiagnosis(v: unknown): v is ConnectionDiagnosis {
  return object(v) && Object.keys(v).sort().join(',') === 'can_preserve,can_reconfigure,category,requires_reboot,service_verified,steps,summary,version'
    && v.version === 1 && typeof v.category === 'string' && /^[A-Z0-9_]{1,80}$/.test(v.category)
    && typeof v.summary === 'string' && !!v.summary && v.summary.length <= 2000
    && Array.isArray(v.steps) && v.steps.length <= 12 && v.steps.every(s => typeof s === 'string' && !!s && s.length <= 2000)
    && ['service_verified', 'can_preserve', 'can_reconfigure', 'requires_reboot'].every(k => typeof v[k] === 'boolean')
}
export function validRecoveryInspection(v: unknown): v is RecoveryInspection {
  return object(v) && Object.keys(v).sort().join(',') === 'diagnosis,identity_digest,resources_safe' && validConnectionDiagnosis(v.diagnosis)
    && typeof v.resources_safe === 'boolean' && (v.identity_digest === null || typeof v.identity_digest === 'string' && /^[0-9a-f]{64}$/.test(v.identity_digest))
}
const issue = (category: string, summary: string, steps: string[]): ConnectionDiagnosis => ({ version: 1, category, summary, steps, service_verified: false, can_preserve: false, can_reconfigure: false, requires_reboot: false })
function parseInput(v: unknown): ConnectionRecoveryInput {
  if (!object(v) || v.confirmed !== true || typeof v.mode !== 'string' || !['preserve', 'new_device', 'prepare_reboot'].includes(v.mode)) throw new Error('请先明确确认连接恢复操作')
  const keys = v.mode === 'new_device' ? ['mode', 'confirmed', 'server', 'device_name', 'pairing_code'] : ['mode', 'confirmed']
  if (Object.keys(v).sort().join(',') !== keys.sort().join(',')) throw new Error('连接恢复参数无效')
  if (v.mode === 'new_device') {
    if (typeof v.server !== 'string' || v.server.length > 4096 || typeof v.device_name !== 'string' || !v.device_name.trim() || v.device_name.length > 100
      || typeof v.pairing_code !== 'string' || !v.pairing_code.trim() || v.pairing_code.length > 128) throw new Error('请填写服务地址、设备名称和新配对码')
    try { const url = new URL(v.server); if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) throw new Error() } catch { throw new Error('请填写管理员提供的企业服务地址') }
  }
  return v as unknown as ConnectionRecoveryInput
}
/** Main-only orchestration. Never accepts home/executable/archive paths from renderer. */
export class RuntimeConnectionRecovery {
  private active = false
  get busy() { return this.active }
  constructor(private options: { home: string; node: string; hostEntry: string; supervisor: RuntimeSupervisor; onBeforeRecovery(): Promise<void> }) {}
  private module(name: string): Promise<Record<string, any>> { return import(pathToFileURL(join(dirname(this.options.hostEntry), name)).href) }
  async diagnose(): Promise<ConnectionDiagnosis> {
    if (this.active) return issue('RECOVERY_BUSY', '连接恢复正在进行', ['等待当前恢复完成，不要重复安装或配对。'])
    this.active = true
    this.options.supervisor.suspend(true)
    try { return (await this.options.supervisor.connectionInspection('diagnose')).diagnosis }
    catch {
      if (this.options.supervisor.instanceId) return { ...issue('LOCAL_CHECK_UNAVAILABLE', '本机检查尚未完成，管理操作可能仍在收尾', ['停止执行；仍无法收尾时点击登记重启恢复，登记成功后正常重启Windows。', '重启后重新检查，再按检查结果继续处理。']), requires_reboot: true, can_reconfigure: true }
      return issue('MANAGEMENT_UNAVAILABLE', '本机管理连接尚未建立', ['退出其他Runtime或旧命令行实例；只关闭窗口不会退出后台。', '仍无法退出时正常重启Windows，再打开本客户端检查。', '检查安装文件完整性、当前Windows用户和配置目录权限。'])
    }
    finally { this.options.supervisor.suspend(false); this.active = false }
  }
  private async settleOperation(method: 'pair' | 'stop', params: Record<string, string>): Promise<{ code: number; message: string }> {
    const supervisor = this.options.supervisor
    const accepted = await supervisor.recoveryManagementRequest(method, { request_key: randomUUID(), ...params })
    if (accepted.code !== 0) return { code: accepted.code, message: accepted.error }
    let op = accepted.result as { operation_id?: string; status?: string; code?: number; error?: string; message?: string }
    if (!op?.operation_id) throw new Error('管理操作未确认')
    const deadline = Date.now() + (method === 'stop' ? 30_000 : 90_000)
    while (op.status === 'running' && Date.now() < deadline) {
      await new Promise(resolve => setTimeout(resolve, 200))
      const result = await supervisor.recoveryManagementRequest('operations.get', { operation_id: op.operation_id! })
      if (result.code !== 0) return { code: result.code, message: result.error }
      op = result.result as typeof op
    }
    return op.status === 'succeeded' ? { code: 0, message: '' } : { code: op.code || 11, message: op.error || op.message || '操作尚未确认，请按检查结果处理；未自动重复请求' }
  }
  async reconfigure(value: unknown): Promise<ConnectionRecoveryResult> {
    const input = parseInput(value)
    if (this.active) return { version: 1, status: 'action_required', diagnosis: issue('RECOVERY_BUSY', '连接恢复正在进行', ['等待当前恢复完成。']), new_device: false }
    this.active = true; this.options.supervisor.suspend(true)
    let lease: { release(): Promise<void> } | null = null
    let newDevice = false
    try {
      await this.options.onBeforeRecovery()
      const state = await this.options.supervisor.recoveryManagementRequest('getState', {})
      if (input.mode !== 'prepare_reboot' && state.code === 0 && object(state.result) && ['starting', 'running', 'reconciling', 'draining'].includes(String(state.result.state))) {
        // A passive historical stop can reject; its resource proof below is independent.
        await this.settleOperation('stop', {}).catch(() => ({ code: 11, message: '' }))
      }
      let inspection: RecoveryInspection
      try { inspection = await this.options.supervisor.connectionInspection(input.mode === 'prepare_reboot' ? 'prepare_reboot' : input.mode === 'preserve' ? 'prepare_preserve' : 'prepare_new') }
      catch {
        // A hung drain cannot block evidence registration. Registration never
        // closes a process or archives a home while execution is still owned.
        const registered = await this.options.supervisor.connectionInspection('prepare_reboot')
        return { version: 1, status: 'action_required', diagnosis: registered.diagnosis, new_device: false }
      }
      if (!inspection.resources_safe || input.mode === 'preserve' && !inspection.diagnosis.can_preserve) {
        if (inspection.diagnosis.category === 'EXECUTION_ACTIVE') {
          const registered = await this.options.supervisor.connectionInspection('prepare_reboot')
          return { version: 1, status: 'action_required', diagnosis: registered.diagnosis, new_device: false }
        }
        return { version: 1, status: 'action_required', diagnosis: inspection.diagnosis, new_device: false }
      }
      await this.options.supervisor.closeForConnectionRecovery()
      const leaseModule = await this.module('node_modules/@aid/local-tool-host-core/dist/instanceLease.js')
      lease = await leaseModule.acquireHostLease(this.options.home)
      if (!lease) return { version: 1, status: 'action_required', diagnosis: issue('INSTANCE_CONFLICT', '旧实例已退出，但配置目录被另一Runtime使用', ['先退出另一个Runtime实例，再检查连接。', '原资料未覆盖，不要批量结束所有Node进程。']), new_device: false }
      const store = await this.module('connectionRecoveryStore.js')
      await store.beginConnectionRecovery(this.options.home, this.options.node, input.mode)
      newDevice = input.mode === 'new_device'
      await lease.release(); lease = null
      // New generation, new management intent; never retransmit an old cached request.
      await this.options.supervisor.recoveryManagementRequest('describe', {})
      if (input.mode === 'new_device') {
        const pair = await this.settleOperation('pair', { server: input.server!, device_name: input.device_name!, pairing_code: input.pairing_code! })
        if (pair.code !== 0) return { version: 1, status: 'action_required', diagnosis: issue('PAIRING_INCOMPLETE', pair.message, ['原资料已归档，当前是新的配置目录。', '按上面的错误处理后，在配对区域使用新配对码提交；不会自动重复原请求。', '新配对成功后在网页选定新设备，并从ZIP安装BOSS插件。']), new_device: true }
      }
      const checked = await this.options.supervisor.connectionInspection('diagnose')
      if (input.mode === 'new_device') checked.diagnosis.steps = [...checked.diagnosis.steps, '在网页本地工具中选定新设备，并安装交付ZIP中的BOSS插件。旧任务仍保留在原设备。']
      return { version: 1, status: checked.diagnosis.service_verified && checked.diagnosis.can_preserve ? 'complete' : 'action_required', diagnosis: checked.diagnosis, new_device: input.mode === 'new_device' }
    } catch (error) {
      if (!newDevice && input.mode === 'new_device') {
        try { newDevice = await (await this.module('connectionRecoveryStore.js')).hasArchivedConnectionRecovery(this.options.home) } catch { /* Unverified archive is never projected as a committed new home. */ }
      }
      const local = object(error) && error.code === 11 && typeof error.message === 'string' ? error.message : ''
      return { version: 1, status: 'action_required', diagnosis: issue('RECOVERY_INTERRUPTED', local || (newDevice ? '旧资料已归档，新配置的连接尚未确认' : '连接恢复尚未完成，原资料保留'), ['重新打开客户端会继续同一次已登记的本机恢复事务。', '点击检查连接，先核对当前配置与配对状态；已配对时无需再次重新配置。', '目录权限、Windows凭据保护或安装文件异常时，请管理员修复对应项目后重试。', '没有确认配对结果时生成新的配对码，不反复使用旧码。']), new_device: newDevice }
    } finally { await lease?.release().catch(() => {}); this.options.supervisor.suspend(false); this.active = false }
  }
}
