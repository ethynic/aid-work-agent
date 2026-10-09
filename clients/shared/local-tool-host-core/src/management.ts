import { createHmac, randomBytes, randomUUID } from 'node:crypto'
import { closeSync, existsSync, fsyncSync, mkdirSync, openSync, readFileSync, renameSync, rmSync, writeFileSync } from 'node:fs'
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
  initialize?(): Promise<void> | void
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
  canChangePlugins?(): Promise<boolean>
  mutatePlugin?(method: string, params: Record<string, string>, context: { instance_id: string; prepared_import?: PreparedPluginImport }): Promise<void>
}
interface StoredOperation { digest: string; operation: ManagementOperation; prepared_import?: PreparedPluginImport }
interface Request { request_id: string; method: string; params: Record<string, string> }

function atomicWrite(file: string, contents: string): void {
  const temporary = `${file}.${randomUUID()}.tmp`
  writeFileSync(temporary, contents, { mode: 0o600, flag: 'wx' })
  const fd = openSync(temporary, 'r+')
  try { fsyncSync(fd) } finally { closeSync(fd) }
  try { renameSync(temporary, file) } finally { rmSync(temporary, { force: true }) }
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

  private constructor(private lease: InstanceLease, private adapter: HostAdapter,
    private secret: Buffer, private version: string, private supervisor: 'cli' | 'desktop' | 'runtime_app') {
    this.operationsFile = join(lease.home, 'management-operations.json')
    this.markerFile = join(lease.home, 'host-running.json')
    this.connection = adapter.device() ? 'offline' : 'unpaired'
    adapter.observePlugins?.(() => this.changed())
    if (existsSync(this.operationsFile)) {
      const saved: unknown = JSON.parse(readFileSync(this.operationsFile, 'utf8'))
      if (!saved || typeof saved !== 'object' || Array.isArray(saved)) throw new Error('operation store invalid')
      for (const [key, value] of Object.entries(saved)) {
        const entry = value as StoredOperation
        const op = entry?.operation
        if (!key || !entry || typeof entry.digest !== 'string' || !/^[0-9a-f]{64}$/.test(entry.digest)
          || !op || typeof op.operation_id !== 'string' || !op.operation_id
          || !['start', 'stop', 'pair', 'import', 'enable', 'disable', 'uninstall'].includes(op.operation)
          || !['running', 'succeeded', 'failed', 'reconciling'].includes(op.status)
          || !Number.isInteger(op.code) || typeof op.error !== 'string'
          || (op.status === 'failed' ? op.code < 1 || !op.error : op.code !== 0 || op.error !== '')) throw new Error('operation store invalid')
        this.operations[key] = entry
        if (entry.prepared_import && (op.operation !== 'import' || !/^[0-9a-f-]{36}$/.test(entry.prepared_import.snapshot_id) || !/^[0-9a-f]{64}$/.test(entry.prepared_import.sha256))) throw new Error('operation snapshot invalid')
        if (entry.operation.status === 'running') entry.operation.status = 'reconciling'
      }
      this.persist()
    }
    if (existsSync(this.markerFile)) this.state = 'reconciling'
  }

  static async open(options: { home: string; version: string; supervisor: 'cli' | 'desktop' | 'runtime_app'; adapter: HostAdapter }): Promise<RuntimeHost> {
    const lease = await acquireHostLease(options.home)
    if (!lease) throw new ManagementError(4, '已有Runtime实例使用此配置目录，请先停止原实例')
    try {
      await options.adapter.initialize?.()
      const file = join(lease.home, 'management-secret.bin')
      let secret: Buffer
      if (existsSync(file)) {
        const hex = await options.adapter.unprotect(readFileSync(file, 'utf8'))
        if (!/^[0-9a-f]{64}$/.test(hex)) throw new Error('invalid secret')
        secret = Buffer.from(hex, 'hex')
        if (secret.length !== 32) throw new Error('invalid secret')
      } else {
        if (existsSync(join(lease.home, 'management-operations.json'))) throw new Error('missing secret')
        secret = randomBytes(32)
        atomicWrite(file, await options.adapter.protect(secret.toString('hex')))
      }
      return new RuntimeHost(lease, options.adapter, secret, options.version, options.supervisor)
    } catch (error) {
      await lease.release()
      if (error instanceof ManagementError) throw error
      throw new ManagementError(11, '本机管理记录或系统保护不可用，需核对后恢复')
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
    const device = this.adapter.device()
    return { instance_id: this.instanceId, revision: this.revision, supervisor: this.supervisor,
      state: this.state, connection: this.connection, ...(device ? { device } : {}),
      ...(['reconciling', 'blocked'].includes(this.state) ? { blocked_reason: '旧执行停止事实尚需核对' } : {}) }
  }
  private persist(): void { atomicWrite(this.operationsFile, JSON.stringify(this.operations)) }

  async request(input: unknown): Promise<Record<string, unknown>> {
    const raw = input as Record<string, unknown> | null
    const base = { request_id: typeof raw?.request_id === 'string' && raw.request_id ? raw.request_id : 'invalid',
      method: typeof raw?.method === 'string' && raw.method ? raw.method : 'describe' }
    try {
      const request = parseRequest(input)
      if (this.closed || this.closing) throw new ManagementError(4, 'Runtime管理连接已关闭')
      let result: unknown
      switch (request.method) {
        case 'describe': result = { api_major: 1, host_version: this.version, features: ['events', ...(this.adapter.mutatePlugin ? ['first_party_plugins'] : [])], instance_id: this.instanceId, revision: this.revision }; break
        case 'getState': result = this.getState(); break
        case 'plugins.list': {
          await this.adapter.refreshPlugins?.()
          const plugins = this.adapter.plugins?.() ?? []
          result = { instance_id: this.instanceId, revision: this.revision, plugins }; break
        }
        case 'operations.get': {
          const item = Object.values(this.operations).find(entry => entry.operation.operation_id === request.params.operation_id)
          if (!item) throw new ManagementError(1, '管理操作不存在')
          result = { ...item.operation }; break
        }
        default: {
          const key = request.params.request_key!
          const fields = Object.keys(request.params).filter(field => field !== 'request_key').sort().map(field => [field, request.params[field]])
          const digest = createHmac('sha256', this.secret).update(JSON.stringify([request.method, fields])).digest('hex')
          const previous = this.operations[key]
          if (previous) {
            if (previous.digest !== digest) throw new ManagementError(10, '同一请求键对应不同管理意图')
            result = { ...previous.operation }; break
          }
          if (request.method.startsWith('plugins.') && !this.adapter.mutatePlugin) throw new ManagementError(3, '第一方插件安装管理尚未就绪')
          const pending = this.accepting.get(key)
          if (pending) {
            if (pending.digest !== digest) throw new ManagementError(10, '同一请求键对应不同管理意图')
            result = { ...await pending.promise }; break
          }
          const promise = this.acceptMutation(request, key, digest)
          this.accepting.set(key, { digest, promise })
          try { result = { ...await promise } } finally { this.accepting.delete(key) }
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
        await this.mutate(request, preparedImport)
        operation.status = 'succeeded'
      } catch (error) {
        if (this.state === 'reconciling' || this.state === 'blocked') {
          operation.status = 'reconciling'; operation.message = '执行停止事实尚需核对'
        } else {
          operation.status = 'failed'; operation.code = error instanceof ManagementError ? error.code : 11
          operation.error = error instanceof ManagementError ? error.message : '管理操作未完成，需核对本机状态'
        }
      }
      this.persist(); this.changed()
    }).catch(() => { this.state = 'blocked'; this.changed() })
    return operation
  }

  /** Local CLI compatibility only; intentionally absent from the H1 management wire. */
  async runLocalIdentityChange(change: () => Promise<void> | void): Promise<void> {
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
    if (request.method.startsWith('plugins.')) {
      if (!['running', 'stopped'].includes(this.state)) throw new ManagementError(11, 'Runtime执行事实尚需核对')
      const restart = this.state === 'running'
      if (restart) await this.mutate({ ...request, method: 'stop' })
      if (this.adapter.canChangePlugins && !(await this.adapter.canChangePlugins())) {
        if (restart) await this.mutate({ ...request, method: 'start' })
        throw new ManagementError(11, '旧插件执行或会话任务尚未结案，请先完成原任务核对')
      }
      let failure: unknown
      try { await this.adapter.mutatePlugin!(request.method, request.params, { instance_id: this.instanceId, ...(preparedImport ? { prepared_import: preparedImport } : {}) }); this.changed() }
      catch (error) {
        if (error instanceof ManagementError && error.code === 11) { this.state = 'reconciling'; this.changed(); throw error }
        failure = error
      }
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
        atomicWrite(this.markerFile, JSON.stringify({ instance_id: this.instanceId }))
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
        if (this.state === 'blocked' || (this.state === 'reconciling' && !this.ownsExecution)) throw new ManagementError(11, '旧执行停止事实尚需核对')
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
    if (this.state !== 'stopped') {
      this.closing = false
      throw new ManagementError(11, 'Runtime尚未安全停止，保留实例保护')
    }
    this.closed = true
    this.listeners.clear()
    await this.lease.release()
    this.adapter.onDisposed?.()
  }
}
