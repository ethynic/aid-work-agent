/**
 * 窗口探查（win-click.ps1 的 Win32 辅助类 + BossWindowExperiment.cs 绑定模式部分的 1:1 移植）。
 * 只做原生几何/身份判断，不采集标题内容之外的任何信息（BOSS 关键字匹配除外，见 ps1 原语义）。
 */
import type { NativeApi } from './types.js'
import { readRect } from './physical.js'

/** ps1 FindWindowByTitle：EnumWindows 中第一个可见且标题含关键字的窗口 */
export function findWindowByTitle(api: NativeApi, keyword: string): number {
  let found = 0
  api.enumWindows((hwnd) => {
    if (!api.isWindowVisible(hwnd)) return true
    if (api.getWindowText(hwnd).includes(keyword)) {
      found = hwnd
      return false
    }
    return true
  })
  return found
}

/**
 * ps1 FindRenderWidget：父窗 Chrome_RenderWidgetHostHWND 子窗中可见且面积最大者。
 * 2026-08-31 真机修正语义：Chrome 151 存在多个可见 render widget（弹层/扩展小部件），
 * 第一个可见可能是 96px 小部件导致换算集体打偏——必须取面积最大；全不可见时回退第一个隐藏的。
 */
export function findRenderWidget(api: NativeApi, parent: number): number {
  let firstHidden = 0
  let found = 0
  let bestArea = 0
  api.enumChildWindows(parent, (hwnd) => {
    if (api.getClassName(hwnd) === 'Chrome_RenderWidgetHostHWND') {
      if (api.isWindowVisible(hwnd)) {
        const r = readRect(api, hwnd)
        const area = (r.right - r.left) * (r.bottom - r.top)
        if (area > bestArea) {
          bestArea = area
          found = hwnd
        }
      } else if (firstHidden === 0) {
        firstHidden = hwnd
      }
    }
    return true
  })
  return found !== 0 ? found : firstHidden
}

// ---- BossWindowExperiment 移植（仅 win-click.ps1 用到的部分）----

/** ProcessId(window) */
function processIdOf(api: NativeApi, hwnd: number): number {
  return api.getWindowThreadProcessId(hwnd).pid
}

const GA_ROOT = 2
/** GetWindow 的 uCmd=4 = GW_OWNER：BelongsTo 沿 owner 链上溯（C# 原文即 4） */
const GW_OWNER = 4

/** BelongsTo(window, browser)：window 的根窗（含 owned 顶层对话框）沿 owner 链能到达 browser 即归属 */
export function belongsTo(api: NativeApi, window: number, browser: number): boolean {
  if (window === 0) return false
  let current = api.getAncestor(window, GA_ROOT)
  if (processIdOf(api, current) !== processIdOf(api, browser)) return false
  for (let i = 0; i < 32 && current !== 0; i++, current = api.getWindow(current, GW_OWNER)) {
    if (current === browser) return true
  }
  return false
}

/**
 * Find(processId)：可见 + 同 pid + Chrome_WidgetWin_1 + 标题含 BOSS 的独立主窗必须恰好一个。
 * 两个同浏览器进程的窗口无法用前台优先级区分，只有唯一独立 BOSS 主窗能证明 CDP 目标的本机归属。
 */
export function boundFind(api: NativeApi, processId: number): number {
  const matches: number[] = []
  api.enumWindows((hwnd) => {
    if (
      api.isWindowVisible(hwnd) &&
      processIdOf(api, hwnd) === processId &&
      api.getClassName(hwnd) === 'Chrome_WidgetWin_1' &&
      api.getWindowText(hwnd).includes('BOSS')
    ) {
      matches.push(hwnd)
    }
    return true
  })
  const mainWindows: number[] = []
  for (const candidate of matches) {
    const owned = matches.some((other) => candidate !== other && belongsTo(api, candidate, other))
    if (!owned) mainWindows.push(candidate)
  }
  if (mainWindows.length !== 1) {
    throw new Error(`Expected exactly one independent BOSS window in debug Chrome; found ${mainWindows.length}`)
  }
  return mainWindows[0]!
}

/** Foreground(browser)：还原最小化 + AttachThreadInput 挂前台线程后抬窗（结果由 RequireForeground 校验） */
export function boundForeground(api: NativeApi, browser: number): void {
  if (api.isIconic(browser)) api.showWindow(browser, 9) // SW_RESTORE
  const foregroundThread = api.getWindowThreadProcessId(api.getForegroundWindow()).thread
  const current = api.getCurrentThreadId()
  const attached = foregroundThread !== current && api.attachThreadInput(current, foregroundThread, true)
  try {
    api.bringWindowToTop(browser)
    api.setForegroundWindow(browser)
  } finally {
    if (attached) api.attachThreadInput(current, foregroundThread, false)
  }
}

/** RequireForeground(browser)：前台窗口必须归属目标浏览器，否则观察/输入失效 */
export function requireBoundForeground(api: NativeApi, browser: number): void {
  if (!belongsTo(api, api.getForegroundWindow(), browser)) {
    throw new Error('Debug Chrome is not foreground; observation/input refused')
  }
}
