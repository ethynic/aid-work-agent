import { recoveryObservation } from './recoveryObservation.js'
import { closeSync, existsSync, fstatSync, fsyncSync, lstatSync, mkdirSync, openSync, readFileSync, readSync, renameSync, rmSync, writeFileSync } from 'node:fs'
import { randomUUID } from 'node:crypto'
import { ApiClient } from './apiClient.js'
import { registrationBlocksExecution } from './connectionRegistration.js'
import {
  deviceCapabilities,
  loadConfig,
  machineFingerprint,
  PLATFORM,
  resolveProviderEntries,
  RUNTIME_VERSION,
  runtimeHomeDir,
} from './config.js'
import { loadDeviceToken } from './credentials.js'
import { checkDesktopInteractive } from './desktopCheck.js'
import { deriveResourceKey } from './desktopLock.js'
import { PollLoop } from './pollLoop.js'
import { ProviderSet, type ProviderManager } from './providerManager.js'
import { ResultOutbox, resultOutboxDir } from './resultOutbox.js'
import { dpapiProtect, dpapiUnprotect } from './dpapi.js'
import { logInfo } from './log.js'
import { readdirSync } from 'node:fs'


import { ManagementError, RuntimeHost, PACKAGE_LIMITS, sha256, type ConnectionStatus, type HostAdapter, type PublisherTrust, type RuntimePlatform } from '@aid/local-tool-host-core'
import { PluginStore } from '@aid/local-tool-host-core/plugins/store'
import { configureRuntimeHome, type RuntimeConfig } from './config.js'
import { join } from 'node:path'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { atomicWrite, recoverPairing } from './pairingStore.js'
import { assertNoLegacyRuntimeProcess } from './legacyProcessCheck.js'
import { probePlugin } from './pluginProbe.js'
import { loadPackagedTrust, providerIdsOfTrust } from './productTrust.js'
import { hasJournalCompletionProof } from '@aid/local-tool-host-core/legacy/completionProof'
import { hasSessionCompletion } from './sessionTasks/completionProof.js'
import { ProcessSupervision } from './processSupervision.js'
import { resumeConnectionRecovery, recoverManagementRepairExecution } from './connectionRecoveryStore.js'
import { connectionFailure } from './connectionDiagnosis.js'
import { recordFreshConnectionEpoch } from './connectionRecoveryResources.js'
import { assembleStoreManifests, setManifestOverride, weixinV2Manifest } from './providers.js'

function providerEnvironment(): Record<string, string> {
  const env: Record<string, string> = {}
  for (const key of ['PATH','Path','SystemRoot','WINDIR','COMSPEC','PATHEXT','TEMP','TMP','APPDATA','LOCALAPPDATA','USERPROFILE','USERNAME','SESSIONNAME','PROCESSOR_ARCHITECTURE']) {
    if (process.env[key] !== undefined) env[key] = process.env[key]!
  }
  return env
}

