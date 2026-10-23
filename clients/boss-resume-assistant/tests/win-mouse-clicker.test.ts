import assert from 'node:assert/strict'
import test from 'node:test'
import { WinMouseClicker, WinClickError } from '../src/main/input/WinMouseClicker.js'
import type { NativeInputSession } from '../src/main/input/WinMouseClicker.js'

const VP = { width: 1917, height: 1905 }

/** 记录 perform 请求的 fake 原生会话（默认成功） */
function recordingSession(requests: object[], impl?: (r: object) => Promise<{ output: string[] }>): NativeInputSession {
  return {
    perform: async (r) => {
      requests.push(r)
      return impl ? impl(r) : { output: ['已点击'] }
    },
  }
}

test('真实拖动同进程传入两端坐标，终点越屏时零输入', async () => {
  const requests: object[] = []
  const clicker = new WinMouseClicker({ nativeOpener: async () => recordingSession(requests) })
  await clicker.drag({ x: 996, y: 392 }, { x: 1208.8, y: 392 }, VP)
  assert.equal(requests.length, 1)
  assert.deepEqual({ ...(requests[0] as object) }, { x: 996, y: 392, cssW: 1917, cssH: 1905, dragX: 1209, dragY: 392, op: 'drag' })
  await assert.rejects(clicker.drag({ x: 996, y: 392 }, { x: 9999, y: 392 }, VP), WinClickError)
  assert.equal(requests.length, 1)
})

test('点击请求坐标取整并带 CssW/CssH 视口尺寸', async () => {
  const requests: object[] = []
  const clicker = new WinMouseClicker({ nativeOpener: async () => recordingSession(requests) })
  await clicker.click({ x: 903.75, y: 640.2 }, VP)
  assert.equal(requests.length, 1)
  assert.deepEqual({ ...(requests[0] as object) }, { x: 904, y: 640, cssW: 1917, cssH: 1905, op: 'click' })
})

test('坐标超出视口 → 拒绝盲点（零输入）', async () => {
  const requests: object[] = []
  const clicker = new WinMouseClicker({ nativeOpener: async () => recordingSession(requests) })
  await assert.rejects(clicker.click({ x: 2000, y: 100 }, VP), WinClickError)
  await assert.rejects(clicker.click({ x: -1, y: 100 }, VP), WinClickError)
  assert.equal(requests.length, 0)
})

test('clickAndType：空文本 / 坐标超视口 → WinClickError，零输入', async () => {
  const requests: object[] = []
  const clicker = new WinMouseClicker({ nativeOpener: async () => recordingSession(requests) })
  await assert.rejects(clicker.clickAndType({ x: 100, y: 100 }, VP, ''), WinClickError)
  await assert.rejects(clicker.clickAndType({ x: 9999, y: 100 }, VP, '你好'), WinClickError)
  assert.equal(requests.length, 0)
})

test('bound physical click, drag and typing activate the exact target before one packaged input and pass its debug port', async () => {
  const events: string[] = []
  const requests: object[] = []
  const clicker = new WinMouseClicker({
    cdpPort: 9333,
    beforeInput: async () => { events.push('activate-original-target') },
    nativeOpener: async () => recordingSession(requests, async (r) => { events.push('input'); assert.equal((r as { cdpPort?: number }).cdpPort, 9333); return { output: [] } }),
  })
  await clicker.click({ x: 10, y: 20 }, VP)
  await clicker.drag({ x: 10, y: 20 }, { x: 20, y: 20 }, VP)
  await clicker.clickAndType({ x: 10, y: 20 }, VP, 'test')
  assert.deepEqual(events, Array(3).fill(['activate-original-target', 'input']).flat())
  await assert.rejects(clicker.click({ x: -1, y: 20 }, VP), WinClickError)
  assert.equal(events.length, 6, 'invalid intent cannot activate or input')
})

test('a failed target activation is a proven zero-input guard failure for every physical action', async () => {
  let inputs = 0
  const clicker = new WinMouseClicker({
    cdpPort: 9333,
    beforeInput: async () => { throw new Error('original tab closed') },
    nativeOpener: async () => recordingSession([], async () => { inputs++; return { output: [] } }),
  })
  for (const action of [() => clicker.click({ x: 10, y: 20 }, VP), () => clicker.drag({ x: 10, y: 20 }, { x: 20, y: 20 }, VP), () => clicker.clickAndType({ x: 10, y: 20 }, VP, 'test')]) {
    await assert.rejects(action(), (error: unknown) => error instanceof WinClickError && error.exitCode === 2 && error.message.includes('STALE_OBSERVATION'))
  }
  assert.equal(inputs, 0)
  assert.throws(() => new WinMouseClicker({ cdpPort: 9333 }), WinClickError)
})

// ---- native 唯一通道：参数 / 错误映射 / 加载失败 fail-loud ----

test('默认即原生通道：click/drag/clickAndType 请求形状正确', async () => {
  const requests: object[] = []
  const clicker = new WinMouseClicker({ nativeOpener: async () => recordingSession(requests) })
  await clicker.click({ x: 903.75, y: 640.2 }, VP)
  await clicker.drag({ x: 10, y: 10 }, { x: 20.8, y: 20 }, VP)
  await clicker.clickAndType({ x: 1016, y: 1195 }, VP, '你好')
  assert.equal(requests.length, 3)
  assert.deepEqual(requests.map((r) => ({ ...(r as Record<string, unknown>) })), [
    { x: 904, y: 640, cssW: 1917, cssH: 1905, op: 'click' },
    { x: 10, y: 10, cssW: 1917, cssH: 1905, dragX: 21, dragY: 20, op: 'drag' },
    { x: 1016, y: 1195, cssW: 1917, cssH: 1905, text: '你好', op: 'clickAndType' },
  ])
})

