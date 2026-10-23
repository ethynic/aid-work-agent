/**
 * 真实 Win32 输入测试（默认 skip；环境变量 AID_WIN32_REAL_INPUT=1 且 win32 才运行）。
 *
 * 运行方式：
 *   set AID_WIN32_REAL_INPUT=1 && cd clients/shared/win32-native-input && npm run test:real
 * 说明：测试会短暂抢占真实鼠标/键盘焦点（约 15 秒），运行期间请勿操作鼠标键盘。
 * 会启动一个独立 profile 的 Chrome（--app data: 页面）和一个 PowerShell WinForms
 * 窗口；结束后全部关闭清理，不触碰系统已有的浏览器/记事本窗口。
 *
 * 覆盖（Phase 1 验收策略——不依赖 BOSS 生产场景）：
 * 1. Chrome（生产同款目标）：真实窗口上点击+逐字键入中文/emoji（代理项），
 *    页面 oninput 同步 document.title，读回断言一致（等价于 WM_GETTEXT 读回
 *    Edit 控件：系统 notepad 是单实例 Store 应用且可能已有用户文件，直接写
 *    风险不可控；Chrome 是真实生产目标，断言更强）。
 * 2. 物理光标断言（GetCursorPos 与目标屏幕点一致）；拖动后光标到达终点。
 * 3. 守卫反例：topmost 窗口遮挡落点 → REFUSED exit 2，零输入；遮挡移除后成功。
 */
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import koffi from 'koffi'

const RUN = process.env.AID_WIN32_REAL_INPUT === '1' && process.platform === 'win32'
const options = { skip: RUN ? false : '需要 AID_WIN32_REAL_INPUT=1（会占用真实鼠标键盘约 15 秒）在 win32 上运行' }

import {
  createKoffiApi,
  performPhysicalInput,
} from '../../dist/index.js'

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

