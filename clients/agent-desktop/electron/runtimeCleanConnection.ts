import { join, dirname } from 'node:path'
import { pathToFileURL } from 'node:url'
import type { RuntimeSupervisor } from './runtimeSupervisor.js'
import type { ConnectionDiagnosis, ConnectionRecoveryResult } from './runtimeConnectionRecovery.js'
import { resumeRuntimeAtLogin } from './runtimeStartup.js'

const issue = (category: string, summary: string, service_verified = false): ConnectionDiagnosis => ({ version: 1, category, summary,
  steps: [], service_verified, can_preserve: false, can_reconfigure: true, requires_reboot: false })
/** Independent Runtime uses clean generations. Legacy recovery remains for older clients. */
export class RuntimeCleanConnection {
  private active = false
  private error: ConnectionDiagnosis | null = null
  private retry?: ReturnType<typeof setTimeout>
  private disposed = false
  get busy() { return this.active }
  constructor(private options: {
    metadataHome: string; hostEntry: string; legacyHome: string; previousHome: string | null
    home(): string; supervisor(): RuntimeSupervisor
    replaceHome(target?: string): Promise<string>
    beforeChange(): Promise<void>
    afterChange?(): Promise<void>
  }) {}
  private module(name: string): Promise<Record<string, any>> { return import(pathToFileURL(join(dirname(this.options.hostEntry), name)).href) }
  async registrationBlocksExecution(): Promise<boolean> {
    try { return !!await (await this.module('cleanRegistration.js')).readCleanRegistration(this.options.metadataHome) }
    catch { return false } // Corrupt old registration cannot disable a fresh, unpaired Host.
  }
  async registrationStatus(): Promise<ConnectionDiagnosis | null> { return this.error }
  private failure(error: unknown): ConnectionDiagnosis {
    const e = error as { code?: string | number; status?: number; message?: string; name?: string }
    if (e.code === 'PAIRING_CODE_REQUIRED') return issue('PAIRING_REQUIRED', '尚未完成配对，请输入新的配对码')
    if (e.code === 'PAIRING_PROTOCOL_UNSUPPORTED' || e.status === 422) return issue('SERVER_UPDATE_REQUIRED', '服务端尚未支持自动连接切换，请发布配套服务端后重新配对')
    if (e.status === 400) return issue('PAIRING_CODE_INVALID', '配对码无效、过期或已使用，请生成新的配对码')
    if (e.status === 401 || e.name === 'DeviceRevokedError') return issue('PAIRING_REQUIRED', '设备授权已失效，请使用新配对码连接')
    if (e.status === 403) return issue('SERVER_FORBIDDEN', '配对身份或服务访问权限不匹配，请检查登录账号和服务地址')
    if (e.status && e.status >= 500 || e.name === 'NetworkError') return issue('CONNECTING', '企业服务暂时不可连接，网络恢复后会自动继续，无需重复配对')
    if (e.code === 11 && e.message) return issue('LOCAL_SYSTEM_ERROR', e.message)
    return issue('LOCAL_SYSTEM_ERROR', '连接未完成，请检查安装文件、当前用户目录权限和Windows凭据保护')
  }
  private schedule() {
    if (this.disposed || this.retry || this.error?.category !== 'CONNECTING') return
    this.retry = setTimeout(() => { this.retry = undefined; void this.resume() }, 10_000)
    this.retry.unref?.()
  }
  async resume(): Promise<void> {
    if (this.active || this.disposed) return
    this.active = true
    try {
      const store = await this.module('cleanRegistration.js')
      const r = await store.readCleanRegistration(this.options.metadataHome)
      if (!r) {
        if (await store.finishLegacyRegistration(this.options.metadataHome, this.options.home())) {
          await resumeRuntimeAtLogin(request => this.options.supervisor().recoveryManagementRequest((request as { method: string }).method, (request as { params: Record<string, string> }).params))
        }
        this.error = null
        clearTimeout(this.retry); this.retry = undefined
        return
      }
      await store.withCleanRegistration(this.options.metadataHome, async () => {
        await this.prepareHome(store, r)
        await store.pairCleanRegistration(this.options.metadataHome, r)
        await this.activate(store, r)
      })
    } catch (error) { this.error = this.failure(error) }
    finally { this.active = false; this.schedule() }
  }
  private async prepareHome(store: Record<string, any>, r: { home: string | null }): Promise<void> {
    if (r.home) return
    // Stop the old execution before invalidating its cloud identity. Allocate an
    // empty management home even if the upcoming HTTP request cannot complete.
    await this.options.beforeChange()
    r.home = await this.options.replaceHome()
    await store.saveCleanRegistration(this.options.metadataHome, r)
  }
  private async activate(store: Record<string, any>, r: { home: string | null; phase: string }): Promise<void> {
    await this.options.beforeChange()
    // Even an installed transaction may need a new supervisor after restart.
    const home = await this.options.replaceHome(r.home ?? undefined)
    await store.installCleanRegistration(this.options.metadataHome, r, home)
    await resumeRuntimeAtLogin(request => this.options.supervisor().recoveryManagementRequest((request as { method: string }).method, (request as { params: Record<string, string> }).params))
    store.finishCleanRegistration(this.options.metadataHome)
    await this.options.afterChange?.()
    this.error = null
  }
  async diagnose(): Promise<ConnectionDiagnosis> {
    await this.resume()
    if (this.error) return this.error
    try {
      const result = await (await this.module('connectionDiagnosis.js')).diagnoseConnection(this.options.home())
      return { ...result, can_preserve: false, requires_reboot: false,
        steps: result.service_verified ? [] : ['请检查当前服务地址和网络；需要更换身份时直接输入新配对码。'] }
    }
    catch (error) { return this.failure(error) }
  }
  async reconfigure(value: unknown): Promise<ConnectionRecoveryResult> {
    const input = value as Record<string, unknown> | null
    if (!input || input.confirmed !== true || !['register_new', 'new_device', 'continue_registration'].includes(String(input.mode))
      || (input.mode !== 'continue_registration' && (Object.keys(input).sort().join(',') !== 'confirmed,device_name,mode,pairing_code,server'
        || typeof input.server !== 'string' || typeof input.device_name !== 'string' || typeof input.pairing_code !== 'string' || !input.pairing_code))) throw new Error('请填写服务地址、设备名称和有效配对码')
    if (this.active) return { version: 1, status: 'action_required', new_device: false, diagnosis: issue('CONNECTING', '正在配置连接，请等待完成') }
    if (input.mode === 'continue_registration') {
      await this.resume()
      return { version: 1, status: this.error ? 'action_required' : 'complete', new_device: !this.error, diagnosis: this.error ?? issue('VERIFIED', '设备连接已完成', true) }
    }
    this.active = true; clearTimeout(this.retry); this.retry = undefined
    try {
      const store = await this.module('cleanRegistration.js')
      await store.withCleanRegistration(this.options.metadataHome, async () => {
        // An uncertain request is resumed before accepting another code.
        let r = await store.readCleanRegistration(this.options.metadataHome).catch(() => null)
        // A deliberate new server is a new connection intent. An unavailable
        // former enterprise must not prevent registering with the new one.
        if (r && String(input.server).replace(/\/+$/, '') !== r.server.replace(/\/+$/, '')) r = null
        if (r) {
          await this.prepareHome(store, r)
          try { await store.pairCleanRegistration(this.options.metadataHome, r) }
          catch (error) {
            const e = error as { code?: string; status?: number; name?: string }
            if (!(e.code === 'PAIRING_CODE_REQUIRED' || e.name === 'DeviceRevokedError' || e.status === 400 || e.status === 401 || e.status === 422)) throw error
            r = null
          }
        }
        if (!r) {
          r = await store.beginCleanRegistration(this.options.metadataHome, input, [this.options.home(), this.options.previousHome, this.options.legacyHome].filter(Boolean))
          await this.prepareHome(store, r)
          await store.pairCleanRegistration(this.options.metadataHome, r, input.pairing_code)
        }
        await this.activate(store, r)
      })
      this.error = null
      return { version: 1, status: 'complete', new_device: true, diagnosis: issue('VERIFIED', '新设备已连接，旧配置已停止使用', true) }
    } catch (error) {
      this.error = this.failure(error)
      return { version: 1, status: 'action_required', new_device: false, diagnosis: this.error }
    } finally { this.active = false; this.schedule() }
  }
  dispose(): void { this.disposed = true; clearTimeout(this.retry) }
}
