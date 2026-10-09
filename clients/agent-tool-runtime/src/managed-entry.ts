/** Trusted direct Node child entry for H3; no TCP listener or CLI side effects. */
import { randomUUID } from 'node:crypto'
import { ManagementError, type RuntimeHost, type RuntimePlatform, type RuntimePlatformResponse } from '@aid/local-tool-host-core'
import { openRuntimeHost } from './runtimeHost.js'

export interface ManagementIpc {
  send(message: Record<string, unknown>): void
  onMessage(listener: (message: unknown) => void): void
  onDisconnect(listener: () => void): void
}

export function attachManagementIpc(host: RuntimeHost, channel: ManagementIpc): void {
  let disconnected = false
  const send = (message: Record<string, unknown>) => {
    if (!disconnected) channel.send(message)
  }
  const unsubscribe = host.observe(send)
  channel.onMessage(message => {
    if ((message as { kind?: unknown } | null)?.kind === 'runtime_platform_response') return
    if (!disconnected) void host.request(message).then(send).catch(() => {
      console.error('受管Runtime管理请求未完成，需核对')
    })
  })
  channel.onDisconnect(() => {
    if (disconnected) return
    disconnected = true
    unsubscribe()
    // The same queue closes admission and drains accepted work, including result delivery.
    void (async () => {
      await host.request({ request_id: 'parent-disconnect', method: 'stop', params: { request_key: randomUUID() } })
      await host.dispose()
    })().catch(() => { console.error('受管Runtime停止事实尚需核对，保留实例保护') })
  })
}

async function main(): Promise<void> {
  if (!process.send) throw new Error('受管Runtime需要直属父进程私有IPC')
  // H3 launches this entry with its fixed Node executable and trusted home environment.
  // Runtime never accepts executable/home paths through renderer management messages.
  const supervisor = process.argv.includes('--supervisor=desktop') ? 'desktop' : 'runtime_app'
  const messages = new Set<(message: unknown) => void>()
  const disconnects = new Set<() => void>()
  const channel: ManagementIpc = {
    send(message) { if (process.connected) process.send!(message, error => { if (error) console.error('Runtime管理IPC发送失败') }) },
    onMessage(listener) { messages.add(listener) },
    onDisconnect(listener) { disconnects.add(listener) },
  }
  const platform = createRuntimePlatformIpc(channel)
  const describes: Record<string, unknown>[] = []
  let host: RuntimeHost | undefined
  let failure: ManagementError | undefined
  let failureTimer: NodeJS.Timeout | undefined
  let connected = true
  const failHandshake = (request: Record<string, unknown>) => {
    clearTimeout(failureTimer)
    const response = { request_id: request.request_id, method: 'describe', code: failure!.code, error: failure!.message, result: null }
    process.send!(response, () => { process.disconnect(); process.exitCode = 1 })
  }
  // Listen before lease/DPAPI initialization: even an early conflict answers the parent's
  // real describe request through the existing H1 failure shape.
  process.on('message', message => {
    const frame = message as Record<string, unknown> | null
    if (frame?.kind === 'runtime_platform_response') { for (const listener of messages) listener(message); return }
    if (host) { for (const listener of messages) listener(message); return }
    if (frame?.method === 'describe' && typeof frame.request_id === 'string' && frame.request_id
      && frame.params && typeof frame.params === 'object' && !Array.isArray(frame.params) && Object.keys(frame.params).length === 0) {
      if (failure) failHandshake(frame)
      else if (describes.length < 16) describes.push(frame)
    } else if (typeof frame?.request_id === 'string' && typeof frame.method === 'string') {
      channel.send({ request_id: frame.request_id, method: frame.method, code: 4, error: 'Runtime尚未完成启动握手', result: null })
    }
  })
  process.once('disconnect', () => { connected = false; for (const listener of disconnects) listener() })
  try {
    host = await openRuntimeHost({ nodeExecutable: process.execPath, supervisor, platform })
    attachManagementIpc(host, channel)
    if (!connected) { await host.dispose(); return }
    for (const request of describes) for (const listener of messages) listener(request)
  } catch (error) {
    failure = error instanceof ManagementError ? error : new ManagementError(11, '受管Runtime启动失败，需核对本机配置')
    if (!connected) { process.exitCode = 1; return }
    if (describes.length) failHandshake(describes[0]!)
    else failureTimer = setTimeout(() => { if (process.connected) process.disconnect(); process.exitCode = 1 }, 30_000)
  }
}

/** Platform frames are private Main replies, never routed to the H1 management parser. */
export function createRuntimePlatformIpc(channel: ManagementIpc, timeoutMs = 30_000): RuntimePlatform {
  const pending = new Map<string, { resolve: (value: import('@aid/local-tool-host-core').SelectedPackageSnapshot) => void; reject: (error: Error) => void; timer: NodeJS.Timeout }>()
  let connected = true
  channel.onMessage(value => {
    const response = value as RuntimePlatformResponse | null
    if (response?.kind !== 'runtime_platform_response' || typeof response.id !== 'string') return
    const item = pending.get(response.id)
    if (!item) return
    pending.delete(response.id); clearTimeout(item.timer)
    if (!Number.isInteger(response.code) || typeof response.error !== 'string'
      || (response.code === 0 ? response.error !== '' : response.code < 1 || !response.error || response.result !== null)) {
      item.reject(new ManagementError(11, '本机文件选择通道返回无效')); return
    }
    if (response.code !== 0) { item.reject(new ManagementError(response.code, response.error)); return }
    const snapshot = response.result
    if (!snapshot || typeof snapshot.staged_path !== 'string' || !snapshot.staged_path || !Number.isSafeInteger(snapshot.size)
      || snapshot.size < 1 || typeof snapshot.sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(snapshot.sha256)) {
      item.reject(new ManagementError(11, '本机文件选择快照无效')); return
    }
    item.resolve(snapshot)
  })
  channel.onDisconnect(() => {
    connected = false
    for (const item of pending.values()) { clearTimeout(item.timer); item.reject(new ManagementError(11, '本机文件选择通道已断开')) }
    pending.clear()
  })
  return { takeSelectedPackage(params) {
    if (!connected) return Promise.reject(new ManagementError(11, '本机文件选择通道已断开'))
    return new Promise((resolve, reject) => {
      const id = randomUUID()
      const timer = setTimeout(() => { pending.delete(id); reject(new ManagementError(7, '本机文件选择等待超时，请重新选择')) }, timeoutMs)
      pending.set(id, { resolve, reject, timer })
      try { channel.send({ kind: 'runtime_platform_request', id, method: 'takeSelectedPackage', params }) }
      catch { pending.delete(id); clearTimeout(timer); reject(new ManagementError(11, '本机文件选择请求未发送')) }
    })
  } }
}

if (process.argv[1]?.replace(/\\/g, '/').endsWith('/managed-entry.js')) {
  void main().catch(() => { console.error('受管Runtime启动失败，请检查实例冲突、配置和系统保护'); process.exitCode = 1 })
}