export class LegacyExecution implements HostAdapter {
  private pluginStore: PluginStore | null = null
  private pluginsChanged: (() => void) | undefined
  mutatePlugin?: HostAdapter['mutatePlugin']
  preparePluginImport?: HostAdapter['preparePluginImport']
  private loop: PollLoop | null = null
  private providers: ProviderSet | null = null
  private runPromise: Promise<void> | null = null
  private runFailure: unknown = null
  private stopping = false
  private supervision: ProcessSupervision
  constructor(private nodeExecutable: string, private home: string = runtimeHomeDir(), private pluginOptions?: { trust: PublisherTrust[]; platform?: RuntimePlatform }, private cleanGeneration = false) {
    this.supervision = new ProcessSupervision(home, nodeExecutable)
    if (pluginOptions?.platform) {
      this.preparePluginImport = async (params, context) => {
        const snapshot = await pluginOptions.platform!.takeSelectedPackage({ selection_ref: params.selection_ref!, request_key: params.request_key!, instance_id: context.instance_id })
        if (!Number.isSafeInteger(snapshot.size) || snapshot.size < 1 || snapshot.size > PACKAGE_LIMITS.archive || lstatSync(snapshot.staged_path).isSymbolicLink()) throw new ManagementError(8, '选择的插件文件无效或过大')
        const fd = openSync(snapshot.staged_path, 'r')
        let bytes: Buffer
        try {
          const before = fstatSync(fd)
          if (!before.isFile() || before.size !== snapshot.size) throw new ManagementError(7, '选择的插件文件已变化，请重新选择')
          bytes = Buffer.alloc(snapshot.size)
          let offset = 0
          while (offset < bytes.length) {
            const read = readSync(fd, bytes, offset, bytes.length - offset, null)
            if (!read) throw new ManagementError(7, '选择的插件文件已变化，请重新选择')
            offset += read
          }
          if (readSync(fd, Buffer.alloc(1), 0, 1, null)) throw new ManagementError(7, '选择的插件文件已变化，请重新选择')
          const after = fstatSync(fd)
          if (after.size !== before.size || after.mtimeMs !== before.mtimeMs || bytes.length !== snapshot.size || sha256(bytes) !== snapshot.sha256) throw new ManagementError(7, '选择的插件文件已变化，请重新选择')
        } finally { closeSync(fd) }
        const directory = join(this.home, 'management-imports')
        if (existsSync(directory) && lstatSync(directory).isSymbolicLink()) throw new ManagementError(11, 'Host离线包暂存目录需要核对')
        mkdirSync(directory, { recursive: true })
        const snapshot_id = randomUUID(); const file = join(directory, `${snapshot_id}.zip`)
        writeFileSync(file, bytes, { flag: 'wx', mode: 0o600 })
        const persisted = openSync(file, 'r+'); try { fsyncSync(persisted) } finally { closeSync(persisted) }
        return { snapshot_id, sha256: snapshot.sha256 }
      }
      this.mutatePlugin = async (method, params, context) => {
        const store = this.pluginStore!
        if (method === 'plugins.import') {
          const prepared = context.prepared_import
          if (!prepared || !/^[0-9a-f-]{36}$/.test(prepared.snapshot_id)) throw new ManagementError(11, '已接单离线包快照缺失，需核对')
          const file = join(this.home, 'management-imports', `${prepared.snapshot_id}.zip`)
          if (lstatSync(file).isSymbolicLink() || lstatSync(file).size > PACKAGE_LIMITS.archive) throw new ManagementError(11, '已接单离线包快照无效，需核对')
          const bytes = readFileSync(file)
          if (sha256(bytes) !== prepared.sha256) throw new ManagementError(11, '已接单离线包快照已变化，需核对')
          await store.import(bytes)
        } else if (method === 'plugins.enable') await store.enable(params.installation_id!)
      else if (method === 'plugins.disable') await store.disable(params.installation_id!)
      else if (method === 'plugins.uninstall') await store.uninstall(params.installation_id!)
      else throw new ManagementError(3, '不支持此插件管理动作')
      }
    }
  }
  observePlugins(listener: () => void): void { this.pluginsChanged = listener }
  beforeManagementInit = async () => {
    await resumeConnectionRecovery(this.home, this.nodeExecutable)
    if (process.platform === 'win32' && readdirSync(this.home).length === 0) await recordFreshConnectionEpoch(this.home, this.nodeExecutable)
  }
  pauseForConnectionRecovery(): void {
    this.loop?.stopClaiming()
    this.providers?.stopAdmission()
  }
  plugins() { return this.pluginStore?.list() ?? [] }
  cleanupPluginImports(retained: readonly string[]): void {
    const directory = join(this.home, 'management-imports')
    if (!existsSync(directory)) return
    if (lstatSync(directory).isSymbolicLink()) throw new Error('插件暂存目录需要检查')
    const keep = new Set(retained.map(id => `${id}.zip`))
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.zip$/.test(entry.name) || keep.has(entry.name)) continue
      if (entry.isFile() && !entry.isSymbolicLink()) rmSync(join(directory, entry.name), { force: true })
    }
  }
  async refreshPlugins() { await this.pluginStore?.refresh() }
  async canChangePlugins(method?: string): Promise<boolean> {
    // Management drains these execution lanes before switching a release. Historical
    // journals/outbox stay authoritative, but do not prevent unrelated maintenance.
    return !this.loop && !this.providers && !this.runPromise
  }
  async initialize(): Promise<void> {
    configureRuntimeHome(this.home)
    if (!this.cleanGeneration) await assertNoLegacyRuntimeProcess()
    try { await recoverPairing() } catch { throw new ManagementError(11, '设备配对资料未能恢复，原身份文件和待处理事务已保留') }
    if (this.pluginOptions) {
      const makeStore = () => new PluginStore({ home: this.home, trust: this.pluginOptions!.trust,
        // 契约 2.20 修订：安装白名单由产品信任配置驱动（trust roots 覆盖的 provider_id
        // 并集）——往信任配置加新 provider 身份即可安装接入，无需改源码；无信任配置的
        // CLI/A1 资产不产生安装权限，PluginStore 维持 FIRST_PARTY_PROVIDERS 默认
        ...(this.pluginOptions!.trust ? { providers: providerIdsOfTrust(this.pluginOptions!.trust) } : {}),
        platform: { platform: process.platform, arch: process.arch, node_version: process.versions.node, node_abi: process.versions.modules, runtime_version: RUNTIME_VERSION },
        probe: (root, manifest) => probePlugin(this.nodeExecutable, root, manifest, providerEnvironment()),
        onChanged: () => this.pluginsChanged?.() })
      try { this.pluginStore = makeStore(); await this.pluginStore.initialize() } catch (error) {
        if (this.cleanGeneration && existsSync(join(this.home, 'plugins')) && !lstatSync(join(this.home, 'plugins')).isSymbolicLink()) {
          // A corrupt plugin inventory is isolated from device connectivity.
          renameSync(join(this.home, 'plugins'), join(this.home, `plugins-invalid-${randomUUID()}`))
          this.pluginStore = makeStore(); await this.pluginStore.initialize()
          return
        }
        if (error instanceof ManagementError) throw error
        throw new ManagementError(11, '插件目录无法初始化，安装记录已保留')
      }
    } else if (existsSync(join(this.home, 'plugins/inventory.json'))) {
      throw new ManagementError(9, '此配置目录含受管插件，需使用具备发布信任配置的Runtime产品')
    }
  }
  onDisposed(): void { compositionOpen = false; configureRuntimeHome(undefined) }
  protect = dpapiProtect
  unprotect = dpapiUnprotect
  executionMarker = (instanceId: string) => this.supervision.marker(instanceId)
  recoverPreviousExecution = async (marker: string) => await this.supervision.recover(marker) || await recoverManagementRepairExecution(this.home, this.nodeExecutable)
  device() {
    const config = loadConfig()
    return config ? { device_id: config.device_id, ...(typeof config.name === 'string' && config.name ? { name: config.name } : {}) } : undefined
  }
  async canReplaceIdentity(): Promise<boolean> {
    const home = runtimeHomeDir()
    if (existsSync(join(home, 'identity-reconciliation.bin'))) throw new ManagementError(11, '旧任务结果待核对；原设备可以继续连接，暂不能更换配对身份')
    if (existsSync(join(home, 'pairing.pending.json'))) return false
    // Historical records without original ACK evidence stay blocked. New known-effect
    // journals can settle only through the proof written before outbox deletion.
    for (const dir of ['result-outbox', 'journal', 'session-tasks']) {
      const location = join(home, dir)
      try {
        const files = existsSync(location) ? readdirSync(location) : []
        const settled = dir === 'journal' ? files.every(file => hasJournalCompletionProof(home, file))
          : dir === 'session-tasks' ? (await Promise.all(files.map(file => hasSessionCompletion(join(location, file), file, { protect: dpapiProtect, unprotect: dpapiUnprotect })))).every(Boolean)
          : files.length === 0
        if (!settled) {
          throw new ManagementError(11, dir === 'result-outbox' ? '旧设备结果尚需确认' : '历史执行事实缺少结案证明')
        }
      } catch (error) {
        if (error instanceof ManagementError) throw error
        throw new ManagementError(11, '历史执行事实无法读取，需核对')
      }
    }
    return true
  }
  async pair(params: { server: string; device_name: string; pairing_code: string }): Promise<void> {
    if (await registrationBlocksExecution(runtimeHomeDir())) throw new ManagementError(11, '新注册尚未完成，请在客户端完成连接配置，不要重复配对')
    let url: URL
    try { url = new URL(params.server) } catch { throw new ManagementError(1, '服务地址无效') }
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) throw new ManagementError(1, '服务地址无效')
    const config = loadConfig()
    let result
    try {
      result = await new ApiClient(params.server).pair({ code: params.pairing_code.trim(), name: params.device_name,
        platform: PLATFORM, runtime_version: RUNTIME_VERSION,
        capabilities: deviceCapabilities(this.effectiveConfig(config), false, false, false, false, false, this.storeDeclarations()),
        machine_fingerprint: machineFingerprint() })
    } catch (error) {
      const failure = connectionFailure(error, true)
      throw new ManagementError(failure.category === 'PAIRING_CODE_INVALID' ? 1 : 11, [failure.summary, ...failure.steps].join('。'))
    }
    // 历史 config 可能仍携带已移除的 skills 键：解构丢弃，新配对事务从源头干净
    // （白名单外的键会让存量 pending 在 recoverPairing 被拒，卡住下一次启动）。
    const { skills: _legacySkills, ...carried } = { ...config } as RuntimeConfig & { skills?: unknown }
    const next: RuntimeConfig = { ...carried, server: params.server, device_id: result.device_id, name: params.device_name }
    const pending = join(runtimeHomeDir(), 'pairing.pending.json')
    atomicWrite(pending, JSON.stringify({ config: next, encrypted_token: await dpapiProtect(result.device_token) }))
    await recoverPairing()
  }
  async start(connection: (value: ConnectionStatus) => void, executionEnded: () => void): Promise<void> {
    if (await registrationBlocksExecution(runtimeHomeDir())) throw new ManagementError(11, '新注册尚未完成，请在客户端完成连接配置；可正常退出')
    await recoverPairing()
    const config = loadConfig()
    if (!config) throw new ManagementError(5, '请先配对设备')
    const server = config.server
    let token: string
    try { token = await loadDeviceToken() } catch { throw new ManagementError(9, '设备凭证不可用，请核对本机身份') }
    await this.pluginStore?.refresh()
    const entries = Object.fromEntries(Object.entries(this.resolveEntries(config)).filter(([, entry]) => existsSync(entry)))
    const store = this.storeDeclarations()
    const { manifests: storeManifests, parallelTools } = store
    const api = new ApiClient(server, token)
    const wecomLegacyEnv: Record<string, string> = {}
    for (const key of ['AID_WECOM_SERVER_URL', 'AID_WECOM_SERVER_TOKEN']) {
      if (process.env[key] !== undefined) wecomLegacyEnv[key] = process.env[key]!
    }
    // Keep legacy WeCom credentials for already delivered plugins; new plugins use pairing.
    const pairedEnv = { AIDWORK_RUNTIME_SERVER: server, AIDWORK_RUNTIME_DEVICE_TOKEN: token }
    const providers = new ProviderSet(entries, { nodeExecutable: this.nodeExecutable, baseEnv: providerEnvironment(),
      ...(this.supervision.enabled ? { prepareLaunch: (entry: string) => this.supervision.prepareLaunch(entry) } : {}),
      providerEnv: { wecom: { ...wecomLegacyEnv, ...pairedEnv }, 'boss-recruiting': pairedEnv,
        ...(storeManifests.weixin ? { weixin: { AID_WEIXIN_SERVER_URL: server } } : {}) } })
    this.providers = providers
    this.runFailure = null
    this.stopping = false
    // 签名商店声明同时约束运行与能力上报。旧 v2Send 标志只用于仍在运行的遗留直配。
    setManifestOverride('weixin', null)
    if (!storeManifests.weixin && entries.weixin && config.providers?.['weixin']?.v2Send === true) {
      setManifestOverride('weixin', weixinV2Manifest())
      logInfo('[runtime] weixin 遗留直配显式配置 v2Send=true，采用兼容 manifest')
    }
    if (store.hasDeclarations) logInfo(`[runtime] 商店声明插件: ${Object.keys(storeManifests).join(', ')}`)
    logInfo(`[runtime] providers: ${Object.keys(entries).join(', ')}`)
    // v2 写路径数据目录（journal/ + result-outbox/，跟随 runtime home；启动重投共用同一 outbox 实例）
    const dataDir = runtimeHomeDir()
    const bossRecoveryReady = Boolean(entries['boss-recruiting'] && existsSync(resolve(dirname(entries['boss-recruiting']), '../main/operations/bossRecoveryProbe.js')))
    const wecomRecoveryReady = Boolean(entries.wecom && this.supervision.enabled)
    const recoveryReady = bossRecoveryReady || wecomRecoveryReady
    const capabilities = () => {
      let wecom: ProviderManager | undefined
      if (entries.wecom && !this.stopping) {
        try { wecom = providers.get('wecom') } catch { /* The existing recovery operation temporarily owns the pool. */ }
      }
      // Discovery never delays heartbeat/claim and cannot invoke a business tool.
      // A failed transport is retried by the next heartbeat, without another worker.
      void wecom?.inspectWecomBusinessTools().catch(() => {})
      const value = deviceCapabilities(this.effectiveConfig(config), wecom?.wecomBusinessReady,
        wecom?.wecomDirectoryReady, wecom?.wecomDirectorySearchReady,
        wecom?.wecomConfigDoctorReady, wecom?.wecomConfigCalibrateReady,
        store)
      if (recoveryReady && this.supervision.enabled) value.capabilities = [...(value.capabilities as string[]), 'runtime_recovery_stop_v1']
      if (wecomRecoveryReady) value.capabilities = [...(value.capabilities as string[]), 'runtime_recovery_wecom_v1']
      if (recoveryReady) { value.providers = [...(value.providers as string[]), 'runtime-recovery']; value.capabilities = [...(value.capabilities as string[]), 'runtime_recovery_observe_v2'] }
      return value
    }
    const loop = new PollLoop({
      api,
      runnerDeps: {
        providers,
        manifests: storeManifests,
        beforeProviderCall: async (inv) => {
          const provider = inv.provider ?? 'boss-recruiting'
          if (provider === 'boss-recruiting' && inv.tool_name !== 'boss_recovery_probe'
              || provider === 'wecom' && !['wecom_probe', 'wecom_send_status'].includes(inv.tool_name)) {
            await this.supervision.noteBusinessExecution()
          }
        },
        ...(recoveryReady ? { recoveryObservation: recoveryObservation(providers, this.supervision) } : {}),
        desktopCheck: async () => (await checkDesktopInteractive()).interactive,
        desktopResourceKey: deriveResourceKey(),
        runtimeDataDir: dataDir,
        resultOutbox: new ResultOutbox(resultOutboxDir(dataDir)),
      },
      // 2026-09-10 排障整改：invocation 生命周期行（领取/开始/终态回传/重试）此前只 console.log 到
      // stdout，服务化启动无重定向时文件日志全无回传链路痕迹（2026-09-10 客户现场诊断包实证）。
      // logInfo = stderr + %APPDATA% 文件双写，前缀格式不变。
      onEvent: (msg) => logInfo(msg),
      capabilities,
      canClaim: () => Object.values(this.resolveEntries(config)).some(entry => existsSync(entry)),
      onConnection: connection,
      // 契约 2.20：并行接单——与能力上报同源（assembleStoreManifests 派生），PollLoop
      // 据此判定领到的作业是否后台并行执行；无声明插件 parallel_tools 为空 → 行为与现状一致
      parallelTools,
    })

    if (config.sessionTasks === true) {
      logInfo('[session-tasks] 微信自动回复已停用，聊天正文仅在本机读取')
    }
    this.loop = loop
    this.runPromise = loop.run().then(() => {
      if (!this.stopping) executionEnded()
    }, error => {
      this.runFailure = error; loop.shutdown()
      if (!this.stopping) executionEnded()
    })
  }
  async stop(): Promise<void> {
    this.stopping = true
    this.loop?.stopClaiming()
    await this.loop?.drain()
    await this.runPromise
    // All lanes have stopped admitting work; accepted calls may still resolve their Provider first.
    this.providers?.stopAdmission()
    await this.providers?.waitForIdle()
    await this.providers?.shutdownAll()
    this.loop = null; this.providers = null; this.runPromise = null
    // A loop error is not proof of an active process after every execution lane
    // and provider has drained. Business journals/outbox remain unchanged.
    this.runFailure = null
  }
  private resolveEntries(config: RuntimeConfig | null): Record<string, string> {
    const entries = resolveProviderEntries(config)
    // The new connector has no legacy entrypoint. It is executable only through a verified package.
    delete entries.xiaohongshu
    return this.pluginStore?.resolveEntries(entries) ?? entries
  }
  private storeDeclarations() {
    const declared = this.pluginStore?.declaredPlugins() ?? []
    return { ...assembleStoreManifests(declared), hasDeclarations: declared.length > 0 }
  }
  private effectiveConfig(config: RuntimeConfig | null, entries = this.resolveEntries(config)): RuntimeConfig {
    return { ...(config ?? { server: '', device_id: '' }), bossCliEntry: undefined, providers: Object.fromEntries(Object.entries(entries).map(([key, entry]) => [key, { ...config?.providers?.[key], entry }])) }
  }
}

