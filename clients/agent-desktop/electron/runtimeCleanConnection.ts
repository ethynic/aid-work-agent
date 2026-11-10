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
  phase: 'requested' | 'paired' | 'installed'; home: string | null
  device_id: string | null; request: { registration_id: string }
}

/** Independent Runtime uses clean generations. Legacy recovery remains for older clients. */
export class RuntimeCleanConnection {
  private active = false
  private error: ConnectionDiagnosis | null = null
  private confirmedBeforeSave: Registration | null = null
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
      return !!r && r.phase !== 'requested' && !(r.phase === 'installed' && r.home && sameHome(r.home, this.options.home()))
    } catch { return false } // A damaged registration cannot gate the original Host.
  }
  async registrationStatus(): Promise<ConnectionDiagnosis | null> { return this.error }
  private failure(error: unknown): ConnectionDiagnosis {
    const e = error as { code?: string | number; status?: number; message?: string; name?: string }
    if (e.code === 'PAIRING_REDIRECT') return issue('PAIRING_REDIRECT', '企业服务地址返回重定向，请核对地址后重新发起配对')
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
    const confirmed = this.confirmedBeforeSave
    if (!confirmed) return saved
    if (saved && (saved.request.registration_id !== confirmed.request.registration_id
      || saved.phase !== 'requested' && saved.device_id !== confirmed.device_id)) throw new Error('another registration occupies the local receipt')
    if (!saved || saved.phase === 'requested') await store.saveCleanRegistration(this.options.metadataHome, confirmed)
    this.confirmedBeforeSave = null
    return saved && saved.phase !== 'requested' ? saved : confirmed
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
        await this.activate(store, r)
        return true
      })
    } catch (error) {
      const pending = this.confirmedBeforeSave ?? (r as Registration | null)
      this.error = pending && pending.phase !== 'requested' ? this.installFailure() : this.failure(error)
      return false
    } finally { this.active = false }
  }
  private installFailure(): ConnectionDiagnosis {
    return issue('REGISTRATION_CLEANUP_PENDING', '服务端已确认配对，本机未完成保存或切换。请检查本机资料，勿重复配对')
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
    if (this.error?.category === 'REGISTRATION_CLEANUP_PENDING') return this.error
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
        if (r && r.phase !== 'requested') {
          throw new Error('prior confirmed registration requires local installation')
        }
        if (r) await this.retireRequested(store, r)
        current = this.options.supervisor()
        r = await store.beginCleanRegistration(this.options.metadataHome, input, [this.options.home()]) as Registration
        wasRunning = await this.stopActive(); stopped = true
        try { await store.pairCleanRegistration(this.options.metadataHome, r, input.pairing_code) }
        catch (error) {
          // The store sets paired only after validating the cloud reply. Keep
          // that receipt until its first durable write succeeds.
          if (r.phase === 'paired') this.confirmedBeforeSave = r
          throw error
        }
        await this.activate(store, r, stopped)
        committed = true
      })
      return { version: 1, status: 'complete', new_device: true, diagnosis: this.error ?? issue('VERIFIED', '新设备已连接，旧配置已停止使用', true) }
    } catch (error) {
      const pending = r as Registration | null
      this.error = pending && pending.phase !== 'requested' ? this.installFailure() : this.failure(error)
      return { version: 1, status: 'action_required', new_device: false, diagnosis: this.error! }
    } finally {
      if (!committed && stopped && this.options.supervisor() === current) {
        current.suspend(false)
        const pending = r as Registration | null
        if (wasRunning && (!pending || pending.phase === 'requested')) await this.startActive().catch(() => {})
      }
      this.active = false
    }
  }
  dispose(): void { this.disposed = true }
}
