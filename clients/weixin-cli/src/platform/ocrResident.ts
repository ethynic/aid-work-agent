/**
 * 常驻 OCR 客户端（C2，设计 §6）。
 *
 * spawn 仓库 venv python 跑 drivers/py/ocr_server.py（一次启动多次请求）；
 * 崩溃显式抛 OcrServerUnavailable（调用方不得以空结果冒充），可重启，
 * 重启后调用方必须重新对齐观察水位（设计 §6）。
 * 不升级最低 Node 版本、不引入 native 依赖。
 */
import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { join } from 'node:path'

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
  /** venv python 绝对路径（测试注入；默认按仓库布局推导） */
  pythonPath?: string
  /** ocr_server.py 绝对路径 */
  scriptPath?: string
}

function defaultPythonPath(): string {
  const scriptDir = fileURLToPath(new URL('.', import.meta.url)) // dist/src/platform/
  const repoRoot = join(scriptDir, '..', '..', '..', '..', '..')
  return join(repoRoot, 'venv', 'Scripts', 'python.exe')
}

function defaultScriptPath(): string {
  const scriptDir = fileURLToPath(new URL('.', import.meta.url))
  return join(scriptDir, '..', '..', '..', 'drivers', 'py', 'ocr_server.py')
}

export class ResidentOcr {
  private proc: ChildProcessWithoutNullStreams | null = null
  private nextId = 1
  private readonly pending = new Map<number, { resolve: (b: OcrBox[]) => void; reject: (e: Error) => void }>()
  private buffer = ''
  private starting: Promise<void> | null = null

  constructor(private readonly opts: ResidentOcrOptions = {}) {}

  /** 启动常驻进程（幂等）；失败抛 OcrServerUnavailable */
  async ensureStarted(): Promise<void> {
    if (this.proc && this.proc.exitCode === null) return
    if (this.starting) return this.starting
    this.starting = new Promise<void>((resolve, reject) => {
      const python = this.opts.pythonPath ?? defaultPythonPath()
      const script = this.opts.scriptPath ?? defaultScriptPath()
      const proc = spawn(python, [script], { stdio: ['pipe', 'pipe', 'pipe'] }) as ChildProcessWithoutNullStreams
      const fail = (why: string) => {
        this.starting = null
        for (const p of this.pending.values()) p.reject(new OcrServerUnavailable(why))
        this.pending.clear()
        reject(new OcrServerUnavailable(why))
      }
      proc.on('error', (err) => fail(`OCR 服务启动失败: ${err.message}`))
      let warmed = false
      proc.stdout.setEncoding('utf-8')
      proc.stdout.on('data', (chunk: string) => {
        this.buffer += chunk
        let idx: number
        while ((idx = this.buffer.indexOf('\n')) >= 0) {
          const line = this.buffer.slice(0, idx)
          this.buffer = this.buffer.slice(idx + 1)
          if (!line.trim()) continue
          try {
            const msg = JSON.parse(line) as { id: number | null; ok: boolean; boxes?: unknown; error?: string }
            if (msg.id === null) {
              // 启动横幅/无 id 输出：首个响应即视为就绪
              if (!warmed) {
                warmed = true
                this.starting = null
                resolve()
              }
              continue
            }
            const p = this.pending.get(msg.id)
            if (!p) continue
            this.pending.delete(msg.id)
            if (msg.ok) {
              const boxes = (msg.boxes as Array<[string, number, number, number, number, number]> | undefined) ?? []
              p.resolve(
                boxes.map((b) => ({ text: b[0], score: b[1], x0: b[2], y0: b[3], x1: b[4], y1: b[5] })),
              )
            } else {
              p.reject(new OcrServerUnavailable(`OCR 请求失败: ${msg.error ?? 'unknown'}`))
            }
          } catch {
            /* 非 JSON 行忽略 */
          }
        }
      })
      proc.stderr.setEncoding('utf-8')
      proc.stderr.on('data', () => {
        /* 噪声忽略；崩溃经 exit 检测 */
      })
      proc.on('exit', () => {
        const pending = [...this.pending.values()]
        this.pending.clear()
        this.proc = null
        this.starting = null
        for (const p of pending) p.reject(new OcrServerUnavailable('OCR 服务进程退出'))
        if (!warmed) fail('OCR 服务进程启动后立即退出')
      })
      // 就绪探针：对空图片路径发一个请求，收到任意响应（ok:false 走 reject，
      // 也证明服务已就绪——评审 P1-3）；30s 启动超时兜底
      const probeId = this.nextId++
      this.proc = proc
      const finishWarm = () => {
        if (warmed) return
        warmed = true
        clearTimeout(startupTimer)
        this.starting = null
        resolve()
      }
      const startupTimer = setTimeout(() => {
        if (!warmed) {
          proc.kill()
          fail('OCR 服务启动超时（30s）')
        }
      }, 30_000)
      startupTimer.unref?.()
      this.pending.set(probeId, {
        resolve: finishWarm,
        reject: finishWarm,
      })
      proc.stdin.write(JSON.stringify({ id: probeId, image: '' }) + '\n')
    })
    return this.starting
  }

  /** OCR 一张图（可选裁剪区域，坐标为原图像素）；进程不可用自动重启一次 */
  async recognize(imagePath: string, crop?: [number, number, number, number]): Promise<OcrBox[]> {
    await this.ensureStarted()
    const attempt = async (): Promise<OcrBox[]> => {
      const proc = this.proc
      if (!proc) throw new OcrServerUnavailable('OCR 服务不可用')
      const id = this.nextId++
      return new Promise<OcrBox[]>((resolve, reject) => {
        this.pending.set(id, { resolve, reject })
        proc.stdin.write(JSON.stringify({ id, image: imagePath, crop }) + '\n')
      })
    }
    try {
      return await attempt()
    } catch (err) {
      if (!(err instanceof OcrServerUnavailable)) throw err
      // 一次自动重启重试（重启后调用方须重新对齐水位）
      this.proc = null
      await this.ensureStarted()
      return attempt()
    }
  }

  async shutdown(): Promise<void> {
    const proc = this.proc
    this.proc = null
    if (!proc) return
    await new Promise<void>((resolve) => {
      proc.on('exit', () => resolve())
      proc.stdin.end()
      setTimeout(() => {
        proc.kill()
        resolve()
      }, 2_000).unref?.()
    })
  }
}