test('绑定模式传 cdpPort 且先激活原标签', async () => {
  const events: string[] = []
  const session: NativeInputSession = { perform: async (r) => { events.push(`input:${(r as { cdpPort?: number }).cdpPort ?? 'none'}`); return { output: [] } } }
  const clicker = new WinMouseClicker({
    cdpPort: 9333,
    beforeInput: async () => { events.push('activate') },
    nativeOpener: async () => session,
  })
  await clicker.click({ x: 10, y: 20 }, VP)
  await clicker.clickAndType({ x: 10, y: 20 }, VP, 't')
  assert.deepEqual(events, ['activate', 'input:9333', 'activate', 'input:9333'])
})

test('原生守卫拒绝（exit 2）与效果未知（exit 1）映射为 WinClickError', async () => {
  const mk = (name: string, exitCode: 1 | 2, message: string) => ({ perform: async () => { throw Object.assign(new Error(message), { name, exitCode }) } })
  const refused = new WinMouseClicker({ nativeOpener: async () => mk('NativeInputError', 2, '落点仍被遮挡') })
  await assert.rejects(refused.click({ x: 1, y: 1 }, VP), (e: unknown) => {
    assert.ok(e instanceof WinClickError)
    assert.equal(e.exitCode, 2)
    assert.match(e.message, /落点仍被遮挡/)
    return true
  })
  const unknown = new WinMouseClicker({ nativeOpener: async () => mk('NativeInputError', 1, '[BOSS_INPUT_UNKNOWN] 拖动期间窗口几何变化') })
  await assert.rejects(unknown.click({ x: 1, y: 1 }, VP), (e: unknown) => e instanceof WinClickError && e.exitCode === 1 && e.message.includes('[BOSS_INPUT_UNKNOWN]'))
  const other = new WinMouseClicker({ nativeOpener: async () => ({ perform: async () => { throw new Error('koffi X') } }) })
  await assert.rejects(other.click({ x: 1, y: 1 }, VP), (e: unknown) => e instanceof WinClickError && e.exitCode === undefined && /原生输入通道失败/.test(e.message))
})

test('原生加载失败 → fail-loud 抛 WinClickError（无 PowerShell 回退），失败缓存放大重试', async () => {
  let opens = 0
  const clicker = new WinMouseClicker({
    nativeOpener: async () => { opens++; throw new Error('koffi 加载失败: DLL') },
  })
  for (const _ of [1, 2]) {
    await assert.rejects(clicker.click({ x: 5, y: 5 }, VP), (e: unknown) => {
      assert.ok(e instanceof WinClickError)
      assert.match(e.message, /原生输入通道不可用（无 PowerShell 回退）: koffi 加载失败: DLL/)
      return true
    })
  }
  assert.equal(opens, 1, '失败会话同样缓存，不重复尝试加载')
})

test('AID_BOSS_INPUT 环境变量不再影响通道（双通道已删除，恒为 native）', async () => {
  const previous = process.env.AID_BOSS_INPUT
  process.env.AID_BOSS_INPUT = 'powershell'
  try {
    const requests: object[] = []
    const clicker = new WinMouseClicker({ nativeOpener: async () => recordingSession(requests) })
    await clicker.click({ x: 1, y: 1 }, VP)
    assert.equal(requests.length, 1, '仍走原生会话，环境变量被忽略')
  } finally {
    if (previous === undefined) delete process.env.AID_BOSS_INPUT
    else process.env.AID_BOSS_INPUT = previous
  }
})

// ---- 滚轮（native）：参数 / 校验 / 绑定激活 ----

test('wheel 原生请求形状带 wheelDeltaY/notches 与 cdpPort', async () => {
  const requests: object[] = []
  const clicker = new WinMouseClicker({
    cdpPort: 9333,
    beforeInput: async () => {},
    nativeOpener: async () => recordingSession(requests, async () => ({ output: ['已滚动 3 格 × -120'] })),
  })
  await clicker.wheel({ x: 531, y: 658 }, VP, -120, 3)
  assert.deepEqual({ ...(requests[0] as object) }, {
    x: 531, y: 658, cssW: VP.width, cssH: VP.height,
    wheelDeltaY: -120, notches: 3, cdpPort: 9333, op: 'wheel',
  })
})

test('wheel 参数校验：deltaY=0 / notches=0 → WinClickError 零输入', async () => {
  const requests: object[] = []
  const clicker = new WinMouseClicker({ nativeOpener: async () => recordingSession(requests) })
  for (const bad of [() => clicker.wheel({ x: 10, y: 20 }, VP, 0, 3), () => clicker.wheel({ x: 10, y: 20 }, VP, -120, 0)]) {
    await assert.rejects(bad(), (error: unknown) => error instanceof WinClickError && !error.message.includes('UNKNOWN'))
  }
  assert.equal(requests.length, 0)
})

test('wheel 绑定模式：输入前激活原标签；激活失败零输入 STALE_OBSERVATION', async () => {
  const events: string[] = []
  const ok = new WinMouseClicker({ cdpPort: 9333, beforeInput: async () => { events.push('activate') }, nativeOpener: async () => recordingSession([], async () => { events.push('input'); return { output: [] } }) })
  await ok.wheel({ x: 10, y: 20 }, VP, -120, 2)
  assert.deepEqual(events, ['activate', 'input'])
  let inputs = 0
  const broken = new WinMouseClicker({ cdpPort: 9333, beforeInput: async () => { throw new Error('original tab closed') }, nativeOpener: async () => recordingSession([], async () => { inputs++; return { output: [] } }) })
  await assert.rejects(broken.wheel({ x: 10, y: 20 }, VP, -120, 2), (error: unknown) => error instanceof WinClickError && error.exitCode === 2)
  assert.equal(inputs, 0)
})
