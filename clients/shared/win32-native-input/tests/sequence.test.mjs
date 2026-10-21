/**
 * 单元测试：mock NativeApi（内存 fake Win32 世界），覆盖
 * 调用序列 / 守卫拒绝（遮挡、光标失配、几何变化、路径遮挡、进程数≠1）/
 * BOSS_INPUT_UNKNOWN 会计边界 / 坐标换算（含银行家舍入）/ DPI 包裹 /
 * 计时输出 / 逐字键入（中文+emoji 代理项）/ 绑定模式窗口唯一性 / 桌面探测。
 * 不发任何真实输入；真实输入测试见 tests/real/（AID_WIN32_REAL_INPUT=1 门控）。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import {
  DpiPhysicalScope,
  findBrowserPidByDebugPort,
  NativeInputError,
  performPhysicalInput,
  probeInputDesktop,
  psInt,
  boundFind,
} from '../dist/index.js'

// ---- fake Win32 世界 ----

let nextHwnd = 0x100

function makeFakeWorld(options = {}) {
  const state = {
    windows: new Map(), // hwnd -> { hwnd, parent, title, cls, visible, minimized, rect, pid, z }
    cursor: { x: 0, y: 0 },
    foreground: 0,
    dpi: -1, // 进程默认 DPI_AWARENESS_CONTEXT_UNAWARE(-1)
    thread: 4242,
    typed: [],
    wheels: [],
    calls: [],
    sleeps: [],
    chrome: options.chrome ?? [],
    refuseDpiEnter: options.refuseDpiEnter ?? false,
    refuseDpiRestore: options.refuseDpiRestore ?? false,
    loseDpiContextAt: null, // 第 N 次 setCursorPos 后外部丢失 DPI 上下文
    jitterAtRead: null, // 第 N 次光标回读时被用户挪走 1px（悬停期间碰鼠标）
    onBeforeDragStep: null,
    setCursorPosCount: 0,
    desktopHandle: 0x9999,
    closedDesktops: [],
  }
  const topZ = () => Math.max(0, ...[...state.windows.values()].map((w) => w.z)) + 1

  const api = {
    setThreadDpiAwarenessContext(context) {
      state.calls.push(`dpi_set(${context})`)
      if (context === -4 && state.refuseDpiEnter) return 0
      if (context !== -4 && state.refuseDpiRestore) return 0 // 恢复原值失败
      const previous = state.dpi
      state.dpi = context
      return previous === 0 ? 1 : previous
    },
    getThreadDpiAwarenessContext() {
      return state.dpi
    },
    areDpiAwarenessContextsEqual(a, b) {
      return a === b
    },
    getCurrentThreadId() {
      return state.thread
    },
    getWindowRect(hwnd) {
      const w = state.windows.get(hwnd)
      return w ? { ...w.rect } : undefined
    },
    getPhysicalCursorPos() {
      state.readCount = (state.readCount ?? 0) + 1
      // 第 N 次回读时光标被用户挪走 1px（发生在 move 的回读校验之后/悬停期间）
      if (state.jitterAtRead === state.readCount) return { x: state.cursor.x + 1, y: state.cursor.y }
      return { ...state.cursor }
    },
    setCursorPos(x, y) {
      state.setCursorPosCount++
      state.calls.push(`setCursor(${x},${y})`)
      state.cursor = { x, y }
      if (state.loseDpiContextAt === state.setCursorPosCount) state.dpi = -1
      return true
    },
    windowFromPoint(x, y) {
      // 有效 Z：取根窗（父子同栈：抬父=抬整个家族）；topmost 永远在普通窗之上
      const rootOf = (w) => {
        let cur = w
        while (cur && cur.parent) cur = state.windows.get(cur.parent)
        return cur ?? w
      }
      let best = 0
      let bestRank = -1
      let bestDepth = -1
      for (const w of state.windows.values()) {
        if (!w.visible) continue
        const r = w.rect
        if (x < r.left || x >= r.right || y < r.top || y >= r.bottom) continue
        let depth = 0
        for (let p = w.parent; p; p = state.windows.get(p)?.parent) depth++
        const rank = (w.topmost ? 1e9 : 0) + (rootOf(w)?.z ?? 0)
        // 有效 Z 优先（topmost/根窗更高者胜出）；同栈时更深的（子窗）优先
        if (rank > bestRank || (rank === bestRank && depth > bestDepth)) {
          best = w.hwnd
          bestRank = rank
          bestDepth = depth
        }
      }
      return best
    },
    enumWindows(visit) {
      for (const w of [...state.windows.values()].sort((a, b) => a.z - b.z)) {
        if (!visit(w.hwnd)) return true
      }
      return true
    },
    enumChildWindows(parent, visit) {
      for (const w of [...state.windows.values()].filter((w) => w.parent === parent)) {
        if (!visit(w.hwnd)) return true
      }
      return true
    },
    isWindowVisible(hwnd) {
      return state.windows.get(hwnd)?.visible ?? false
    },
    getWindowText(hwnd) {
      return state.windows.get(hwnd)?.title ?? ''
    },
    getClassName(hwnd) {
      return state.windows.get(hwnd)?.cls ?? ''
    },
    setForegroundWindow(hwnd) {
      state.calls.push(`setForeground(${hwnd})`)
      state.foreground = hwnd
      // 强制前台完成后的弹窗/提示窗（真实场景：前台化之后新窗口出现抢占落点）
      if (state.onActivated) {
        const hook = state.onActivated
        state.onActivated = null
        hook()
      }
      return true
    },
    bringWindowToTop(hwnd) {
      state.calls.push(`bringToTop(${hwnd})`)
      const w = state.windows.get(hwnd)
      if (w) w.z = topZ()
      return true
    },
    getForegroundWindow() {
      return state.foreground
    },
    getWindowThreadProcessId(hwnd) {
      const w = state.windows.get(hwnd)
      const pid = w?.pid ?? 0
      return { thread: pid + 1, pid }
    },
    attachThreadInput(current, target, attach) {
      state.calls.push(`attach(${current},${target},${attach ? 1 : 0})`)
      return true
    },
    isIconic(hwnd) {
      return state.windows.get(hwnd)?.minimized ?? false
    },
    showWindow(hwnd) {
      state.calls.push(`showWindow(${hwnd},9)`)
      const w = state.windows.get(hwnd)
      if (w) {
        w.minimized = false
        w.visible = true
      }
      return true
    },
    setWindowPos(hwnd) {
      state.calls.push(`setWindowPos(${hwnd},TOP)`)
      const w = state.windows.get(hwnd)
      if (w) w.z = topZ()
      return true
    },
    getAncestor(hwnd) {
      let w = state.windows.get(hwnd)
      while (w && w.parent) w = state.windows.get(w.parent)
      return w ? w.hwnd : 0
    },
    getWindow(hwnd, command) {
      // GetWindow(hwnd, 4) = GW_OWNER（C# BelongsTo 的 owner 链）
      if (command !== 4) return 0
      return state.windows.get(hwnd)?.owner ?? 0
    },
    mouseEventLeftDown() {
      state.calls.push('LEFTDOWN')
      // 拖动阶段开始前注入的几何变化钩子
      if (state.onBeforeDragStep) {
        const hook = state.onBeforeDragStep
        state.onBeforeDragStep = null
        hook()
      }
    },
    mouseEventLeftUp() {
      state.calls.push('LEFTUP')
    },
    mouseEventWheel(deltaY) {
      // fake 层记录有符号值（-120=向下）；uint wrap 是 koffi 绑定层的责任
      state.calls.push(`WHEEL(${deltaY})`)
      state.wheels.push(deltaY)
    },
    typeUnicode(codeUnit) {
      state.calls.push(`typed(0x${codeUnit.toString(16)})`)
      state.typed.push(codeUnit)
    },
    snapshotProcesses() {
      return state.chrome.map((entry) => ({ pid: entry.pid, exeName: entry.exeName }))
    },
    readProcessCommandLine(pid) {
      return state.chrome.find((entry) => entry.pid === pid)?.commandLine
    },
    openInputDesktop() {
      return state.desktopHandle
    },
    closeDesktop(handle) {
      state.closedDesktops.push(handle)
      return true
    },
  }

  function addWindow({ parent = 0, owner = 0, title = '', cls = 'StdWnd', visible = true, minimized = false, rect, pid = 7000, z, topmost = false }) {
    const hwnd = ++nextHwnd
    state.windows.set(hwnd, { hwnd, parent, owner, title, cls, visible, minimized, rect, pid, z: z ?? topZ(), topmost })
    return hwnd
  }

  const sleep = async (ms) => {
    state.sleeps.push(ms)
  }

  return { state, api, addWindow, sleep }
}

/** 标准场景：BOSS 主窗（未绑定路径）+ render widget + 干净落点 */
function standardWorld(extra = {}) {
  const world = makeFakeWorld(extra)
  const win = world.addWindow({ title: 'BOSS直聘 - Google Chrome', rect: { left: 0, top: 0, right: 1920, bottom: 1080 }, pid: 7000 })
  const rw = world.addWindow({
    parent: win,
    cls: 'Chrome_RenderWidgetHostHWND',
    rect: { left: 0, top: 40, right: 1920, bottom: 1080 },
    pid: 7000,
  })
  world.state.foreground = win
  return { world, win, rw }
}

