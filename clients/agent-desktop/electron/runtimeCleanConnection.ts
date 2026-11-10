import { join, dirname, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import type { RuntimeSupervisor } from './runtimeSupervisor.js'
import type { ConnectionDiagnosis, ConnectionRecoveryResult } from './runtimeConnectionRecovery.js'
import { resumeRuntimeAtLogin } from './runtimeStartup.js'
import { lstatSync, readFileSync } from 'node:fs'

/** Read only the active server's public origin; never return credentials or other configuration. */
export function readRuntimeConnectionInfo(home: string | null): { server: string | null } {
  if (!home) return { server: null }
  try {
    const file = join(home, 'config.json')
    const stat = lstatSync(file)
    if (!stat.isFile() || stat.isSymbolicLink() || stat.size > 65_536) return { server: null }
    const config = JSON.parse(readFileSync(file, 'utf8').replace(/^\uFEFF/, ''))
    if (typeof config?.server !== 'string') return { server: null }
    const url = new URL(config.server)
    return { server: ['http:', 'https:'].includes(url.protocol) ? url.origin : null }
  } catch { return { server: null } }
}

const issue = (category: string, summary: string, service_verified = false): ConnectionDiagnosis => ({ version: 1, category, summary,
  steps: [], service_verified, can_preserve: false, can_reconfigure: true, requires_reboot: false })
const sameHome = (a: string, b: string) => resolve(a).toLowerCase() === resolve(b).toLowerCase()
interface Registration {
  phase: 'requested' | 'prepared' | 'confirming' | 'paired' | 'installed'; home: string | null
  device_id: string | null; request: { registration_id: string }
}
const confirmed = (r: Registration | null): boolean => r?.phase === 'paired' || r?.phase === 'installed'
function preparationEnded(error: unknown): boolean {
  const code = (error as { code?: string } | null)?.code
  return ['PAIRING_PREPARATION_INVALID', 'PAIRING_PREPARATION_EXPIRED', 'REGISTRATION_CONFLICT',
    'REPLACEMENT_TARGET_NOT_FOUND', 'REPLACEMENT_ALREADY_COMPLETED'].includes(code ?? '')
}

/** Independent Runtime uses clean generations. Legacy recovery remains for older clients. */
export class RuntimeCleanConnection {
  private active = false
  private error: ConnectionDiagnosis | null = null
  private confirmedBeforeSave: Registration | null = null
  private resumeOriginalRegistration: string | null = null
  private disposed = false
  restoredOriginal = false
  get busy() { return this.active }
  constructor(private options: {
    metadataHome: string; hostEntry: string
    home(): string; supervisor(): RuntimeSupervisor
    restorePriorHome(target: string): Promise<boolean>
    stageHome(): Promise<string>
    validateHome(target: string): void
    commitHome(target: string): Promise<void>
    beforeChange(): Promise<void>
    afterChange?(): Promise<void>
  }) {}
  private module(name: string): Promise<Record<string, any>> { return import(pathToFileURL(join(dirname(this.options.hostEntry), name)).href) }
  async registrationBlocksExecution(): Promise<boolean> {
    if (this.confirmedBeforeSave) return true
    try {
      const r: Registration | null = await (await this.module('cleanRegistration.js')).readCleanRegistration(this.options.metadataHome)
      return r?.phase === 'confirming' || confirmed(r) && !(r?.phase === 'installed' && r.home && sameHome(r.home, this.options.home()))
    } catch { return false } // A damaged registration cannot gate the original Host.
  }
  cancelOriginalResume(): void { this.resumeOriginalRegistration = null }
  async registrationStatus(): Promise<ConnectionDiagnosis | null> { return this.error }
  private failure(error: unknown): ConnectionDiagnosis {
    const e = error as { code?: string | number; status?: number; message?: string; name?: string }
    if (e.code === 'PAIRING_REDIRECT') return issue('PAIRING_REDIRECT', '企业服务地址返回重定向，请核对地址后重新发起配对')
    if (e.code === 'PAIRING_UPGRADE_REQUIRED' || e.status === 426) return issue('SERVER_UPDATE_REQUIRED', '本次连接需要支持准备与确认的新版本')
    if (e.code === 'PAIRING_PREPARATION_CONFLICT') return issue('PAIRING_REQUIRED', '该配对码已有准备或已作废，请核对设备后生成新配对码')
    if (e.code === 'PAIRING_CODE_REQUIRED') return issue('PAIRING_REQUIRED', '尚未完成配对，请输入新的配对码')
    if (e.status === 422) return issue('SERVER_UPDATE_REQUIRED', '服务端拒绝新版配对请求，请检查服务端版本后重新配对')
    if (e.status === 400) return issue('PAIRING_CODE_INVALID', '配对码无效、过期或已使用，请生成新的配对码')
    if (e.status === 401 || e.name === 'DeviceRevokedError') return issue('PAIRING_REQUIRED', '设备授权已失效，请使用新配对码连接')
    if (e.status === 403) return issue('SERVER_FORBIDDEN', '服务端拒绝本次配对，请检查授权后重新发起')
    if (e.status === 408 || (e.status ?? 0) >= 500 || e.name === 'NetworkError' || e.code === 'PAIRING_PROTOCOL_UNSUPPORTED')
      return issue('REGISTRATION_UNCONFIRMED', '未能确认服务端配对结果，本机原设备资料已保留。请先在网页确认设备状态，再决定是否重新配对')
    if (e.status === 429) return issue('PAIRING_REFUSED', '服务端请求过多，请稍后重新发起配对')
    if ((e.status ?? 0) >= 400) return issue('PAIRING_REFUSED', '服务端拒绝本次配对，请检查服务地址与授权后重新发起')
    if (e.code === 11 && e.message) return issue('LOCAL_SYSTEM_ERROR', e.message)
    return issue('LOCAL_SYSTEM_ERROR', '连接未完成，请检查安装文件、当前用户目录权限和Windows凭据保护')
  }
  private async retireRequested(store: Record<string, any>, r: Registration) {
    if (r.home && sameHome(r.home, this.options.home()) && await this.options.restorePriorHome(r.home)) this.restoredOriginal = true
    store.retireCleanRegistration(this.options.metadataHome)
  }
  private async readPendingRegistration(store: Record<string, any>): Promise<Registration | null> {
    const saved: Registration | null = await store.readCleanRegistration(this.options.metadataHome)
    const receipt = this.confirmedBeforeSave
    if (!receipt) return saved
    if (saved && (saved.request.registration_id !== receipt.request.registration_id
      || confirmed(saved) && saved.device_id !== receipt.device_id)) throw new Error('another registration occupies the local receipt')
    if (!confirmed(saved)) await store.saveCleanRegistration(this.options.metadataHome, receipt)
    this.confirmedBeforeSave = null
    return confirmed(saved) ? saved : receipt
  }

  async resume(): Promise<boolean> {
    if (this.active || this.disposed) return false
    this.active = true
    let r: Registration | null = null
    try {
      const store = await this.module('cleanRegistration.js')
      return await store.withCleanRegistration(this.options.metadataHome, async () => {
        r = await this.readPendingRegistration(store)
        if (!r) {
          let completed: boolean
          try { completed = await store.finishLegacyRegistration(this.options.metadataHome, this.options.home()) }
          catch (error) {
            this.error = this.failure(error)
            if (this.error.category === 'REGISTRATION_UNCONFIRMED') this.error = issue('NETWORK_FAILED', '原设备激活未完成，请检查网络后再次检查连接')
            return false
          }
          if (completed) await this.startActive()
          this.error = null
          return false
        }
        if (r.phase === 'requested') {
          await this.retireRequested(store, r)
          this.error = null
          return false
        }
        if (r.phase === 'prepared') {
          store.discardPreparedCleanRegistration(this.options.metadataHome)
          this.error = issue('PAIRING_REQUIRED', '上次配对在发送确认前中止，原设备资料已保留。请使用新配对码重新发起')
          return false
        }
        if (r.phase === 'confirming' && !await this.checkPrepared(store, r)) return false
        await this.activate(store, r)
        return true
      })
    } catch (error) {
      const pending = this.confirmedBeforeSave ?? (r as Registration | null)
      this.error = confirmed(pending) ? this.installFailure()
        : pending?.phase === 'confirming' ? this.pendingConfirmation() : this.failure(error)
      return false
    } finally { this.active = false }
  }
  private installFailure(): ConnectionDiagnosis {
    return issue('REGISTRATION_CLEANUP_PENDING', '服务端已确认配对，本机未完成保存或切换。请检查本机资料，勿重复配对')
  }
  private pendingConfirmation(): ConnectionDiagnosis {
    return issue('REGISTRATION_PENDING', '配对确认结果尚未取得。原设备资料已保留，执行暂缓启动，请检查配对结果')
  }
  private discardPrepared(store: Record<string, any>): void {
    store.discardPreparedCleanRegistration(this.options.metadataHome)
    this.error = issue('PAIRING_PREPARATION_ENDED', '本次配对准备已过期或被拒绝，请核对原设备后生成新配对码')
  }
  private async checkPrepared(store: Record<string, any>, r: Registration): Promise<boolean> {
    let state: string
    try { state = await store.checkCleanRegistration(this.options.metadataHome, r) }
    catch (error) {
      if (confirmed(r)) this.confirmedBeforeSave = r
      throw error
    }
    if (state === 'committed') { this.cancelOriginalResume(); return true }
    if (state === 'expired' || state === 'rejected') {
      this.discardPrepared(store)
      const resumeOriginal = this.resumeOriginalRegistration === r.request.registration_id
      this.cancelOriginalResume()
      if (resumeOriginal) await this.startActive().catch(() => {
        this.error = issue('PAIRING_PREPARATION_ENDED', '本次配对已结束，原设备未能启动，请检查执行状态')
      })
    } else this.error = this.pendingConfirmation()
    return false
  }
  private async stopActive(): Promise<boolean> {
    let wasRunning = false
    await this.options.beforeChange()
    await this.options.supervisor().stopAndExit(undefined, state => { wasRunning = state.state === 'running' || state.state === 'starting'; return Promise.resolve() })
    return wasRunning
  }
  private async startActive(): Promise<void> {
    const supervisor = this.options.supervisor()
    await resumeRuntimeAtLogin(request => supervisor.request(request, 'startup'))
  }
  private async activate(store: Record<string, any>, r: Registration, alreadyStopped = false): Promise<void> {
    const activeHome = this.options.home()
    const previous = this.options.supervisor()
    let stopped = alreadyStopped
    try {
      if (!await store.matchesInstalledCleanRegistration(r, activeHome)) {
        if (!stopped) { await this.stopActive(); stopped = true }
        const home = r.home ?? await this.options.stageHome()
        this.options.validateHome(home)
        await store.installCleanRegistration(this.options.metadataHome, r, home)
        await this.options.commitHome(home)
      }
      // A successful cloud registration is complete before starting execution.
      // Start failures never recreate or resend a pairing transaction.
      store.finishCleanRegistration(this.options.metadataHome)
    } finally {
      if (stopped && previous === this.options.supervisor()) previous.suspend(false)
    }
    this.error = null
    await this.options.afterChange?.()
    try { await this.startActive() }
    catch { this.error = issue('EXECUTION_START_FAILED', '设备连接已保存，Runtime执行未启动，请检查本机执行状态', true) }
  }
  private async probe(): Promise<ConnectionDiagnosis> {
    const result = await (await this.module('connectionDiagnosis.js')).diagnoseConnection(this.options.home())
    return { ...result, can_preserve: false, requires_reboot: false,
      steps: result.service_verified ? [] : ['请检查当前服务地址和网络；需要更换身份时直接输入新配对码。'] }
  }
  async diagnose(): Promise<ConnectionDiagnosis> {
    if (await this.resume()) return this.error ?? issue('VERIFIED', '设备连接已完成', true)
    if (this.error && ['REGISTRATION_PENDING', 'REGISTRATION_CLEANUP_PENDING', 'PAIRING_PREPARATION_ENDED'].includes(this.error.category)) return this.error
    try {
      const result = await this.probe()
      return result.service_verified ? result : this.error ?? result
    } catch (error) { return this.failure(error) }
  }
  async reconfigure(value: unknown): Promise<ConnectionRecoveryResult> {
    const input = value as Record<string, unknown> | null
    if (!input || input.confirmed !== true || !['register_new', 'new_device', 'continue_registration'].includes(String(input.mode))
      || (input.mode !== 'continue_registration' && (Object.keys(input).sort().join(',') !== 'confirmed,device_name,mode,pairing_code,server'
        || typeof input.server !== 'string' || typeof input.device_name !== 'string' || typeof input.pairing_code !== 'string' || !input.pairing_code))) throw new Error('请填写服务地址、设备名称和有效配对码')
    if (this.active || this.disposed) return { version: 1, status: 'action_required', new_device: false, diagnosis: issue('CONNECTING', '正在配置连接，请等待完成') }
    if (input.mode === 'continue_registration') {
      const completed = await this.resume()
      return { version: 1, status: completed ? 'complete' : 'action_required', new_device: completed,
        diagnosis: this.error ?? (completed ? issue('VERIFIED', '设备连接已完成', true) : issue('PAIRING_REQUIRED', '没有可以继续的连接注册，请检查原设备或使用新配对码')) }
    }
    if (this.confirmedBeforeSave) return { version: 1, status: 'action_required', new_device: false, diagnosis: this.installFailure() }
    this.active = true
    let current = this.options.supervisor()
    let r: Registration | null = null, stopped = false, wasRunning = false, committed = false
    try {
      const store = await this.module('cleanRegistration.js')
      await store.withCleanRegistration(this.options.metadataHome, async () => {
        try { r = await store.readCleanRegistration(this.options.metadataHome) as Registration | null }
        catch { store.retireCleanRegistration(this.options.metadataHome) }
        if (r?.phase === 'requested') await this.retireRequested(store, r)
        else if (r?.phase === 'prepared') store.discardPreparedCleanRegistration(this.options.metadataHome)
        else if (r) throw new Error('prior registration requires status verification or local installation')
        current = this.options.supervisor()
        r = await store.beginCleanRegistration(this.options.metadataHome, input, [this.options.home()]) as Registration
        await store.prepareCleanRegistration(this.options.metadataHome, r, input.pairing_code)
        try { wasRunning = await this.stopActive(); stopped = true }
        catch {
          store.discardPreparedCleanRegistration(this.options.metadataHome)
          r = null
          this.error = issue('PAIRING_STOP_FAILED', '旧执行未确认停止，本次未发送配对确认。请处理执行状态后重新发起配对')
          return
        }
        try { await store.confirmCleanRegistration(this.options.metadataHome, r) }
        catch (error) {
          // The store sets paired only after validating the cloud reply. Keep
          // that receipt until its first durable write succeeds.
          const code = (error as { code?: string } | null)?.code
          if (confirmed(r)) this.confirmedBeforeSave = r
          else if (r.phase === 'prepared' || code === 'PAIRING_REDIRECT' || code === 'PAIRING_INPUT_INVALID') {
            store.discardPreparedCleanRegistration(this.options.metadataHome)
            r = null
          } else if (preparationEnded(error)) { this.discardPrepared(store); r = null }
          throw error
        }
        await this.activate(store, r, stopped)
        committed = true
      })
      if (!committed) return { version: 1, status: 'action_required', new_device: false, diagnosis: this.error! }
      return { version: 1, status: 'complete', new_device: true, diagnosis: this.error ?? issue('VERIFIED', '新设备已连接，旧配置已停止使用', true) }
    } catch (error) {
      const pending = r as Registration | null
      this.error = confirmed(pending) ? this.installFailure()
        : pending?.phase === 'confirming' ? this.pendingConfirmation()
          : preparationEnded(error) ? issue('PAIRING_PREPARATION_ENDED', '本次配对准备已过期或被拒绝，请核对原设备后生成新配对码') : this.failure(error)
      return { version: 1, status: 'action_required', new_device: false, diagnosis: this.error! }
    } finally {
      if (!committed && stopped && this.options.supervisor() === current) {
        current.suspend(false)
        const pending = r as Registration | null
        if (wasRunning && pending?.phase === 'confirming') this.resumeOriginalRegistration = pending.request.registration_id
        else if (wasRunning && !confirmed(pending)) await this.startActive().catch(() => {})
      }
      this.active = false
    }
  }
  dispose(): void { this.disposed = true }
}
