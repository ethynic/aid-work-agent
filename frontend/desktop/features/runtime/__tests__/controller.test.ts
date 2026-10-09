import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { RuntimeController } from '../controller'
import { decodeResponse, isHostEvent, type ManagementRequest, type RuntimeManagementPort } from '../contracts'

const state = (revision = 1, instance = 'host-one') => ({ instance_id: instance, revision, supervisor: 'runtime_app', state: 'stopped', connection: 'offline', device: { device_id: 'device-one' } })
const list = (revision = 1, instance = 'host-one', version?: string) => ({ instance_id: instance, revision, plugins: [{ installation_id: 'installed-one', plugin_id: 'weixin', display_name: '微信', release_id: 'opaque-release', enabled: true, ready: false, ...(version ? { version } : {}) }] })
const operation = (status = 'succeeded', name = 'import') => ({ operation_id: 'op-one', operation: name, status, code: 0, error: '' })
function reply(request: ManagementRequest, result: unknown) { return { request_id: request.request_id, method: request.method, code: 0, error: '', result } }
function deferred<T = unknown>() { let resolve!: (value: T) => void; const promise = new Promise<T>((done) => { resolve = done }); return { promise, resolve } }
function makePort(features = ['events', 'first_party_plugins']) {
  let eventListener: (event: unknown) => void = () => {}
  let disconnectListener = () => {}
  const port: RuntimeManagementPort = {
    request: vi.fn(async (request) => reply(request, request.method === 'describe' ? { api_major: 1, host_version: '0.2.14', features, instance_id: 'host-one', revision: 1 } : request.method === 'getState' ? state() : request.method === 'plugins.list' ? list() : operation('succeeded', request.method.replace('plugins.', '')))),
    observe: vi.fn((listener) => { eventListener = listener; return vi.fn() }),
    onDisconnect: vi.fn((listener) => { disconnectListener = listener; return vi.fn() }),
    choosePackage: vi.fn(async () => ({ selection_ref: 'opaque-selection', label: 'weixin.zip' })),
  }
  return { port, emit: (value: unknown) => eventListener(value), disconnect: () => disconnectListener() }
}
const controllers: RuntimeController[] = []
function create(port: RuntimeManagementPort, timeout = 1000) { const controller = new RuntimeController(port, timeout); controllers.push(controller); return controller }
afterEach(() => { controllers.forEach((controller) => controller.dispose()); controllers.length = 0; vi.useRealTimers() })