const VP = { cssW: 1917, cssH: 1039 }

async function runClick(world, request = {}, log = () => {}) {
  return performPhysicalInput(
    { api: world.api, sleep: world.sleep, log },
    { x: 958, y: 500, cssW: VP.cssW, cssH: VP.cssH, op: 'click', ...request },
  )
}

// ---- psInt：PowerShell [int] 银行家舍入 ----

test('psInt 与 PowerShell [int] 一致（.5 取偶）', () => {
  assert.equal(psInt(2.5), 2)
  assert.equal(psInt(3.5), 4)
  assert.equal(psInt(1.4), 1)
  assert.equal(psInt(1.6), 2)
  assert.equal(psInt(0.5), 0)
  assert.equal(psInt(1.5), 2)
  assert.equal(psInt(-0.5), 0)
})

// ---- 坐标换算 ----

test('坐标换算：widget 物理矩形按 CssW/CssH 线性缩放（150% DPI 场景）', async () => {
  // rw: {0,40,1920,1080}（vw=1920 vh=1040），cssW=1917 → px=1917 映射到 x=1920
  const { world, rw } = standardWorld()
  const output = []
  const result = await runClick(world, {}, (line) => output.push(line))
  const target = result.output.find((line) => line.startsWith('目标屏幕坐标'))
  // x = 0 + 958 * 1920/1917 = 959.49... → 959；y = 40 + 500 * 1040/1039 = 540.48 → 540
  assert.equal(target, '目标屏幕坐标: (959, 540)')
  assert.equal(world.state.calls.at(-1), 'dpi_set(-1)', '最后一步恢复 DPI 上下文')
  assert.equal(world.state.calls.at(-2), 'LEFTUP')
  assert.ok(world.state.calls.includes('LEFTDOWN'))
})

