/** Bound observation/input primitives; localOperationRecovery owns orchestration. */
import { randomUUID } from 'node:crypto'
import { existsSync } from 'node:fs'
import { lstat, mkdir, open, readFile, readdir, realpath, rename, unlink, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { setTimeout as sleep } from 'node:timers/promises'
import { captureWindow, clickWindow, escapeWindow, type WindowObservation } from '../input/windowExperiment.js'
import { execPowerShellScript } from '../input/powerShell.js'
import { CancelledError, CodedOperationError } from '../operations/types.js'
import type { BossSession } from '../operations/bossContext.js'
import { pngMetadata } from '../operations/bossScreenshot.js'
import { visualDiagnose, VisualDiagnoseError } from './visualDiagnoseClient.js'

export type VisualContext = Record<string, unknown>
export interface VisualInspectArgs { request_id?: string; visual_context?: VisualContext; retrieve_only?: boolean; observation_mode?: 'page' | 'window' }
interface DismissAction { kind: 'dismiss'; space: 'page_image_px' | 'window_image_px'; point: { x: number; y: number }; target_box: { x: number; y: number; width: number; height: number }; target_label: string }
interface EscapeAction { kind: 'press_escape'; target_label: string }
type Action = DismissAction | EscapeAction
interface PageMetrics { width: number; height: number }
interface RecordData { request_id: string; recovery_ref: string; url: string; captured_at: string; context: VisualContext; image?: { width: number; height: number }; observation_mode?: 'page' | 'window'; window_observation?: WindowObservation; browser_target_ref?: string; target_id?: string; page_metrics?: PageMetrics; response?: { status: number; body: unknown } }
export interface VisualRecoveryDeps {
  root?: string
  diagnose?: typeof visualDiagnose
  compare?: (original: string, fresh: string, region: { x: number; y: number; width: number; height: number }) => Promise<boolean>
  now?: () => number
  settleMs?: number
  captureWindow?: typeof captureWindow
  clickWindow?: typeof clickWindow
  escapeWindow?: typeof escapeWindow
}
const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i
const OPERATION_NAME = /^boss_[a-z][a-z0-9_]{0,99}$/
const MAX_AGE = 120_000
const object = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v)
const invalid = (message: string): never => { throw new CodedOperationError('INVALID_ARGUMENT', message) }
export function validateVisualArgs(args: VisualInspectArgs): string | null {
  if (args.observation_mode !== undefined && !['page', 'window'].includes(args.observation_mode)) return 'observation_mode 需要 page 或 window'
  if (!args.visual_context && !args.request_id && !args.retrieve_only && !args.observation_mode) return null
  if (!UUID.test(args.request_id ?? '')) return '视觉诊断需要 UUID request_id'
  if (args.retrieve_only) return args.visual_context ? 'retrieve_only 不接受新的上下文' : null
  const c = args.visual_context
  if (!object(c) || Object.keys(c).some(k => !['purpose', 'operation', 'execution', 'error', 'verification', 'available_actions'].includes(k))) return 'visual_context 字段无效'
  if (!['diagnose', 'verify'].includes(String(c.purpose)) || !object(c.operation) || !OPERATION_NAME.test(String(c.operation.name)) || !['page_state', 'business_result'].includes(String(c.operation.result_kind))) return '需要安全的 boss_* 操作名及 page_state/business_result 结果类型'
  if (!object(c.execution) || !Number.isInteger(c.execution.attempts_used) || Number(c.execution.attempts_used) < 1) return 'execution.attempts_used 需要正整数'
  if (!Array.isArray(c.available_actions) || c.available_actions.some(a => a !== 'dismiss_click' && a !== 'press_escape') || c.available_actions.length > 2 || new Set(c.available_actions).size !== c.available_actions.length || c.purpose === 'verify' && c.available_actions.length) return '恢复仅支持 dismiss_click/press_escape；后态验证不能建议动作'
  if (Buffer.byteLength(JSON.stringify(c)) > 64 * 1024) return '视觉上下文不能超过 64 KiB'
  return null
}
function assertUrl(url: string): void {
  try { const u = new URL(url); if (u.protocol === 'https:' && ['www.zhipin.com', 'zhipin.com'].includes(u.hostname) && !u.port && !u.username && !u.password) return } catch { /* fail below */ }
  throw new CodedOperationError('WRONG_PAGE', '视觉恢复仅支持合法的 BOSS 页面')
}
async function metricsOf(session: BossSession): Promise<PageMetrics | undefined> {
  const metrics = await session.getPageMetrics?.()
  if (metrics && (![metrics.width, metrics.height].every(v => Number.isFinite(v) && v > 0))) throw stale('网页 CSS 视口尺寸无效，未执行视觉点击')
  return metrics
}
function sameMetrics(a?: PageMetrics, b?: PageMetrics): boolean { return a?.width === b?.width && a?.height === b?.height }
function actionOf(record: RecordData, image?: Buffer): Action | undefined {
  const body = record.response?.body
  if (!object(body) || body.api_version !== 2 || body.request_id !== record.request_id || body.state !== 'completed' || body.action_error || body.diagnosis_error || !object(body.diagnosis)) return
  const a = body.diagnosis.proposed_action
  if (!object(a) || typeof a.target_label !== 'string' || !a.target_label.trim() || record.context.purpose !== 'diagnose' || !Array.isArray(record.context.available_actions)) return
  if (a.kind === 'press_escape') return record.context.available_actions.includes('press_escape') ? a as unknown as EscapeAction : undefined
  const space = record.observation_mode === 'window' ? 'window_image_px' : 'page_image_px'
  if (a.kind !== 'dismiss' || a.space !== space || !object(a.point) || !object(a.target_box) || !record.context.available_actions.includes('dismiss_click')) return
  const p = a.point, b = a.target_box, m = record.image ?? (image ? pngMetadata(image) : undefined)
  if (!m) return
  if (![p.x, p.y, b.x, b.y, b.width, b.height].every(v => typeof v === 'number' && Number.isFinite(v))) return
  const x = Number(p.x), y = Number(p.y), bx = Number(b.x), by = Number(b.y), bw = Number(b.width), bh = Number(b.height)
  if (bx < 0 || by < 0 || bw <= 0 || bh <= 0 || bx + bw > m.width || by + bh > m.height || x < bx || x >= bx + bw || y < by || y >= by + bh) return
  return a as unknown as DismissAction
}
function compareScript(): string {
  const here = dirname(fileURLToPath(import.meta.url))
  for (const up of ['../../../..', '../../..']) { const p = resolve(here, up, 'scripts/visual-region-compare.ps1'); if (existsSync(p)) return p }
  throw new Error('未找到网页局部图像核验脚本')
}
async function compareRegion(original: string, fresh: string, region: { x: number; y: number; width: number; height: number }): Promise<boolean> {
  const r = await execPowerShellScript(compareScript(), ['-Original', original, '-Fresh', fresh, '-X', String(region.x), '-Y', String(region.y), '-Width', String(region.width), '-Height', String(region.height)], { timeout: 15_000 })
  return JSON.parse(r.stdout).equal === true
}
export class VisualRecovery {
  private readonly root: string
  private readonly now: () => number
  constructor(private readonly deps: VisualRecoveryDeps = {}) { this.root = deps.root ?? join(tmpdir(), 'aid-boss-visual'); this.now = deps.now ?? Date.now }
  private directory(id: string): string { if (!UUID.test(id)) invalid('无效视觉引用'); return join(this.root, id.toLowerCase()) }
  private async locked<T>(id: string, fn: (directory: string) => Promise<T>): Promise<T> {
    const directory = this.directory(id)
    await mkdir(this.root, { recursive: true, mode: 0o700 })
    if ((await lstat(this.root)).isSymbolicLink() || (await realpath(this.root)).toLowerCase() !== resolve(this.root).toLowerCase()) invalid('视觉缓存目录不是普通本机目录')
    await mkdir(directory, { recursive: true, mode: 0o700 })
    if ((await lstat(directory)).isSymbolicLink() || (await realpath(directory)).toLowerCase() !== resolve(directory).toLowerCase()) invalid('视觉请求目录不是普通本机目录')
    const path = join(directory, 'lock'), token = randomUUID()
    let lock
    for (let attempt = 0; attempt < 2; attempt++) {
      try { lock = await open(path, 'wx'); break }
      catch (error) {
        if ((error as NodeJS.ErrnoException).code !== 'EEXIST') throw error
        let deadOwner = false
        try {
          const owner = JSON.parse(await readFile(path, 'utf8')) as { pid?: number }
          if (Number.isInteger(owner.pid) && owner.pid! > 0) {
            try { process.kill(owner.pid!, 0) }
            catch (probe) { deadOwner = (probe as NodeJS.ErrnoException).code === 'ESRCH' }
          }
        } catch { /* An incomplete/legacy lock is not evidence that its owner is dead. */ }
        if (!deadOwner || attempt) break
        try { await unlink(path) } catch (remove) { if ((remove as NodeJS.ErrnoException).code !== 'ENOENT') throw remove }
      }
    }
    if (!lock) throw new CodedOperationError('BUSY', '同一视觉请求正在执行，请稍后使用相同 request_id 查询')
    try {
      await lock.writeFile(JSON.stringify({ pid: process.pid, token }))
      return await fn(directory)
    } finally {
      await lock.close()
      // A reclaimed lock must never be removed by the previous owner's cleanup.
      try { if (JSON.parse(await readFile(path, 'utf8')).token === token) await unlink(path) } catch (error) { if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error }
    }
  }
  private async save(directory: string, record: RecordData): Promise<void> {
    const temporary = join(directory, `${randomUUID()}.json`)
    await writeFile(temporary, JSON.stringify(record), { flag: 'wx', mode: 0o600 })
    await rename(temporary, join(directory, 'record.json'))
  }
  private async removeImages(directory: string): Promise<void> {
    // Only the two known images owned by this request; keep its receipt for idempotent retrieval.
    await Promise.allSettled(['page.png', 'fresh.png', 'page.png.json', 'fresh.png.json'].map(file => unlink(join(directory, file))))
  }
  private async cleanupExpiredImages(): Promise<void> {
    try {
      if ((await lstat(this.root)).isSymbolicLink()) return
      const entries = await readdir(this.root, { withFileTypes: true })
      // Bound housekeeping and stay within this cache's UUID directories; never traverse links or recursively delete.
      for (const entry of entries.filter(e => e.isDirectory() && !e.isSymbolicLink() && UUID.test(e.name) && (existsSync(join(this.root, e.name, 'page.png')) || existsSync(join(this.root, e.name, 'fresh.png')))).slice(0, 100)) {
        const directory = this.directory(entry.name)
        try {
          if ((await realpath(directory)).toLowerCase() !== resolve(directory).toLowerCase()) continue
          const record = JSON.parse(await readFile(join(directory, 'record.json'), 'utf8')) as RecordData
          const capturedAt = Date.parse(record.captured_at)
          if (Number.isFinite(capturedAt) && this.now() - capturedAt > MAX_AGE) await this.removeImages(directory)
        } catch { /* A partial/unavailable record does not authorize deleting other files. */ }
      }
    } catch (error) { if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error }
  }
  async inspect(args: VisualInspectArgs, session: BossSession, signal: AbortSignal): Promise<Record<string, unknown>> {
    const error = validateVisualArgs(args); if (error) invalid(error)
    const id = args.request_id!.toLowerCase()
    await this.cleanupExpiredImages()
    return this.locked(id, async directory => {
      let record: RecordData | undefined
      try { record = JSON.parse(await readFile(join(directory, 'record.json'), 'utf8')) as RecordData } catch (e) { if ((e as NodeJS.ErrnoException).code !== 'ENOENT') throw e }
      if (record?.browser_target_ref && record.browser_target_ref !== session.browserTargetRef) throw stale('观察请求与原操作的浏览器绑定不一致')
      if (record && args.observation_mode !== undefined && args.observation_mode !== (record.observation_mode ?? 'page')) invalid('同一 request_id 的 observation_mode 不能改变')
      if (record && args.visual_context && JSON.stringify(record.context) !== JSON.stringify(args.visual_context)) invalid('同一 request_id 的操作上下文不能改变')
      if (!record && !args.retrieve_only) {
        const url = await session.getUrl(); assertUrl(url)
        const mode = args.observation_mode ?? 'page'
        const targetId = await session.getTargetId?.()
        // Window recovery must not call Page/DOM methods: JS dialogs may block all of them.
        const metrics = mode === 'page' ? await metricsOf(session) : undefined
        if (signal.aborted) throw new CancelledError()
        let image: Buffer, windowObservation: WindowObservation | undefined
        if (mode === 'window') {
          if (!targetId || !session.activateTarget) throw new CodedOperationError('UI_CHANGED', '当前会话不能激活绑定 BOSS 标签，未观察其他窗口')
          await session.activateTarget()
          windowObservation = await (this.deps.captureWindow ?? captureWindow)(join(directory, 'page.png'), session.cdpPort ?? 9222)
          if (!windowObservation.window.process_started_at || !Number.isFinite(Date.parse(windowObservation.window.process_started_at))) throw new CodedOperationError('UI_CHANGED', '窗口观察缺少 Chrome 进程创建身份')
          image = await readFile(join(directory, 'page.png'))
        } else image = await session.captureFullpage()
        const metadata = pngMetadata(image)
        if (await session.getUrl() !== url || await session.getTargetId?.() !== targetId || mode === 'page' && !sameMetrics(metrics, await metricsOf(session))) throw stale('截图期间页面或视口已切换，请重新观察')
        record = { request_id: id, recovery_ref: randomUUID(), url, captured_at: windowObservation?.captured_at ?? new Date(this.now()).toISOString(), context: args.visual_context!, image: { width: metadata.width, height: metadata.height }, target_id: targetId, page_metrics: metrics, observation_mode: mode, window_observation: windowObservation, browser_target_ref: session.browserTargetRef }
        if (mode === 'page') await writeFile(join(directory, 'page.png'), image, { flag: 'wx', mode: 0o600 })
        await this.save(directory, record)
      }
      if (signal.aborted) throw new CancelledError()
      // Existing references retrieve the original backend receipt. Never recapture or silently create a new paid request.
      const previous = record?.response
      let response: { status: number; body: unknown }
      if (previous && object(previous.body) && previous.body.state === 'completed') response = previous
      else {
        const firstDispatch = !!record && this.now() - Date.parse(record.captured_at) <= MAX_AGE && existsSync(join(directory, 'page.png')) && !existsSync(join(directory, 'dispatched')) && !args.retrieve_only
        if (firstDispatch) await writeFile(join(directory, 'dispatched'), '', { flag: 'wx', mode: 0o600 })
        try {
          response = await (this.deps.diagnose ?? visualDiagnose)(firstDispatch
            ? { requestId: id, apiVersion: 2, imageBytes: await readFile(join(directory, 'page.png')), context: record!.context, capturedAt: record!.captured_at, windowObservation: record!.window_observation, signal }
            : { requestId: id, apiVersion: 2, retrieveOnly: true, signal })
        } catch (error) {
          if (error instanceof VisualDiagnoseError && !error.receiptUnknown) {
            if (firstDispatch) await unlink(join(directory, 'dispatched'))
            throw new CodedOperationError('INVALID_ARGUMENT', error.message)
          }
          if (record && this.now() - Date.parse(record.captured_at) > MAX_AGE) await this.removeImages(directory)
          return { visual: { request_id: id, http_status: null, state: 'unknown', diagnosis: null, diagnosis_error: 'RECEIPT_UNAVAILABLE' }, message: '视觉回执未知，请使用同一 request_id 取回；未执行恢复点击' }
        }
        if (record) { record.response = response; await this.save(directory, record) }
      }
      const body = object(response.body) ? response.body : { response: response.body }
      const result: Record<string, unknown> = { visual: { ...body, request_id: id, http_status: response.status } }
      if (record) result.observation = { mode: record.observation_mode ?? 'page', space: record.observation_mode === 'window' ? 'window_image_px' : 'page_image_px', ...record.image }
      if (record) {
        const age = this.now() - Date.parse(record.captured_at)
        const consumed = existsSync(join(directory, 'consumed'))
        if (consumed || age < 0 || age > MAX_AGE) {
          await this.removeImages(directory)
          result.recovery_action_error = consumed ? 'ALREADY_CONSUMED' : 'STALE_OBSERVATION'
          return result
        }
        const image = record.image ? undefined : await readFile(join(directory, 'page.png'))
        if (actionOf(record, image)) {
          const ref = join(this.root, `${record.recovery_ref}.ref`)
          try { await writeFile(ref, id, { flag: 'wx', mode: 0o600 }) } catch (e) { if ((e as NodeJS.ErrnoException).code !== 'EEXIST') throw e }
          result.recovery_ref = record.recovery_ref
        } else if (object(response.body) && response.body.state === 'completed') await this.removeImages(directory)
      }
      return result
    })
  }
  async dismiss(ref: string, session: BossSession, signal: AbortSignal): Promise<Record<string, unknown>> {
    if (!UUID.test(ref)) invalid('无效 recovery_ref')
    let id: string
    try { id = await readFile(join(this.root, `${ref.toLowerCase()}.ref`), 'utf8') } catch { throw stale('视觉恢复引用不存在，请重新诊断') }
    return this.locked(id, async directory => {
      const record = JSON.parse(await readFile(join(directory, 'record.json'), 'utf8')) as RecordData
      if (record.recovery_ref !== ref.toLowerCase()) invalid('视觉恢复引用不匹配')
      if (record.browser_target_ref && record.browser_target_ref !== session.browserTargetRef) throw stale('恢复引用与原操作的浏览器绑定不一致')
      const age = this.now() - Date.parse(record.captured_at)
      if (existsSync(join(directory, 'consumed'))) throw new CodedOperationError('UI_CHANGED', '视觉恢复引用已消费，不重复输入', { recovery_action_error: 'ALREADY_CONSUMED', input_status: 'not_started' })
      if (age < 0 || age > MAX_AGE) { await this.removeImages(directory); throw stale('视觉观察已过期，请重新诊断') }
      let original: Buffer
      try { original = await readFile(join(directory, 'page.png')) } catch { throw stale('原观察图片已清理，请重新诊断') }
      const action = actionOf(record, original)
      if (!action) throw new CodedOperationError('UI_CHANGED', '保存的诊断没有可执行的关闭或 Escape 建议', { recovery_action_error: 'NO_VALID_ACTION', input_status: 'not_started' })
      const url = await session.getUrl()
      if (url !== record.url) throw stale('页面已切换，未执行输入')
      const targetId = await session.getTargetId?.()
      if (targetId !== record.target_id) throw stale('标签页或 CSS 视口已改变，未执行输入')
      const mode = record.observation_mode ?? 'page'
      const a = pngMetadata(original)
      let metrics: PageMetrics | undefined, inputPoint: { x: number; y: number } | undefined
      try {
        if (mode === 'window') {
          if (!record.window_observation || !session.activateTarget) throw stale('缺少原窗口绑定，未执行输入')
          await session.activateTarget()
          let freshWindow: WindowObservation
          try { freshWindow = await (this.deps.captureWindow ?? captureWindow)(join(directory, 'fresh.png'), session.cdpPort ?? 9222) }
          catch { throw stale('无法重新确认绑定窗口或前景，请重新观察') }
          const before = record.window_observation, after = freshWindow
          if (before.cdp_port !== after.cdp_port || !sameWindow(before, after)) throw stale('窗口身份或几何已改变，未执行输入')
          // clickWindow repeats its local-pixel/ownership guard immediately before DOWN.
          if (action.kind === 'dismiss') inputPoint = { x: Math.floor(action.point.x), y: Math.floor(action.point.y) }
        } else {
          metrics = await metricsOf(session)
          if (!sameMetrics(metrics, record.page_metrics)) throw stale('标签页或 CSS 视口已改变，未执行输入')
          if (action.kind === 'dismiss') {
            const fresh = await session.captureFullpage(), b = pngMetadata(fresh)
            if (a.width !== b.width || a.height !== b.height) throw stale('视口尺寸已改变，未执行输入')
            const region = { x: Math.max(0, Math.min(a.width - Math.min(32, a.width), Math.floor(action.point.x) - 16)), y: Math.max(0, Math.min(a.height - Math.min(32, a.height), Math.floor(action.point.y) - 16)), width: Math.min(32, a.width), height: Math.min(32, a.height) }
            const freshPath = join(directory, 'fresh.png'); await writeFile(freshPath, fresh, { mode: 0o600 })
            if (!await (this.deps.compare ?? compareRegion)(join(directory, 'page.png'), freshPath, region)) throw stale('关闭控件已变化，未执行点击')
            inputPoint = { x: action.point.x * (metrics?.width ?? a.width) / a.width, y: action.point.y * (metrics?.height ?? a.height) / a.height }
          }
        }
        if (await session.getUrl() !== record.url || await session.getTargetId?.() !== record.target_id || mode === 'page' && !sameMetrics(await metricsOf(session), record.page_metrics)) throw stale('页面、标签或视口已变化，未执行输入')
      } catch (error) {
        if (error instanceof CodedOperationError || error instanceof CancelledError) throw error
        throw stale('无法重新确认页面或窗口绑定，未投递关闭输入')
      }
      if (signal.aborted) throw new CancelledError()
      // Claim before input: an interrupted/unknown input must never replay this reference.
      try { await writeFile(join(directory, 'consumed'), '', { flag: 'wx', mode: 0o600 }) }
      catch (error) { if ((error as NodeJS.ErrnoException).code === 'EEXIST') throw new CodedOperationError('UI_CHANGED', '视觉恢复引用已消费，不重复输入', { recovery_action_error: 'ALREADY_CONSUMED', input_status: 'not_started' }); throw error }
      try {
        if (mode === 'window') {
          if (action.kind === 'press_escape') await (this.deps.escapeWindow ?? escapeWindow)(join(directory, 'page.png.json'))
          else await (this.deps.clickWindow ?? clickWindow)(join(directory, 'page.png.json'), inputPoint!.x, inputPoint!.y)
        } else if (action.kind === 'press_escape') await session.pressEscape()
        else await session.clickBrowse(inputPoint!)
      } catch (error) {
        const message = error instanceof Error ? error.message : ''
        if (mode === 'window' && !message.includes('[BOSS_INPUT_UNKNOWN]') && (message.includes('[BOSS_WINDOW_REFUSED]') || message.includes('STALE_OBSERVATION') || message.includes('[BOSS_POWERSHELL_PREPARE]') || message.includes('[BOSS_POWERSHELL_LAUNCH]'))) throw stale('窗口关闭输入在投递前被拒绝，请重新观察')
        throw new CodedOperationError('EXECUTION_UNKNOWN', '关闭输入结果未知；引用已消费，请重新观察，勿重放', { input_status: 'unknown', recovery_action_error: 'INPUT_UNKNOWN', recovery_ref: ref, request_id: id })
      } finally { await this.removeImages(directory) }
      try { await sleep(this.deps.settleMs ?? 800, undefined, { signal }) } catch { throw new CodedOperationError('CANCELLED', '关闭输入已投递，等待渲染期间取消；请重新观察', { input_status: 'attempted', recovery_ref: ref, request_id: id }) }
      return { recovery_ref: ref, request_id: id, input_status: 'attempted', action_kind: action.kind, space: mode === 'window' ? 'window_image_px' : 'page_image_px', input_space: mode === 'window' ? 'window_image_px' : action.kind === 'dismiss' ? 'cdp_css_px' : 'cdp_key', ...(inputPoint ? { input_point: inputPoint } : {}), target_label: action.target_label }
    })
  }
}

function stale(message: string): CodedOperationError {
  return new CodedOperationError('UI_CHANGED', `STALE_OBSERVATION: ${message}`, { recovery_action_error: 'STALE_OBSERVATION', input_status: 'not_started' })
}
function sameWindow(a: WindowObservation, b: WindowObservation): boolean {
  const x = a.window, y = b.window
  return !!x.process_started_at && x.process_started_at === y.process_started_at && x.hwnd === y.hwnd && x.pid === y.pid && x.left === y.left && x.top === y.top && x.width === y.width && x.height === y.height
}
