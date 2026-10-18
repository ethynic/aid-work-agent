import { existsSync } from 'node:fs'
import { readFile, writeFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { pngMetadata } from '../operations/bossScreenshot.js'
import { execPowerShellScript } from './powerShell.js'

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

export async function captureWindow(outputPath: string, cdpPort = 9222, run: WindowScriptRunner = execPowerShellScript): Promise<WindowObservation & { metadata_path: string }> {
  if (!outputPath?.trim()) throw new Error('screenshot 需要 --output <PNG路径>')
  const output = resolve(outputPath)
  const result = await run(scriptPath(), ['-Action', 'capture', '-CdpPort', String(cdpPort), '-Output', output], { timeout: 30_000 })
  const captured = JSON.parse(result.stdout) as Pick<WindowObservation, 'window' | 'captured_at' | 'owned_dialogs'>
  const image = await readFile(output)
  const metadata = pngMetadata(image)
  if (metadata.width !== captured.window.width || metadata.height !== captured.window.height) throw new Error('窗口截图与物理窗口尺寸不一致')
  const observation: WindowObservation = { ...captured, ...metadata, space: 'window_image_px', output, cdp_port: cdpPort }
  const metadataPath = `${output}.json`
  await writeFile(metadataPath, JSON.stringify(observation, null, 2), 'utf8')
  return { ...observation, metadata_path: metadataPath }
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


export interface WindowEscapeResult {
  key: 'Escape'
  sent: true
  input_status: 'attempted'
  hwnd: string
}

/** One fixed Escape; callers must observe the page again to verify its effect. */
export async function escapeWindow(observationPath: string, maxAgeSeconds = 120, run: WindowScriptRunner = execPowerShellScript): Promise<WindowEscapeResult> {
  const observation = await loadWindowObservation(observationPath)
  if (!Number.isInteger(maxAgeSeconds) || maxAgeSeconds < 1 || maxAgeSeconds > 600) throw new Error('max-age-seconds 必须为 1-600 秒')
  const age = Date.now() - Date.parse(observation.captured_at)
  if (age < 0 || age > maxAgeSeconds * 1000) throw new Error('窗口观察已过期，请重新截图')
  const result = await run(scriptPath(), ['-Action', 'escape', '-CdpPort', String(observation.cdp_port), '-Observation', resolve(observationPath), '-MaxAgeSeconds', String(maxAgeSeconds)], { timeout: 30_000 })
  let receipt: WindowEscapeResult
  try { receipt = JSON.parse(result.stdout) as WindowEscapeResult }
  catch { throw new Error('[BOSS_INPUT_UNKNOWN] Escape 输入回执无法解析，需重新观察') }
  if (!receipt || receipt.key !== 'Escape' || receipt.sent !== true || receipt.input_status !== 'attempted' || receipt.hwnd !== observation.window.hwnd) throw new Error('[BOSS_INPUT_UNKNOWN] Escape 输入回执不完整，需重新观察')
  return receipt
}