// ---- 调用序列（拟人移动 / 悬停 / 按下 / 释放 / DPI 包裹）----

test('点击调用序列：DPI 包裹 → 强制前台 → 10 步拟人移动 → 悬停 → 守卫 → 按下 → 释放 → 恢复 DPI', async () => {
  const { world, rw } = standardWorld()
  const logs = []
  await runClick(world, {}, (line) => logs.push(line))
  const calls = world.state.calls
  assert.equal(calls[0], 'dpi_set(-4)')
  assert.equal(calls.at(-1), 'dpi_set(-1)', '最终恢复原上下文')
  // 非绑定强制前台：attach → bringToTop → setForeground → detach
  const attachIndex = calls.findIndex((c) => c.startsWith('attach('))
  assert.ok(attachIndex >= 0)
  assert.ok(calls.includes('bringToTop(257)') || calls.some((c) => /^bringToTop\(\d+\)$/.test(c)))
  assert.ok(calls.includes('setForeground(257)') || calls.some((c) => /^setForeground\(\d+\)$/.test(c)))
  assert.match(calls[attachIndex], /,1\)$/)
  assert.match(calls[attachIndex + 1], /^bringToTop/)
  assert.match(calls[attachIndex + 3], /,0\)$/)
  // 10 步移动 + 每步 25ms + 500ms 悬停
  const moves = calls.filter((c) => c.startsWith('setCursor('))
  assert.equal(moves.length, 10)
  assert.deepEqual(world.state.sleeps.slice(0, 12), [400, 25, 25, 25, 25, 25, 25, 25, 25, 25, 25, 500])
  // 按下 → 70ms → 释放
  assert.ok(calls.indexOf('LEFTDOWN') < calls.indexOf('LEFTUP'))
  assert.equal(world.state.sleeps.at(-1), 70)
  // 计时单行
  const perf = logs.find((line) => line.startsWith('[boss-perf]'))
  assert.ok(perf, '输出 [boss-perf] 单行')
  assert.match(perf, /^\[boss-perf\] tool=win32-native-input op=click ok=1 total_ms=\d+ activate_ms=\d+ discover_ms=\d+ guard_ms=\d+ move_ms=\d+ press_ms=\d+/)
})

test('移动插值从当前光标出发逐步逼近目标', async () => {
  const { world } = standardWorld()
  world.state.cursor = { x: 0, y: 0 }
  await runClick(world)
  const moves = world.state.calls.filter((c) => c.startsWith('setCursor(')).map((c) => c.match(/\((\d+),(\d+)\)/).slice(1, 3).map(Number))
  assert.deepEqual(moves[0], [96, 54], '第 1 步 = 起点 + 1/10')
  assert.deepEqual(moves[9], [959, 540], '第 10 步 = 目标点')
})

// ---- 落点守卫：遮挡 → 抬窗重试一次 → 仍遮挡 exit 2 ----

test('落点被遮挡：抬窗重试后仍遮挡 → REFUSED exit 2，绝不按下', async () => {
  const { world, win } = standardWorld()
  // 顶置遮挡窗盖住目标点（HWND_TOP 抬主窗也无法越过更高的 z）
  world.addWindow({ title: '会话终端', rect: { left: 900, top: 500, right: 1100, bottom: 700 }, z: 1, topmost: true })
  const logs = []
  await assert.rejects(runClick(world, {}, (line) => logs.push(line)), (error) => {
    assert.ok(error instanceof NativeInputError)
    assert.equal(error.exitCode, 2)
    assert.equal(error.kind, 'REFUSED')
    assert.match(error.message, /落点仍被遮挡/)
    assert.match(error.message, /cursor_match=0; ownership_match=0/)
    assert.match(error.message, /widget=0x[0-9a-f]+; hit=0x[0-9a-f]+; foreground=0x[0-9a-f]+/)
    return true
  })
  assert.ok(!world.state.calls.includes('LEFTDOWN'), '守卫拒绝绝不盲点')
  assert.ok(world.state.calls.some((c) => c.startsWith('setWindowPos(')), '抬窗重试过一次')
  assert.equal(world.state.calls.filter((c) => c.startsWith('setWindowPos(')).length, 1, '只重试一次')
  assert.ok(world.state.sleeps.includes(300))
  const guard = logs.find((line) => line.startsWith('[boss-input-guard]'))
  assert.ok(guard, '输出守卫取证行')
  assert.match(guard, /kind=occlusion op=click/)
})

