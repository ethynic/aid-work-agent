import { randomUUID } from 'node:crypto'
import { closeSync, existsSync, fsyncSync, lstatSync, mkdirSync, openSync, readFileSync, readdirSync, realpathSync, renameSync, rmSync, writeFileSync } from 'node:fs'
import { lstat, readFile, readdir } from 'node:fs/promises'
import { dirname, join, resolve } from 'node:path'
import semver from 'semver'
import { ManagementError, type PluginSummary } from '../management.js'
import { extractVerifiedPackage, FIRST_PARTY_PROVIDERS, PACKAGE_LIMITS, parseToolDeclaration, readSignedEnvelope, sha256, verifyOfflinePackage, verifyReleasePlatform, verifyRuntimeManifest, type PackagePlatform, type PublisherTrust, type ReleaseEnvelope, type RuntimeManifest, type ToolDeclaration, type VerifiedPackage } from './package.js'
import { providerKeyOfPlugin } from '../legacy/providers.js'

interface Installation {
  installation_id: string; plugin_id: string; release_id: string; version: string
  directory: string; outer_digest: string; package_digest: string; manifest_digest: string; enabled: boolean; uninstalled: boolean
}
type FileIntegrity = { size: number; sha256: string }
type ReleaseMetadata = Pick<VerifiedPackage, 'envelope' | 'manifest' | 'declaration'>
interface VerifiedRelease extends ReleaseMetadata {
  outer_digest: string
  installedFiles: ReadonlyMap<string, FileIntegrity>
  verificationInputs: string
}
interface ReleaseState { metadata?: ReleaseMetadata; verified?: VerifiedRelease; ready: boolean; reason?: string }
type PluginCheckPass = 'initialize' | 'refresh' | 'maintenance' | 'metadata' | 'background' | 'first_use' | 'verified_reuse'
type PluginCheckPhase = 'archive_envelope' | 'manifest' | 'archive_read' | 'archive_verify' | 'archive_digest' | 'payload_verify' | 'version' | 'doctor' | 'admission'
class PluginCheckCancelled extends Error {}
export interface PluginProbeContext {
  versionVerified: boolean
  measure<T>(phase: 'version' | 'doctor', work: () => Promise<T>): Promise<T>
}
export interface PluginStoreOptions {
  home: string; trust: PublisherTrust[]; platform: PackagePlatform
  probe: (releaseRoot: string, manifest: RuntimeManifest, context?: PluginProbeContext) => Promise<{ ready: boolean; reason?: string }>
  /** 可安装 provider_id 允许清单（默认第一方三家；测试用未知插件经此扩容，非逐插件运行时知识） */
  providers?: readonly string[]
  deferStartupChecks?: boolean
  onChanged?: () => void
  onCheckTiming?: (event: { pluginId: string; pass: PluginCheckPass; phase: PluginCheckPhase; elapsedMs: number; succeeded: boolean; files?: number }) => void
}
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/
const reconcile = (): never => { throw new ManagementError(11, '插件持久状态需要核对，未自动恢复或重放安装') }
function rejectLinks(path: string): void {
  const parent = dirname(path)
  if (parent !== path) rejectLinks(parent)
  if (existsSync(path) && lstatSync(path).isSymbolicLink()) reconcile()
}
function atomicWrite(file: string, contents: Buffer | string): void {
  rejectLinks(file)
  const temporary = `${file}.${randomUUID()}.tmp`
  writeFileSync(temporary, contents, { flag: 'wx', mode: 0o600 })
  const fd = openSync(temporary, 'r+')
  try { fsyncSync(fd) } finally { closeSync(fd) }
  try { renameSync(temporary, file) } finally { rmSync(temporary, { force: true }) }
}

