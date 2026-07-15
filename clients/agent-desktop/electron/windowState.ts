import { mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs'
import path from 'node:path'

export interface WindowBounds {
  x: number
  y: number
  width: number
  height: number
}

export interface SavedWindowState {
  bounds: WindowBounds
  maximized: boolean
}

export interface DisplayArea {
  x: number
  y: number
  width: number
  height: number
}

const DEFAULT_BOUNDS: WindowBounds = { x: 120, y: 80, width: 1200, height: 800 }
const MIN_VISIBLE_PIXELS = 80

function defaultBounds(displays: DisplayArea[]): WindowBounds {
  const area = displays[0]
  if (!area) return DEFAULT_BOUNDS
  const width = Math.max(720, Math.min(DEFAULT_BOUNDS.width, area.width))
  const height = Math.max(500, Math.min(DEFAULT_BOUNDS.height, area.height))
  return {
    x: area.x + Math.max(0, Math.round((area.width - width) / 2)),
    y: area.y + Math.max(0, Math.round((area.height - height) / 2)),
    width,
    height,
  }
}

function hasVisibleArea(bounds: WindowBounds, area: DisplayArea): boolean {
  const overlapWidth = Math.max(0, Math.min(bounds.x + bounds.width, area.x + area.width) - Math.max(bounds.x, area.x))
  const overlapHeight = Math.max(0, Math.min(bounds.y + bounds.height, area.y + area.height) - Math.max(bounds.y, area.y))
  return overlapWidth >= MIN_VISIBLE_PIXELS && overlapHeight >= MIN_VISIBLE_PIXELS
}

export function restoreWindowState(value: unknown, displays: DisplayArea[]): SavedWindowState {
  const fallback = { bounds: defaultBounds(displays), maximized: false }
  if (!value || typeof value !== 'object') return fallback
  const candidate = value as Partial<SavedWindowState>
  const bounds = candidate.bounds
  if (!bounds || ![bounds.x, bounds.y, bounds.width, bounds.height].every(Number.isFinite)) {
    return fallback
  }
  if (bounds.width < 720 || bounds.height < 500 || bounds.width > 10000 || bounds.height > 10000) {
    return fallback
  }
  if (!displays.some((display) => hasVisibleArea(bounds, display))) {
    return fallback
  }
  return { bounds: { ...bounds }, maximized: candidate.maximized === true }
}

export function loadWindowState(filePath: string, displays: DisplayArea[]): SavedWindowState {
  try {
    return restoreWindowState(JSON.parse(readFileSync(filePath, 'utf8')), displays)
  } catch {
    return restoreWindowState(undefined, displays)
  }
}

export function saveWindowState(filePath: string, state: SavedWindowState): void {
  mkdirSync(path.dirname(filePath), { recursive: true })
  const temporaryPath = `${filePath}.tmp`
  writeFileSync(temporaryPath, JSON.stringify(state), { encoding: 'utf8', mode: 0o600 })
  renameSync(temporaryPath, filePath)
}

export type ZoomCommand = 'in' | 'out' | 'reset' | null

export function getZoomCommand(input: { control: boolean; meta: boolean; key: string }): ZoomCommand {
  if (!input.control && !input.meta) return null
  if (input.key === '+' || input.key === '=') return 'in'
  if (input.key === '-') return 'out'
  if (input.key === '0') return 'reset'
  return null
}

export function nextZoomFactor(current: number, command: Exclude<ZoomCommand, null>): number {
  if (command === 'reset') return 1
  const delta = command === 'in' ? 0.1 : -0.1
  return Math.min(2, Math.max(0.5, Math.round((current + delta) * 10) / 10))
}

export function focusExistingWindow(window: { isMinimized(): boolean; restore(): void; focus(): void }): void {
  if (window.isMinimized()) window.restore()
  window.focus()
}