test('落点被遮挡：抬窗重试成功 → 继续点击', async () => {
  const { world, win } = standardWorld()
  // 前台化之后才出现的普通弹窗（z 高于已抬的目标）；HWND_TOP 再抬一次即可恢复命中
  world.state.onActivated = () => {
    world.addWindow({ title: '悬浮窗', rect: { left: 900, top: 500, right: 1100, bottom: 700 } })
  }
  const logs = []
  await runClick(world, {}, (line) => logs.push(line))
  assert.ok(world.state.calls.includes('LEFTDOWN'), '重试通过后正常点击')
  assert.ok(world.state.calls.some((c) => c.startsWith('setWindowPos(')))
})

test('落点守卫通过时不抬窗', async () => {
  const { world } = standardWorld()
  await runClick(world)
  assert.ok(!world.state.calls.some((c) => c.startsWith('setWindowPos(')))
})

// ---- 光标守卫：悬停后光标被挪走 → 拒绝 ----

test('悬停后用户挪动光标 → 光标守卫拒绝（两项谓词），不按下', async () => {
  const { world } = standardWorld()
  world.state.jitterAtRead = 12 // 第 12 次回读 = 悬停后 RequireCursorOnWidget 的读
  await assert.rejects(runClick(world), (error) => {
    assert.ok(error instanceof NativeInputError)
    assert.equal(error.exitCode, 1)
    assert.notEqual(error.kind, 'BOSS_INPUT_UNKNOWN', '输入开始前的失败不是效果未知')
    assert.match(error.message, /Cursor or render-widget ownership changed/)
    assert.match(error.message, /cursor_match=0/)
    assert.match(error.message, /expected_screen=\(959,540\)/)
    assert.match(error.message, /actual_cursor=\(960,540\)/)
    return true
  })
  assert.ok(!world.state.calls.includes('LEFTDOWN'))
})

// ---- 拖动 ----

test('拖动：16 步插值 + 双端点几何校验 + 路径守卫 + finally 释放', async () => {
  const { world } = standardWorld()
  world.state.cursor = { x: 958, y: 500 }
  const result = await performPhysicalInput(
    { api: world.api, sleep: world.sleep, log: () => {} },
    { x: 500, y: 600, cssW: VP.cssW, cssH: VP.cssH, dragX: 1000, dragY: 600, op: 'drag' },
  )
  assert.ok(result.output.includes('已拖动'))
  const calls = world.state.calls
  assert.ok(calls.includes('LEFTDOWN') && calls.includes('LEFTUP'))
  // 拖动期间 16 次额外移动
  assert.equal(calls.filter((c) => c.startsWith('setCursor(')).length, 10 + 16)
  assert.ok(world.state.sleeps.includes(70) && world.state.sleeps.filter((s) => s === 25).length >= 16)
})

test('拖动期间窗口几何变化 → BOSS_INPUT_UNKNOWN（输入已开始）且左键已释放', async () => {
  const { world, rw } = standardWorld()
  world.state.onBeforeDragStep = () => {
    world.state.windows.get(rw).rect = { left: 0, top: 40, right: 1024, bottom: 768 }
    world.state.onBeforeDragStep = null
  }
  await assert.rejects(
    performPhysicalInput(
      { api: world.api, sleep: world.sleep, log: () => {} },
      { x: 500, y: 600, cssW: VP.cssW, cssH: VP.cssH, dragX: 600, dragY: 600, op: 'drag' },
    ),
    (error) => {
      assert.ok(error instanceof NativeInputError)
      assert.equal(error.kind, 'BOSS_INPUT_UNKNOWN')
      assert.equal(error.exitCode, 1)
      assert.match(error.message, /\[BOSS_INPUT_UNKNOWN\] 拖动期间窗口几何变化/)
      return true
    },
  )
  assert.ok(world.state.calls.includes('LEFTDOWN') && world.state.calls.includes('LEFTUP'), 'finally 释放左键')
})

test('拖动终点被遮挡 → 按下前拒绝（BOSS_INPUT_UNKNOWN 会计边界：MarkInputStarted 之后）', async () => {
  const { world } = standardWorld()
  // topmost 面板遮挡拖动终点区域（抬窗也压不过 topmost）
  world.addWindow({ title: '面板', rect: { left: 1000, top: 500, right: 1200, bottom: 700 }, z: 1, topmost: true })
  await assert.rejects(
    performPhysicalInput(
      { api: world.api, sleep: world.sleep, log: () => {} },
      { x: 500, y: 600, cssW: VP.cssW, cssH: VP.cssH, dragX: 1000, dragY: 600, op: 'drag' },
    ),
    (error) => {
      assert.match(error.message, /\[BOSS_INPUT_UNKNOWN\] 拖动终点被遮挡/)
      assert.equal(error.kind, 'BOSS_INPUT_UNKNOWN')
      return true
    },
  )
  assert.ok(!world.state.calls.includes('LEFTDOWN'), '终点校验在按下之前，不产生按下')
})

test('非法拖动参数：仅一端坐标 → BOSS_INPUT_UNKNOWN（ps1 在 MarkInputStarted 之后校验）', async () => {
  const { world } = standardWorld()
  await assert.rejects(
    performPhysicalInput(
      { api: world.api, sleep: world.sleep, log: () => {} },
      { x: 500, y: 600, cssW: VP.cssW, cssH: VP.cssH, dragX: 600, op: 'drag' },
    ),
    (error) => error instanceof NativeInputError && error.kind === 'BOSS_INPUT_UNKNOWN' && /拖动终点必须同时提供/.test(error.message),
  )
  assert.ok(!world.state.calls.includes('LEFTDOWN'))
})

