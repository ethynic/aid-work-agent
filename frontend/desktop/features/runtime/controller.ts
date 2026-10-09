import { reactive } from 'vue'
import { decodeResponse, decodeSelection, isDescription, isHostEvent, isHostState, isOperation, isPluginList, type Description, type HostState, type ManagementMethod, type ManagementOperation, type PluginList, type RuntimeManagementPort } from './contracts'

class RejectedManagementRequest extends Error {}

export class RuntimeController {
  readonly view = reactive({ connected: false, connecting: false, state: null as HostState | null, plugins: null as PluginList | null, description: null as Description | null, stateDirty: true, pluginsDirty: true, error: '', operation: null as ManagementOperation | null, busy: false, retryAvailable: false, selectionLabel: '' })
  private generation = 0
  private instance = ''
  private knownInstance = ''
  private watermark = -1
  private handshakeRevisions = new Map<string, number>()
  private handshakeOverflow = false
  private sequence = 0
  private appliedState = 0
  private appliedList = 0
  private stateFailed = false
  private listFailed = false
  private operationQuery = 0
  private appliedOperationQuery = 0
  private cancellations = new Set<() => void>()
  private unsubscribe?: () => void
  private disconnectListener?: () => void
  private refreshTimer?: ReturnType<typeof setTimeout>
  private operationTimer?: ReturnType<typeof setTimeout>
  private refreshPromise?: Promise<void>
  private intent?: { method: ManagementMethod; params: Record<string, string>; instance: string }
  private disposed = false

