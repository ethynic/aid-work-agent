import { spawn, type ChildProcess } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import type { RuntimePlatformRequest, RuntimePlatformResponse, SelectedPackageSnapshot } from '@aid/local-tool-host-core'

interface Request { request_id: string; method: string; params: Record<string, string> }
interface Reply { request_id: string; method: string; code: number; error: string; result: unknown }
const object = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value)
const methods: Record<string, string[]> = {
  describe: [], getState: [], 'plugins.list': [], 'operations.get': ['operation_id'],
  start: ['request_key'], stop: ['request_key'], pair: ['request_key', 'server', 'device_name', 'pairing_code'],
  'plugins.import': ['request_key', 'selection_ref'], 'plugins.enable': ['request_key', 'installation_id'],
  'plugins.disable': ['request_key', 'installation_id'], 'plugins.uninstall': ['request_key', 'installation_id'],
}
export function parseRuntimeRequest(value: unknown): Request {
  if (!object(value) || Object.keys(value).sort().join(',') !== 'method,params,request_id'
    || typeof value.request_id !== 'string' || !value.request_id || value.request_id.length > 200
    || typeof value.method !== 'string' || !Object.hasOwn(methods, value.method) || !object(value.params)) throw new Error('Runtime管理请求无效')
  const fields = methods[value.method]!
  if (Object.keys(value.params).length !== fields.length || fields.some(key => typeof (value.params as Record<string, unknown>)[key] !== 'string' || !(value.params as Record<string, string>)[key] || (value.params as Record<string, string>)[key]!.length > 4096)) throw new Error('Runtime管理参数无效')
  return value as unknown as Request
}
interface Pending { method: string; resolve(reply: Reply): void; reject(error: Error): void; timer: ReturnType<typeof setTimeout> }
export interface RuntimeSupervisorOptions {
  nodeExecutable: string; hostEntry: string; home: string; supervisor: 'desktop' | 'runtime_app'
  takeSelectedPackage(request: RuntimePlatformRequest['params']): Promise<SelectedPackageSnapshot>
  onEvent(event: unknown): void; onDisconnect(): void
  requestTimeoutMs?: number; drainTimeoutMs?: number
  /** Test seam; production always starts the trusted absolute Node/entry. */
  spawnChild?: (node: string, args: string[], options: Parameters<typeof spawn>[2]) => ChildProcess
}

export class RuntimeSupervisor {
  private child?: ChildProcess
  private ready?: Promise<Reply>
  private exited?: Promise<number | null>
  private childExited = true
  private generation = 0
  private pending = new Map<string, Pending>()
  private platformPending = new Set<string>()
  private stopOperation?: string
  private accepting = true
  instanceId = ''
  constructor(private options: RuntimeSupervisorOptions) {}