// ---- clickAndType 逐字键入 ----

test('clickAndType：中文+emoji 按 UTF-16 code unit 逐字键入（代理项成对发送）', async () => {
  const { world } = standardWorld()
  const text = '你好😊a'
  const result = await performPhysicalInput(
    { api: world.api, sleep: world.sleep, log: () => {} },
    { x: 500, y: 600, cssW: VP.cssW, cssH: VP.cssH, text, op: 'clickAndType' },
  )
  assert.deepEqual(world.state.typed, ['你'.charCodeAt(0), '好'.charCodeAt(0), 0xd83d, 0xde0a, 'a'.charCodeAt(0)])
  assert.ok(result.output.includes('已输入 5 字'), 'ps1 语义按 code unit 计数')
  // 顺序（ps1）：LEFTUP 先于任何键入——按住左键输入会变成拖选
  const calls = world.state.calls
  const firstTyped = calls.findIndex((c) => c.startsWith('typed('))
  const lastTyped = calls.findLastIndex((c) => c.startsWith('typed('))
  assert.ok(firstTyped > 0 && lastTyped > 0, '有键入')
  assert.ok(calls.indexOf('LEFTUP') < firstTyped, 'LEFTUP 在首个键入之前')
  assert.ok(calls.indexOf('LEFTUP') > -1, '已释放左键')
  const sleeps = world.state.sleeps
  const typeStart = sleeps.lastIndexOf(500)
  assert.ok(typeStart >= 0, '点击后 500ms 聚焦稳定（type 阶段的 500ms）')
  assert.deepEqual(sleeps.slice(typeStart + 1), [200, 200, 200, 200, 200], '每字 200ms 拟人节奏')
  assert.equal(sleeps[typeStart - 1], 70, '紧随按下 70ms')
})

test('clickAndType：点击聚焦失败路径不受键入影响（文本为空则纯点击）', async () => {
  const { world } = standardWorld()
  await performPhysicalInput(
    { api: world.api, sleep: world.sleep, log: () => {} },
    { x: 500, y: 600, cssW: VP.cssW, cssH: VP.cssH, text: '', op: 'click' },
  )
  assert.equal(world.state.typed.length, 0)
})

// ---- 绑定模式 ----

function boundChromeWorld(processes) {
  const world = makeFakeWorld({ chrome: processes })
  const win = world.addWindow({ title: 'BOSS直聘 - Google Chrome', cls: 'Chrome_WidgetWin_1', rect: { left: 0, top: 0, right: 1920, bottom: 1080 }, pid: 3131 })
  const rw = world.addWindow({ parent: win, cls: 'Chrome_RenderWidgetHostHWND', rect: { left: 0, top: 40, right: 1920, bottom: 1080 }, pid: 3131 })
  world.state.foreground = win
  return { world, win, rw }
}

test('绑定模式：唯一调试 Chrome → 绑定找窗 → 前台 → 输入；命令行匹配语义与 ps1 一致', async () => {
  const { world, win } = boundChromeWorld([
    { pid: 3131, exeName: 'chrome.exe', commandLine: '"C:\\Chrome\\chrome.exe" --remote-debugging-port=9333 --user-data-dir=C:\\chrome-debug https://www.zhipin.com' },
    { pid: 3132, exeName: 'chrome.exe', commandLine: '"C:\\Chrome\\chrome.exe" --type=renderer --remote-debugging-port=9333' },
    { pid: 3133, exeName: 'chrome.exe', commandLine: '"C:\\Chrome\\chrome.exe" --remote-debugging-port=19333' },
    { pid: 7001, exeName: 'notepad.exe', commandLine: 'notepad.exe --remote-debugging-port=9333' },
  ])
  const result = await performPhysicalInput(
    { api: world.api, sleep: world.sleep, log: () => {} },
    { x: 958, y: 500, cssW: VP.cssW, cssH: VP.cssH, cdpPort: 9333, op: 'click' },
  )
  assert.ok(result.output.some((line) => line.startsWith('目标屏幕坐标')))
  assert.ok(world.state.calls.includes('LEFTDOWN'))
})

test('绑定模式：调试 Chrome 数量≠1 → STALE_OBSERVATION exit 2，零输入', async () => {
  for (const count of [0, 2]) {
    const processes = Array.from({ length: count }, (_, i) => ({ pid: 3000 + i, exeName: 'chrome.exe', commandLine: `chrome.exe --remote-debugging-port=9333 --profile=${i}` }))
    const { world } = boundChromeWorld(processes)
    await assert.rejects(
      performPhysicalInput(
        { api: world.api, sleep: world.sleep, log: () => {} },
        { x: 100, y: 100, cssW: VP.cssW, cssH: VP.cssH, cdpPort: 9333, op: 'click' },
      ),
      (error) => error instanceof NativeInputError && error.exitCode === 2 && error.kind === 'STALE_OBSERVATION' && /ambiguous/.test(error.message),
    )
    assert.ok(!world.state.calls.includes('LEFTDOWN'))
  }
})