  // Signed offline OCR packages require full integrity scans, including on reconnect.
  constructor(private port: RuntimeManagementPort, private readonly timeoutMs = 120_000) {}
  get supportsPlugins() { return this.view.description?.features.includes('first_party_plugins') === true }
  async connect(port = this.port) {
    this.resetConnection()
    this.disposed = false
    this.port = port
    const generation = this.generation
    this.view.connecting = true
    this.view.error = ''
    this.unsubscribe = port.observe((event) => {
      if (generation !== this.generation) return
      if (!isHostEvent(event)) { this.view.error = 'Runtime 状态通知格式不兼容'; return }
      if (!this.instance) {
        // describe may be late: retain this generation's watermarks until it confirms identity.
        const previous = this.handshakeRevisions.get(event.instance_id)
        if (previous !== undefined) this.handshakeRevisions.set(event.instance_id, Math.max(previous, event.revision))
        else if (this.handshakeRevisions.size < 8) this.handshakeRevisions.set(event.instance_id, event.revision)
        else this.handshakeOverflow = true
        return
      }
      if (event.instance_id !== this.instance) { void this.connect().catch(() => {}); return }
      this.advance(event.revision)
      this.scheduleRefresh()
    })
    this.disconnectListener = port.onDisconnect?.(() => {
      if (generation !== this.generation) return
      this.resetConnection()
      this.view.error = 'Runtime 管理连接已断开，请重新连接。'
    })
    try {
      const result = await this.query('describe')
      if (!isDescription(result) || generation !== this.generation) return
      if (this.handshakeOverflow) throw new Error('Runtime 握手期间出现过多实例通知，请重新连接核对。')
      const instanceChanged = this.knownInstance !== '' && this.knownInstance !== result.instance_id
      this.knownInstance = result.instance_id
      this.instance = result.instance_id
      this.view.description = result
      this.advance(Math.max(result.revision, this.handshakeRevisions.get(result.instance_id) ?? -1))
      this.handshakeRevisions.clear()
      this.view.connected = true
      await this.refresh()
      if (instanceChanged && this.intent) {
        this.intent = undefined; this.view.retryAvailable = false; this.view.selectionLabel = ''
        this.view.error = 'Runtime 实例已变更，旧请求结果需要核对；未自动重放管理操作。'
      } else if (this.intent?.instance === this.instance) this.view.retryAvailable = true
      if (this.view.operation) { await this.refreshOperation(); this.trackOperation() }
    } catch (error) {
      if (generation === this.generation) { this.resetConnection(); this.view.error = this.message(error) }
    }
    finally { if (generation === this.generation) this.view.connecting = false }
  }
  private resetConnection(clearIntent = false) {
    ++this.generation
    this.unsubscribe?.(); this.unsubscribe = undefined
    this.disconnectListener?.(); this.disconnectListener = undefined
    for (const cancel of this.cancellations) cancel()
    clearTimeout(this.refreshTimer); clearTimeout(this.operationTimer)
    this.refreshTimer = undefined; this.operationTimer = undefined
    this.refreshPromise = undefined
    this.instance = ''; this.watermark = -1; this.sequence = 0; this.appliedState = 0; this.appliedList = 0
    this.handshakeRevisions.clear(); this.handshakeOverflow = false
    this.stateFailed = false; this.listFailed = false
    this.operationQuery = 0; this.appliedOperationQuery = 0
    if (clearIntent) { this.intent = undefined; this.knownInstance = ''; this.view.operation = null; this.view.selectionLabel = '' }
    Object.assign(this.view, { connected: false, connecting: false, state: null, plugins: null, description: null, stateDirty: true, pluginsDirty: true, busy: false, retryAvailable: false })
  }
  dispose() { this.disposed = true; this.resetConnection(true) }
  private message(error: unknown) { return error instanceof Error ? error.message : 'Runtime 管理操作失败' }
  private query(method: ManagementMethod, params: Record<string, string> = {}) {
    const request = { request_id: crypto.randomUUID(), method, params }
    const generation = this.generation
    const port = this.port
    return new Promise<unknown>((resolve, reject) => {
      let settled = false
      const finish = (error?: Error, value?: unknown) => {
        if (settled) return
        settled = true; clearTimeout(timer); this.cancellations.delete(cancel)
        if (error) reject(error); else resolve(value)
      }
      const cancel = () => finish(new Error('Runtime 管理连接已变更'))
      const timer = setTimeout(() => finish(new Error('Runtime 管理请求超时，操作结果尚未确认。')), this.timeoutMs)
      this.cancellations.add(cancel)
      Promise.resolve().then(() => port.request(request)).then((raw) => {
        if (generation !== this.generation) { cancel(); return }
        try {
          const reply = decodeResponse(raw, request)
          if (reply.code !== 0) throw new RejectedManagementRequest(`${reply.error}（代码 ${reply.code}）`)
          finish(undefined, reply.result)
        } catch (error) { finish(error instanceof Error ? error : new Error('Runtime 管理回复不可用')) }
      }, (error) => finish(new Error(this.message(error))))
    })
  }
  private advance(revision: number) {
    this.watermark = Math.max(this.watermark, revision)
    this.view.stateDirty = this.stateFailed || !this.view.state || this.view.state.revision < this.watermark
    this.view.pluginsDirty = this.listFailed || !this.view.plugins || this.view.plugins.revision < this.watermark
  }
  async refreshState() {
    const sequence = ++this.sequence
    const generation = this.generation
    const state = await this.query('getState').catch((error) => { if (generation === this.generation) { this.stateFailed = true; this.view.stateDirty = true }; throw error })
    if (generation !== this.generation || !isHostState(state)) return
    if (state.instance_id !== this.instance) { this.stateFailed = true; this.view.stateDirty = true; throw new Error('Runtime 实例已变更，请重新连接。') }
    if (state.revision < this.watermark || (state.revision === this.view.state?.revision && sequence < this.appliedState)) return
    this.view.state = state; this.appliedState = sequence; this.stateFailed = false; this.advance(state.revision)
  }
  async refreshPlugins() {
    const sequence = ++this.sequence
    const generation = this.generation
    const list = await this.query('plugins.list').catch((error) => { if (generation === this.generation) { this.listFailed = true; this.view.pluginsDirty = true }; throw error })
    if (generation !== this.generation || !isPluginList(list)) return
    if (list.instance_id !== this.instance) { this.listFailed = true; this.view.pluginsDirty = true; throw new Error('Runtime 实例已变更，请重新连接。') }
    if (list.revision < this.watermark || (list.revision === this.view.plugins?.revision && sequence < this.appliedList)) return
    this.view.plugins = list; this.appliedList = sequence; this.listFailed = false; this.advance(list.revision)
  }
  refresh(): Promise<void> {
    if (!this.view.connected) return Promise.resolve()
    if (this.refreshPromise) return this.refreshPromise
    const generation = this.generation
    const current = Promise.allSettled([this.refreshState(), this.refreshPlugins()]).then((results) => {
      if (generation !== this.generation) return
      const error = results.find((result) => result.status === 'rejected')
      this.view.error = error?.status === 'rejected' ? this.message(error.reason) : ''
      // Stale replies are not successful refreshes; preserve dirty and repair the other snapshot.
      if (!error && (this.view.stateDirty || this.view.pluginsDirty)) this.scheduleRefresh()
    }).finally(() => { if (this.refreshPromise === current) this.refreshPromise = undefined })
    this.refreshPromise = current
    return current
  }
  private scheduleRefresh() {
    if (this.disposed || !this.view.connected || this.refreshTimer) return
    this.refreshTimer = setTimeout(() => { this.refreshTimer = undefined; void this.refresh() }, 100)
  }
  async mutate(method: Exclude<ManagementMethod, 'describe' | 'getState' | 'plugins.list' | 'operations.get'>, params: Record<string, string> = {}, requestKey = crypto.randomUUID()) {
    if (!this.view.connected || this.view.busy || this.intent || this.view.operation?.status === 'running' || this.view.operation?.status === 'reconciling') return
    if (method.startsWith('plugins.') && !this.supportsPlugins) { this.view.error = '此 Runtime 尚不支持第一方插件管理'; return }
    this.intent = { method, params: { ...params, request_key: requestKey }, instance: this.instance }
    await this.retry()
  }
  async retry() {
    if (!this.intent || this.view.busy || this.intent.instance !== this.instance || !this.view.connected) return
    const intent = this.intent
    const generation = this.generation
    this.view.busy = true; this.view.retryAvailable = false; this.view.error = ''
    try {
      const operation = await this.query(intent.method, intent.params)
      if (generation !== this.generation || !isOperation(operation)) return
      this.view.operation = operation
      this.operationQuery = 0; this.appliedOperationQuery = 0
      this.intent = undefined // Once accepted, query the operation; never install a second time.
      this.trackOperation()
      await this.refresh()
    } catch (error) {
      if (generation === this.generation) {
        this.view.error = this.message(error)
        this.view.retryAvailable = !(error instanceof RejectedManagementRequest)
        if (!this.view.retryAvailable) this.intent = undefined
      }
    } finally { if (generation === this.generation) this.view.busy = false }
  }
  async importPackage() {
    if (!this.supportsPlugins || !this.view.connected || this.view.busy || this.intent || this.view.operation?.status === 'running' || this.view.operation?.status === 'reconciling') return
    const generation = this.generation
    const instance = this.instance
    const requestKey = crypto.randomUUID()
    this.view.busy = true; this.view.error = ''
    try {
      const selection = decodeSelection(await this.choosePackage(instance, requestKey))
      if (!selection || generation !== this.generation) return
      this.view.selectionLabel = selection.label
      this.view.busy = false
      await this.mutate('plugins.import', { selection_ref: selection.selection_ref }, requestKey)
    } catch (error) { if (generation === this.generation) this.view.error = this.message(error) }
    finally { if (generation === this.generation) this.view.busy = false }
  }
  private choosePackage(instance: string, requestKey: string) {
    const generation = this.generation
    const port = this.port
    return new Promise<unknown>((resolve, reject) => {
      let settled = false
      const finish = (error?: Error, result?: unknown) => {
        if (settled) return
        settled = true; clearTimeout(timer); this.cancellations.delete(cancel)
        if (error) reject(error); else resolve(result)
      }
      const cancel = () => finish(new Error('Runtime 管理连接已变更'))
      const timer = setTimeout(() => finish(new Error('选择离线包超时，请重新选择。')), this.timeoutMs)
      this.cancellations.add(cancel)
      Promise.resolve().then(() => port.choosePackage({ instance_id: instance, request_key: requestKey })).then((result) => {
        if (generation !== this.generation) cancel(); else finish(undefined, result)
      }, (error) => finish(new Error(this.message(error))))
    })
  }
  async refreshOperation() {
    const previous = this.view.operation
    if (!previous || !this.view.connected) return
    const generation = this.generation
    const query = ++this.operationQuery
    try {
      const operation = await this.query('operations.get', { operation_id: previous.operation_id })
      if (generation !== this.generation || !isOperation(operation) || this.view.operation?.operation_id !== previous.operation_id) return
      if (operation.operation_id !== previous.operation_id || operation.operation !== previous.operation) throw new Error('Runtime 管理操作回复不匹配')
      if (query < this.appliedOperationQuery) return
      this.appliedOperationQuery = query
      this.view.operation = operation
      await this.refresh()
      this.trackOperation()
    } catch (error) { if (generation === this.generation) this.view.error = this.message(error) }
  }
  private trackOperation() {
    clearTimeout(this.operationTimer)
    if (this.view.operation?.status === 'running') this.operationTimer = setTimeout(() => { void this.refreshOperation() }, 750)
  }
}
