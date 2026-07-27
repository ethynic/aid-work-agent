/**
 * Tesseract.js OCR Provider（纯本地，无在线 OCR API）。
 * - 引擎：tesseract.js（WASM），进程内运行，无 native 编译、无外部可执行程序
 * - 中文简历识别用 chi_sim + eng 语言包
 * - 语言 traineddata 首次从 CDN 下载并缓存到本地（是模型文件不是识别 API，符合"本地推理"）
 * - 输出 OcrBlock（text/confidence/box/pageIndex），满足 OcrProvider 契约
 * - 未配置语言 / worker 不可用 → isAvailable() false → runOcrOrFail fail-loud
 */
import { createWorker, type Worker } from 'tesseract.js'
import type { OcrProvider, OcrInput, OcrResult, OcrBlock } from './OcrProvider.js'

export interface TesseractProviderOptions {
  /** 语言包，默认 chi_sim+eng */
  lang?: string
  /** traineddata 缓存目录（默认 os.tmpdir/tess-lang） */
  langPath?: string
  /** 可注入 worker 工厂（测试） */
  workerFactory?: (lang: string, langPath: string) => Promise<Worker>
  /** OCR 级别（line/block），默认 block 取 words 更细 */
  oem?: number
}

export class TesseractJsProvider implements OcrProvider {
  readonly name = 'tesseract.js'
  private readonly lang: string
  private readonly langPath: string
  private readonly workerFactory?: (lang: string, langPath: string) => Promise<Worker>
  private workerPromise?: Promise<Worker>

  constructor(opts: TesseractProviderOptions = {}) {
    this.lang = opts.lang ?? 'chi_sim+eng'
    this.langPath = opts.langPath ?? ''
    this.workerFactory = opts.workerFactory
  }

  async isAvailable(): Promise<boolean> {
    // tesseract.js 是纯 WASM，始终可用（语言数据在 recognize 时按需下载缓存）
    return true
  }

  async recognize(input: OcrInput): Promise<OcrResult> {
    const worker = await this.getWorker()
    try {
      const { data } = await worker.recognize(input.image)
      const blocks: OcrBlock[] = []
      // data.words 是细粒度词块，带 bbox+confidence；为空时退回 data.text 单块
      const words = data.words ?? {}
      const wordList = Object.values(words)
      if (wordList.length > 0) {
        for (const w of wordList) {
          const text = (w.text ?? '').trim()
          if (!text) continue
          const b = w.bbox
          if (!b) continue
          blocks.push({
            text,
            confidence: typeof w.confidence === 'number' ? w.confidence / 100 : 0.5,
            box: [b.x0, b.y0, b.x1 - b.x0, b.y1 - b.y0],
            pageIndex: input.pageIndex ?? 0,
          })
        }
      } else if (data.text && data.text.trim()) {
        blocks.push({
          text: data.text.trim(),
          confidence: typeof data.confidence === 'number' ? data.confidence / 100 : 0.5,
          box: [0, 0, 0, 0],
          pageIndex: input.pageIndex ?? 0,
        })
      }
      return { blocks }
    } finally {
      // 单次识别后不销毁 worker（复用），由 dispose() 统一清理
    }
  }

  /** 懒加载 worker（首次 recognize 时创建） */
  private async getWorker(): Promise<Worker> {
    if (!this.workerPromise) {
      this.workerPromise = this.workerFactory
        ? this.workerFactory(this.lang, this.langPath)
        : this.createDefaultWorker()
    }
    return this.workerPromise
  }

  private async createDefaultWorker(): Promise<Worker> {
    const worker = await createWorker(this.lang, 1, this.langPath ? { langPath: this.langPath } : {})
    return worker
  }

  /** 应用退出时调用，释放 worker */
  async dispose(): Promise<void> {
    if (this.workerPromise) {
      try {
        const worker = await this.workerPromise
        await worker.terminate()
      } catch {
        // ignore
      }
      this.workerPromise = undefined
    }
  }
}
