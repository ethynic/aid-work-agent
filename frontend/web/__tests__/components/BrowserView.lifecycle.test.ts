/** Real component/API logic; only external ticket HTTP, WS and blob IO are controlled. */
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import BrowserView from '@/components/browser/BrowserView.vue'
import { controlledRunnerFetch } from '../mocks/runnerFetch'

class ObservedSocket {
  static OPEN = 1
  static sockets: ObservedSocket[] = []
  readyState = 1
  bufferedAmount = 0
  binaryType = ''
  onopen: ((event: Event) => void) | null = null
  onclose: ((event: CloseEvent) => void) | null = null
  onerror: ((event: Event) => void) | null = null
  onmessage: ((event: MessageEvent) => void) | null = null
  closed = false
  sent: string[] = []
  constructor(readonly url: string) { ObservedSocket.sockets.push(this) }
  send(value: string) { this.sent.push(value) }
  close() { this.closed = true; this.readyState = 3 }
}

describe('Native Browser view observer lifetime', () => {
  let network: ReturnType<typeof controlledRunnerFetch>
  let wrappers: VueWrapper[]
  const createURL = vi.fn(() => 'blob:fictional-browser-frame')
  const revokeURL = vi.fn()
  let createDescriptor: PropertyDescriptor | undefined
  let revokeDescriptor: PropertyDescriptor | undefined
  const props = () => ({ runId: 'native-run-A', assistanceId: 'native-wait-A',
    authHeaders: { Authorization: 'Bearer fictional-A' }, controlEnabled: false })

  beforeEach(() => {
    network = controlledRunnerFetch(); wrappers = []
    ObservedSocket.sockets = []; createURL.mockClear(); revokeURL.mockClear()
    createDescriptor = Object.getOwnPropertyDescriptor(URL, 'createObjectURL')
    revokeDescriptor = Object.getOwnPropertyDescriptor(URL, 'revokeObjectURL')
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createURL })
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: revokeURL })
    vi.stubGlobal('fetch', network.fetch); vi.stubGlobal('WebSocket', ObservedSocket)
  })
  afterEach(async () => {
    for (const wrapper of wrappers) wrapper.unmount()
    network.detachOutstanding(); await flushPromises(); vi.unstubAllGlobals()
    if (createDescriptor) Object.defineProperty(URL, 'createObjectURL', createDescriptor)
    else Reflect.deleteProperty(URL, 'createObjectURL')
    if (revokeDescriptor) Object.defineProperty(URL, 'revokeObjectURL', revokeDescriptor)
    else Reflect.deleteProperty(URL, 'revokeObjectURL')
  })

  it('a late ticket after unmount cannot create a socket, image or cancellation', async () => {
    const wrapper = mount(BrowserView, { props: props() }); wrappers.push(wrapper)
    expect(network.pending).toHaveLength(1)
    const ticket = network.pending[0]
    wrapper.unmount(); wrappers = []
    ticket.respond({ ticket: 'fictional-late-ticket' }); await flushPromises()
    expect(ObservedSocket.sockets).toHaveLength(0)
    expect(createURL).not.toHaveBeenCalled()
    expect(network.requests.map(item => new URL(item.url).pathname)).toEqual(['/api/browser/runs/native-run-A/view_ticket'])
  })

  it('aid and subject changes ignore old tickets and old binary callbacks', async () => {
    const wrapper = mount(BrowserView, { props: props() }); wrappers.push(wrapper)
    const oldTicket = network.pending[0]
    await wrapper.setProps({ assistanceId: 'native-wait-B', authHeaders: { Authorization: 'Bearer fictional-B' } })
    oldTicket.respond({ ticket: 'fictional-stale-ticket' }); await flushPromises()
    expect(ObservedSocket.sockets).toHaveLength(0)
    // A synchronous identity watcher can observe aid and auth prop assignments
    // separately. Every intermediate ticket must stay unable to open a socket.
    const currentTickets = network.pending.slice()
    for (const intermediate of currentTickets.slice(0, -1)) {
      intermediate.respond({ ticket: 'fictional-intermediate-ticket' }); await flushPromises()
      expect(ObservedSocket.sockets).toHaveLength(0)
    }
    currentTickets[currentTickets.length - 1].respond({ ticket: 'fictional-current-ticket' }); await flushPromises()
    const socket = ObservedSocket.sockets[0]
    const staleMessage = socket.onmessage!
    staleMessage(new MessageEvent('message', { data: JSON.stringify({ type: 'frame' }) }))
    staleMessage(new MessageEvent('message', { data: new Blob(['fictional-jpeg']) }))
    expect(createURL).toHaveBeenCalledTimes(1)
    await wrapper.setProps({ runId: 'native-run-B', assistanceId: 'native-wait-C' })
    expect(socket.closed).toBe(true)
    staleMessage(new MessageEvent('message', { data: JSON.stringify({ type: 'frame' }) }))
    staleMessage(new MessageEvent('message', { data: new Blob(['stale-jpeg']) }))
    expect(createURL).toHaveBeenCalledTimes(1)
    expect(revokeURL).toHaveBeenCalledWith('blob:fictional-browser-frame')
    expect(network.requests.some(item => new URL(item.url).pathname.endsWith('/cancel'))).toBe(false)
  })

  it('take enables keyboard on the existing connection; detach only releases the observer', async () => {
    const wrapper = mount(BrowserView, { props: props() }); wrappers.push(wrapper)
    network.pending[0].respond({ ticket: 'fictional-current-ticket' }); await flushPromises()
    const socket = ObservedSocket.sockets[0]
    await wrapper.get('[tabindex]').trigger('keydown', { key: 'F' })
    expect(socket.sent).toEqual([])
    await wrapper.setProps({ controlEnabled: true })
    await wrapper.get('[tabindex]').trigger('keydown', { key: 'F' })
    expect(socket.sent.map(value => JSON.parse(value))).toEqual([{ type: 'keyboard', key: 'F' }])
    expect(network.requests).toHaveLength(1)
    wrapper.unmount(); wrappers = []
    expect(socket.closed).toBe(true)
    expect(network.requests.some(item => new URL(item.url).pathname.endsWith('/cancel'))).toBe(false)
  })
})
