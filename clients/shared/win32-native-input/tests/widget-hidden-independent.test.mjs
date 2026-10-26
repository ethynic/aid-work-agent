import assert from 'node:assert/strict'
import test from 'node:test'
import { performPhysicalInput } from '../dist/index.js'

// Independent Win32 boundary: no native FFI is created and no real input is sent.
function world() {
  const state = { dpi: -1, foreground: 10, cursor: { x: 0, y: 0 },
    children: [], visible: new Set([10]), enumerations: 0, events: [], sleeps: [], logs: [] }
  const api = {
    setThreadDpiAwarenessContext(value) { const old = state.dpi; state.dpi = value; return old },
    getThreadDpiAwarenessContext: () => state.dpi,
    areDpiAwarenessContextsEqual: (a, b) => a === b,
    getCurrentThreadId: () => 1,
    getWindowRect: (id) => id === 10 ? { left: 0, top: 0, right: 1400, bottom: 1000 }
      : { left: 100, top: 80, right: 1300, bottom: 880 },
    getPhysicalCursorPos: () => ({ ...state.cursor }),
    setCursorPos(x, y) { state.cursor = { x, y }; return true },
    windowFromPoint: () => state.children.find(id => state.visible.has(id)) ?? 0,
    enumWindows(visit) { visit(10); return true },
    enumChildWindows(parent, visit) { assert.equal(parent, 10); state.enumerations++; state.children.forEach(visit); return true },
    isWindowVisible: (id) => state.visible.has(id),
    getWindowText: () => 'BOSS synthetic',
    getClassName: (id) => id === 10 ? 'Chrome_WidgetWin_1' : 'Chrome_RenderWidgetHostHWND',
    setForegroundWindow(id) { state.foreground = id; return true },
    bringWindowToTop: () => true,
    getForegroundWindow: () => state.foreground,
    getWindowThreadProcessId: () => ({ thread: 1, pid: 123 }),
    attachThreadInput: () => true,
    isIconic: () => false,
    showWindow: () => true,
    setWindowPos: () => true,
    getAncestor: (id) => id === 10 ? 10 : 10,
    getWindow: () => 0,
    mouseEventLeftDown() { state.events.push('down') },
    mouseEventLeftUp() { state.events.push('up') },
    typeUnicode() { state.events.push('type') },
    mouseEventWheel() { state.events.push('wheel') },
    snapshotProcesses: () => [{ pid: 123, exeName: 'chrome.exe' }],
    readProcessCommandLine: () => 'chrome --remote-debugging-port=9222',
    openInputDesktop: () => 1,
    closeDesktop: () => true,
  }
  const context = { api, sleep: async (ms) => { state.sleeps.push(ms) }, log: line => state.logs.push(line) }
  const request = { x: 300, y: 200, cssW: 1200, cssH: 800, cdpPort: 9222 }
  return { state, context, request }
}

test('independent: absent widget is re-enumerated after activation, then exactly one business input is delivered', async () => {
  const { state, context, request } = world()
  context.sleep = async ms => {
    state.sleeps.push(ms)
    if (ms === 200) { state.children = [21]; state.visible.add(21) }
  }
  await performPhysicalInput(context, request)
  assert.equal(state.enumerations, 2)
  assert.deepEqual(state.events, ['down', 'up'])
  assert.deepEqual(state.cursor, { x: 400, y: 280 })
  assert.equal(state.dpi, -1)
})

test('independent: permanent missing widget refuses with bounded waits and no physical events', async () => {
  const { state, context, request } = world()
  await assert.rejects(performPhysicalInput(context, request), error => error.exitCode === 2
    && /render widget unavailable; input refused/.test(error.message)
    && !error.message.includes('BOSS_INPUT_UNKNOWN'))
  assert.equal(state.enumerations, 7)
  assert.equal(state.sleeps.filter(ms => ms === 200).length, 6)
  assert.deepEqual(state.events, [])
  assert.equal(state.dpi, -1)
})

test('independent: defunct hidden widget cannot prevent discovery of the valid visible widget behind it', async () => {
  const { state, context, request } = world()
  state.children = [30, 31]
  state.visible.add(31)
  const readRect = context.api.getWindowRect
  context.api.getWindowRect = id => id === 30 ? { left: 0, top: 0, right: 0, bottom: 0 } : readRect(id)
  await performPhysicalInput(context, request)
  assert.deepEqual(state.events, ['down', 'up'])
  assert.equal(state.enumerations, 1)
})

test('independent: cancellation during new widget wait cannot deliver a click even if a widget becomes available', async () => {
  const { state, context, request } = world()
  const abort = new AbortController()
  context.signal = abort.signal
  context.sleep = async ms => {
    state.sleeps.push(ms)
    if (ms === 200) { state.children = [22]; state.visible.add(22); abort.abort() }
  }
  await assert.rejects(performPhysicalInput(context, request), error => error.exitCode === 2
    && !error.message.includes('BOSS_INPUT_UNKNOWN'))
  assert.equal(state.enumerations, 1)
  assert.deepEqual(state.events, [])
  assert.equal(state.dpi, -1)
})

test('independent: explicit target resolver remains one-shot and does not opt into Chrome discovery retries', async () => {
  const { state, context, request } = world()
  let resolutions = 0
  const custom = { ...request, cdpPort: undefined, resolveTarget() { resolutions++; return { win: 10, rw: 29 } } }
  await assert.rejects(performPhysicalInput(context, custom), /不可见/)
  assert.equal(resolutions, 1)
  assert.equal(state.enumerations, 0)
  assert.equal(state.sleeps.filter(ms => ms === 200).length, 0)
  assert.deepEqual(state.events, [])
})

test('independent: foreground ownership loss while waiting still refuses all input', async () => {
  const { state, context, request } = world()
  context.api.getAncestor = id => id === 99 ? 99 : 10
  context.api.getWindowThreadProcessId = id => ({ thread: 1, pid: id === 99 ? 999 : 123 })
  context.sleep = async ms => {
    state.sleeps.push(ms)
    if (ms === 200) { state.children = [24]; state.visible.add(24); state.foreground = 99 }
  }
  await assert.rejects(performPhysicalInput(context, request), error => error.exitCode === 2
    && /not foreground/.test(error.message))
  assert.deepEqual(state.events, [])
  assert.equal(state.enumerations, 1)
})