describe('Runtime production consumer', () => {
  it('preserves matching handshake notifications when describe and both initial snapshots arrive late', async () => {
    vi.useFakeTimers()
    const { port, emit } = makePort(); const controller = create(port)
    const handshake = deferred(); let describeRequest!: ManagementRequest
    let revision = 1
    vi.mocked(port.request).mockImplementation((request) => {
      if (request.method === 'describe') { describeRequest = request; return handshake.promise }
      return Promise.resolve(reply(request, request.method === 'getState' ? state(revision) : list(revision)))
    })
    const connect = controller.connect(); await flushPromises()
    emit({ event: 'state_changed', instance_id: 'host-one', revision: 5 })
    emit({ event: 'state_changed', instance_id: 'host-one', revision: 3 })
    handshake.resolve(reply(describeRequest, { api_major: 1, host_version: 'test', features: ['events'], instance_id: 'host-one', revision: 1 }))
    await connect
    expect(controller.view.stateDirty).toBe(true)
    expect(controller.view.pluginsDirty).toBe(true)
    expect(controller.view.state).toBeNull()
    expect(controller.view.plugins).toBeNull()
    revision = 5
    await vi.advanceTimersByTimeAsync(100)
    expect(controller.view.state?.revision).toBe(5)
    expect(controller.view.plugins?.revision).toBe(5)
    expect(controller.view.stateDirty).toBe(false)
    expect(controller.view.pluginsDirty).toBe(false)
  })
  it('does not merge another instance handshake watermark into the described instance', async () => {
    const { port, emit } = makePort(); const controller = create(port)
    const handshake = deferred(); let describeRequest!: ManagementRequest
    const normal = vi.mocked(port.request).getMockImplementation()!
    vi.mocked(port.request).mockImplementation((request) => {
      if (request.method === 'describe') { describeRequest = request; return handshake.promise }
      return normal(request)
    })
    const connect = controller.connect(); await flushPromises()
    emit({ event: 'state_changed', instance_id: 'other-host', revision: 99 })
    handshake.resolve(reply(describeRequest, { api_major: 1, host_version: 'test', features: ['events'], instance_id: 'host-one', revision: 1 }))
    await connect
    expect(controller.view.state?.revision).toBe(1)
    expect(controller.view.plugins?.revision).toBe(1)
    expect(controller.view.stateDirty).toBe(false)
    expect(controller.view.pluginsDirty).toBe(false)
  })
  it('fails handshake explicitly when its bounded instance notification cache overflows', async () => {
    const { port, emit } = makePort(); const controller = create(port)
    const handshake = deferred(); let describeRequest!: ManagementRequest
    vi.mocked(port.request).mockImplementation((request) => { describeRequest = request; return handshake.promise })
    const connect = controller.connect(); await flushPromises()
    for (let index = 0; index < 9; index++) emit({ event: 'state_changed', instance_id: `other-host-${index}`, revision: 5 })
    handshake.resolve(reply(describeRequest, { api_major: 1, host_version: 'test', features: ['events'], instance_id: 'host-one', revision: 1 }))
    await connect
    expect(controller.view.connected).toBe(false)
    expect(controller.view.stateDirty).toBe(true)
    expect(controller.view.pluginsDirty).toBe(true)
    expect(controller.view.error).toContain('过多实例通知')
    expect(vi.mocked(port.request)).toHaveBeenCalledOnce()
  })
  it('clears handshake watermarks on disconnect and ignores notifications from the old generation', async () => {
    const { port, emit, disconnect } = makePort(); const controller = create(port)
    const handshake = deferred(); let describeRequest!: ManagementRequest
    const normal = vi.mocked(port.request).getMockImplementation()!
    vi.mocked(port.request).mockImplementation((request) => {
      if (request.method === 'describe') { describeRequest = request; return handshake.promise }
      return normal(request)
    })
    const connect = controller.connect(); await flushPromises()
    const oldListener = vi.mocked(port.observe).mock.calls[0]![0]
    emit({ event: 'state_changed', instance_id: 'host-one', revision: 8 })
    disconnect(); await connect
    vi.mocked(port.request).mockImplementation(normal)
    await controller.connect()
    oldListener({ event: 'state_changed', instance_id: 'host-one', revision: 99 })
    handshake.resolve(reply(describeRequest, { api_major: 1, host_version: 'test', features: ['events'], instance_id: 'host-one', revision: 8 }))
    await flushPromises()
    expect(controller.view.state?.revision).toBe(1)
    expect(controller.view.plugins?.revision).toBe(1)
    expect(controller.view.stateDirty).toBe(false)
    expect(controller.view.pluginsDirty).toBe(false)
  })
  it('keeps the original import key after a lost reply and same-instance reconnect', async () => {
    const { port, disconnect } = makePort(); const controller = create(port); await controller.connect()
    const normal = vi.mocked(port.request).getMockImplementation()!
    const sent: ManagementRequest[] = []
    vi.mocked(port.request).mockImplementation((request) => {
      if (request.method !== 'plugins.import') return normal(request)
      sent.push(request); return sent.length === 1 ? new Promise(() => {}) : Promise.resolve(reply(request, operation()))
    })
    const pending = controller.importPackage(); await flushPromises(); disconnect(); await pending
    await controller.connect()
    expect(controller.view.retryAvailable).toBe(true)
    await controller.retry()
    expect(sent).toHaveLength(2)
    expect(sent[1]?.params).toEqual(sent[0]?.params)
    expect(vi.mocked(port.choosePackage)).toHaveBeenCalledTimes(1)
    expect(controller.view.operation?.status).toBe('succeeded')
  })
  it('queries an accepted operation after reconnect without issuing another mutation', async () => {
    const { port, disconnect } = makePort(); const controller = create(port); await controller.connect()
    const normal = vi.mocked(port.request).getMockImplementation()!
    vi.mocked(port.request).mockImplementation(async (request) => request.method === 'start' ? reply(request, operation('running', 'start')) : request.method === 'operations.get' ? reply(request, operation('succeeded', 'start')) : normal(request))
    await controller.mutate('start'); disconnect(); await controller.connect()
    expect(vi.mocked(port.request).mock.calls.filter(([request]) => request.method === 'start')).toHaveLength(1)
    expect(vi.mocked(port.request).mock.calls.some(([request]) => request.method === 'operations.get' && request.params.operation_id === 'op-one')).toBe(true)
    expect(controller.view.operation?.status).toBe('succeeded')
  })
  it('never automatically replays an uncertain intent into a new Host instance', async () => {
    const { port, disconnect } = makePort(); const controller = create(port); await controller.connect()
    const normal = vi.mocked(port.request).getMockImplementation()!
    vi.mocked(port.request).mockImplementation((request) => request.method === 'pair' ? new Promise(() => {}) : normal(request))
    const pending = controller.mutate('pair', { server: 'https://isolated.test', device_name: 'test', pairing_code: 'one-use' }); await flushPromises(); disconnect(); await pending
    const replacement = makePort()
    vi.mocked(replacement.port.request).mockImplementation(async request => reply(request, request.method === 'describe' ? { api_major: 1, host_version: 'test', features: [], instance_id: 'host-two', revision: 1 } : request.method === 'getState' ? state(1, 'host-two') : list(1, 'host-two')))
    await controller.connect(replacement.port); await controller.retry()
    expect(controller.view.retryAvailable).toBe(false)
    expect(controller.view.error).toContain('未自动重放')
    expect(vi.mocked(replacement.port.request).mock.calls.some(([request]) => request.method === 'pair')).toBe(false)
  })
  it('rejects legacy array inventory, mismatched correlation and extra secret fields', () => {
    const request: ManagementRequest = { request_id: 'one', method: 'plugins.list', params: {} }
    expect(() => decodeResponse(reply(request, []), request)).toThrow()
    expect(() => decodeResponse({ ...reply(request, list()), request_id: 'other' }, request)).toThrow()
    expect(() => decodeResponse(reply(request, { ...list(), token: 'secret' }), request)).toThrow()
    expect(() => decodeResponse(reply(request, { ...list(), plugins: [{ ...list().plugins[0], version: '' }] }), request)).toThrow()
    expect(isHostEvent({ event: 'state_changed', instance_id: 'one', revision: 2, path: 'private' })).toBe(false)
  })
  it('accepts the earlier higher revision even while a later list query is pending', async () => {
    const { port } = makePort(); const controller = create(port); await controller.connect()
    const first = deferred(); const second = deferred(); const requests: ManagementRequest[] = []
    vi.mocked(port.request).mockImplementation((request) => { requests.push(request); return requests.length === 1 ? first.promise : second.promise })
    const oldQuery = controller.refreshPlugins(); const newQuery = controller.refreshPlugins(); await flushPromises()
    first.resolve(reply(requests[0]!, list(8, 'host-one', '2.0.0'))); await oldQuery
    expect(controller.view.plugins?.revision).toBe(8)
    expect(controller.view.stateDirty).toBe(true)
    second.resolve(reply(requests[1]!, list(7, 'host-one', '1.9.0'))); await newQuery
    expect(controller.view.plugins?.plugins[0]?.version).toBe('2.0.0')
    expect(controller.view.pluginsDirty).toBe(false)
  })
  it('uses applied query sequence only for equal revision and never regresses the other snapshot watermark', async () => {
    const { port } = makePort(); const controller = create(port); await controller.connect()
    const first = deferred(); const second = deferred(); const requests: ManagementRequest[] = []
    vi.mocked(port.request).mockImplementation((request) => { requests.push(request); return requests.length === 1 ? first.promise : second.promise })
    const oldQuery = controller.refreshPlugins(); const newQuery = controller.refreshPlugins(); await flushPromises()
    second.resolve(reply(requests[1]!, list(3, 'host-one', 'new'))); await newQuery
    first.resolve(reply(requests[0]!, list(3, 'host-one', 'old'))); await oldQuery
    expect(controller.view.plugins?.plugins[0]?.version).toBe('new')
    vi.mocked(port.request).mockImplementation(async (request) => reply(request, state(2)))
    await controller.refreshState()
    expect(controller.view.state?.revision).toBe(1)
    expect(controller.view.stateDirty).toBe(true)
  })
  it('ends old Promise waits on reconnect and ignores late replies from that generation', async () => {
    const { port } = makePort(); const controller = create(port); await controller.connect()
    const stale = deferred(); let request!: ManagementRequest
    vi.mocked(port.request).mockImplementation((next) => { request = next; return stale.promise })
    const pending = controller.refreshPlugins().catch((error: Error) => error.message); await flushPromises()
    const replacement = makePort()
    vi.mocked(replacement.port.request).mockImplementation(async (next) => reply(next, next.method === 'describe' ? { api_major: 1, host_version: '0.2.14', features: [], instance_id: 'host-two', revision: 1 } : next.method === 'getState' ? state(1, 'host-two') : list(1, 'host-two')))
    await controller.connect(replacement.port)
    expect(await pending).toContain('连接已变更')
    stale.resolve(reply(request, list(100))); await flushPromises()
    expect(controller.view.plugins?.instance_id).toBe('host-two')
    expect(controller.view.plugins?.revision).toBe(1)
  })
  it('keeps dirty after timeout and releases local wait on disconnect', async () => {
    vi.useFakeTimers()
    const { port, disconnect } = makePort(); const controller = create(port, 20); await controller.connect()
    vi.mocked(port.request).mockImplementation(() => new Promise(() => {}))
    const pending = controller.refresh(); await vi.advanceTimersByTimeAsync(21); await pending
    // Failed refresh does not imply a newer snapshot has been consumed.
    expect(controller.view.error).toContain('超时')
    expect(controller.view.stateDirty).toBe(true)
    expect(controller.view.pluginsDirty).toBe(true)
    const wait = controller.refreshState().catch((error: Error) => error.message); await flushPromises(); disconnect()
    expect(await wait).toContain('连接已变更')
    expect(controller.view.state).toBeNull()
    expect(controller.view.stateDirty).toBe(true)
  })
  it('uses notification revision as the watermark and repairs both snapshots without clearing dirty on a stale reply', async () => {
    const { port, emit } = makePort(); const controller = create(port); await controller.connect()
    vi.useFakeTimers()
    let currentRevision = 2
    vi.mocked(port.request).mockImplementation(async (request) => reply(request, request.method === 'getState' ? state(currentRevision) : list(currentRevision)))
    emit({ event: 'state_changed', instance_id: 'host-one', revision: 5 })
    expect(controller.view.stateDirty).toBe(true)
    expect(controller.view.pluginsDirty).toBe(true)
    await vi.advanceTimersByTimeAsync(100)
    expect(controller.view.state?.revision).toBe(1)
    expect(controller.view.pluginsDirty).toBe(true)
    currentRevision = 5
    await vi.advanceTimersByTimeAsync(100)
    expect(controller.view.state?.revision).toBe(5)
    expect(controller.view.plugins?.revision).toBe(5)
    expect(controller.view.stateDirty).toBe(false)
    expect(controller.view.pluginsDirty).toBe(false)
  })
  it('does not let a late running operation poll overwrite a completed newer query', async () => {
    const { port } = makePort(); const controller = create(port); await controller.connect()
    const normal = vi.mocked(port.request).getMockImplementation()!
    vi.mocked(port.request).mockImplementation(async (request) => request.method === 'stop' ? reply(request, operation('running', 'stop')) : normal(request))
    await controller.mutate('stop')
    const first = deferred(); const second = deferred(); const requests: ManagementRequest[] = []
    vi.mocked(port.request).mockImplementation((request) => { if (request.method !== 'operations.get') return normal(request); requests.push(request); return requests.length === 1 ? first.promise : second.promise })
    const oldPoll = controller.refreshOperation(); const newPoll = controller.refreshOperation(); await flushPromises()
    second.resolve(reply(requests[1]!, operation('succeeded', 'stop'))); await newPoll
    first.resolve(reply(requests[0]!, operation('running', 'stop'))); await oldPoll
    expect(controller.view.operation?.status).toBe('succeeded')
  })
  it('retries import with the original key and opaque selection after an unknown result', async () => {
    vi.useFakeTimers()
    const { port } = makePort(); const controller = create(port, 20); await controller.connect()
    const requests: ManagementRequest[] = []
    const normal = vi.mocked(port.request).getMockImplementation()!
    vi.mocked(port.request).mockImplementation((request) => {
      if (request.method !== 'plugins.import') return normal(request)
      requests.push(request)
      return requests.length === 1 ? new Promise(() => {}) : Promise.resolve(reply(request, operation()))
    })
    const install = controller.importPackage(); await vi.advanceTimersByTimeAsync(21); await install
    expect(controller.view.retryAvailable).toBe(true)
    await controller.retry()
    expect(requests[0]?.params).toEqual(requests[1]?.params)
    expect(requests[0]?.params.selection_ref).toBe('opaque-selection')
    expect(vi.mocked(port.choosePackage)).toHaveBeenCalledOnce()
    expect(controller.view.operation?.status).toBe('succeeded')
  })
  it('does not offer plugin mutations without negotiated capability', async () => {
    const { port } = makePort(['events']); const controller = create(port); await controller.connect()
    await controller.importPackage(); await controller.mutate('plugins.enable', { installation_id: 'one' })
    expect(port.choosePackage).not.toHaveBeenCalled()
    expect(vi.mocked(port.request).mock.calls.some(([request]) => request.method.startsWith('plugins.') && request.method !== 'plugins.list')).toBe(false)
  })
  it('ends a hanging chooser on disposal and never imports its late selection', async () => {
    const { port } = makePort(); const controller = create(port); await controller.connect()
    const choice = deferred(); vi.mocked(port.choosePackage).mockReturnValue(choice.promise)
    const install = controller.importPackage(); await flushPromises(); controller.dispose(); await install
    choice.resolve({ selection_ref: 'late', label: 'late.zip' }); await flushPromises()
    expect(vi.mocked(port.request).mock.calls.some(([request]) => request.method === 'plugins.import')).toBe(false)
  })
  it('clears a definitive rejected intent so a corrected pairing can use a new key', async () => {
    const { port } = makePort(); const controller = create(port); await controller.connect()
    const normal = vi.mocked(port.request).getMockImplementation()!
    vi.mocked(port.request).mockImplementation(async (request) => request.method === 'pair' ? { request_id: request.request_id, method: 'pair', code: 11, error: '历史执行事实缺少结案证明', result: null } : normal(request))
    await controller.mutate('pair', { server: 'https://test', device_name: 'test', pairing_code: 'once' })
    expect(controller.view.retryAvailable).toBe(false)
    expect(controller.view.error).toContain('代码 11')
    vi.mocked(port.request).mockImplementation(async (request) => request.method === 'pair' ? reply(request, operation('succeeded', 'pair')) : normal(request))
    await controller.mutate('pair', { server: 'https://test', device_name: 'test', pairing_code: 'new' })
    expect(controller.view.operation?.status).toBe('succeeded')
  })
})
