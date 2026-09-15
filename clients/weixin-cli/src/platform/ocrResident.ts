/**
 * 常驻 OCR 客户端（C2，设计 §6）。
 *
 * 优先随包 embedded Python，开发环境回退仓库 venv；运行 drivers/py/ocr_server.py；
 * 崩溃显式抛 OcrServerUnavailable（调用方不得以空结果冒充），可重启，
 * 重启后调用方必须重新对齐观察水位（设计 §6）。
 * 不升级最低 Node 版本、不引入 native 依赖。
 */
import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { join } from 'node:path'
import { existsSync } from 'node:fs'

export interface OcrBox {
  text: string
  score: number
  x0: number
  y0: number
  x1: number
  y1: number
}

export class OcrServerUnavailable extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'OcrServerUnavailable'
  }
}

export interface ResidentOcrOptions {
  /** 有界请求等待，测试可缩短；不延长发送许可。 */
  requestTimeoutMs?: number
  /** venv python 绝对路径（测试注入；默认按仓库布局推导） */
  pythonPath?: string
  /** ocr_server.py 绝对路径 */
  scriptPath?: string
}

export function defaultPythonPath(scriptDir = fileURLToPath(new URL('.', import.meta.url))): string {
  // Portable distributions put the embedded runtime at the Provider package root.
  // Prefer it to the developer fallback, so relocation needs no repository venv.
  const bundled = join(scriptDir, '..', '..', '..', 'ocr-python', 'python.exe')
  if (existsSync(bundled)) return bundled
  const repoRoot = join(scriptDir, '..', '..', '..', '..', '..')
  return join(repoRoot, 'venv', 'Scripts', 'python.exe')
}

function defaultScriptPath(): string {
  const scriptDir = fileURLToPath(new URL('.', import.meta.url))
  return join(scriptDir, '..', '..', '..', 'drivers', 'py', 'ocr_server.py')
}

type OcrPending = { resolve: (boxes: OcrBox[]) => void; reject: (error: Error) => void }
type OcrContext = {
  proc: ChildProcessWithoutNullStreams
  pending: Map<number, OcrPending>
  ready: Promise<void>
  stop: (error: Error) => void
}

export class ResidentOcr {
  private context: OcrContext | null = null
  private nextId = 1
  constructor(private readonly opts: ResidentOcrOptions = {}) {}

  private start(): OcrContext {
    if (this.context) return this.context
    const proc = spawn(this.opts.pythonPath ?? defaultPythonPath(), [this.opts.scriptPath ?? defaultScriptPath()],
      { stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true }) as ChildProcessWithoutNullStreams
    const pending = new Map<number, OcrPending>()
    let buffer = '', stopped = false, warmed = false
    let readyResolve!: () => void, readyReject!: (error: Error) => void
    const ready = new Promise<void>((resolve, reject) => { readyResolve = resolve; readyReject = reject })
    const context: OcrContext = { proc, pending, ready, stop: error => {
      if (stopped) return
      stopped = true
      clearTimeout(startupTimer)
      if (this.context === context) this.context = null
      if (!warmed) readyReject(error)
      for (const request of pending.values()) request.reject(error)
      pending.clear()
      proc.kill()
    } }
    const startupTimer = setTimeout(() => context.stop(new OcrServerUnavailable('OCR 服务启动超时（30s）')), 30_000)
    this.context = context
    proc.on('error', () => context.stop(new OcrServerUnavailable('OCR 服务启动失败')))
    proc.on('exit', () => context.stop(new OcrServerUnavailable('OCR 服务进程退出')))
    proc.stdin.on('error', () => context.stop(new OcrServerUnavailable('OCR 请求写入失败')))
    proc.stderr.on('data', () => { /* Never log OCR text or paths. */ })
    proc.stdout.setEncoding('utf8')
    proc.stdout.on('data', (chunk: string) => {
      if (stopped) return
      buffer += chunk
      if (buffer.length > 4_000_000) { context.stop(new OcrServerUnavailable('OCR 响应过大')); return }
      let index: number
      while ((index = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, index); buffer = buffer.slice(index + 1)
        if (!line.trim()) continue
        try {
          const message = JSON.parse(line) as { id: number; ok: boolean; boxes?: Array<[string, number, number, number, number, number]> }
          const request = pending.get(message.id)
          if (!request) continue
          pending.delete(message.id)
          if (!message.ok) request.reject(new OcrServerUnavailable('OCR 识别失败'))
          else if (!Array.isArray(message.boxes) || message.boxes.length > 2048 || message.boxes.some(b =>
            !Array.isArray(b) || b.length !== 6 || typeof b[0] !== 'string' || b[0].length > 20_000 ||
            b.slice(1).some(n => typeof n !== 'number' || !Number.isFinite(n)) || b[1] < 0 || b[1] > 1 ||
            b[2] < 0 || b[3] < 0 || b[4] <= b[2] || b[5] <= b[3])) request.reject(new OcrServerUnavailable('OCR 响应无效'))
          else request.resolve(message.boxes.map(b => ({ text:b[0], score:b[1], x0:b[2], y0:b[3], x1:b[4], y1:b[5] })))
        } catch { context.stop(new OcrServerUnavailable('OCR 响应格式无效')); return }
      }
    })
    const probeId = this.nextId++
    const warm = () => { if (stopped) return; warmed = true; clearTimeout(startupTimer); readyResolve() }
    pending.set(probeId, { resolve: warm, reject: warm })
    proc.stdin.write(JSON.stringify({ id: probeId, image: '' }) + '\n')
    return context
  }

  async ensureStarted(): Promise<void> { await this.start().ready }

  /** crop用原图坐标；返回框相对于裁剪图。取消/超时停止该代进程，不重试本次观察。 */
  async recognize(imagePath: string, crop?: [number, number, number, number], signal?: AbortSignal): Promise<OcrBox[]> {
    if (signal?.aborted) throw new OcrServerUnavailable('OCR 请求已取消')
    const context = this.start()
    const abort = () => context.stop(new OcrServerUnavailable('OCR 请求已取消'))
    signal?.addEventListener('abort', abort, { once: true })
    if (signal?.aborted) abort()
    try {
      await context.ready
      if (signal?.aborted) throw new OcrServerUnavailable('OCR 请求已取消')
      if (this.context !== context) throw new OcrServerUnavailable('OCR 服务已停止')
      const id = this.nextId++
      return await new Promise<OcrBox[]>((resolve, reject) => {
        const timer = setTimeout(() => context.stop(new OcrServerUnavailable('OCR 请求超时')), this.opts.requestTimeoutMs ?? 30_000)
        context.pending.set(id, {
          resolve: boxes => { clearTimeout(timer); resolve(boxes) },
          reject: error => { clearTimeout(timer); reject(error) },
        })
        context.proc.stdin.write(JSON.stringify({ id, image: imagePath, crop }) + '\n')
      })
    } finally { signal?.removeEventListener('abort', abort) }
  }

  async shutdown(): Promise<void> {
    const context = this.context
    if (!context) return
    context.stop(new OcrServerUnavailable('OCR 服务已停止'))
    if (context.proc.exitCode !== null) return
    await new Promise<void>(resolve => {
      const timer = setTimeout(resolve, 2000)
      context.proc.once('exit', () => { clearTimeout(timer); resolve() })
    })
  }
}
