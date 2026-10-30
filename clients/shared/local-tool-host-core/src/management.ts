import { createHash, createHmac, randomBytes, randomUUID } from 'node:crypto'
import { closeSync, existsSync, fsyncSync, lstatSync, mkdirSync, openSync, readFileSync, renameSync, rmSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { acquireHostLease, type InstanceLease } from './instanceLease.js'

export type HostStatus = 'stopped' | 'starting' | 'running' | 'draining' | 'reconciling' | 'blocked'
export type ConnectionStatus = 'unpaired' | 'connecting' | 'online' | 'offline' | 'revoked'
export interface DeviceSummary { device_id: string; name?: string }
export interface PluginSummary {
  installation_id: string; plugin_id: string; display_name: string; release_id: string
  enabled: boolean; ready: boolean; reason?: string; version?: string
}
export interface ManagementOperation {
  operation_id: string; operation: string; status: 'running' | 'succeeded' | 'failed' | 'reconciling'
  code: number; error: string; message?: string
}
export class ManagementError extends Error {
  constructor(readonly code: number, message: string) { super(message) }
}
export interface PreparedPluginImport { snapshot_id: string; sha256: string }
export interface HostAdapter {
  beforeManagementInit?(): Promise<void>
  pauseForConnectionRecovery?(): void
  initialize?(): Promise<void> | void
  executionMarker?(instanceId: string): Promise<Record<string, unknown>>
  recoverPreviousExecution?(marker: string): Promise<boolean>
  onDisposed?(): void
  device(): DeviceSummary | undefined
  protect(value: string): Promise<string>
  unprotect(value: string): Promise<string>
  start(connection: (value: ConnectionStatus) => void, executionEnded: () => void): Promise<void>
  stop(): Promise<void>
  pair(params: { server: string; device_name: string; pairing_code: string }): Promise<void>
  canReplaceIdentity(): Promise<boolean>
  plugins?(): PluginSummary[]
  refreshPlugins?(): Promise<void>
  observePlugins?(listener: () => void): void
  preparePluginImport?(params: Record<string, string>, context: { instance_id: string }): Promise<PreparedPluginImport>
  cleanupPluginImports?(retained: readonly string[]): Promise<void> | void
  canChangePlugins?(method?: string): Promise<boolean>
  mutatePlugin?(method: string, params: Record<string, string>, context: { instance_id: string; prepared_import?: PreparedPluginImport }): Promise<void>
}
interface StoredOperation { digest: string; operation: ManagementOperation; prepared_import?: PreparedPluginImport }
interface Request { request_id: string; method: string; params: Record<string, string> }

function readOperations(file: string): Record<string, StoredOperation> {
  const operations: Record<string, StoredOperation> = Object.create(null)
  if (!existsSync(file)) return operations
  let text: string
  try { text = readFileSync(file, 'utf8') } catch { throw new ManagementError(11, '管理操作记录无法读取，原文件已保留') }
  let saved: unknown
  try { saved = JSON.parse(text.replace(/^\uFEFF/, '')) } catch { throw new ManagementError(11, '管理操作记录格式无法读取，原文件已保留') }
  if (!saved || typeof saved !== 'object' || Array.isArray(saved)) throw new ManagementError(11, '管理操作记录格式无效，原文件已保留')
  for (const [key, value] of Object.entries(saved)) {
    const entry = value as StoredOperation
    const op = entry?.operation
    if (!key || !entry || typeof entry.digest !== 'string' || !/^[0-9a-f]{64}$/.test(entry.digest)
      || !op || typeof op.operation_id !== 'string' || !op.operation_id
      || !['start', 'stop', 'pair', 'import', 'enable', 'disable', 'uninstall'].includes(op.operation)
      || !['running', 'succeeded', 'failed', 'reconciling'].includes(op.status)
      || !Number.isSafeInteger(op.code) || typeof op.error !== 'string'
      || Object.keys(op).some(field => !['operation_id', 'operation', 'status', 'code', 'error', 'message'].includes(field))
      || (Object.hasOwn(op, 'message') && (typeof op.message !== 'string' || !op.message))
      || (op.status === 'failed' ? op.code < 1 || !op.error : op.code !== 0 || op.error !== '')) throw new ManagementError(11, '管理操作记录字段无效，原文件已保留')
    if (entry.prepared_import && (op.operation !== 'import' || !/^[0-9a-f-]{36}$/.test(entry.prepared_import.snapshot_id) || !/^[0-9a-f]{64}$/.test(entry.prepared_import.sha256))) throw new ManagementError(11, '旧插件安装快照记录无效，原文件已保留')
    // Projection only. Opening a management connection must never require rewriting
    // valid historical records or imply that an interrupted operation was completed.
    if (op.status === 'running') op.status = 'reconciling'
    operations[key] = entry
  }
  return operations
}

function atomicWrite(file: string, contents: string): void {
  const temporary = `${file}.${randomUUID()}.tmp`
  writeFileSync(temporary, contents, { mode: 0o600, flag: 'wx' })
  const fd = openSync(temporary, 'r+')
  try { fsyncSync(fd) } finally { closeSync(fd) }
  try { renameSync(temporary, file) } finally { rmSync(temporary, { force: true }) }
}
async function readRetiredKeys(file: string, adapter: HostAdapter): Promise<Set<string>> {
  if (!existsSync(file)) return new Set()
  try {
    const metadata = lstatSync(file)
    if (metadata.isSymbolicLink() || !metadata.isFile() || metadata.size > 4 * 1024 * 1024) throw new Error('invalid')
    const retired = JSON.parse(await adapter.unprotect(readFileSync(file, 'utf8')))
    if (!retired || typeof retired !== 'object' || Object.keys(retired).sort().join(',') !== 'keys,version' || retired.version !== 1 || !Array.isArray(retired.keys) || retired.keys.length > 100000 || retired.keys.some((key: unknown) => typeof key !== 'string' || !/^[0-9a-f]{64}$/.test(key))) throw new Error('invalid')
    return new Set(retired.keys)
  } catch { throw new ManagementError(11, '已归档操作保护记录无法读取，请使用连接恢复处理') }
}
function parseRequest(input: unknown): Request {
  if (!input || typeof input !== 'object' || Array.isArray(input)) throw new ManagementError(1, '管理请求格式无效')
  const request = input as Record<string, unknown>
  if (Object.keys(request).some(key => !['request_id', 'method', 'params'].includes(key))
    || typeof request.request_id !== 'string' || !request.request_id
    || typeof request.method !== 'string' || !request.params || typeof request.params !== 'object' || Array.isArray(request.params)) {
    throw new ManagementError(1, '管理请求格式无效')
  }
  const fields: Record<string, string[]> = {
    describe: [], getState: [], 'plugins.list': [], start: ['request_key'], stop: ['request_key'],
    pair: ['request_key', 'server', 'device_name', 'pairing_code'],
    'plugins.import': ['request_key', 'selection_ref'],
    'plugins.enable': ['request_key', 'installation_id'], 'plugins.disable': ['request_key', 'installation_id'],
    'plugins.uninstall': ['request_key', 'installation_id'], 'operations.get': ['operation_id'],
  }
  const required = Object.hasOwn(fields, request.method) ? fields[request.method] : undefined
  const params = request.params as Record<string, unknown>
  if (!required || Object.keys(params).length !== required.length
    || required.some(key => typeof params[key] !== 'string' || !params[key])) throw new ManagementError(1, '管理方法或参数无效')
  return request as unknown as Request
}

/** Candidate 0.3 producer. The caller must already be a trusted in-process/IPC adapter. */
export class RuntimeHost {
  readonly instanceId = randomUUID()
  private revision = 0
  private state: HostStatus = 'stopped'
  private connection: ConnectionStatus
  private operations: Record<string, StoredOperation> = Object.create(null)
  private accepting = new Map<string, { digest: string; promise: Promise<ManagementOperation> }>()
  private listeners = new Set<(event: Record<string, unknown>) => void>()
  private queue: Promise<void> = Promise.resolve()
  private readonly operationsFile: string
  private readonly markerFile: string
  private closed = false
  private closing = false
  private ownsExecution = false
  private recovered = false
  private operationsReadable = true
  private recoveryDevice?: DeviceSummary
  private retiredKeys = new Set<string>()
  private recoveryRegistered = false

  private constructor(private lease: InstanceLease, private adapter: HostAdapter,
    private secret: Buffer, private version: string, private supervisor: 'cli' | 'desktop' | 'runtime_app', private startupFailure?: ManagementError) {
    this.operationsFile = join(lease.home, 'management-operations.json')
    this.markerFile = join(lease.home, 'host-running.json')
    if (startupFailure) {
      try {
        const device = adapter.device()
        if (device && typeof device.device_id === 'string' && device.device_id) this.recoveryDevice = { device_id: device.device_id, ...(typeof device.name === 'string' && device.name ? { name: device.name } : {}) }
      } catch { /* Failed initialization cannot make the read-only view fail again. */ }
      this.connection = this.recoveryDevice ? 'offline' : 'unpaired'
      this.state = 'blocked'
      try { this.operations = readOperations(this.operationsFile) } catch { this.operationsReadable = false }
      return
    }
    this.connection = adapter.device() ? 'offline' : 'unpaired'
    adapter.observePlugins?.(() => this.changed())
    this.operations = readOperations(this.operationsFile)
    if (existsSync(this.markerFile)) this.state = 'reconciling'
  }

  static async open(options: { home: string; version: string; supervisor: 'cli' | 'desktop' | 'runtime_app'; adapter: HostAdapter; readOnlyOnFailure?: boolean }): Promise<RuntimeHost> {
    const lease = await acquireHostLease(options.home)
    if (!lease) throw new ManagementError(4, '已有Runtime实例使用此配置目录，请先停止原实例')
    const recoveryAllowed = options.readOnlyOnFailure === true && options.supervisor === 'runtime_app'
    let failureMessage = '本机执行环境初始化失败，设备和历史记录已保留'
    try {
      await options.adapter.beforeManagementInit?.()
      const checkedRetiredKeys = await readRetiredKeys(join(lease.home, 'management-retired-request-keys.bin'), options.adapter)
      let checkedSecret: Buffer | undefined
      const file = join(lease.home, 'management-secret.bin')
      if (recoveryAllowed) {
        // Diagnose management-only failures before pairing recovery or plugin
        // initialization can write anything. The original files stay authoritative.
        readOperations(join(lease.home, 'management-operations.json'))
        if (existsSync(file)) {
          failureMessage = '管理密钥无法读取或解密，设备和历史记录已保留'
          const hex = await options.adapter.unprotect(readFileSync(file, 'utf8'))
          if (!/^[0-9a-f]{64}$/.test(hex)) throw new ManagementError(11, '管理密钥格式无效，原密钥和历史记录已保留')
          checkedSecret = Buffer.from(hex, 'hex')
        } else if (existsSync(join(lease.home, 'management-operations.json'))) {
          throw new ManagementError(11, '管理密钥缺失，旧管理记录已保留，未重置设备身份')
        }
      }
      failureMessage = '本机执行环境初始化失败，设备和历史记录已保留'
      await options.adapter.initialize?.()
      let secret: Buffer
      if (checkedSecret) secret = checkedSecret
      else if (existsSync(file)) {
        failureMessage = '管理密钥无法读取或解密，设备和历史记录已保留'
        const hex = await options.adapter.unprotect(readFileSync(file, 'utf8'))
        if (!/^[0-9a-f]{64}$/.test(hex)) throw new ManagementError(11, '管理密钥格式无效，原密钥和历史记录已保留')
        secret = Buffer.from(hex, 'hex')
        if (secret.length !== 32) throw new Error('invalid secret')
      } else {
        if (existsSync(join(lease.home, 'management-operations.json'))) throw new ManagementError(11, '管理密钥缺失，旧管理记录已保留，未重置设备身份')
        failureMessage = 'Windows凭据保护暂不可用，设备和历史记录已保留'
        secret = randomBytes(32)
        atomicWrite(file, await options.adapter.protect(secret.toString('hex')))
      }
      failureMessage = '本机设备状态无法读取，设备和历史记录已保留'
      const host = new RuntimeHost(lease, options.adapter, secret, options.version, options.supervisor)
      host.retiredKeys = checkedRetiredKeys
      if (recoveryAllowed && !options.adapter.device() && Object.values(host.operations).some(entry => ['pair', 'start'].includes(entry.operation.operation) && ['succeeded', 'reconciling'].includes(entry.operation.status))) {
        host.startupFailure = new ManagementError(11, '设备配置无法读取，但旧配对或启动记录仍在；原身份和任务记录已保留。当前为只读管理，可正常退出或卸载')
        host.state = 'blocked'
      }
      failureMessage = '旧执行的停止证明无法读取，原设备和任务记录已保留'
      if (!host.startupFailure && existsSync(host.markerFile) && await options.adapter.recoverPreviousExecution?.(readFileSync(host.markerFile, 'utf8'))) {
        renameSync(host.markerFile, join(lease.home, `host-interrupted-${randomUUID()}.json`))
        host.state = 'stopped'; host.recovered = true
      }
      await host.cleanupPluginImports()
      return host
    } catch (error) {
      const failure = error instanceof ManagementError ? error : new ManagementError(11, failureMessage)
      if (recoveryAllowed && failure.code === 11) {
        return new RuntimeHost(lease, options.adapter, Buffer.alloc(0), options.version, options.supervisor,
          new ManagementError(11, `${failure.message}。当前为只读管理，可正常退出或卸载`))
      }
      await lease.release()
      throw failure
    }
  }

  observe(listener: (event: Record<string, unknown>) => void): () => void {
    this.listeners.add(listener)
    try { listener({ event: 'state_changed', instance_id: this.instanceId, revision: this.revision }) } catch { /* Observers do not control execution. */ }
    return () => this.listeners.delete(listener)
  }
  private changed(): void {
    this.revision++
    for (const listener of this.listeners) {
      try { listener({ event: 'state_changed', instance_id: this.instanceId, revision: this.revision }) } catch { /* Observers do not control execution. */ }
    }
  }
  getState(): Record<string, unknown> {
    const device = this.startupFailure ? this.recoveryDevice : this.adapter.device()
    return { instance_id: this.instanceId, revision: this.revision, supervisor: this.supervisor,
      state: this.state, connection: this.connection, ...(device ? { device } : {}),
      ...(['reconciling', 'blocked'].includes(this.state) ? { blocked_reason: this.startupFailure?.message ?? '旧执行尚未确认停止，请停止 Runtime 后重试；原配对和任务结果保留，不会重复执行。' } : {}) }
  }
  /** Local composition metadata; deliberately absent from strict H1 state schema. */
  get recoveredAfterInterruption(): boolean { return this.recovered }
  private persist(): void { atomicWrite(this.operationsFile, JSON.stringify(this.operations)) }

  private async cleanupPluginImports(): Promise<void> {
    if (this.startupFailure || this.closed || this.closing || this.accepting.size) return
    const retained = Object.values(this.operations).filter(entry => ['running', 'reconciling'].includes(entry.operation.status))
      .flatMap(entry => entry.prepared_import ? [entry.prepared_import.snapshot_id] : [])
    try { await this.adapter.cleanupPluginImports?.(retained) }
    catch { console.warn('插件临时文件未能清理，请检查目录权限；安装和原管理结果仍保留') }
  }

  async request(input: unknown): Promise<Record<string, unknown>> {
    const raw = input as Record<string, unknown> | null
    const base = { request_id: typeof raw?.request_id === 'string' && raw.request_id ? raw.request_id : 'invalid',
      method: typeof raw?.method === 'string' && raw.method ? raw.method : 'describe' }
    try {
      const request = parseRequest(input)
      if (this.closed || this.closing) throw new ManagementError(4, 'Runtime管理连接已关闭')
      let result: unknown
      switch (request.method) {
        case 'describe': result = { api_major: 1, host_version: this.version, features: ['events', ...(!this.startupFailure && this.adapter.mutatePlugin ? ['first_party_plugins'] : [])], instance_id: this.instanceId, revision: this.revision }; break
        case 'getState': result = this.getState(); break
        case 'plugins.list': {
          if (this.startupFailure) throw this.startupFailure
          const plugins = this.adapter.plugins?.() ?? []
          result = { instance_id: this.instanceId, revision: this.revision, plugins }; break
        }
        case 'operations.get': {
          if (!this.operationsReadable) throw this.startupFailure ?? new ManagementError(11, '管理操作记录无法读取，原文件已保留')
          const item = Object.values(this.operations).find(entry => entry.operation.operation_id === request.params.operation_id)
          if (!item) throw new ManagementError(1, '管理操作不存在')
          result = { ...item.operation }; break
        }
        default: {
          if (this.startupFailure) throw this.startupFailure
          const key = request.params.request_key!
          if (this.retiredKeys.has(createHash('sha256').update(key).digest('hex'))) throw new ManagementError(10, '该请求已随旧管理资料归档，请明确发起新的操作')
          const fields = Object.keys(request.params).filter(field => field !== 'request_key').sort().map(field => [field, request.params[field]])
          const digest = createHmac('sha256', this.secret).update(JSON.stringify([request.method, fields])).digest('hex')
          const previous = this.operations[key]
          if (previous) {
            if (previous.digest !== digest) throw new ManagementError(10, '同一请求键对应不同管理意图')
            result = { ...previous.operation }; break
          }
          if (this.recoveryRegistered && request.method !== 'stop') throw new ManagementError(11, '重启恢复已登记，已停止接受新执行与身份变更；请正常重启Windows后继续恢复')
          if (request.method.startsWith('plugins.') && !this.adapter.mutatePlugin) throw new ManagementError(3, '第一方插件安装管理尚未就绪')
          const pending = this.accepting.get(key)
          if (pending) {
            if (pending.digest !== digest) throw new ManagementError(10, '同一请求键对应不同管理意图')
            result = { ...await pending.promise }; break
          }
          const promise = this.acceptMutation(request, key, digest)
          this.accepting.set(key, { digest, promise })
          try { result = { ...await promise } } finally {
            this.accepting.delete(key)
            if (request.method === 'plugins.import') await this.cleanupPluginImports()
          }
        }
      }
      return { ...base, code: 0, error: '', result }
    } catch (error) {
      return { ...base, code: error instanceof ManagementError ? error.code : 11,
        error: error instanceof ManagementError ? error.message : '管理记录写入失败，需核对', result: null }
    }
  }

  private async acceptMutation(request: Request, key: string, digest: string): Promise<ManagementOperation> {
    let preparedImport: PreparedPluginImport | undefined
    if (request.method === 'plugins.import') {
      if (!this.adapter.preparePluginImport) throw new ManagementError(3, '本机文件选择通道尚未就绪')
      preparedImport = await this.adapter.preparePluginImport(request.params, { instance_id: this.instanceId })
      if (!preparedImport || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(preparedImport.snapshot_id)
        || !/^[0-9a-f]{64}$/.test(preparedImport.sha256) || Object.keys(preparedImport).sort().join(',') !== 'sha256,snapshot_id') throw new ManagementError(11, 'Host离线包快照记录无效')
    }
    if (this.closed || this.closing) throw new ManagementError(4, 'Runtime管理连接已关闭')
    const operation: ManagementOperation = { operation_id: randomUUID(), operation: request.method.replace(/^plugins\./, ''), status: 'running', code: 0, error: '' }
    this.operations[key] = { digest, operation, ...(preparedImport ? { prepared_import: preparedImport } : {}) }
    try { this.persist() } catch (error) { delete this.operations[key]; throw error }
    this.queue = this.queue.then(async () => {
      try {
        if (this.closed) throw new ManagementError(4, 'Runtime管理连接已关闭')
        if (this.recoveryRegistered && request.method !== 'stop') throw new ManagementError(11, '重启恢复已登记，此操作未执行；请正常重启Windows后继续恢复')
        await this.mutate(request, preparedImport)
        operation.status = 'succeeded'
      } catch (error) {
        if (this.state === 'reconciling' || this.state === 'blocked') {
          operation.status = 'reconciling'
          // H1 reconciling operations retain code0/error-empty; the original
          // diagnosis belongs in message so existing consumers and reopen agree.
          operation.message = error instanceof ManagementError
            ? `${error.message}（代码 ${error.code}）`
            : '管理操作未完成，需核对本机状态（代码 11）'
        } else {
          operation.status = 'failed'; operation.code = error instanceof ManagementError ? error.code : 11
          operation.error = error instanceof ManagementError ? error.message : '管理操作未完成，需核对本机状态'
        }
      }
      this.persist(); this.changed()
      if (preparedImport) await this.cleanupPluginImports()
    }).catch(() => { this.state = 'blocked'; this.changed() })
    return operation
  }

  /** Local CLI compatibility only; intentionally absent from the H1 management wire. */
  async runLocalIdentityChange(change: () => Promise<void> | void): Promise<void> {
    if (this.startupFailure) throw this.startupFailure
    if (this.closed || this.closing) throw new ManagementError(4, 'Runtime管理连接已关闭')
    const work = this.queue.then(async () => {
      if (this.closed || this.state !== 'stopped' || !(await this.adapter.canReplaceIdentity())) {
        throw new ManagementError(11, '请先完成旧设备执行与回执核对')
      }
      await change()
      this.connection = this.adapter.device() ? 'offline' : 'unpaired'
      this.changed()
    })
    this.queue = work.catch(() => {})
    return work
  }

  private async mutate(request: Request, preparedImport?: PreparedPluginImport): Promise<void> {
    if (this.recoveryRegistered && request.method !== 'stop') throw new ManagementError(11, '重启恢复已登记，已停止接受新执行；请正常重启Windows后继续恢复')
    if (request.method.startsWith('plugins.')) {
      if (this.state === 'reconciling' && !this.ownsExecution) await this.mutate({ ...request, method: 'stop' })
      if (!['running', 'stopped'].includes(this.state)) throw new ManagementError(11, 'Runtime执行事实尚需核对')
      const restart = this.state === 'running'
      if (restart) await this.mutate({ ...request, method: 'stop' })
      if (this.adapter.canChangePlugins && !(await this.adapter.canChangePlugins(request.method))) {
        if (restart) await this.mutate({ ...request, method: 'start' })
        throw new ManagementError(11, '插件仍有活动执行，请停止相关执行后重试；原安装已保留')
      }
      let failure: unknown
      try { await this.adapter.mutatePlugin!(request.method, request.params, { instance_id: this.instanceId, ...(preparedImport ? { prepared_import: preparedImport } : {}) }); this.changed() }
      catch (error) { failure = error }
      // Safe stop closed every execution lane before active release changes. Retained old
      // release files/outbox remain available; restart admits only the new truthful inventory.
      if (restart) await this.mutate({ ...request, method: 'start' })
      if (failure) throw failure
      return
    }
    switch (request.method) {
      case 'start':
        if (this.state === 'running') return
        if (this.state !== 'stopped') throw new ManagementError(11, 'Runtime尚未安全停止')
        if (!this.adapter.device()) throw new ManagementError(5, '请先配对设备')
        atomicWrite(this.markerFile, JSON.stringify({ instance_id: this.instanceId, ...await this.adapter.executionMarker?.(this.instanceId) }))
        this.state = 'starting'; this.connection = 'connecting'; this.changed()
        this.ownsExecution = true
        try {
          await this.adapter.start(value => { this.connection = value; this.changed() }, () => {
            if (this.state === 'running' || this.state === 'starting') { this.state = 'reconciling'; this.changed() }
          })
          if (this.getState().state === 'reconciling') throw new ManagementError(11, '执行循环已结束，需核对')
          if (this.state === 'starting') { this.state = 'running'; this.changed() }
        } catch (error) {
          // Adapter validation errors are emitted before execution admission. Still reap any
          // initialized resources before declaring stopped; other failures retain the marker.
          if (error instanceof ManagementError && [1, 4, 5, 9].includes(error.code)) {
            try {
              await this.adapter.stop()
              rmSync(this.markerFile, { force: true })
              this.ownsExecution = false
              this.state = 'stopped'; this.connection = this.adapter.device() ? 'offline' : 'unpaired'; this.changed()
              throw error
            } catch (cleanup) {
              if (cleanup === error) throw error
            }
          }
          this.state = 'reconciling'; this.changed(); throw new ManagementError(11, '启动未完成，需核对')
        }
        break
      case 'stop':
        if (this.state === 'stopped') return
        if (this.state === 'blocked') throw new ManagementError(11, '执行环境初始化失败，请检查连接或重新安装；原记录已保留')
        if (this.state === 'reconciling' && !this.ownsExecution) {
          if (!existsSync(this.markerFile) || !await this.adapter.recoverPreviousExecution?.(readFileSync(this.markerFile, 'utf8'))) {
            throw new ManagementError(11, '旧执行尚未确认停止，请停止原Runtime后重试；不会重发旧任务')
          }
          renameSync(this.markerFile, join(this.lease.home, `host-interrupted-${randomUUID()}.json`))
          this.state = 'stopped'; this.recovered = true; this.changed()
          return
        }
        this.state = 'draining'; this.changed()
        try { await this.adapter.stop() } catch { this.state = 'reconciling'; this.changed(); throw new ManagementError(11, '执行停止事实尚需核对') }
        rmSync(this.markerFile, { force: true })
        this.ownsExecution = false
        this.state = 'stopped'; this.changed()
        break
      case 'pair':
        if (this.state !== 'stopped' || !(await this.adapter.canReplaceIdentity())) throw new ManagementError(11, '请先完成旧设备执行与回执核对')
        await this.adapter.pair(request.params as unknown as { server: string; device_name: string; pairing_code: string })
        this.connection = 'offline'; this.changed()
        break
    }
  }

  async dispose(): Promise<void> {
    if (this.closed) return
    this.closing = true
    await Promise.allSettled([...this.accepting.values()].map(item => item.promise))
    await this.queue
    // Closing this management process does not settle an older execution. A Host
    // that never started work may release its lease while retaining old evidence.
    if (this.ownsExecution || !['stopped', 'reconciling', 'blocked'].includes(this.state)) {
      this.closing = false
      throw new ManagementError(11, 'Runtime尚未安全停止，保留实例保护')
    }
    this.closed = true
    this.listeners.clear()
    await this.lease.release()
    this.adapter.onDisposed?.()
  }

  /** Direct Main/Host coordination only; deliberately absent from H1 methods. */
  async inspectConnectionRecovery<T>(inspect: (ownsExecution: boolean) => Promise<T>, pause = false, registrationOnly = false): Promise<T> {
    if (this.supervisor !== 'runtime_app' || this.closed) throw new ManagementError(4, '连接恢复只支持当前Runtime客户端')
    // Registration freezes admission and records evidence only. It never needs a
    // network-dependent drain, and cannot archive files or release ownership.
    if (registrationOnly) { this.recoveryRegistered = true; this.adapter.pauseForConnectionRecovery?.(); return inspect(this.ownsExecution) }
    if (this.closing) throw new ManagementError(4, '本机管理操作仍在收尾，请登记重启恢复')
    this.closing = true
    try {
      if (pause) this.adapter.pauseForConnectionRecovery?.()
      let timer: ReturnType<typeof setTimeout> | undefined
      try {
        await Promise.race([Promise.allSettled([...this.accepting.values()].map(item => item.promise)).then(() => this.queue),
          new Promise<never>((_, reject) => { timer = setTimeout(() => reject(new ManagementError(11, '停止执行尚未确认，请登记重启恢复后正常重启Windows')), 10_000) })])
      } finally { clearTimeout(timer) }
      return await inspect(this.ownsExecution)
    } finally { if (!this.closed) this.closing = false }
  }
}