/** Host drains execution before mutations. Keep the current and previous release. */
export class PluginStore {
  private readonly root: string
  private readonly releases: string
  private readonly inventory: string
  private readonly pending: string
  private readonly allowedProviders: readonly string[]
  private records: Installation[] = []
  private states = new Map<string, ReleaseState>()
  private readonly sessionChecks = new Map<string, { done: Promise<boolean | undefined>; requireExecution: () => void }>()
  private readonly executionProofs = new Map<string, VerifiedRelease>()
  private initialized = false
  private pendingWarning: string | undefined
  private cleanupWarning: string | undefined
  private queue: Promise<unknown> = Promise.resolve()

  constructor(private readonly options: PluginStoreOptions) {
    this.allowedProviders = options.providers ?? FIRST_PARTY_PROVIDERS
    const home = resolve(options.home)
    rejectLinks(home)
    mkdirSync(home, { recursive: true })
    this.root = join(realpathSync(home), 'plugins')
    this.releases = join(this.root, 'releases')
    this.inventory = join(this.root, 'inventory.json')
    this.pending = join(this.root, 'pending.json')
  }

  async initialize(): Promise<void> {
    if (this.initialized) return
    rejectLinks(this.releases); rejectLinks(this.inventory); rejectLinks(this.pending)
    mkdirSync(this.releases, { recursive: true })
    if (existsSync(this.inventory)) {
      try {
        if (!lstatSync(this.inventory).isFile() || lstatSync(this.inventory).size > 1024 * 1024) reconcile()
        const saved = JSON.parse(readFileSync(this.inventory, 'utf8'))
        if (!saved || saved.version !== 1 || !Array.isArray(saved.records) || saved.records.length > this.allowedProviders.length
          || Object.keys(saved).some(key => !['version', 'records'].includes(key))) reconcile()
        const ids = new Set<string>(); const providers = new Set<string>()
        for (const record of saved.records) {
          if (!record || !uuid.test(record.installation_id) || !uuid.test(record.directory)
            || !this.allowedProviders.includes(record.plugin_id)
            || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(record.release_id) || !semver.valid(record.version)
            || !/^[0-9a-f]{64}$/.test(record.outer_digest) || !/^sha256:[0-9a-f]{64}$/.test(record.package_digest)
            || !/^sha256:[0-9a-f]{64}$/.test(record.manifest_digest) || typeof record.enabled !== 'boolean'
            || typeof record.uninstalled !== 'boolean' || (record.uninstalled && record.enabled)
            || ids.has(record.installation_id) || providers.has(record.plugin_id)
            || Object.keys(record).some(key => !['installation_id', 'plugin_id', 'release_id', 'version', 'directory', 'outer_digest', 'package_digest', 'manifest_digest', 'enabled', 'uninstalled'].includes(key))) reconcile()
          ids.add(record.installation_id); providers.add(record.plugin_id)
        }
        this.records = saved.records
      } catch { reconcile() }
    }
    for (const record of this.records) {
      if (!record.uninstalled) this.states.set(record.installation_id, this.options.deferStartupChecks
        ? await this.checkMetadata(record) : await this.checkRelease(record, 'initialize'))
    }
    // The atomic inventory is the commit point. A leftover installation intent
    // does not replay work or invalidate either the old or new committed release.
    try { this.clearPending() } catch { /* Keep the committed release usable; retry cleanup before any plugin write. */ }
    this.initialized = true
  }

  list(): PluginSummary[] {
    this.assertAvailable()
    return this.records.filter(record => !record.uninstalled).map(record => {
      const state = this.states.get(record.installation_id)
      const metadata = state?.verified ?? state?.metadata
      // 契约 2.20 门禁：验签安装但无有效调度声明 = 旧 CLI，显示「需要升级插件」且不进入执行解析
      const missingDeclaration = Boolean(record.enabled && metadata && !metadata.declaration)
      const reason = [missingDeclaration ? '需要升级插件（缺少调度声明）' : undefined, state?.reason, this.pendingWarning, this.cleanupWarning]
        .filter(Boolean).join('；')
      return {
        installation_id: record.installation_id, plugin_id: record.plugin_id,
        display_name: metadata?.manifest.display_name ?? record.plugin_id, release_id: record.release_id,
        version: record.version, enabled: record.enabled, ready: (state?.ready ?? false) && !missingDeclaration,
        ...(reason ? { reason } : {}),
      }
    })
  }

