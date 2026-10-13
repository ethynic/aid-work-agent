import { randomUUID } from 'node:crypto'
import { dirname, join } from 'node:path'
import { pathToFileURL } from 'node:url'
import type { RuntimeSupervisor } from './runtimeSupervisor.js'

export interface ConnectionDiagnosis {
  version: 1; category: string; summary: string; steps: string[]
  service_verified: boolean; can_preserve: boolean; can_reconfigure: boolean; requires_reboot: boolean
}
export interface ConnectionRecoveryInput { mode: 'preserve' | 'new_device' | 'register_new' | 'prepare_reboot' | 'continue_registration'; confirmed: true; server?: string; device_name?: string; pairing_code?: string }
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
  if (!object(v) || v.confirmed !== true || typeof v.mode !== 'string' || !['preserve', 'new_device', 'register_new', 'prepare_reboot', 'continue_registration'].includes(v.mode)) throw new Error('请先明确确认连接恢复操作')
  const keys = ['new_device', 'register_new'].includes(v.mode) ? ['mode', 'confirmed', 'server', 'device_name', 'pairing_code'] : ['mode', 'confirmed']
  if (Object.keys(v).sort().join(',') !== keys.sort().join(',')) throw new Error('连接恢复参数无效')
  if (['new_device', 'register_new'].includes(v.mode)) {
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
  async registrationStatus(): Promise<ConnectionDiagnosis | null> {
    return (await this.module('connectionRegistration.js')).registrationStatus(this.options.home)
  }
  async registrationBlocksExecution(): Promise<boolean> {
    return (await this.module('connectionRegistration.js')).registrationBlocksExecution(this.options.home)
  }
  async diagnose(): Promise<ConnectionDiagnosis> {
    if (this.active) return issue('RECOVERY_BUSY', '连接恢复正在进行', ['等待当前恢复完成，不要重复安装或配对。'])
    this.active = true
    this.options.supervisor.suspend(true)
    try {
      const registration = await this.module('connectionRegistration.js')
      if (await registration.registrationStatus(this.options.home)) return await registration.verifyRegisteredConnection(this.options.home)
      return (await this.options.supervisor.connectionInspection('diagnose')).diagnosis
    }
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
    if (input.mode === 'new_device') return { version: 1, status: 'action_required', new_device: false,
      diagnosis: issue('CLIENT_UPDATE_REQUIRED', '当前界面不支持新版连接注册，配对码尚未提交', ['请安装完整最新版客户端后重新打开，再输入有效配对码。', '原配置未覆盖，没有创建暂存身份或消费配对码。']) }
    if (this.active) return { version: 1, status: 'action_required', diagnosis: issue('RECOVERY_BUSY', '连接恢复正在进行', ['等待当前恢复完成。']), new_device: false }
    this.active = true; this.options.supervisor.suspend(true)
    let lease: { release(): Promise<void> } | null = null
    let newDevice = false
    try {
      const registration = await this.module('connectionRegistration.js')
      if (input.mode === 'prepare_reboot' && await registration.registrationStatus(this.options.home)) {
        return await registration.withConnectionRegistration(this.options.home, async () => {
          const inspection = await this.options.supervisor.connectionInspection('prepare_reboot')
          await registration.rememberRegistrationAction(this.options.home, inspection.diagnosis)
          return { version: 1, status: 'action_required', diagnosis: await registration.verifyRegisteredConnection(this.options.home), new_device: false }
        })
      }
      if (input.mode === 'register_new' || input.mode === 'continue_registration') {
        return await registration.withConnectionRegistration(this.options.home, async () => {
          if (input.mode === 'register_new') {
            const result = await registration.registerConnection(this.options.home, input)
            return { version: 1, status: 'action_required', diagnosis: result, new_device: false }
          }
          const verified = await registration.verifyRegisteredConnection(this.options.home)
          if (!verified.service_verified) return { version: 1, status: 'action_required', diagnosis: verified, new_device: await registration.registrationAlreadyActive(this.options.home) }
          if (await registration.registrationAlreadyActive(this.options.home)) {
            const result = await registration.finishRegisteredConnection(this.options.home)
            return { version: 1, status: result.category === 'VERIFIED' || result.category === 'DEVICE_NOT_SELECTED' ? 'complete' : 'action_required', diagnosis: result, new_device: true }
          }
          await this.options.onBeforeRecovery()
          const state = await this.options.supervisor.recoveryManagementRequest('getState', {})
          if (state.code === 0 && object(state.result) && ['starting', 'running', 'reconciling', 'draining'].includes(String(state.result.state))) await this.settleOperation('stop', {}).catch(() => {})
          const inspection = await this.options.supervisor.connectionInspection('prepare_new')
          if (!inspection.resources_safe) {
            await registration.rememberRegistrationAction(this.options.home, inspection.diagnosis)
            return { version: 1, status: 'action_required', diagnosis: await registration.verifyRegisteredConnection(this.options.home), new_device: false }
          }
          await this.options.supervisor.closeForConnectionRecovery()
          lease = await (await this.module('node_modules/@aid/local-tool-host-core/dist/instanceLease.js')).acquireHostLease(this.options.home)
          if (!lease) throw new Error('Runtime配置目录正在使用')
          await registration.installRegisteredConnection(this.options.home, this.options.node)
          newDevice = true
          const result = await registration.finishRegisteredConnection(this.options.home)
          return { version: 1, status: result.category === 'VERIFIED' || result.category === 'DEVICE_NOT_SELECTED' ? 'complete' : 'action_required', diagnosis: result, new_device: true }
        })
      }
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
      newDevice = false
      await lease.release(); lease = null
      // New generation, new management intent; never retransmit an old cached request.
      await this.options.supervisor.recoveryManagementRequest('describe', {})
      const checked = await this.options.supervisor.connectionInspection('diagnose')
      return { version: 1, status: checked.diagnosis.service_verified && checked.diagnosis.can_preserve ? 'complete' : 'action_required', diagnosis: checked.diagnosis, new_device: false }
    } catch (error) {
      if (input.mode === 'register_new' || input.mode === 'continue_registration') {
        const local = object(error) && error.code === 11 && typeof error.message === 'string' ? error.message : '连接配置尚未完成，请检查连接并继续处理；未自动重复配对'
        return { version: 1, status: 'action_required', diagnosis: issue('REGISTRATION_INTERRUPTED', local, ['点击检查连接核对已保存的新注册进度。', '新凭据已保存时点击完成连接配置，无需再次输入配对码。']), new_device: newDevice }
      }
      const local = object(error) && error.code === 11 && typeof error.message === 'string' ? error.message : ''
      return { version: 1, status: 'action_required', diagnosis: issue('RECOVERY_INTERRUPTED', local || (newDevice ? '旧资料已归档，新配置的连接尚未确认' : '连接恢复尚未完成，原资料保留'), ['重新打开客户端会继续同一次已登记的本机恢复事务。', '点击检查连接，先核对当前配置与配对状态；已配对时无需再次重新配置。', '目录权限、Windows凭据保护或安装文件异常时，请管理员修复对应项目后重试。', '没有确认配对结果时生成新的配对码，不反复使用旧码。']), new_device: newDevice }
    } finally { await lease?.release().catch(() => {}); this.options.supervisor.suspend(false); this.active = false }
  }
}