test('绑定模式：无关 chrome.exe 命令行读不到 → 跳过不阻断（WMI null CommandLine 同语义）；全部读不到 → STALE', async () => {
  // 目标唯一可读 + 无关进程读不到：正常执行（评审 P2：不得因无关进程阻断全部绑定输入）
  const { world } = boundChromeWorld([
    { pid: 3131, exeName: 'chrome.exe', commandLine: 'chrome.exe --remote-debugging-port=9333' },
    { pid: 3999, exeName: 'chrome.exe', commandLine: undefined },
  ])
  await performPhysicalInput(
    { api: world.api, sleep: world.sleep, log: () => {} },
    { x: 100, y: 100, cssW: VP.cssW, cssH: VP.cssH, cdpPort: 9333, op: 'click' },
  )
  assert.ok(world.state.calls.includes('LEFTDOWN'), '目标可读时输入应正常执行')

  // 目标自身读不到（唯一 chrome）：0 命中兜底拒绝，零输入
  const unreadable = boundChromeWorld([{ pid: 3131, exeName: 'chrome.exe', commandLine: undefined }])
  await assert.rejects(
    performPhysicalInput(
      { api: unreadable.world.api, sleep: unreadable.world.sleep, log: () => {} },
      { x: 100, y: 100, cssW: VP.cssW, cssH: VP.cssH, cdpPort: 9333, op: 'click' },
    ),
    (error) => error.kind === 'STALE_OBSERVATION' && /ambiguous/.test(error.message),
  )
})

test('绑定模式：输入开始前的窗口/widget 失效 → STALE exit 2；前台被抢 → STALE', async () => {
  // 独立 BOSS 主窗数量≠1
  const { world } = boundChromeWorld([{ pid: 3131, exeName: 'chrome.exe', commandLine: 'chrome.exe --remote-debugging-port=9333' }])
  world.addWindow({ title: 'BOSS直聘 - 另一个窗口', cls: 'Chrome_WidgetWin_1', rect: { left: 0, top: 0, right: 800, bottom: 600 }, pid: 3131 })
  await assert.rejects(
    performPhysicalInput({ api: world.api, sleep: world.sleep, log: () => {} }, { x: 100, y: 100, cssW: VP.cssW, cssH: VP.cssH, cdpPort: 9333, op: 'click' }),
      (error) => error instanceof NativeInputError && error.kind === 'STALE_OBSERVATION' && /found 2|found 0/.test(error.message),
  )
})

test('绑定模式前台被抢（键入中途）→ BOSS_INPUT_UNKNOWN（输入已开始）', async () => {
  const { world, win } = boundChromeWorld([{ pid: 3131, exeName: 'chrome.exe', commandLine: 'chrome.exe --remote-debugging-port=9333' }])
  // 第 2 个字符键入前把前台抢走
  let typed = 0
  const originalType = world.api.typeUnicode.bind(world.api)
  world.api.typeUnicode = (unit) => {
    if (++typed === 2) world.state.foreground = 0
    originalType(unit)
  }
  await assert.rejects(
    performPhysicalInput(
      { api: world.api, sleep: world.sleep, log: () => {} },
      { x: 500, y: 600, cssW: VP.cssW, cssH: VP.cssH, cdpPort: 9333, text: 'abc', op: 'clickAndType' },
    ),
    (error) => error.kind === 'BOSS_INPUT_UNKNOWN' && /Debug Chrome is not foreground/.test(error.message) && error.message.startsWith('[BOSS_INPUT_UNKNOWN]'),
  )
})

test('boundFind：owned 对话框不计入独立主窗（仍唯一返回主窗）', () => {
  const { world, win } = boundChromeWorld([{ pid: 3131, exeName: 'chrome.exe', commandLine: 'x' }])
  // owned 顶层对话框：owner 链指向主窗 → 归属，不计入独立主窗
  world.addWindow({ title: 'BOSS打印', cls: '#32770', rect: { left: 0, top: 0, right: 100, bottom: 100 }, pid: 3131, owner: win })
  assert.equal(boundFind(world.api, 3131), win)
  // 同 pid 无 owner 的另一个 Chrome_WidgetWin_1 → 独立主窗数量 2，拒绝
  world.addWindow({ title: 'BOSS直聘 - 另一个窗口', cls: 'Chrome_WidgetWin_1', rect: { left: 0, top: 0, right: 800, bottom: 600 }, pid: 3131 })
  assert.throws(() => boundFind(world.api, 3131), /found 2/)
})

// ---- DPI 上下文 ----

test('DPI 进入失败 → 直接拒绝，不做任何输入', async () => {
  const { world } = standardWorld({ refuseDpiEnter: true })
  await assert.rejects(runClick(world), /Cannot enter per-monitor-v2 DPI context/)
  assert.ok(!world.state.calls.includes('LEFTDOWN'))
})

test('DPI 上下文中途丢失（拟人移动期间）→ 拒绝且不按下', async () => {
  const { world } = standardWorld()
  world.state.loseDpiContextAt = 5
  await assert.rejects(runClick(world), (error) => {
    assert.match(error.message, /Physical DPI context lost; input refused/)
    assert.notEqual(error.kind, 'BOSS_INPUT_UNKNOWN')
    return true
  })
  assert.ok(!world.state.calls.includes('LEFTDOWN'))
})