  /** Authenticated dispatch metadata. Provider launch still requires verifyForExecution. */
  declaredPlugins(): Array<{ plugin_id: string; provider_key: string; envelope: ReleaseEnvelope; manifest: RuntimeManifest; declaration: ToolDeclaration }> {
    this.assertAvailable()
    const declared: ReturnType<PluginStore['declaredPlugins']> = []
    for (const record of this.records) {
      const state = this.states.get(record.installation_id)
      const metadata = state?.verified ?? state?.metadata
      if (!record.uninstalled && record.enabled && metadata?.declaration) {
        declared.push({ plugin_id: record.plugin_id, provider_key: providerKeyOfPlugin(record.plugin_id),
          envelope: metadata.envelope, manifest: metadata.manifest, declaration: metadata.declaration })
      }
    }
    return declared
  }

  async refresh(): Promise<void> {
    return this.serialize(async () => {
      const before = JSON.stringify(this.list())
      // Full self-contained OCR releases are large. Keep only one inflated archive in
      // memory at a time; publish the complete refreshed snapshot in one sync step.
      const states: Array<readonly [string, ReleaseState]> = []
      for (const record of this.records) {
        if (!record.uninstalled) states.push([record.installation_id, await this.checkRelease(record, 'refresh')])
      }
      for (const [id, state] of states) this.states.set(id, state)
      if (before !== JSON.stringify(this.list())) this.options.onChanged?.()
    })
  }

  resolveEntries(legacy: Record<string, string>, _bootstrapKeys: readonly string[] = []): Record<string, string> {
    this.assertAvailable()
    const entries = { ...legacy }
    for (const record of this.records) {
      const key = providerKeyOfPlugin(record.plugin_id)
      delete entries[key]
      const state = this.states.get(record.installation_id)
      const metadata = state?.verified ?? state?.metadata
      // App login/window readiness is diagnostic. Verified installed tools remain
      // discoverable so a call can explain how to restore the business app.
      // 契约 2.20：无有效调度声明（旧 CLI）不进入执行解析；其他插件照常。
      if (!record.uninstalled && record.enabled && metadata?.declaration) {
        entries[key] = join(this.releases, record.directory, 'payload', metadata.manifest.entrypoint[0]!)
      }
    }
    return entries
  }

  /** The exact executable path and active release must still match the dispatch candidate. */
  async verifyForExecution(providerKey: string, entry: string, atLaunch = false): Promise<void> {
    this.assertAvailable()
    const record = this.records.find(item => providerKeyOfPlugin(item.plugin_id) === providerKey)
    if (!record) return // Legacy directly configured providers retain their own launch contract.
    const unavailable = () => new ManagementError(8, '插件尚未通过完整检查，请检查或重新导入该插件')
    const state = this.states.get(record.installation_id)
    const metadata = state?.verified ?? state?.metadata
    if (record.uninstalled || !record.enabled || !metadata?.declaration
      || entry !== join(this.releases, record.directory, 'payload', metadata.manifest.entrypoint[0]!)) throw unavailable()
    const reused = state?.verified?.verificationInputs === this.verificationInputs()
    if (!await this.verifyForSession(record)) throw unavailable()
    if (reused && atLaunch) this.recordTiming(record, 'verified_reuse', 'admission', 0, true)
  }

  async verifyEnabledPlugins(signal: AbortSignal): Promise<void> {
    this.assertAvailable()
    for (const record of this.records) {
      if (signal.aborted) return
      const state = this.states.get(record.installation_id)
      if (!record.uninstalled && record.enabled && (state?.metadata || state?.verified)) {
        await this.verifyForSession(record, signal)
      }
    }
  }

