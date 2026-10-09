import { closeSync, existsSync, fstatSync, fsyncSync, lstatSync, mkdirSync, openSync, readFileSync, readSync, writeFileSync } from 'node:fs'
import { randomUUID, randomBytes } from 'node:crypto'
import { ApiClient } from './apiClient.js'
import {
  defaultSkillsDir,
  deviceCapabilities,
  loadConfig,
  machineFingerprint,
  PLATFORM,
  resolveProviderEntries,
  RUNTIME_VERSION,
  runtimeHomeDir,
} from './config.js'
import { SkillRunnerHandler } from './skillRunner.js'
import { loadDeviceToken } from './credentials.js'
import { checkDesktopInteractive } from './desktopCheck.js'
import { deriveResourceKey } from './desktopLock.js'
import { PollLoop } from './pollLoop.js'
import { ProviderSet } from './providerManager.js'
import { runInvocation } from './invocationRunner.js'
import { ResultOutbox, resultOutboxDir } from './resultOutbox.js'
import { SessionTaskEngine } from './sessionTasks/engine.js'
import { NameSessionBridge } from './sessionTasks/nameBridge.js'
import { acquireSessionTasksSingleInstance, type SingleInstanceGuard } from './sessionTasks/singleInstance.js'
import { enforceRetention } from './sessionTasks/retention.js'
import { dpapiProtect, dpapiUnprotect } from './dpapi.js'
import { withDesktopLock, desktopLockName } from './desktopLock.js'
import { logInfo } from './log.js'
import { readdirSync } from 'node:fs'


