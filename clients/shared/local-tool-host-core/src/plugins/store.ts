import { randomUUID } from 'node:crypto'
import { closeSync, existsSync, fsyncSync, lstatSync, mkdirSync, openSync, readFileSync, readdirSync, realpathSync, renameSync, rmSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import semver from 'semver'
import { ManagementError, type PluginSummary } from '../management.js'
import { extractVerifiedPackage, FIRST_PARTY_PROVIDERS, PACKAGE_LIMITS, verifyOfflinePackage, type PackagePlatform, type PublisherTrust, type RuntimeManifest, type VerifiedPackage } from './package.js'

interface Installation {
  installation_id: string; plugin_id: string; release_id: string; version: string
  directory: string; outer_digest: string; package_digest: string; manifest_digest: string; enabled: boolean; uninstalled: boolean
}
interface ReleaseState { manifest?: RuntimeManifest; ready: boolean; reason?: string }
export interface PluginStoreOptions {
  home: string; trust: PublisherTrust[]; platform: PackagePlatform
  probe: (releaseRoot: string, manifest: RuntimeManifest) => Promise<{ ready: boolean; reason?: string }>
  onChanged?: () => void
}
const LEGACY_KEYS: Record<string, string> = {
  'ai.aidwork.boss-recruiting': 'boss-recruiting', 'ai.aidwork.weixin': 'weixin', 'ai.aidwork.wecom': 'wecom',
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

/** Host drains execution before calling a mutation. Old releases remain immutable and retained. */
export class PluginStore {
  private readonly root: string
  private readonly releases: string
  private readonly inventory: string
  private readonly pending: string
  private records: Installation[] = []
  private states = new Map<string, ReleaseState>()
  private initialized = false
  private blocked = false
  private queue: Promise<unknown> = Promise.resolve()

  constructor(private readonly options: PluginStoreOptions) {
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
    if (existsSync(this.pending)) reconcile()
    if (existsSync(this.inventory)) {
      try {
        if (!lstatSync(this.inventory).isFile() || lstatSync(this.inventory).size > 1024 * 1024) reconcile()
        const saved = JSON.parse(readFileSync(this.inventory, 'utf8'))
        if (!saved || saved.version !== 1 || !Array.isArray(saved.records) || saved.records.length > 3
          || Object.keys(saved).some(key => !['version', 'records'].includes(key))) reconcile()
        const ids = new Set<string>(); const providers = new Set<string>()
        for (const record of saved.records) {
          if (!record || !uuid.test(record.installation_id) || !uuid.test(record.directory)
            || !(FIRST_PARTY_PROVIDERS as readonly string[]).includes(record.plugin_id)
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
      if (!record.uninstalled) this.states.set(record.installation_id, await this.checkRelease(record))
    }
    this.initialized = true
  }

  list(): PluginSummary[] {
    this.assertAvailable()
    return this.records.filter(record => !record.uninstalled).map(record => {
      const state = this.states.get(record.installation_id)
      return {
        installation_id: record.installation_id, plugin_id: record.plugin_id,
        display_name: state?.manifest?.display_name ?? record.plugin_id, release_id: record.release_id,
        version: record.version, enabled: record.enabled, ready: state?.ready ?? false,
        ...(state?.reason ? { reason: state.reason } : {}),
      }
    })
  }

  async refresh(): Promise<void> {
    return this.serialize(async () => {
      const before = JSON.stringify(this.list())
      // Full self-contained OCR releases are large. Keep only one inflated archive in
      // memory at a time; publish the complete refreshed snapshot in one sync step.
      const states: Array<readonly [string, ReleaseState]> = []
      for (const record of this.records) {
        if (!record.uninstalled) states.push([record.installation_id, await this.checkRelease(record)])
      }
      for (const [id, state] of states) this.states.set(id, state)
      if (before !== JSON.stringify(this.list())) this.options.onChanged?.()
    })
  }

  resolveEntries(legacy: Record<string, string>): Record<string, string> {
    this.assertAvailable()
    const entries = { ...legacy }
    for (const record of this.records) {
      const key = LEGACY_KEYS[record.plugin_id]!
      delete entries[key]
      const state = this.states.get(record.installation_id)
      if (!record.uninstalled && record.enabled && state?.ready && state.manifest) {
        entries[key] = join(this.releases, record.directory, 'payload', state.manifest.entrypoint[0]!)
      }
    }
    return entries
  }

  async import(bytes: Buffer): Promise<void> {
    // Freeze the selected byte snapshot before the first await; callers retain their own Buffer.
    const snapshot = Buffer.from(bytes)
    return this.serialize(async () => {
      const pkg = await verifyOfflinePackage(snapshot, this.options.trust, this.options.platform)
      const previous = this.records.find(record => record.plugin_id === pkg.envelope.provider_id)
      if (previous) {
        if (previous.release_id === pkg.envelope.provider_release_id) {
          if (previous.package_digest !== pkg.envelope.package_digest || previous.manifest_digest !== pkg.envelope.manifest_digest) throw new ManagementError(8, '同一发布版本的内容发生变化')
          if (!previous.uninstalled) {
            const before = JSON.stringify(this.list()); this.states.set(previous.installation_id, await this.checkRelease(previous))
            if (before !== JSON.stringify(this.list())) this.options.onChanged?.()
            return
          }
        } else if (!semver.gt(pkg.envelope.provider_version, previous.version)) {
          throw new ManagementError(8, '插件升级必须使用更高版本，不能覆盖同版本或降级')
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
        this.checkFiles(payload, pkg)
        const readiness = await this.options.probe(payload, pkg.manifest)
        const next = this.records.filter(record => record.plugin_id !== installation.plugin_id).concat(installation)
        this.commit(next)
        this.states.set(installation.installation_id, { manifest: pkg.manifest, ...readiness })
        this.options.onChanged?.()
      })
    })
  }

  async enable(installation_id: string): Promise<void> { return this.change(installation_id, 'enable') }
  async disable(installation_id: string): Promise<void> { return this.change(installation_id, 'disable') }
  async uninstall(installation_id: string): Promise<void> { return this.change(installation_id, 'uninstall') }

  private async change(id: string, action: 'enable' | 'disable' | 'uninstall'): Promise<void> {
    return this.serialize(async () => {
      const record = this.records.find(item => item.installation_id === id)
      if (!record || (record.uninstalled && action !== 'uninstall')) throw new ManagementError(1, '插件安装实例不存在')
      if (record.uninstalled) return
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
    })
  }

  private assertAvailable(): void { if (!this.initialized || this.blocked) reconcile() }
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
    if (existsSync(this.pending)) { this.blocked = true; reconcile() }
    atomicWrite(this.pending, JSON.stringify({ version: 1, action, installation_id: id }))
    try { await work() } finally {
      try { rejectLinks(this.pending); rmSync(this.pending) } catch { this.blocked = true; reconcile() }
    }
  }
  private async checkRelease(record: Installation): Promise<ReleaseState> {
    try {
      const release = join(this.releases, record.directory)
      const archive = join(release, 'archive.zip'); rejectLinks(archive)
      if (!lstatSync(archive).isFile() || lstatSync(archive).size > PACKAGE_LIMITS.archive) throw new Error()
      const pkg = await verifyOfflinePackage(readFileSync(archive), this.options.trust, this.options.platform)
      if (pkg.outer_digest !== record.outer_digest || pkg.envelope.provider_id !== record.plugin_id
        || pkg.envelope.provider_release_id !== record.release_id || pkg.envelope.provider_version !== record.version
        || pkg.envelope.package_digest !== record.package_digest || pkg.envelope.manifest_digest !== record.manifest_digest) throw new Error()
      const payload = join(release, 'payload'); this.checkFiles(payload, pkg)
      return { manifest: pkg.manifest, ...await this.options.probe(payload, pkg.manifest) }
    } catch { return { ready: false, reason: '插件文件、发布信任或运行依赖需要重新核对' } }
  }
  private checkFiles(root: string, pkg: VerifiedPackage): void {
    rejectLinks(root)
    const seen = new Set<string>()
    const visit = (path: string, prefix: string): void => {
      for (const entry of readdirSync(path, { withFileTypes: true })) {
        const file = join(path, entry.name); const relative = prefix + entry.name
        if (entry.isSymbolicLink()) throw new Error('linked plugin payload')
        if (entry.isDirectory()) visit(file, relative + '/')
        else {
          const expected = pkg.files.get(relative)
          const stat = lstatSync(file)
          if (!entry.isFile() || !expected || stat.size !== expected.length || !readFileSync(file).equals(expected)) throw new Error('modified plugin payload')
          seen.add(relative)
        }
      }
    }
    visit(root, '')
    if (seen.size !== pkg.files.size) throw new Error('incomplete plugin payload')
  }
}