  private async verifyForSession(record: Installation, background?: AbortSignal): Promise<boolean> {
    const id = record.installation_id
    const previous = this.states.get(id)?.verified
    if (previous?.verificationInputs === this.verificationInputs()) {
      this.executionProofs.set(id, previous)
      return true
    }
    const pending = this.sessionChecks.get(id)
    if (pending) {
      if (!background) pending.requireExecution()
      const result = await pending.done
      if (result !== undefined || background) return result ?? false
      // An accepted invocation can reach admission after a background cancellation has already begun.
      return this.verifyForSession(record)
    }
    let required = !background
    const checkActive = () => { if (background?.aborted && !required) throw new PluginCheckCancelled() }
    const done = this.checkRelease(record, background ? 'background' : 'first_use', checkActive).then(validated => {
      checkActive()
      if (!this.records.includes(record)) return false
      this.states.set(id, validated)
      if (validated.verified) this.executionProofs.set(id, validated.verified)
      this.options.onChanged?.()
      return Boolean(validated.verified)
    }).catch(error => {
      if (error instanceof PluginCheckCancelled) return undefined
      throw error
    }).finally(() => this.sessionChecks.delete(id))
    this.sessionChecks.set(id, { done, requireExecution: () => { required = true } })
    return (await done) ?? false
  }

  /** Called after the execution window has drained, without reading the installed payload. */
  resetExecutionChecks(): void {
    let changed = false
    for (const [id, proof] of this.executionProofs) {
      if (this.states.get(id)?.verified !== proof) continue
      const { envelope, manifest, declaration } = proof
      this.states.set(id, { metadata: { envelope, manifest, declaration }, ready: false, reason: '等待检查插件' })
      changed = true
    }
    this.executionProofs.clear()
    if (changed) this.options.onChanged?.()
  }

  async import(bytes: Buffer): Promise<void> {
    // Freeze the selected byte snapshot before the first await; callers retain their own Buffer.
    const snapshot = Buffer.from(bytes)
    return this.serialize(async () => {
      this.clearPending()
      const verificationInputs = this.verificationInputs()
      const pkg = await verifyOfflinePackage(snapshot, this.options.trust, this.options.platform, this.allowedProviders)
      await this.assertRetainedRelease(pkg)
      const previous = this.records.find(record => record.plugin_id === pkg.envelope.provider_id)
      if (previous) {
        if (previous.release_id === pkg.envelope.provider_release_id) {
          if (previous.package_digest !== pkg.envelope.package_digest || previous.manifest_digest !== pkg.envelope.manifest_digest) throw new ManagementError(8, '同一发布版本的内容发生变化')
          if (!previous.uninstalled) {
            const before = JSON.stringify(this.list()); const state = await this.checkRelease(previous)
            this.states.set(previous.installation_id, state)
            if (before !== JSON.stringify(this.list())) this.options.onChanged?.()
            if (state.verified) return
            // The selected bytes are trusted and identical, but installed files
            // are damaged. Re-extract to a fresh release before switching active.
          }
        }
      }
      const directory = randomUUID()
      const installation: Installation = {
        installation_id: previous?.installation_id ?? randomUUID(), plugin_id: pkg.envelope.provider_id,
        release_id: pkg.envelope.provider_release_id, version: pkg.envelope.provider_version,
        directory, outer_digest: pkg.outer_digest, package_digest: pkg.envelope.package_digest, manifest_digest: pkg.envelope.manifest_digest,
        enabled: previous && !previous.uninstalled ? previous.enabled : true, uninstalled: false,
      }
      const release = join(this.releases, directory)
      await this.transaction('import', installation.installation_id, async () => {
        mkdirSync(release)
        atomicWrite(join(release, 'archive.zip'), snapshot)
        const payload = join(release, 'payload'); mkdirSync(payload)
        extractVerifiedPackage(pkg, payload)
        // Validate extracted bytes before any probe can execute the signed entrypoint.
        const verified = this.verifiedRelease(pkg, verificationInputs)
        await this.checkFiles(payload, verified.installedFiles)
        const readiness = await this.options.probe(payload, pkg.manifest)
        const next = this.records.filter(record => record.plugin_id !== installation.plugin_id).concat(installation)
        this.commit(next)
        this.states.set(installation.installation_id, { verified, ...readiness })
        this.options.onChanged?.()
      })
      await this.cleanupOldReleases(installation.plugin_id, previous?.directory)
    })
  }

