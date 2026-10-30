import { createHash, randomUUID } from 'node:crypto'
import { execFile } from 'node:child_process'
import { existsSync, lstatSync, mkdirSync, readFileSync, readdirSync, realpathSync, renameSync, rmSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

export interface RuntimeInstallationOptions {
  legacyHome: string; userDataPath: string; appId: string; node: string; hostEntry: string; executable?: string
  stopLegacy?: () => Promise<void>
  unprotect?: (cipher: string) => Promise<string>
  protect?: (plaintext: string) => Promise<string>
  verifyIdentity?: (config: MigratedConfiguration, token: string) => Promise<boolean>
  migratePlugins?: (source: string, target: string) => Promise<void>
}
export interface MigratedConfiguration { server: string; device_id: string; name?: string }
export interface RuntimeInstallation {
  home: string; metadataHome: string; metadataDirectory: string; installationId: string; migrated: boolean; fresh: boolean; priorHome: string | null
}
interface Pointer { version: 2; id: string; home: string; priorHome: string | null; initialized: boolean; installDirectory?: string }
const uuid = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/
const validToken = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value)
const same = (a: string, b: string) => path.resolve(a).toLowerCase() === path.resolve(b).toLowerCase()
function assertDirectory(directory: string) {
  if (!path.isAbsolute(directory) || path.dirname(directory) === directory) throw new Error('Runtime配置目录必须是明确的绝对路径')
  let ancestor = directory
  while (!existsSync(ancestor)) ancestor = path.dirname(ancestor)
  if (!same(realpathSync(ancestor), ancestor)) throw new Error('Runtime配置目录不能通过目录链接重定向')
  mkdirSync(directory, { recursive: true })
  if (lstatSync(directory).isSymbolicLink() || !same(realpathSync(directory), directory)) throw new Error('Runtime配置目录异常，请检查目录权限')
}
function atomic(file: string, value: string) {
  const temporary = file + '.' + randomUUID() + '.tmp'
  writeFileSync(temporary, value, { flag: 'wx' }); renameSync(temporary, file)
}
export function runtimeInstallationMetadata(options: Pick<RuntimeInstallationOptions, 'userDataPath'>): string {
  const root = path.join(options.userDataPath, 'installation-v2'); assertDirectory(root); return root
}
function safeRead(file: string): string {
  const stat = lstatSync(file)
  if (!stat.isFile() || stat.isSymbolicLink() || stat.size > 65536 || !same(realpathSync(file), file)) throw new Error('旧配置不可读取')
  return readFileSync(file, 'utf8').replace(/^\uFEFF/, '')
}
function configuration(value: unknown): MigratedConfiguration | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const raw = value as Record<string, unknown>
  if (typeof raw.server !== 'string' || raw.server.length > 4096 || typeof raw.device_id !== 'string' || !uuid.test(raw.device_id)
      || raw.name !== undefined && (typeof raw.name !== 'string' || !raw.name.trim() || raw.name.length > 100)) return null
  try { const url = new URL(raw.server); if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) return null } catch { return null }
  return { server: raw.server, device_id: raw.device_id, ...(typeof raw.name === 'string' ? { name: raw.name } : {}) }
}
async function credentialModule(options: RuntimeInstallationOptions): Promise<{ dpapiProtect(v: string): Promise<string>; dpapiUnprotect(v: string): Promise<string> }> {
  return import(pathToFileURL(path.join(path.dirname(options.hostEntry), 'dpapi.js')).href)
}
async function validIdentity(config: MigratedConfiguration, token: string): Promise<boolean> {
  // An outage must not erase a usable identity. Only explicit authentication
  // rejection proves that it cannot be reused; no claim or business work occurs.
  try {
    const response = await fetch(new URL('api/local-tools/runtime/heartbeat', config.server.replace(/\/?$/, '/')), {
      method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: '{}', signal: AbortSignal.timeout(8000),
    })
    return response.status !== 401 && response.status !== 403
  } catch { return true }
}
function readPointer(root: string): Pointer | null {
  const file = path.join(root, 'current.json')
  if (!existsSync(file)) return null
  const record = JSON.parse(safeRead(file)) as Pointer
  if (!record || typeof record !== 'object' || Array.isArray(record) || record.version !== 2 || !uuid.test(record.id) || typeof record.home !== 'string' || typeof record.initialized !== 'boolean'
      || !(record.priorHome === null || typeof record.priorHome === 'string')) throw new Error('当前安装登记损坏，请重新安装客户端')
  assertHome(root, record.home, record.id)
  return record
}
function assertHome(root: string, home: string, id: string) {
  if (!path.isAbsolute(home)) throw new Error('Runtime安装目录登记无效')
  if (same(home, path.join(path.dirname(root), 'rt'))) return
  if (path.isAbsolute(home) && same(path.dirname(home), path.join(path.dirname(root), 'connections')) && uuid.test(path.basename(home))) return
  const parent = path.join(root, 'generations', id, 'connections')
  if (!path.isAbsolute(home) || !same(path.dirname(home), parent) || !uuid.test(path.basename(home))) throw new Error('Runtime安装目录登记无效')
}
function allocateHome(root: string): string {
  const stable = path.join(path.dirname(root), 'rt')
  assertDirectory(stable)
  if (readdirSync(stable).length === 0) return stable
  const home = path.join(path.dirname(root), 'connections', randomUUID()); assertDirectory(home); return home
}
function packageKey(options: RuntimeInstallationOptions): string {
  return createHash('sha256').update(path.resolve(options.hostEntry).toLowerCase()).update(readFileSync(options.hostEntry)).digest('hex')
}
async function stopOld(options: RuntimeInstallationOptions) {
  if (options.stopLegacy) return options.stopLegacy()
  if (process.platform !== 'win32') return
  const script = path.resolve(path.dirname(options.hostEntry), '../runtime-clean-install.ps1')
  await new Promise<void>((resolve, reject) => execFile('powershell.exe', ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', script,
    '-Mode', 'Stop', '-AppId', options.appId, '-InstallDirectory', path.resolve(path.dirname(options.hostEntry), '../../..'), '-ExcludePid', String(process.pid)],
  { windowsHide: true, timeout: 30000, env: Object.fromEntries(Object.entries(process.env).filter(([key]) => key.toLowerCase() !== 'psmodulepath')) }, (error) => error ? reject(new Error('旧Runtime进程接管失败，请检查当前用户权限或系统PowerShell')) : resolve()))
}
async function migrate(options: RuntimeInstallationOptions, root: string, home: string, source: string): Promise<boolean> {
  const dpapi = options.unprotect && options.protect ? null : await credentialModule(options)
  const unprotect = options.unprotect ?? dpapi!.dpapiUnprotect
  const protect = options.protect ?? dpapi!.dpapiProtect
  let config: MigratedConfiguration | null = null, token: string | null = null, pending = false, staged = false
  // A previously paired protocol-2 identity wins over the active old identity.
  // Only credentials are migrated; resource/activation journals are never loaded.
  try {
    const registration = JSON.parse(await unprotect(safeRead(path.join(source + '.connection-registration', 'state.bin'))))
    if (registration.version === 1 && same(registration.home, source) && ['paired', 'activating', 'active'].includes(registration.phase)) {
      config = configuration(registration.config); token = registration.token; pending = registration.replacement_pending === true
      staged = registration.phase !== 'active' || pending
    }
  } catch { /* Legacy damage is a migration miss, not a startup failure. */ }
  if (!config || !validToken(token)) {
    pending = false; staged = false
    try { config = configuration(JSON.parse(safeRead(path.join(source, 'config.json')))); token = await unprotect(safeRead(path.join(source, 'credentials.bin'))) }
    catch {
      try {
        const previous = JSON.parse(await unprotect(safeRead(path.join(root, 'previous-identity.bin'))))
        config = configuration(previous.config); token = previous.token
      } catch { return false }
    }
  }
  if (!config || !validToken(token)) return false
  // Keep only protected identity proof, allowing a new pair to retire this exact
  // binding even when its authentication has already been revoked.
  atomic(path.join(root, 'previous-identity.bin'), await protect(JSON.stringify({ config, token })))
  if (!await (options.verifyIdentity ?? validIdentity)(config, token)) return false
  atomic(path.join(home, 'credentials.bin'), await protect(token))
  atomic(path.join(home, 'config.json'), JSON.stringify(config))
  if (staged) atomic(path.join(root, 'legacy-replacement-pending.json'), JSON.stringify({ version: 1, device_id: config.device_id, replacement_pending: pending }))
  return true
}
async function migratePlugins(options: RuntimeInstallationOptions, source: string, target: string): Promise<void> {
  if (options.migratePlugins) return options.migratePlugins(source, target)
  const inventory = path.join(source, 'plugins/inventory.json')
  if (!existsSync(inventory)) return
  let records: Record<string, unknown>[]
  try {
    const stat = lstatSync(inventory)
    if (!stat.isFile() || stat.isSymbolicLink() || stat.size > 1024 * 1024 || !same(realpathSync(inventory), inventory)) return
    const value = JSON.parse(readFileSync(inventory, 'utf8'))
    if (value.version !== 1 || !Array.isArray(value.records) || value.records.length > 3) return
    records = value.records
  } catch { return }
  const base = path.join(path.dirname(options.hostEntry), 'node_modules/@aid/local-tool-host-core/dist/plugins')
  const { PluginStore } = await import(pathToFileURL(path.join(base, 'store.js')).href)
  const { verifyOfflinePackage, PACKAGE_LIMITS, FIRST_PARTY_PROVIDERS } = await import(pathToFileURL(path.join(base, 'package.js')).href)
  const trust = JSON.parse(safeRead(path.resolve(path.dirname(options.hostEntry), '../trust-roots.json'))).roots
  const nodeFacts = await new Promise<string>((resolve, reject) => execFile(options.node,
    ['-p', 'JSON.stringify({platform:process.platform,arch:process.arch,node_version:process.versions.node,node_abi:process.versions.modules})'],
    { encoding: 'utf8', windowsHide: true, timeout: 10000, env: { ...process.env, NODE_OPTIONS: '', NODE_PATH: '' } }, (error, stdout) => error ? reject(new Error('新版Node环境检查失败')) : resolve(stdout)))
  const platform = { ...JSON.parse(nodeFacts), runtime_version: JSON.parse(safeRead(path.join(path.dirname(options.hostEntry), 'package.json'))).version }
  const storeOptions = { home: target, trust, platform, probe: async () => ({ ready: false, reason: '正在检查新版插件环境' }) }
  let store = new PluginStore(storeOptions)
  try { await store.initialize() }
  catch {
    const pluginRoot = path.join(target, 'plugins')
    if (existsSync(pluginRoot)) renameSync(pluginRoot, path.join(target, 'plugins-isolated-' + randomUUID()))
    store = new PluginStore(storeOptions); await store.initialize()
  }
  for (const record of records) {
    if (!record || record.uninstalled !== false || typeof record.enabled !== 'boolean' || typeof record.directory !== 'string'
        || !uuid.test(record.directory) || !FIRST_PARTY_PROVIDERS.includes(record.plugin_id)) continue
    let bytes: Buffer
    try {
      const archive = path.join(source, 'plugins/releases', record.directory, 'archive.zip')
      const stat = lstatSync(archive)
      if (!stat.isFile() || stat.isSymbolicLink() || stat.size > PACKAGE_LIMITS.archive || !same(realpathSync(archive), archive)) continue
      bytes = readFileSync(archive)
      const pkg = await verifyOfflinePackage(bytes, trust, platform)
      if (pkg.envelope.provider_id !== record.plugin_id) continue
    } catch {
      console.warn(`[runtime-installation] 插件迁移未完成，请重新安装兼容离线包：${record.plugin_id}`)
      continue
    }
    // Import the verified archive into new payloads and fresh metadata. Never
    // copy old payload directories, plugin cache, journals or unfinished ops.
    try {
      await store.import(bytes)
      if (!record.enabled) {
        const item = store.list().find((plugin: { plugin_id: string }) => plugin.plugin_id === record.plugin_id)
        if (item) await store.disable(item.installation_id)
      }
    } catch {
      // Store commits atomically. Keep packages already migrated successfully;
      // failure of another plugin cannot remove the customer's working BOSS tools.
      console.warn(`[runtime-installation] 插件迁移未完成，请重新安装兼容离线包：${record.plugin_id}`)
    }
  }
}
export async function prepareRuntimeInstallation(options: RuntimeInstallationOptions): Promise<RuntimeInstallation> {
  const root = runtimeInstallationMetadata(options)
  const stamp = path.resolve(path.dirname(options.hostEntry), '../../../runtime-installation.id')
  let id: string | null = null
  if (existsSync(stamp)) { id = safeRead(stamp).trim(); if (!uuid.test(id)) throw new Error('安装代际登记无效，请重新运行安装包') }
  const ticket = path.join(root, 'packages', packageKey(options) + '.json')
  if (!id && existsSync(ticket)) { const v = JSON.parse(safeRead(ticket)); if (uuid.test(v.id)) id = v.id }
  let current: Pointer | null
  try { current = readPointer(root) }
  catch (error) {
    if (!(error instanceof SyntaxError) && (!(error instanceof Error) || !['当前安装登记损坏，请重新安装客户端', 'Runtime安装目录登记无效'].includes(error.message))) throw error
    // A readable but corrupt old pointer cannot block the trusted installed
    // generation. Directory/permission/link errors still propagate explicitly.
    current = null
  }
  const installDirectory = path.resolve(path.dirname(options.hostEntry), '../../..')
  if (!id && current) {
    if (!current.installDirectory || !same(current.installDirectory, installDirectory)) throw new Error('该安装已被新版替代，请打开最新安装的客户端')
    id = current.id
  }
  let priorHome = options.legacyHome
  if (id && current && id !== current.id) {
    // Per-machine upgrades can be installed by a different Windows account.
    // A new machine-wide stamp must not move another user's existing paired
    // home. Explicit Clean has already replaced that user's pointer in Finalize.
    if (!existsSync(stamp) || !current.installDirectory || !same(current.installDirectory, installDirectory)) throw new Error('该安装已被新版替代，请打开最新安装的客户端')
    id = current.id
  }
  if (!current) {
    id ??= randomUUID()
    const home = allocateHome(root)
    current = { version: 2, id, home, priorHome, initialized: false, installDirectory }
    atomic(path.join(root, 'current.json'), JSON.stringify(current))
  } else id ??= current.id
  assertDirectory(path.dirname(ticket)); if (!existsSync(ticket)) atomic(ticket, JSON.stringify({ id }))
  let migrated = existsSync(path.join(current.home, 'config.json'))
  const fresh = !current.initialized
  if (!current.initialized) {
    await stopOld(options)
    assertDirectory(current.home)
    if (current.priorHome) {
      if (!ownedHome(root, current.priorHome, options.legacyHome)) current.priorHome = options.legacyHome
      migrated = await migrate(options, root, current.home, current.priorHome)
      await migratePlugins(options, current.priorHome, current.home)
    }
    current.initialized = true; atomic(path.join(root, 'current.json'), JSON.stringify(current))
    if (current.priorHome) await cleanupRuntimePreviousHome(options, current.priorHome)
  }
  return { home: current.home, metadataHome: root, metadataDirectory: root, installationId: current.id, priorHome: current.priorHome, migrated, fresh }
}
/** Called after the owned Host is stopped, under the Main registration lease. */
export async function rotateRuntimeConnection(options: RuntimeInstallationOptions, currentHome: string): Promise<string> {
  const root = runtimeInstallationMetadata(options), current = readPointer(root)
  if (!current || !same(current.home, currentHome)) throw new Error('连接已被另一个客户端更新，请刷新连接状态')
  const home = path.join(path.dirname(root), 'connections', randomUUID()); assertDirectory(home)
  await migratePlugins(options, currentHome, home)
  atomic(path.join(root, 'current.json'), JSON.stringify({ ...current, home, priorHome: currentHome, initialized: true }))
  // The current registration may still reference the former home; cleanup
  // recognizes that and defers. Main can call the same helper after activation.
  await cleanupRuntimePreviousHome(options, currentHome)
  return home
}
/** Resume only a previously allocated connection in the current installation. */
export async function selectRuntimeConnection(options: RuntimeInstallationOptions, target: string): Promise<string> {
  const root = runtimeInstallationMetadata(options), current = readPointer(root)
  if (!current) throw new Error('当前安装尚未登记')
  if (current.priorHome && same(target, current.priorHome) && !same(target, current.home)) throw new Error('旧连接已被替换，请继续使用新的连接目录')
  assertHome(root, target, current.id)
  if (!existsSync(target)) throw new Error('已分配的新连接目录不存在')
  assertDirectory(target)
  atomic(path.join(root, 'current.json'), JSON.stringify({ ...current, home: target, initialized: true }))
  return target
}
/** Best effort only. Never deletes stable registration or follows old links. */
export async function cleanupRuntimePreviousHome(options: RuntimeInstallationOptions, source: string): Promise<'removed' | 'retained' | 'cleanup_failed'> {
  try {
    const root = runtimeInstallationMetadata(options), current = readPointer(root)
    if (!current || same(source, current.home) || !existsSync(source)) return 'retained'
    if (!ownedHome(root, source, options.legacyHome)) return 'retained'
    if (!path.isAbsolute(source) || lstatSync(source).isSymbolicLink() || !lstatSync(source).isDirectory() || !same(realpathSync(source), source)) return 'retained'
    const pending = path.join(root, 'clean-registration.bin')
    if (existsSync(pending)) {
      try {
        const unprotect = options.unprotect ?? (await credentialModule(options)).dpapiUnprotect
        const record = JSON.parse(await unprotect(safeRead(pending)))
        if (record.home && same(record.home, source)) return 'retained'
      } catch { return 'retained' }
    }
    // Retired control state can be removed, but task facts and unacknowledged
    // results belong to their original identity even after a clean replacement.
    for (const name of ['journal', 'outbox', 'result-outbox', 'session-tasks', 'process-supervision', 'host-running.json', 'identity-reconciliation.bin', 'invocation-fences']) {
      if (existsSync(path.join(source, name))) return 'retained'
    }
    const replaceable = new Set(['config.json', 'credentials.bin', 'management-secret.bin', 'management-operations.json', 'management-retired-request-keys.bin', 'plugins'])
    if (readdirSync(source).some(name => !replaceable.has(name))) return 'retained'
    // Absolute exact range verified above. Node rm removes a junction itself;
    // it does not recursively delete targets outside this product-owned root.
    rmSync(source, { recursive: true, force: true })
    return 'removed'
  } catch { return 'cleanup_failed' }
}
function ownedHome(root: string, source: string, legacy: string): boolean {
  if (!path.isAbsolute(source)) return false
  if (same(source, legacy)) return true
  if (same(source, path.join(path.dirname(root), 'rt'))) return true
  if (same(path.dirname(source), path.join(path.dirname(root), 'connections')) && uuid.test(path.basename(source))) return true
  const relative = path.relative(path.join(root, 'generations'), source).split(path.sep)
  return relative.length === 3 && uuid.test(relative[0]!) && relative[1] === 'connections' && uuid.test(relative[2]!)
}
