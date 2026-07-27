/**
 * 窗口位置/尺寸/最大化状态持久化。
 * 存储在 Electron userData 目录的 window-state.json，不保存任何页面内容或凭据。
 */
import { app, BrowserWindow } from 'electron'
import fs from 'node:fs'
import path from 'node:path'

interface WindowBounds {
  x?: number
  y?: number
  width: number
  height: number
  isMaximized?: boolean
}

const DEFAULT_BOUNDS: WindowBounds = { width: 1200, height: 800 }

function stateFilePath(): string {
  return path.join(app.getPath('userData'), 'window-state.json')
}

export function loadWindowBounds(): WindowBounds {
  try {
    const raw = fs.readFileSync(stateFilePath(), 'utf8')
    const parsed = JSON.parse(raw) as Partial<WindowBounds>
    return {
      x: typeof parsed.x === 'number' ? parsed.x : undefined,
      y: typeof parsed.y === 'number' ? parsed.y : undefined,
      width: typeof parsed.width === 'number' && parsed.width > 0 ? parsed.width : DEFAULT_BOUNDS.width,
      height: typeof parsed.height === 'number' && parsed.height > 0 ? parsed.height : DEFAULT_BOUNDS.height,
      isMaximized: !!parsed.isMaximized,
    }
  } catch {
    return { ...DEFAULT_BOUNDS }
  }
}

export function trackWindowState(window: BrowserWindow): void {
  const save = () => {
    const isMaximized = window.isMaximized()
    const bounds = isMaximized ? window.getNormalBounds() : window.getBounds()
    const state: WindowBounds = {
      x: bounds.x,
      y: bounds.y,
      width: bounds.width,
      height: bounds.height,
      isMaximized,
    }
    try {
      fs.writeFileSync(stateFilePath(), JSON.stringify(state), 'utf8')
    } catch {
      // 写入失败不阻塞主流程
    }
  }
  window.on('resize', save)
  window.on('move', save)
  window.on('maximize', save)
  window.on('unmaximize', save)
  window.on('close', save)
}
