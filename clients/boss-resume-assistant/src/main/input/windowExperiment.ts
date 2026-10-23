import { randomUUID } from 'node:crypto'
import { existsSync, writeFileSync } from 'node:fs'
import { mkdtemp, readFile, rmdir, writeFile, unlink } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { pngMetadata } from '../operations/bossScreenshot.js'
import { execPowerShellScript } from './powerShell.js'
import { CancelledError } from '../operations/types.js'

export interface WindowObservation {
  output: string
  width: number
  height: number
  sha256: string
  space: 'window_image_px'
  captured_at: string
  cdp_port: number
  window: { hwnd: string; pid: number; process_started_at?: string; left: number; top: number; width: number; height: number }
  owned_dialogs: unknown[]
  repositioned?: boolean
  geometry?: WindowGeometry
}

function scriptPath(): string {
  const here = dirname(fileURLToPath(import.meta.url))
  for (const up of ['../../../..', '../../..']) {
    const candidate = resolve(here, up, 'scripts/window-experiment.ps1')
    if (existsSync(candidate)) return candidate
  }
  throw new Error('未找到 BOSS CLI window-experiment.ps1')
}

export type WindowScriptRunner = typeof execPowerShellScript

export interface WindowGeometry { window: number[]; screen: number[]; work: number[]; dpi: number }
export interface WindowCaptureBudget { repositionsUsed: number }
export interface WindowCaptureOptions { budget?: WindowCaptureBudget; signal?: AbortSignal; allowReposition?: boolean }
const FAILURE_REASONS = new Set(['window_outside_screen', 'window_identity_changed', 'window_reposition_failed', 'window_geometry_unavailable', 'window_capture_failed', 'window_capture_cancelled'])
export class WindowCaptureError extends Error {
  constructor(readonly reason: string, readonly geometry?: WindowGeometry) { super(`绑定窗口采集失败（${reason}）`); this.name = 'WindowCaptureError' }
}
function geometryOf(value: unknown): WindowGeometry | undefined {
  const g = value as WindowGeometry | undefined
  if (!g || !['window', 'screen', 'work'].every(k => Array.isArray(g[k as keyof WindowGeometry]) && (g[k as keyof WindowGeometry] as number[]).length === 4
    && (g[k as keyof WindowGeometry] as number[]).every(n => Number.isSafeInteger(n))) || !Number.isInteger(g.dpi) || g.dpi <= 0 || g.dpi > 960) return
  return { window: g.window, screen: g.screen, work: g.work, dpi: g.dpi }
}
function captureFailure(error: unknown): { reason: string; geometry?: WindowGeometry; window?: WindowObservation['window'] } | undefined {
  const match = (error instanceof Error ? error.message : '').match(/\[BOSS_WINDOW_DIAGNOSTIC\](\{[^\r\n]+\})/)
  if (!match) return
  try {
    const value = JSON.parse(match[1]!)
    if (!FAILURE_REASONS.has(value.reason)) return
    const w = value.window
    const window = w && /^\d+$/.test(w.hwnd) && Number.isInteger(w.pid) && w.pid > 0 && typeof w.process_started_at === 'string' && Number.isFinite(Date.parse(w.process_started_at)) ? w : undefined
    return { reason: value.reason, geometry: geometryOf(value.geometry), window }
  } catch { return }
}