let compositionOpen = false
export async function openRuntimeHost(options: { home?: string; nodeExecutable?: string; supervisor?: 'cli' | 'desktop' | 'runtime_app'; platform?: RuntimePlatform; trust?: PublisherTrust[]; cleanGeneration?: boolean } = {}) {
  // Legacy helpers have process-wide home state; a Node child owns one composition.
  if (compositionOpen) throw new ManagementError(4, '本进程已有Runtime Host')
  compositionOpen = true
  const home = resolve(options.home ?? runtimeHomeDir())
  try {
    // Core preflight may enter read-only mode before adapter.initialize runs.
    // Its device snapshot must still read this composition's explicitly bound home.
    configureRuntimeHome(home)
    const trust = options.trust ?? loadPackagedTrust(dirname(fileURLToPath(import.meta.url)))
    return await RuntimeHost.open({ home, version: RUNTIME_VERSION, supervisor: options.supervisor ?? 'cli', readOnlyOnFailure: options.supervisor === 'runtime_app',
      adapter: new LegacyExecution(options.nodeExecutable ?? process.execPath, home, trust ? { platform: options.platform, trust } : undefined,
        options.supervisor === 'runtime_app' && options.cleanGeneration === true) })
  } catch (error) {
    compositionOpen = false; configureRuntimeHome(undefined)
    throw error
  }
}