test('真实输入：Chrome 点击键入读回 / 光标断言 / 拖动 / 遮挡守卫', options, async () => {
  const api = createKoffiApi()
  // 测试专用 koffi 断言 API（只读断言/窗口控制，不注入输入）
  const user32 = koffi.load('user32.dll')
  koffi.struct('WniTPOINT2', { x: 'long', y: 'long' })
  const GetCursorPosT = user32.func('bool __stdcall GetCursorPos(_Out_ WniTPOINT2 *pt)')
  const SetWindowPos = user32.func('bool __stdcall SetWindowPos(intptr hWnd, intptr after, int x, int y, int cx, int cy, uint32 flags)')

  const marker = `aid${Date.now().toString(36)}${Math.floor(Math.random() * 1e6).toString(36)}`
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'aid-chrome-'))
  const typed = '你好😊Ab物理输入'
  const page = `data:text/html,<title>${marker}</title><body style="margin:0"><input id="i" style="width:100vw;height:100vh;display:block;font-size:20px" oninput="document.title='T:'+encodeURIComponent(i.value)"></body>`
  const chromeExe = ['C:/Program Files/Google/Chrome/Application/chrome.exe', 'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe']
    .find((p) => fs.existsSync(p))
  if (!chromeExe) throw new Error('未找到 chrome.exe')
  const chrome = spawn(chromeExe, [`--user-data-dir=${profile.replace(/\\/g, '\\\\')}`, '--no-first-run', '--no-default-browser-check', `--app=${page}`], { stdio: 'ignore' })
  const host = spawn('powershell.exe', ['-NoProfile', '-STA', '-ExecutionPolicy', 'Bypass', '-File', fileURLToPath(new URL('hostWindow.ps1', import.meta.url)), '-Marker', marker], { stdio: 'ignore' })
  const killAll = () => {
    spawn('taskkill', ['/PID', String(chrome.pid), '/T', '/F'], { stdio: 'ignore' })
    spawn('taskkill', ['/PID', String(host.pid), '/T', '/F'], { stdio: 'ignore' })
  }
  let win = 0
  let cover = 0
  let rw = 0
  try {
    // 等待 Chrome 窗口（标题含 marker）与遮挡宿主窗口 B 就绪
    for (let i = 0; i < 66 && (!win || !cover); i++) {
      if (!win) {
        api.enumWindows((hwnd) => {
          if (api.isWindowVisible(hwnd) && api.getWindowText(hwnd).startsWith(marker)) { win = hwnd; return false }
          return true
        })
      }
      if (!cover) {
        api.enumWindows((hwnd) => {
          if (api.isWindowVisible(hwnd) && api.getWindowText(hwnd).includes(`AID_TEST_B_${marker}`)) { cover = hwnd; return false }
          return true
        })
      }
      if (!win || !cover) await sleep(250)
    }
    assert.ok(win, 'Chrome 窗口未出现')
    assert.ok(cover, '遮挡宿主窗口未出现')

    // 渲染子窗：按生产语义（可见面积最大）而不是第一个
    let best = 0, bestArea = 0
    api.enumChildWindows(win, (hwnd) => {
      if (api.getClassName(hwnd) === 'Chrome_RenderWidgetHostHWND' && api.isWindowVisible(hwnd)) {
        const r = api.getWindowRect(hwnd)
        const a = (r.right - r.left) * (r.bottom - r.top)
        if (a > bestArea) { bestArea = a; best = hwnd }
      }
      return true
    })
    rw = best
    assert.ok(rw, '未找到 Chrome_RenderWidgetHostHWND')
    const box = api.getWindowRect(rw)
    const cssW = box.right - box.left
    const cssH = box.bottom - box.top
    const resolveTarget = () => ({ win, rw })

    // === 1. 点击 + 逐字键入（中文/emoji/英文混合）；title 读回 ===
    const out = await performPhysicalInput(
      { api, log: () => {} },
      { x: Math.round(cssW * 0.3), y: Math.round(cssH * 0.5), cssW, cssH, text: typed, op: 'clickAndType', resolveTarget },
    )
    assert.ok(out.output.some((line) => line.includes(`已输入 ${typed.length} 字`)))
    for (let i = 0; i < 40; i++) {
      const t = api.getWindowText(win)
      if (t.startsWith('T:')) break
      await sleep(100)
    }
    const title = api.getWindowText(win)
    assert.ok(title.startsWith('T:'), `页面未回写输入: ${title}`)
    assert.equal(decodeURIComponent(title.slice(2)), typed, '读回内容与键入一致（中文+emoji 代理项重组）')

    // 物理光标断言：点击后光标停在目标点（±1px）
    const expectedX = Math.round(box.left + (box.right - box.left) * 0.3)
    const expectedY = Math.round(box.top + (box.bottom - box.top) * 0.5)
    const pos = {}
    assert.ok(GetCursorPosT(pos), 'GetCursorPos 失败')
    assert.ok(Math.abs(pos.x - expectedX) <= 1 && Math.abs(pos.y - expectedY) <= 1, `光标 (${pos.x},${pos.y}) 未到达目标 (${expectedX},${expectedY})`)

    // === 2. 拖动（真实左键按下/移动/释放），光标到达终点 ===
    await performPhysicalInput(
      { api, log: () => {} },
      { x: Math.round(cssW * 0.3), y: Math.round(cssH * 0.5), cssW, cssH, dragX: Math.round(cssW * 0.7), dragY: Math.round(cssH * 0.5), op: 'drag', resolveTarget },
    )
    const ex2 = Math.round(box.left + (box.right - box.left) * 0.7)
    const ey2 = Math.round(box.top + (box.bottom - box.top) * 0.5)
    const pos2 = {}
    assert.ok(GetCursorPosT(pos2), 'GetCursorPos 失败')
    assert.ok(Math.abs(pos2.x - ex2) <= 1 && Math.abs(pos2.y - ey2) <= 1, `拖动后光标 (${pos2.x},${pos2.y}) 未到达终点 (${ex2},${ey2})`)

    // === 3. 守卫反例：topmost 窗口遮挡落点 → REFUSED exit 2，零输入 ===
    const coverRect = api.getWindowRect(cover)
    const coverW = coverRect.right - coverRect.left
    const coverH = coverRect.bottom - coverRect.top
    const occludedPx = Math.round(cssW * 0.6)
    const occludedPy = Math.round(cssH * 0.5)
    const occludedScreenX = Math.round(box.left + (box.right - box.left) * 0.6)
    const occludedScreenY = Math.round(box.top + (box.bottom - box.top) * 0.5)
    const SWP_NOSIZE = 0x0001
    SetWindowPos(cover, -1, occludedScreenX - Math.floor(coverW / 2), occludedScreenY - Math.floor(coverH / 2), 0, 0, SWP_NOSIZE) // HWND_TOPMOST
    await sleep(300)
    await assert.rejects(
      performPhysicalInput(
        { api, log: () => {} },
        { x: occludedPx, y: occludedPy, cssW, cssH, op: 'click', resolveTarget },
      ),
      (error) => {
        assert.equal(error.exitCode, 2)
        assert.equal(error.kind, 'REFUSED')
        assert.match(error.message, /落点仍被遮挡/)
        return true
      },
    )
    // 遮挡移除（B 移出屏幕外）→ 同一点可点击成功
    SetWindowPos(cover, -1, 3000, 3000, 0, 0, SWP_NOSIZE)
    await sleep(300)
    const out3 = await performPhysicalInput(
      { api, log: () => {} },
      { x: occludedPx, y: occludedPy, cssW, cssH, op: 'click', resolveTarget },
    )
    assert.ok(out3.output.includes('已点击'))
    // 输入内容未被影响
    assert.equal(decodeURIComponent(api.getWindowText(win).slice(2)), typed)
  } finally {
    killAll()
    await sleep(500)
    try { fs.rmSync(profile, { recursive: true, force: true }) } catch { /* 尽力清理 */ }
  }
})
