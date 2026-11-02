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
    openContentViewport: () => ({ readRect() {
      if (state.missingContent) throw new Error('Content document unavailable; input refused')
      return { left: 100, top: 80, right: 1300, bottom: 880 }
    }, close() { state.closed = (state.closed ?? 0) + 1 } }),
    getFocusedWindow: () => state.foreground,
    getPhysicalCursorPos: () => ({ ...state.cursor }),
    setCursorPos(x, y) { state.cursor = { x, y }; return true },
    windowFromPoint: () => state.children.find(id => state.visible.has(id)) ?? 10,
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

test('absent widget does not block a known content point or start discovery retries', async () => {
  const { state, context, request } = world()
  context.sleep = async ms => {
    state.sleeps.push(ms)
    if (ms === 200) { state.children = [21]; state.visible.add(21) }
  }
  await performPhysicalInput(context, request)
  assert.equal(state.enumerations, 0)
  assert.deepEqual(state.events, ['down', 'up'])
  assert.deepEqual(state.cursor, { x: 400, y: 280 })
  assert.equal(state.dpi, -1)
})

test('missing content geometry refuses without input and cannot become execution unknown', async () => {
  const { state, context, request } = world()
  state.missingContent = true
  await assert.rejects(performPhysicalInput(context, request), error => error.exitCode === 2
    && /Content document unavailable; input refused/.test(error.message)
    && !error.message.includes('BOSS_INPUT_UNKNOWN'))
  assert.equal(state.enumerations, 0)
  assert.equal(state.sleeps.filter(ms => ms === 200).length, 0)
  assert.equal(state.closed, 1)
  assert.deepEqual(state.events, [])
  assert.equal(state.dpi, -1)
})

test('defunct hidden widget geometry cannot influence the content point', async () => {
  const { state, context, request } = world()
  state.children = [30, 31]
  state.visible.add(31)
  const readRect = context.api.getWindowRect
  context.api.getWindowRect = id => id === 30 ? { left: 0, top: 0, right: 0, bottom: 0 } : readRect(id)
  await performPhysicalInput(context, request)
  assert.deepEqual(state.events, ['down', 'up'])
  assert.equal(state.enumerations, 0)
})

test('cancellation while moving cannot deliver a click even with valid content geometry', async () => {
  const { state, context, request } = world()
  const abort = new AbortController()
  context.signal = abort.signal
  context.sleep = async ms => {
    state.sleeps.push(ms)
    if (ms === 25) { state.children = [22]; state.visible.add(22); abort.abort() }
  }
  await assert.rejects(performPhysicalInput(context, request), error => error.exitCode === 2
    && !error.message.includes('BOSS_INPUT_UNKNOWN'))
  assert.equal(state.enumerations, 0)
  assert.deepEqual(state.events, [])
  assert.equal(state.dpi, -1)
})

test('explicit content resolver remains one-shot and does not opt into discovery retries', async () => {
  const { state, context, request } = world()
  let resolutions = 0
  state.missingContent = true
  const custom = { ...request, cdpPort: undefined, resolveTarget() { resolutions++; return { win: 10 } } }
  await assert.rejects(performPhysicalInput(context, custom), /Content document unavailable/)
  assert.equal(resolutions, 1)
  assert.equal(state.enumerations, 0)
  assert.equal(state.sleeps.filter(ms => ms === 200).length, 0)
  assert.deepEqual(state.events, [])
})

test('foreground ownership loss while moving still refuses all input', async () => {
  const { state, context, request } = world()
  context.api.getAncestor = id => id === 99 ? 99 : 10
  context.api.getWindowThreadProcessId = id => ({ thread: 1, pid: id === 99 ? 999 : 123 })
  context.sleep = async ms => {
    state.sleeps.push(ms)
    if (ms === 25) { state.children = [24]; state.visible.add(24); state.foreground = 99 }
  }
  await assert.rejects(performPhysicalInput(context, request), error => error.exitCode === 2
    && /not foreground/.test(error.message))
  assert.deepEqual(state.events, [])
  assert.equal(state.enumerations, 0)
})

test('hidden widget is irrelevant to both keyboard typing and wheel input', async () => {
  for (const extra of [{ text: '中😊', op: 'clickAndType' }, { wheelDeltaY: -120, notches: 2, op: 'wheel' }]) {
    const { state, context, request } = world()
    state.children = [21]
    await performPhysicalInput(context, { ...request, ...extra })
    assert.equal(state.enumerations, 0)
    assert.deepEqual(state.events, extra.text ? ['down', 'up', 'type', 'type', 'type'] : ['wheel', 'wheel'])
    assert.equal(state.closed, 1)
  }
})