  private ensure(): Promise<Reply> {
    if (this.ready) return this.ready
    if (this.child && !this.childExited) return this.handshake(this.generation)
    const generation = ++this.generation
    this.childExited = false; this.instanceId = ''; this.stopOperation = undefined
    const env: NodeJS.ProcessEnv = {}
    for (const key of ['PATH', 'Path', 'SystemRoot', 'WINDIR', 'COMSPEC', 'PATHEXT', 'TEMP', 'TMP', 'APPDATA', 'LOCALAPPDATA', 'USERPROFILE', 'USERNAME', 'SESSIONNAME', 'PROCESSOR_ARCHITECTURE']) if (process.env[key] !== undefined) env[key] = process.env[key]
    env.AIDWORK_RUNTIME_HOME = this.options.home
    const child = (this.options.spawnChild ?? spawn)(this.options.nodeExecutable, [this.options.hostEntry, `--supervisor=${this.options.supervisor}`], { env, shell: false, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe', 'ipc'] })
    this.child = child
    // Consume diagnostics without retaining package paths, pairing data or arbitrary logs.
    child.stdout?.resume(); child.stderr?.resume()
    this.exited = new Promise(resolve => child.once('exit', code => {
      this.childExited = true
      const wasReady = !!this.instanceId
      this.instanceId = ''; this.ready = undefined
      this.failPending('Runtime进程已退出，操作结果需核对')
      this.platformPending.clear(); child.stdout?.destroy(); child.stderr?.destroy()
      if (child.connected) child.disconnect()
      if (wasReady) this.options.onDisconnect()
      resolve(code)
    }))
    child.once('error', () => { this.failPending('Runtime无法启动，请核对安装资源'); this.ready = undefined; this.childExited = true })
    child.on('disconnect', () => {
      this.failPending('Runtime管理连接已断开，操作结果需核对')
      const wasReady = !!this.instanceId
      this.instanceId = ''; this.ready = undefined
      if (wasReady) this.options.onDisconnect()
    })
    child.on('message', message => {
      if (generation !== this.generation || this.childExited) return
      if (object(message) && message.kind === 'runtime_platform_request') { void this.platformRequest(message, child, generation); return }
      if (object(message) && message.event === 'state_changed') { this.options.onEvent(message); return }
      if (!object(message) || typeof message.request_id !== 'string') return
      const pending = this.pending.get(message.request_id)
      if (!pending) return
      this.pending.delete(message.request_id); clearTimeout(pending.timer)
      if (message.method !== pending.method || !Number.isSafeInteger(message.code) || Number(message.code) < 0 || typeof message.error !== 'string' || !Object.hasOwn(message, 'result')
        || (message.code === 0 ? message.error !== '' : !message.error || message.result !== null)) { pending.reject(new Error('Runtime管理回复格式不兼容')); return }
      pending.resolve(message as unknown as Reply)
    })
    return this.handshake(generation)
  }

  private handshake(generation: number): Promise<Reply> {
    this.ready = this.send('describe', {}).then(reply => {
      if (generation === this.generation && reply.code === 0) {
        if (!object(reply.result) || reply.result.api_major !== 1 || typeof reply.result.instance_id !== 'string' || !reply.result.instance_id) throw new Error('Runtime管理版本不兼容')
        this.instanceId = reply.result.instance_id
      }
      return reply
    }).catch(error => { if (generation === this.generation) this.ready = undefined; throw error })
    return this.ready
  }

  private send(method: string, params: Record<string, string>): Promise<Reply> {
    const child = this.child
    if (!child?.connected || this.childExited) return Promise.reject(new Error('Runtime管理连接不可用'))
    const id = randomUUID()
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { this.pending.delete(id); reject(new Error('Runtime管理请求超时，操作结果尚未确认')) }, this.options.requestTimeoutMs ?? 120_000)
      this.pending.set(id, { method, resolve, reject, timer })
      child.send({ request_id: id, method, params }, error => {
        if (!error) return
        const item = this.pending.get(id)
        if (item) { clearTimeout(item.timer); this.pending.delete(id); item.reject(new Error('Runtime管理连接已断开')) }
      })
    })
  }

  async request(value: unknown): Promise<Reply> {
    const request = parseRuntimeRequest(value)
    if (!this.accepting) throw new Error('Runtime正在退出，请等待执行收尾')
    const startup = await this.ensure()
    if (startup.code !== 0) return { ...startup, request_id: request.request_id, method: request.method }
    const reply = await this.send(request.method, request.params)
    if (request.method === 'start' && reply.code === 0) this.stopOperation = undefined
    return { ...reply, request_id: request.request_id }
  }

  private async platformRequest(raw: Record<string, unknown>, child: ChildProcess, generation: number): Promise<void> {
    if (typeof raw.id !== 'string' || !raw.id || raw.id.length > 200 || this.platformPending.has(raw.id)) return
    const id = raw.id
    this.platformPending.add(id)
    let response: RuntimePlatformResponse
    try {
      if (Object.keys(raw).sort().join(',') !== 'id,kind,method,params' || raw.method !== 'takeSelectedPackage' || !object(raw.params)
        || Object.keys(raw.params).sort().join(',') !== 'instance_id,request_key,selection_ref'
        || !this.instanceId || raw.params.instance_id !== this.instanceId
        || ['instance_id', 'request_key', 'selection_ref'].some(key => typeof (raw.params as Record<string, unknown>)[key] !== 'string' || !(raw.params as Record<string, string>)[key])) throw new Error('选包引用已失效')
      const result = await this.options.takeSelectedPackage(raw.params as unknown as RuntimePlatformRequest['params'])
      response = { kind: 'runtime_platform_response', id, code: 0, error: '', result }
    } catch { response = { kind: 'runtime_platform_response', id, code: 7, error: '选包引用已失效或文件改变，请重新选择', result: null } }
    finally { this.platformPending.delete(id) }
    if (generation === this.generation && !this.childExited && child.connected) child.send(response, () => {})
  }

  private failPending(message: string): void {
    for (const item of this.pending.values()) { clearTimeout(item.timer); item.reject(new Error(message)) }
    this.pending.clear()
  }

  async stopAndExit(): Promise<void> {
    this.accepting = false
    let confirmed = false
    try {
      if (!this.child || this.childExited) { confirmed = true; return }
      const startup = await this.ready
      if (startup?.code === 0) {
        if (!this.stopOperation) {
          const stop = await this.send('stop', { request_key: randomUUID() })
          if (stop.code !== 0 || !object(stop.result) || typeof stop.result.operation_id !== 'string') throw new Error('Runtime停止请求未确认')
          this.stopOperation = stop.result.operation_id
        }
        const deadline = Date.now() + (this.options.drainTimeoutMs ?? 30_000)
        for (;;) {
          const operation = await this.send('operations.get', { operation_id: this.stopOperation })
          if (operation.code !== 0 || !object(operation.result)) throw new Error('Runtime停止结果未确认')
          if (operation.result.status === 'succeeded') break
          if (operation.result.status !== 'running') {
            // Terminal operations remain historical facts; a later explicit exit may request a fresh stop check.
            this.stopOperation = undefined
            throw new Error('Runtime仍在收尾或需要核对，暂不能退出或更新')
          }
          if (Date.now() >= deadline) throw new Error('Runtime仍在收尾或需要核对，暂不能退出或更新')
          await new Promise(resolve => setTimeout(resolve, 100))
        }
        const state = await this.send('getState', {})
        if (state.code !== 0 || !object(state.result) || state.result.instance_id !== this.instanceId || state.result.state !== 'stopped') throw new Error('Runtime尚未安全停止')
        if (this.child.connected) this.child.disconnect()
      }
      let timer: ReturnType<typeof setTimeout> | undefined
      try {
        await Promise.race([this.exited, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('Runtime进程退出尚未确认')), 15_000) })])
        confirmed = true
      } finally { clearTimeout(timer) }
    } finally { this.accepting = !confirmed }
  }
}