  async enable(installation_id: string): Promise<void> { return this.change(installation_id, 'enable') }
  async disable(installation_id: string): Promise<void> { return this.change(installation_id, 'disable') }
  async uninstall(installation_id: string): Promise<void> { return this.change(installation_id, 'uninstall') }

  private async change(id: string, action: 'enable' | 'disable' | 'uninstall'): Promise<void> {
    return this.serialize(async () => {
      this.clearPending()
      const record = this.records.find(item => item.installation_id === id)
      if (!record || (record.uninstalled && action !== 'uninstall')) throw new ManagementError(1, '插件安装实例不存在')
      if (record.uninstalled) { await this.cleanupOldReleases(record.plugin_id); return }
      if (action === 'disable' && !record.enabled) return
      const state = action === 'enable' ? await this.checkRelease(record) : undefined
      if (action === 'enable' && record.enabled) {
        const before = JSON.stringify(this.list()); this.states.set(id, state!)
        if (before !== JSON.stringify(this.list())) this.options.onChanged?.()
        return
      }
      await this.transaction(action, id, async () => {
        this.commit(this.records.map(item => item !== record ? item : { ...record, enabled: action === 'enable', uninstalled: action === 'uninstall' }))
        if (state) this.states.set(id, state)
        this.options.onChanged?.()
      })
      if (action === 'uninstall') await this.cleanupOldReleases(record.plugin_id)
    })
  }

  private assertAvailable(): void { if (!this.initialized) reconcile() }
  private serialize<T>(work: () => Promise<T>): Promise<T> {
    const task = this.queue.then(async () => { this.assertAvailable(); rejectLinks(this.releases); rejectLinks(this.inventory); rejectLinks(this.pending); return work() })
    this.queue = task.catch(() => undefined)
    return task
  }
  private commit(records: Installation[]): void {
    atomicWrite(this.inventory, JSON.stringify({ version: 1, records }))
    this.records = records
  }
  private async transaction(action: string, id: string, work: () => Promise<void>): Promise<void> {
    atomicWrite(this.pending, JSON.stringify({ version: 1, action, installation_id: id }))
    try { await work() } finally {
      this.clearPending()
    }
  }
  private clearPending(): void {
    try { rejectLinks(this.pending); rmSync(this.pending, { force: true }); this.pendingWarning = undefined }
    catch {
      this.pendingWarning = '插件中断标记无法清理，请检查插件目录权限；安装状态以已提交清单为准'
      throw new ManagementError(11, this.pendingWarning)
    }
  }
  private async assertRetainedRelease(pkg: VerifiedPackage): Promise<void> {
    // Reuse retained signed archives, not an ever-growing release history. Once
    // S4 removes an unused archive, this store does not reconstruct its identity.
    for (const entry of readdirSync(this.releases, { withFileTypes: true })) {
      if (!uuid.test(entry.name) || !entry.isDirectory() || entry.isSymbolicLink()) continue
      let envelope: ReleaseEnvelope
      try {
        const file = join(this.releases, entry.name, 'archive.zip'); rejectLinks(file)
        const stat = lstatSync(file)
        if (!stat.isFile() || stat.size > PACKAGE_LIMITS.archive) continue
        envelope = await readSignedEnvelope(file, this.options.trust, this.allowedProviders)
      } catch { continue } // Damage to an inactive archive cannot block other installs.
      if (envelope.provider_id === pkg.envelope.provider_id && envelope.provider_release_id === pkg.envelope.provider_release_id
        && (envelope.package_digest !== pkg.envelope.package_digest || envelope.manifest_digest !== pkg.envelope.manifest_digest)) {
        throw new ManagementError(8, '同一发布版本的内容发生变化')
      }
    }
  }
  private async cleanupOldReleases(provider: string, previous?: string): Promise<void> {
    // Only the owning Host's drained mutation path calls this. Historical unknown
    // receipts have no release index and never authorize replay or pin every package.
    const keep = new Set(this.records.filter(record => !record.uninstalled).map(record => record.directory))
    if (previous) keep.add(previous)
    this.cleanupWarning = undefined
    try {
      for (const entry of readdirSync(this.releases, { withFileTypes: true })) {
        if (!uuid.test(entry.name) || !entry.isDirectory() || entry.isSymbolicLink() || keep.has(entry.name)) continue
        const directory = resolve(this.releases, entry.name)
        if (dirname(directory) !== this.releases) continue
        let envelope: ReleaseEnvelope | undefined
        try {
          const archive = join(directory, 'archive.zip'); rejectLinks(archive)
          if (lstatSync(archive).size <= PACKAGE_LIMITS.archive) envelope = await readSignedEnvelope(archive, this.options.trust, this.allowedProviders)
        } catch { /* An unreferenced incomplete staging directory is also reclaimable. */ }
        if (envelope && envelope.provider_id !== provider) continue
        rejectLinks(directory)
        rmSync(directory, { recursive: true, force: true })
      }
    } catch { this.cleanupWarning = '旧插件包未能清理，请检查目录权限；当前安装和任务结果保留' }
    if (this.cleanupWarning) { console.warn(this.cleanupWarning); this.options.onChanged?.() }
  }
  private recordTiming(record: Installation, pass: PluginCheckPass, phase: PluginCheckPhase, elapsedMs: number, succeeded: boolean, files?: number): void {
    try { this.options.onCheckTiming?.({ pluginId: record.plugin_id, pass, phase, elapsedMs, succeeded, files }) }
    catch { /* Diagnostic output must not affect plugin admission. */ }
  }

