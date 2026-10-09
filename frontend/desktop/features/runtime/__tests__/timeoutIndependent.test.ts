import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { RuntimeController } from '../controller'
import type { ManagementRequest, RuntimeManagementPort } from '../contracts'

function deferred() {
  let resolve!: (value: unknown) => void
  const promise = new Promise<unknown>(done => { resolve = done })
  return { promise, resolve }
}
function reply(request: ManagementRequest, result: unknown) { return { request_id: request.request_id, method: request.method, code: 0, error: '', result } }
function fixture() {
  let disconnected = () => {}
  const port: RuntimeManagementPort = {
    request: vi.fn(async request => reply(request, request.method === 'describe'
      ? { api_major: 1, host_version: '0.2.14', features: ['events', 'first_party_plugins'], instance_id: 'isolated-host', revision: 1 }
      : request.method === 'getState'
        ? { instance_id: 'isolated-host', revision: 1, supervisor: 'runtime_app', state: 'stopped', connection: 'unpaired' }
        : { instance_id: 'isolated-host', revision: 1, plugins: [] })),
    observe: () => () => {},
    onDisconnect: listener => { disconnected = listener; return () => {} },
    choosePackage: vi.fn(async () => ({ selection_ref: 'isolated-selected-file', label: 'test.zip' })),
  }
  // Deliberately exercise the production default rather than a shorter injected deadline.
  const controller = new RuntimeController(port)
  controllers.push(controller)
  return { port, controller, disconnect: () => disconnected() }
}
const controllers: RuntimeController[] = []
afterEach(() => { controllers.splice(0).forEach(controller => controller.dispose()); vi.useRealTimers() })

describe('independent production Runtime waiting budget', () => {
  it('accepts a valid inventory after 45 seconds instead of falsely timing out at the former 15-second budget', async () => {
    vi.useFakeTimers()
    const { port, controller } = fixture(); await controller.connect()
    const delayed = deferred(); let query!: ManagementRequest
    vi.mocked(port.request).mockImplementation(request => { query = request; return delayed.promise })
    let finished = false
    const pending = controller.refreshPlugins().then(() => { finished = true })
    await flushPromises(); await vi.advanceTimersByTimeAsync(45_000)
    expect(finished).toBe(false)
    expect(controller.view.error).toBe('')
    delayed.resolve(reply(query, { instance_id: 'isolated-host', revision: 2, plugins: [] }))
    await pending
    expect(controller.view.plugins?.revision).toBe(2)
    expect(controller.view.pluginsDirty).toBe(false)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('bounds an unknown install at 120 seconds and only manually retries the original request and selection', async () => {
    vi.useFakeTimers()
    const { port, controller } = fixture(); await controller.connect()
    const normal = vi.mocked(port.request).getMockImplementation()!
    const delayed = deferred(); const sent: ManagementRequest[] = []
    const accepted = { operation_id: 'isolated-operation', operation: 'import', status: 'succeeded', code: 0, error: '' }
    vi.mocked(port.request).mockImplementation(request => {
      if (request.method !== 'plugins.import') return normal(request)
      sent.push(request)
      return sent.length === 1 ? delayed.promise : Promise.resolve(reply(request, accepted))
    })
    let finished = false
    const pending = controller.importPackage().then(() => { finished = true })
    await flushPromises(); await vi.advanceTimersByTimeAsync(119_999)
    expect(finished).toBe(false); expect(controller.view.retryAvailable).toBe(false)
    await vi.advanceTimersByTimeAsync(1); await pending
    expect(finished).toBe(true); expect(controller.view.retryAvailable).toBe(true)
    expect(controller.view.error).toContain('超时'); expect(vi.getTimerCount()).toBe(0)
    expect(sent).toHaveLength(1)
    delayed.resolve(reply(sent[0]!, accepted)); await flushPromises()
    expect(controller.view.operation).toBeNull()
    await controller.retry()
    expect(sent).toHaveLength(2); expect(sent[1]?.params).toEqual(sent[0]?.params)
    expect(vi.mocked(port.choosePackage)).toHaveBeenCalledTimes(1)
    expect(controller.view.operation?.operation_id).toBe('isolated-operation')
    expect(vi.getTimerCount()).toBe(0)
  })

  it('cancels a long inventory wait immediately on disconnect and ignores its late snapshot', async () => {
    vi.useFakeTimers()
    const { port, controller, disconnect } = fixture(); await controller.connect()
    const delayed = deferred(); let query!: ManagementRequest
    vi.mocked(port.request).mockImplementation(request => { query = request; return delayed.promise })
    const pending = controller.refreshPlugins().catch((error: Error) => error.message)
    await flushPromises(); await vi.advanceTimersByTimeAsync(45_000)
    disconnect()
    expect(await pending).toContain('连接已变更')
    expect(controller.view.connected).toBe(false); expect(vi.getTimerCount()).toBe(0)
    delayed.resolve(reply(query, { instance_id: 'isolated-host', revision: 99, plugins: [] })); await flushPromises()
    expect(controller.view.plugins).toBeNull()
  })

  it('ends a chooser wait on disconnect without waiting for its long budget or importing a late selection', async () => {
    vi.useFakeTimers()
    const { port, controller, disconnect } = fixture(); await controller.connect()
    const delayed = deferred(); vi.mocked(port.choosePackage).mockReturnValue(delayed.promise)
    const pending = controller.importPackage()
    await flushPromises(); await vi.advanceTimersByTimeAsync(45_000)
    disconnect(); await pending
    expect(controller.view.busy).toBe(false); expect(vi.getTimerCount()).toBe(0)
    delayed.resolve({ selection_ref: 'late-choice', label: 'late.zip' }); await flushPromises()
    expect(vi.mocked(port.request).mock.calls.some(([request]) => request.method === 'plugins.import')).toBe(false)
  })
})
