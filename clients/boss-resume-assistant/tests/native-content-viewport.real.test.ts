import assert from 'node:assert/strict'
import test from 'node:test'
import { createServer } from 'node:http'
import { spawn, execFileSync } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { createKoffiApi, DpiPhysicalScope } from '@aid/win32-native-input'
import { CdpGateway } from '../src/main/cdp/CdpGateway.js'
import { WinMouseClicker } from '../src/main/input/WinMouseClicker.js'
import { type DomSnapshot, findNodesByString, boundsCenter } from '../src/main/boss/domSnapshot.js'
import { viewportOf } from '../src/main/boss/FilterSetter.js'

const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms))
const enabled = process.platform === 'win32' && process.env.AID_WIN32_REAL_INPUT === '1'

// Own static fixture only: never attach the user's BOSS tab or contact candidates.
test('real: CDP coordinates → native menu/filter click, input and wheel with legacy widgets hidden', {
  skip: enabled ? false : 'Requires AID_WIN32_REAL_INPUT=1; temporarily uses the real mouse/keyboard',
  timeout: 60000,
}, async () => {
  const marker = `BOSS-input-fixture-${Date.now()}`
  const server = createServer((_req, res) => { res.setHeader('content-type', 'text/html; charset=utf-8'); res.end(`<!doctype html>
    <title>${marker}</title><style>*{box-sizing:border-box}html,body{margin:0;width:100%;height:100%;overflow:hidden}button,input{margin:20px;width:240px;height:50px}#s{margin:20px;height:350px;overflow:auto;border:2px solid}#long{height:4000px}iframe{width:100px;height:40px}</style>
    <button onclick="document.title='${marker}:menu'">推荐牛人测试菜单</button><button onclick="document.title='${marker}:filter'">筛选测试按钮</button>
    <input aria-label="测试键入" placeholder="测试键入" oninput="document.title='${marker}:typed:'+encodeURIComponent(this.value)">
    <div id="s" onscroll="document.title='${marker}:wheel:'+this.scrollTop"><div id="long">简历滚动测试区域</div></div><iframe srcdoc="nested iframe"></iframe>`) })
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve))
  const pagePort = (server.address() as { port: number }).port
  const reservation = createServer()
  await new Promise<void>(resolve => reservation.listen(0, '127.0.0.1', resolve))
  const cdpPort = (reservation.address() as { port: number }).port
  await new Promise<void>(resolve => reservation.close(() => resolve()))
  const profile = await mkdtemp(join(tmpdir(), 'aid-content-input-'))
  const chrome = spawn('C:/Program Files/Google/Chrome/Application/chrome.exe', [
    `--user-data-dir=${profile}`, `--remote-debugging-port=${cdpPort}`, '--no-first-run', '--no-default-browser-check',
    `--app=http://127.0.0.1:${pagePort}/`,
  ], { stdio: 'ignore' })
  const api = createKoffiApi(), gateway = new CdpGateway()
  let win = 0
  const legacy: number[] = []
  try {
    let targetId: string | undefined
    for (let i = 0; i < 60 && !targetId; i++) {
      try {
        const response = await fetch(`http://127.0.0.1:${cdpPort}/json/list`)
        const targets = await response.json() as { id: string; url: string }[]
        targetId = targets.find(t => t.url === `http://127.0.0.1:${pagePort}/`)?.id
      } catch { /* Test Chrome is starting. */ }
      if (!targetId) await sleep(100)
    }
    assert.ok(targetId, 'Isolated CDP fixture did not start')
    await gateway.connect(`http://127.0.0.1:${cdpPort}`)
    await gateway.attachToTarget(targetId)
    for (let i = 0; i < 40 && !win; i++) {
      api.enumWindows(h => { if (api.isWindowVisible(h) && api.getWindowText(h).startsWith(marker)) win = h; return true })
      if (!win) await sleep(100)
    }
    assert.ok(win)
    // Cold accessibility: hide the fixture's auxiliary HWNDs before the first
    // UIA observation. Do not let prior initialization hide a provider dependency.
    api.enumChildWindows(win, h => { if (api.getClassName(h) === 'Chrome_RenderWidgetHostHWND') legacy.push(h); return true })
    assert.ok(legacy.length)
    const assertHidden = () => {
      let seen = 0
      api.enumChildWindows(win, h => {
        if (api.getClassName(h) === 'Chrome_RenderWidgetHostHWND') {
          seen++
          assert.equal(api.isWindowVisible(h), false, 'A newly visible widget must not make this test pass')
        }
        return true
      })
      assert.ok(seen)
    }
    const clicker = new WinMouseClicker({ cdpPort, beforeInput: async () => {
      await gateway.activateTarget()
      for (const h of legacy) api.showWindow(h, 0)
      assertHidden()
    }, verifyObservation: async () => assertHidden() })
    const locate = async (text: string) => {
      const snapshot = await gateway.captureDomSnapshot() as DomSnapshot
      const stringIds = snapshot.strings.flatMap((value, i) => value === text ? [i] : [])
      const hits = stringIds.flatMap(id => findNodesByString(snapshot.documents[0]!, id)).filter(h => h.bounds[2]! > 0 && h.bounds[3]! > 0)
      assert.equal(hits.length, 1, `fixture location: ${text}`)
      return { point: boundsCenter(hits[0]!.bounds), viewport: viewportOf(snapshot) }
    }
    for (const [label, suffix] of [['推荐牛人测试菜单', ':menu'], ['筛选测试按钮', ':filter']]) {
      const { point, viewport } = await locate(label!)
      await clicker.click(point, viewport)
      for (let i = 0; i < 20 && api.getWindowText(win) !== marker + suffix; i++) await sleep(50)
      assert.equal(api.getWindowText(win), marker + suffix)
    }
    // Input coordinates also come from DOMSnapshot element layout, not a hardcoded screen point.
    const snap = await gateway.captureDomSnapshot() as DomSnapshot
    const document = snap.documents[0]!
    const inputNode = (document.nodes.nodeName as number[]).findIndex(id => snap.strings[id] === 'INPUT')
    const inputLayout = document.layout.nodeIndex.indexOf(inputNode)
    assert.ok(inputLayout >= 0)
    const typed = '中文😊Win32'
    await clicker.clickAndType(boundsCenter(document.layout.bounds[inputLayout]! as [number, number, number, number]), viewportOf(snap), typed)
    await sleep(150)
    assert.equal(decodeURIComponent(api.getWindowText(win).split(':typed:')[1]!), typed)
    const { point, viewport } = await locate('简历滚动测试区域')
    // Its text is at the top of the scrollable region; this is a genuine visible point.
    await clicker.wheel(point, viewport, -120, 3)
    await sleep(150)
    const scrollTop = Number(api.getWindowText(win).split(':wheel:')[1])
    assert.ok(Number.isFinite(scrollTop) && scrollTop > 0, 'Native wheel must actually move the fixture scroll region')
    assertHidden()
    const scope = DpiPhysicalScope.enter(api)
    try {
      const content = api.openContentViewport(win)
      try { assert.ok(content.readRect().bottom > content.readRect().top) } finally { content.close() }
    } finally { scope.dispose() }
  } finally {
    await gateway.close().catch(() => {})
    if (chrome.pid) execFileSync('taskkill', ['/PID', String(chrome.pid), '/T', '/F'], { stdio: 'ignore' })
    await new Promise<void>(resolve => server.close(() => resolve()))
    await sleep(300)
    await rm(profile, { recursive: true, force: true, maxRetries: 4, retryDelay: 200 })
  }
})