  private measureFor(record: Installation, pass: PluginCheckPass, checkActive?: () => void) {
    return async <T>(phase: PluginCheckPhase, work: () => T | Promise<T>, files?: number): Promise<T> => {
      checkActive?.()
      const started = performance.now()
      let succeeded = false
      let cancelled = false
      try {
        const result = await work()
        succeeded = true
        return result
      } catch (error) {
        cancelled = error instanceof PluginCheckCancelled
        throw error
      } finally {
        if (!cancelled) this.recordTiming(record, pass, phase, Math.round(performance.now() - started), succeeded, files)
      }
    }
  }

  private requireRecordIdentity(record: Installation, envelope: ReleaseEnvelope): void {
    if (envelope.provider_id !== record.plugin_id || envelope.provider_release_id !== record.release_id
      || envelope.provider_version !== record.version || envelope.package_digest !== record.package_digest
      || envelope.manifest_digest !== record.manifest_digest) throw new Error('plugin installation metadata mismatch')
  }

  private async checkMetadata(record: Installation): Promise<ReleaseState> {
    const measure = this.measureFor(record, 'metadata')
    const release = join(this.releases, record.directory)
    try {
      const envelope = await measure('archive_envelope', async () => {
        const archive = join(release, 'archive.zip'); rejectLinks(archive)
        const stat = await lstat(archive)
        if (!stat.isFile() || stat.size > PACKAGE_LIMITS.archive) throw new Error()
        const signed = await readSignedEnvelope(archive, this.options.trust, this.allowedProviders)
        this.requireRecordIdentity(record, signed)
        verifyReleasePlatform(signed, this.options.platform)
        return signed
      })
      const manifest = await measure('manifest', async () => {
        const path = join(release, 'payload', 'runtime-manifest.json'); rejectLinks(path)
        const stat = await lstat(path)
        if (!stat.isFile() || stat.size > 16 * 1024 ** 2) throw new Error()
        const manifest = verifyRuntimeManifest(await readFile(path), envelope, this.options.platform)
        if (!manifest.distribution.required_files.some(file => file?.path === manifest.entrypoint[0])) throw new Error()
        return manifest
      })
      const declaration = parseToolDeclaration(manifest)
      return { metadata: { envelope, manifest, declaration }, ready: false, ...(record.enabled && declaration ? { reason: '等待检查插件' } : {}) }
    } catch { return { ready: false, reason: '插件签名或清单无法确认，请检查或重新导入该插件' } }
  }