test('DPI 恢复失败（输入已开始）→ BOSS_INPUT_UNKNOWN 顶替收敛为 exit 1', async () => {
  const { world } = standardWorld()
  world.state.refuseDpiRestore = true
  world.state.dpi = -1 // 进程原上下文
  await assert.rejects(runClick(world), (error) => {
    assert.ok(error instanceof NativeInputError)
    assert.equal(error.exitCode, 1)
    assert.equal(error.kind, 'BOSS_INPUT_UNKNOWN')
    assert.match(error.message, /\[BOSS_INPUT_UNKNOWN\] Cannot restore DPI context/)
    return true
  })
  assert.ok(world.state.calls.includes('LEFTDOWN'), '点击本体已完成，仅收尾失败')
})

test('DpiPhysicalScope：线程变化拒绝 mark/dispose', () => {
  const { world } = standardWorld()
  const scope = DpiPhysicalScope.enter(world.api)
  world.state.thread = 9999
  assert.throws(() => scope.markInputStarted(), /DPI context thread changed; input refused/)
  assert.throws(() => scope.dispose(), /DPI context thread changed; input must stop/)
})

// ---- 窗口发现 / 最小化 ----

test('窗口最小化（非绑定）：SW_RESTORE + 400ms 后再探查', async () => {
  const { world, win, rw } = standardWorld()
  world.state.windows.get(win).minimized = true
  await runClick(world)
  assert.ok(world.state.calls.some((c) => c.startsWith('showWindow(')))
  assert.equal(world.state.sleeps[0], 400)
})

test('窗口未找到（非绑定）→ exit 1，零输入', async () => {
  const world = makeFakeWorld()
  await assert.rejects(runClick(world), (error) => {
    assert.equal(error.exitCode, 1)
    assert.equal(error.kind, 'FAILED')
    assert.match(error.message, /未找到标题含/)
    return true
  })
})

test('render widget 不可见（非绑定）→ exit 1；主窗在后台提示切前台', async () => {
  const { world, rw } = standardWorld()
  world.state.windows.get(rw).visible = false
  await assert.rejects(runClick(world), (error) => {
    assert.equal(error.exitCode, 1)
    assert.match(error.message, /渲染子窗口不可见/)
    return true
  })
})

test('render widget 选取面积最大者（多个可见 Chrome_RenderWidgetHostHWND）', async () => {
  const world = makeFakeWorld()
  const win = world.addWindow({ title: 'BOSS直聘 - Google Chrome', rect: { left: 0, top: 0, right: 1920, bottom: 1080 } })
  // 先注册一个小的可见 widget（96px 高），真正的大 widget 后注册——面积最大语义
  world.addWindow({ parent: win, cls: 'Chrome_RenderWidgetHostHWND', rect: { left: 0, top: 0, right: 1920, bottom: 96 }, z: 1 })
  const big = world.addWindow({ parent: win, cls: 'Chrome_RenderWidgetHostHWND', rect: { left: 0, top: 40, right: 1920, bottom: 1080 } })
  world.state.foreground = win
  const result = await runClick(world, { x: 958, y: 500 })
  // 命中大 widget：y = 40 + 500*1040/1039 = 540（小 widget 会算出不同的 y）
  assert.equal(result.output.find((l) => l.startsWith('目标屏幕坐标')), '目标屏幕坐标: (959, 540)')
  assert.equal(world.api.windowFromPoint(959, 540), big)
})

test('全部 render widget 隐藏时回退第一个隐藏的并拒绝（不可见）', async () => {
  const { world, rw } = standardWorld()
  world.state.windows.get(rw).visible = false
  await assert.rejects(runClick(world), /渲染子窗口不可见/)
})

// ---- 进程命令行正则语义 ----

test('findBrowserPidByDebugPort：端口边界与 =/空格 两种写法', () => {
  const mk = (cmdline, pid) => ({ pid, exeName: 'chrome.exe', commandLine: cmdline })
  const api = (list) => ({
    snapshotProcesses: () => list.map((e) => ({ pid: e.pid, exeName: e.exeName })),
    readProcessCommandLine: (pid) => list.find((e) => e.pid === pid).commandLine,
  })
  assert.equal(findBrowserPidByDebugPort(api([mk('chrome --remote-debugging-port=9333', 1)]), 9333), 1)
  assert.equal(findBrowserPidByDebugPort(api([mk('chrome --remote-debugging-port 9333', 2)]), 9333), 2)
  // 端口子串（19333）不算匹配：0 命中仍无法证明唯一性 → STALE（ps1 同语义）
  assert.throws(() => findBrowserPidByDebugPort(api([mk('chrome --remote-debugging-port=19333', 3)]), 9333), /ambiguous/)
  assert.throws(() => findBrowserPidByDebugPort(api([mk('chrome --remote-debugging-port 9333x', 4)]), 9333), /ambiguous/)
})

// ---- 桌面探测 ----

test('probeInputDesktop：句柄非 0 → 可交互并关闭；0 → 锁屏', () => {
  const { world } = standardWorld()
  assert.deepEqual(probeInputDesktop(world.api), { interactive: true })
  assert.deepEqual(world.state.closedDesktops, [world.state.desktopHandle])
  world.state.desktopHandle = 0
  assert.deepEqual(probeInputDesktop(world.api), { interactive: false })
})