export async function captureWindow(outputPath: string, cdpPort = 9222, run: WindowScriptRunner = execPowerShellScript, options: WindowCaptureOptions = {}): Promise<WindowObservation & { metadata_path: string }> {
  if (!outputPath?.trim()) throw new Error('screenshot 需要 --output <PNG路径>')
  const output = resolve(outputPath), budget = options.budget ?? { repositionsUsed: 0 }
  const check = () => { if (options.signal?.aborted) throw new CancelledError() }
  check()
  const cancelDirectory = options.signal ? await mkdtemp(join(tmpdir(), 'aid-window-cancel-')) : undefined
  const cancelFile = cancelDirectory ? join(cancelDirectory, `${randomUUID()}.cancel`) : undefined
  const cancel = () => { if (cancelFile) writeFileSync(cancelFile, '', { mode: 0o600 }) }
  options.signal?.addEventListener('abort', cancel, { once: true })
  const args = ['-Action', 'capture', '-CdpPort', String(cdpPort), '-Output', output, ...(cancelFile ? ['-CancelFile', cancelFile] : [])]
  let result: Awaited<ReturnType<WindowScriptRunner>>
  try {
    check()
    try { result = await run(scriptPath(), args, { timeout: 30_000 }) }
    catch (error) {
      check()
      const failure = captureFailure(error)
      if (!failure) throw error
      if (failure.reason !== 'window_outside_screen' || !failure.geometry || !failure.window || options.allowReposition === false || budget.repositionsUsed >= 1) throw new WindowCaptureError(failure.reason, failure.geometry)
      // Consume before launching: cancellation or an uncertain native result never replenishes it.
      budget.repositionsUsed++
      check()
      try { result = await run(scriptPath(), [...args, '-AllowReposition', '-ExpectedWindow', JSON.stringify(failure.window)], { timeout: 30_000 }) }
      catch (retryError) {
        check()
        const next = captureFailure(retryError)
        throw new WindowCaptureError(next?.reason ?? 'window_reposition_failed', next?.geometry ?? failure.geometry)
      }
    }
    check()
    const captured = JSON.parse(result.stdout) as Pick<WindowObservation, 'window' | 'captured_at' | 'owned_dialogs' | 'repositioned' | 'geometry'>
    const image = await readFile(output)
    const metadata = pngMetadata(image)
    if (metadata.width !== captured.window.width || metadata.height !== captured.window.height) throw new WindowCaptureError('window_geometry_unavailable', geometryOf(captured.geometry))
    const observation: WindowObservation = { ...captured, ...metadata, space: 'window_image_px', output, cdp_port: cdpPort }
    const metadataPath = `${output}.json`
    await writeFile(metadataPath, JSON.stringify(observation, null, 2), 'utf8')
    return { ...observation, metadata_path: metadataPath }
  } finally {
    options.signal?.removeEventListener('abort', cancel)
    if (cancelFile) { try { await unlink(cancelFile) } catch (error) { if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error } }
    if (cancelDirectory) await rmdir(cancelDirectory)
  }
}

export async function loadWindowObservation(file: string): Promise<WindowObservation> {
  const observation = JSON.parse(await readFile(file, 'utf8')) as WindowObservation
  const win = observation.window
  if (observation.space !== 'window_image_px' || !win || !/^\d+$/.test(win.hwnd) ||
      ![win.pid, win.left, win.top, win.width, win.height, observation.width, observation.height, observation.cdp_port].every(Number.isInteger) ||
      win.pid <= 0 || win.width <= 0 || win.height <= 0 || observation.width !== win.width || observation.height !== win.height ||
      observation.cdp_port <= 0 || observation.cdp_port > 65535 || !Number.isFinite(Date.parse(observation.captured_at))) throw new Error('无效窗口观察回执')
  const metadata = pngMetadata(await readFile(observation.output))
  if (metadata.sha256 !== observation.sha256 || metadata.width !== observation.width || metadata.height !== observation.height) throw new Error('窗口观察原图 hash/尺寸发生变化')
  return observation
}

export async function clickWindow(observationPath: string, x: number, y: number, maxAgeSeconds = 120, run: WindowScriptRunner = execPowerShellScript): Promise<unknown> {
  const observation = await loadWindowObservation(observationPath)
  if (![x, y].every(Number.isInteger) || x < 0 || y < 0 || x >= observation.width || y >= observation.height) throw new Error('落点必须在窗口原图像素范围内')
  if (!Number.isInteger(maxAgeSeconds) || maxAgeSeconds < 1 || maxAgeSeconds > 600) throw new Error('max-age-seconds 必须为 1-600 秒')
  const age = Date.now() - Date.parse(observation.captured_at)
  if (age < 0 || age > maxAgeSeconds * 1000) throw new Error('窗口观察已过期，请重新截图')
  const result = await run(scriptPath(), ['-Action', 'click', '-CdpPort', String(observation.cdp_port), '-Observation', resolve(observationPath), '-X', String(x), '-Y', String(y), '-MaxAgeSeconds', String(maxAgeSeconds)], { timeout: 30_000 })
  return JSON.parse(result.stdout)
}