  private async checkRelease(record: Installation, pass: PluginCheckPass = 'maintenance', checkActive?: () => void): Promise<ReleaseState> {
    const measure = this.measureFor(record, pass, checkActive)
    try {
      const release = join(this.releases, record.directory)
      const archive = await measure('archive_read', async () => {
        const path = join(release, 'archive.zip'); rejectLinks(path)
        const stat = await lstat(path)
        if (!stat.isFile() || stat.size > PACKAGE_LIMITS.archive) throw new Error()
        return readFile(path)
      })
      const verificationInputs = this.verificationInputs()
      const previous = pass === 'refresh' ? this.states.get(record.installation_id)?.verified : undefined
      const reuse = previous?.verificationInputs === verificationInputs && previous.outer_digest === record.outer_digest
      // An identical archive preserves the verified signature and manifest. The installed files
      // remain an independent input and are always rehashed before any probe or execution.
      const pkg = reuse ? await measure('archive_digest', () => {
        if (sha256(archive) !== previous.outer_digest) throw new Error()
        return previous
      }) : await measure('archive_verify', async (): Promise<VerifiedRelease> => {
        const signed = await verifyOfflinePackage(archive, this.options.trust, this.options.platform, this.allowedProviders)
        const { envelope, outer_digest } = signed
        if (outer_digest !== record.outer_digest) throw new Error()
        this.requireRecordIdentity(record, envelope)
        return this.verifiedRelease(signed, verificationInputs)
      })
      const payload = join(release, 'payload')
      await measure('payload_verify', () => this.checkFiles(payload, pkg.installedFiles, checkActive), pkg.installedFiles.size)
      checkActive?.()
      const readiness = await this.options.probe(payload, pkg.manifest, { versionVerified: reuse, measure })
      return { verified: pkg, ...readiness }
    } catch (error) {
      if (error instanceof PluginCheckCancelled) throw error
      return { ready: false, reason: '插件文件、发布信任或运行依赖需要重新核对' }
    }
  }
  private verificationInputs(): string {
    return JSON.stringify([this.options.trust, this.options.platform, this.allowedProviders])
  }

  private verifiedRelease(pkg: VerifiedPackage, verificationInputs: string): VerifiedRelease {
    const { envelope, manifest, declaration, outer_digest } = pkg
    const installedFiles = new Map(manifest.distribution.required_files.map(({ path, size, sha256 }) => [path, { size, sha256 }]))
    const bytes = pkg.files.get('runtime-manifest.json')!
    installedFiles.set('runtime-manifest.json', { size: bytes.length, sha256: sha256(bytes) })
    return { envelope, manifest, declaration, outer_digest, installedFiles, verificationInputs }
  }

  private async checkFiles(root: string, files: ReadonlyMap<string, FileIntegrity>, checkActive?: () => void): Promise<void> {
    rejectLinks(root)
    const seen = new Set<string>()
    const visit = async (path: string, prefix: string): Promise<void> => {
      checkActive?.()
      // Keep one outstanding file read per plugin so a slow disk does not stall heartbeats.
      for (const entry of await readdir(path, { withFileTypes: true })) {
        checkActive?.()
        const file = join(path, entry.name); const relative = prefix + entry.name
        if (entry.isSymbolicLink()) throw new Error('linked plugin payload')
        if (entry.isDirectory()) {
          if (!(await lstat(file)).isDirectory()) throw new Error('changed plugin directory')
          await visit(file, relative + '/')
        }
        else {
          const expected = files.get(relative)
          const stat = await lstat(file)
          checkActive?.()
          if (!entry.isFile() || !stat.isFile() || !expected || stat.size !== expected.size || sha256(await readFile(file)) !== expected.sha256) throw new Error('modified plugin payload')
          seen.add(relative)
        }
      }
    }
    await visit(root, '')
    checkActive?.()
    if (seen.size !== files.size) throw new Error('incomplete plugin payload')
  }
}