import { ManagementError, RuntimeHost, PACKAGE_LIMITS, sha256, type ConnectionStatus, type HostAdapter, type PublisherTrust, type RuntimePlatform } from '@aid/local-tool-host-core'
import { PluginStore } from '@aid/local-tool-host-core/plugins/store'
import { configureRuntimeHome, type RuntimeConfig } from './config.js'
import { join } from 'node:path'
import { dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { atomicWrite, recoverPairing } from './pairingStore.js'
import { assertNoLegacyRuntimeProcess } from './legacyProcessCheck.js'
import { probePlugin } from './pluginProbe.js'
import { loadPackagedTrust } from './productTrust.js'
import { hasJournalCompletionProof } from '@aid/local-tool-host-core/legacy/completionProof'
import { hasSessionCompletion } from './sessionTasks/completionProof.js'

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
  private engine: SessionTaskEngine | null = null
  private sessionGuard: SingleInstanceGuard | null = null
  private runPromise: Promise<void> | null = null
  private runFailure: unknown = null
  private stopping = false
  constructor(private nodeExecutable: string, private home: string = runtimeHomeDir(), private pluginOptions?: { trust: PublisherTrust[]; platform?: RuntimePlatform }) {
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
  plugins() { return this.pluginStore?.list() ?? [] }
  async refreshPlugins() { await this.pluginStore?.refresh() }
  async canChangePlugins(): Promise<boolean> {
    for (const name of ['journal', 'session-tasks']) {
      const directory = join(this.home, name); const files = existsSync(directory) ? readdirSync(directory) : []
      if (name === 'journal' && !files.every(file => hasJournalCompletionProof(this.home, file))) return false
      if (name === 'session-tasks' && !(await Promise.all(files.map(file => hasSessionCompletion(join(directory, file), file, { protect: dpapiProtect, unprotect: dpapiUnprotect })))).every(Boolean)) return false
    }
    return true
  }
  async initialize(): Promise<void> {
    configureRuntimeHome(this.home)
    await assertNoLegacyRuntimeProcess()
    await recoverPairing()
    if (this.pluginOptions) {
      this.pluginStore = new PluginStore({ home: this.home, trust: this.pluginOptions.trust,
        platform: { platform: process.platform, arch: process.arch, node_version: process.versions.node, node_abi: process.versions.modules, runtime_version: RUNTIME_VERSION },
        probe: (root, manifest) => probePlugin(this.nodeExecutable, root, manifest, providerEnvironment()),
        onChanged: () => this.pluginsChanged?.() })
      await this.pluginStore.initialize()
    } else if (existsSync(join(this.home, 'plugins/inventory.json'))) {
      throw new ManagementError(9, '此配置目录含受管插件，需使用具备发布信任配置的Runtime产品')
    }
  }
  onDisposed(): void { compositionOpen = false; configureRuntimeHome(undefined) }
  protect = dpapiProtect
  unprotect = dpapiUnprotect
  device() {
    const config = loadConfig()
    return config ? { device_id: config.device_id, ...(config.name ? { name: config.name } : {}) } : undefined
  }
  async canReplaceIdentity(): Promise<boolean> {
    const home = runtimeHomeDir()
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
    let url: URL
    try { url = new URL(params.server) } catch { throw new ManagementError(1, '服务地址无效') }
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) throw new ManagementError(1, '服务地址无效')
    const config = loadConfig()
    const result = await new ApiClient(params.server).pair({ code: params.pairing_code.trim(), name: params.device_name,
      platform: PLATFORM, runtime_version: RUNTIME_VERSION, capabilities: deviceCapabilities(this.effectiveConfig(config)), machine_fingerprint: machineFingerprint() })
    const next: RuntimeConfig = { ...config, server: params.server, device_id: result.device_id, name: params.device_name }
    const pending = join(runtimeHomeDir(), 'pairing.pending.json')
    atomicWrite(pending, JSON.stringify({ config: next, encrypted_token: await dpapiProtect(result.device_token) }))
    await recoverPairing()
  }
  async start(connection: (value: ConnectionStatus) => void, executionEnded: () => void): Promise<void> {
    await recoverPairing()
    const config = loadConfig()
    if (!config) throw new ManagementError(5, '请先配对设备')
    const server = config.server
    let token: string
    try { token = await loadDeviceToken() } catch { throw new ManagementError(9, '设备凭证不可用，请核对本机身份') }
    await this.pluginStore?.refresh()
    const entries = Object.fromEntries(Object.entries(this.resolveEntries(config)).filter(([, entry]) => existsSync(entry)))
    const api = new ApiClient(server, token)
    const bridgeKey = randomBytes(32).toString('hex')
    const wecomEnv: Record<string, string> = {}
    for (const key of ['AID_WECOM_SERVER_URL', 'AID_WECOM_SERVER_TOKEN']) {
      if (process.env[key] !== undefined) wecomEnv[key] = process.env[key]!
    }
    // Each Provider gets its own approved credentials, never the Host/device environment.
    const providers = new ProviderSet(entries, { nodeExecutable: this.nodeExecutable, baseEnv: providerEnvironment(),
      providerEnv: { weixin: { AIDWORK_WEIXIN_BRIDGE_KEY: bridgeKey }, wecom: wecomEnv } })
    this.providers = providers
    this.runFailure = null
    this.stopping = false
    const nameBridge = new NameSessionBridge(providers, api, bridgeKey)
    // skill-runner（M2）：config.skills.python 配置且存在才构造 handler（能力真实性——
    // 未配置/解释器缺失不上报能力、不注入 runner，行级 claim 天然不派发该设备）
    const skillsCfg = config.skills
    const skillRunner = skillsCfg?.python && existsSync(skillsCfg.python)
      ? new SkillRunnerHandler({ skillsDir: skillsCfg.dir ?? defaultSkillsDir(), pythonPath: skillsCfg.python })
      : undefined
    if (skillRunner) {
      logInfo(`[runtime] skill-runner 已启用：skills 目录=${skillsCfg!.dir ?? defaultSkillsDir()} 解释器=${skillsCfg!.python}`)
    }
    // #6 能力真实性：v2 会话 manifest 变体仅经 config.providers.weixin.v2Send 显式
    // 协商（隔离测试 / 真实 v2 Provider 交付后）；未协商保持真实 v1 受信形态
    const { setManifestOverride, weixinV2Manifest } = await import('./providers.js')
    setManifestOverride('weixin', null)
    if (config?.providers?.['weixin']?.v2Send === true) {
      setManifestOverride('weixin', weixinV2Manifest())
      logInfo('[runtime] weixin v2 会话能力已显式协商（v2Send=true）：manifest 升级 v2 变体')
    }
    logInfo(`[runtime] providers: ${Object.keys(entries).join(', ')}`)
    // v2 写路径数据目录（journal/ + result-outbox/，跟随 runtime home；启动重投共用同一 outbox 实例）
    const dataDir = runtimeHomeDir()
    const loop = new PollLoop({
      api,
      runnerDeps: {
        providers,
        ...(skillRunner ? { skillRunner } : {}),
        desktopCheck: async () => (await checkDesktopInteractive()).interactive,
        desktopResourceKey: deriveResourceKey(),
        runtimeDataDir: dataDir,
        resultOutbox: new ResultOutbox(resultOutboxDir(dataDir)),
      },
      // 2026-09-10 排障整改：invocation 生命周期行（领取/开始/终态回传/重试）此前只 console.log 到
      // stdout，服务化启动无重定向时文件日志全无回传链路痕迹（2026-09-10 客户现场诊断包实证）。
      // logInfo = stderr + %APPDATA% 文件双写，前缀格式不变。
      onEvent: (msg) => logInfo(msg),
      capabilities: () => deviceCapabilities(this.effectiveConfig(config)),
      canClaim: () => Object.values(this.resolveEntries(config)).some(entry => existsSync(entry)) || Boolean(skillRunner),
      onConnection: connection,
    })

    // ----- 端侧会话任务引擎（C2）：与 pollLoop 共存，共享 Provider 与桌面锁 -----
    let sessionEngine: SessionTaskEngine | null = null
    let sessionGuard: SingleInstanceGuard | null = null
    if (config.sessionTasks === true) {
      sessionGuard = await acquireSessionTasksSingleInstance(dataDir)
      if (sessionGuard === null) {
        throw new ManagementError(4, '已有旧会话任务实例，不能并行启动Runtime')
      } else {
        this.sessionGuard = sessionGuard
        const retention = enforceRetention(dataDir, [], {}) // 启动时评估磁盘水位（终态清理由引擎周期执行）
        if (retention.stopNew) logInfo('[session-tasks] 本地会话日志已达上限（256MiB），停止新观察持久化')
        else if (retention.warn80) logInfo('[session-tasks] 本地会话日志超 80% 水位')
        sessionEngine = new SessionTaskEngine({
          api,
          runtimeHome: dataDir,
          crypto: { protect: dpapiProtect, unprotect: dpapiUnprotect },
          runtimeInstanceId: `rt-${RUNTIME_VERSION}-${randomUUID().slice(0, 8)}`,
          withLock: <T,>(fn: () => Promise<T>) => withDesktopLock(desktopLockName(deriveResourceKey()), fn),
          observer: nameBridge.observe,
          emit: (msg) => logInfo(`[session-tasks] ${msg}`),
          // C3：会话任务发送复用既有 v2 单动作执行器（许可/journal/outbox 全在原链内）；
          // sessionPrecheck 为引擎构造的锁内会话复核（§7 顺序 4），由 runner 在桌面锁内、
          // write-authorize 之前执行
          runInvocation: (inv, sessionPrecheck) =>
            runInvocation(inv, {
              api,
              providers,
              ...(skillRunner ? { skillRunner } : {}),
              desktopCheck: async () => (await checkDesktopInteractive()).interactive,
              desktopResourceKey: deriveResourceKey(),
              runtimeDataDir: dataDir,
              resultOutbox: new ResultOutbox(resultOutboxDir(dataDir)),
              sessionPrecheck,
              prepareProviderCall: nameBridge.prepare,
            }),
        })
        logInfo('[session-tasks] 会话任务引擎已启动（共享桌面锁；observer 经 weixin Provider）')
      }
    }
    this.loop = loop
    this.providers = providers
    this.engine = sessionEngine
    this.sessionGuard = sessionGuard
    const loops = [loop.run()]
    if (sessionEngine) loops.push(sessionEngine.run())
    this.runPromise = Promise.all(loops).then(() => {
      if (!this.stopping) executionEnded()
    }, error => {
      this.runFailure = error; loop.shutdown(); sessionEngine?.shutdown()
      if (!this.stopping) executionEnded()
    })
  }
  async stop(): Promise<void> {
    this.stopping = true
    this.loop?.stopClaiming()
    this.engine?.shutdown()
    await this.loop?.drain()
    await this.runPromise
    await this.engine?.drain()
    // All lanes have stopped admitting work; accepted calls may still resolve their Provider first.
    this.providers?.stopAdmission()
    await this.providers?.waitForIdle()
    await this.providers?.shutdownAll()
    if (this.runFailure) throw new ManagementError(11, '执行循环异常，停止事实需要核对')
    await this.sessionGuard?.release()
    this.loop = null; this.providers = null; this.engine = null; this.sessionGuard = null; this.runPromise = null
  }
  private resolveEntries(config: RuntimeConfig | null): Record<string, string> { const entries = resolveProviderEntries(config); return this.pluginStore?.resolveEntries(entries) ?? entries }
  private effectiveConfig(config: RuntimeConfig | null, entries = this.resolveEntries(config)): RuntimeConfig {
    return { ...(config ?? { server: '', device_id: '' }), bossCliEntry: undefined, providers: Object.fromEntries(Object.entries(entries).map(([key, entry]) => [key, { ...config?.providers?.[key], entry }])) }
  }
}

let compositionOpen = false
export async function openRuntimeHost(options: { home?: string; nodeExecutable?: string; supervisor?: 'cli' | 'desktop' | 'runtime_app'; platform?: RuntimePlatform; trust?: PublisherTrust[] } = {}) {
  // Legacy helpers have process-wide home state; a Node child owns one composition.
  if (compositionOpen) throw new ManagementError(4, '本进程已有Runtime Host')
  compositionOpen = true
  const home = options.home ?? runtimeHomeDir()
  try {
    const trust = options.trust ?? loadPackagedTrust(dirname(fileURLToPath(import.meta.url)))
    return await RuntimeHost.open({ home, version: RUNTIME_VERSION, supervisor: options.supervisor ?? 'cli',
      adapter: new LegacyExecution(options.nodeExecutable ?? process.execPath, home, trust ? { platform: options.platform, trust } : undefined) })
  } catch (error) {
    compositionOpen = false; configureRuntimeHome(undefined)
    throw error
  }
}