// ---- 计时行失败也输出 ----

test('守卫拒绝时也输出 ok=0 的计时单行', async () => {
  const { world } = standardWorld()
  world.addWindow({ title: '遮挡', rect: { left: 900, top: 500, right: 1100, bottom: 700 }, z: 1, topmost: true })
  const logs = []
  await assert.rejects(runClick(world, {}, (line) => logs.push(line)))
  const perf = logs.find((line) => line.startsWith('[boss-perf]'))
  assert.ok(perf && perf.includes('ok=0'))
})

// ---- 滚轮（cv-wheel.ps1 1:1 移植）----

async function runWheel(world, request = {}, log = () => {}) {
  return performPhysicalInput(
    { api: world.api, sleep: world.sleep, log },
    { x: 958, y: 500, cssW: VP.cssW, cssH: VP.cssH, op: 'wheel', ...request },
  )
}

test('滚轮序列：单步光标就位 + 每格守卫后注入 WHEEL，格间 150ms，无左键事件', async () => {
  const { world } = standardWorld()
  const result = await runWheel(world, { wheelDeltaY: -120, notches: 3 })
  const calls = world.state.calls
  // 光标就位是单步直移（cv-wheel.ps1 MoveCursor 语义，不做拟人 10 步）
  assert.equal(calls.filter((c) => c.startsWith('setCursor(')).length, 1)
  assert.equal(calls.filter((c) => c === 'WHEEL(-120)').length, 3)
  assert.ok(!calls.includes('LEFTDOWN') && !calls.includes('LEFTUP'))
  // 200ms 就位 + 格间 150ms × (notches-1)，最后一格后不等
  assert.deepEqual(world.state.sleeps, [400, 200, 150, 150])
  assert.ok(result.output.some((line) => line.includes('已滚动 3 格 × -120')))
  assert.equal(calls.at(-1), 'dpi_set(-1)', '最终恢复 DPI 上下文')
})

test('滚轮 wheelDeltaY 自动推断 op（缺省 op 时），notches 缺省 6（cv-wheel.ps1 默认）', async () => {
  const { world } = standardWorld()
  const logs = []
  await performPhysicalInput(
    { api: world.api, sleep: world.sleep, log: (line) => logs.push(line) },
    { x: 958, y: 500, cssW: VP.cssW, cssH: VP.cssH, wheelDeltaY: -120 },
  )
  assert.equal(world.state.wheels.length, 6)
  const perf = logs.find((line) => line.startsWith('[boss-perf]'))
  assert.ok(perf && perf.includes('op=wheel'))
})

test('滚轮守卫：注入后下一格前光标被挪走 → BOSS_INPUT_UNKNOWN（效果未知会计）', async () => {
  const { world } = standardWorld()
  // 回读序：就位校验(1) → 第 1 格守卫(2) → 第 2 格守卫(3)。第 3 次回读被挪走：
  // 第 1 格已注入（MarkInputStarted 已过）、第 2 格守卫失败 → 效果未知会计
  world.state.jitterAtRead = 3
  await assert.rejects(
    runWheel(world, { wheelDeltaY: -120, notches: 3 }),
    (error) => error instanceof NativeInputError && error.exitCode === 1
      && error.kind === 'BOSS_INPUT_UNKNOWN' && error.message.includes('[BOSS_INPUT_UNKNOWN]'),
  )
  assert.equal(world.state.wheels.length, 1, '第 1 格已发出，第 2 格守卫拦截')
})

test('滚轮守卫：首格注入前光标被挪走 → 无 UNKNOWN 前缀（输入未开始，与 ps1 会计一致）', async () => {
  const { world } = standardWorld()
  world.state.jitterAtRead = 2 // 第 1 格守卫（首格注入前）失败
  await assert.rejects(
    runWheel(world, { wheelDeltaY: -120, notches: 3 }),
    (error) => error instanceof NativeInputError && !error.message.includes('[BOSS_INPUT_UNKNOWN]'),
  )
  assert.equal(world.state.wheels.length, 0)
})

test('滚轮参数校验：DeltaY=0 或 notches=0 → FAILED exit 1，零输入', async () => {
  for (const bad of [{ wheelDeltaY: 0 }, { wheelDeltaY: -120, notches: 0 }]) {
    const { world } = standardWorld()
    await assert.rejects(
      runWheel(world, bad),
      (error) => error instanceof NativeInputError && error.exitCode === 1 && error.kind === 'FAILED',
    )
    assert.equal(world.state.wheels.length, 0)
  }
})

test('绑定模式滚轮：唯一调试 Chrome 定位 + 每格前台校验后注入', async () => {
  const { world } = boundChromeWorld([
    { pid: 3131, exeName: 'chrome.exe', commandLine: '"C:\Chrome\chrome.exe" --remote-debugging-port=9333 --user-data-dir=C:\chrome-debug https://www.zhipin.com' },
  ])
  const result = await performPhysicalInput(
    { api: world.api, sleep: world.sleep, log: () => {} },
    { x: 958, y: 500, cssW: VP.cssW, cssH: VP.cssH, cdpPort: 9333, wheelDeltaY: -120, notches: 2 },
  )
  assert.equal(world.state.wheels.length, 2)
  assert.ok(result.output.some((line) => line.includes('已滚动 2 格')))
})
