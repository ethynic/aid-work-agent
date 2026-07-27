/**
 * OCR Provider 接口（设计文档 §8.3）。
 * - provider 必须本机进程内或本机可执行程序，禁止在线 OCR API
 * - 未配置 / 程序不可用 / 输出字段或坐标非法 → fail-loud 并暂停
 * - 支持长图和分片回退两种输入
 * - 低置信度内容显式标记，不自动修正事实
 *
 * 具体 provider（PaddleOCR 离线 / Tesseract 等）实现此接口后注入。
 * 离线 fixture 只验证 OcrBlock 契约，不能作为识别成功的证据。
 */

/** OCR 输出文本块 */
export interface OcrBlock {
  text: string
  confidence: number // 0~1
  box: [number, number, number, number] // [x, y, w, h]
  pageIndex: number
}

export interface OcrInput {
  /** 长图或单片的 PNG buffer */
  image: Buffer
  /** 分片回退模式：该图在整个序列中的页码 */
  pageIndex?: number
}

export interface OcrResult {
  blocks: OcrBlock[]
  /** 平均置信度（provider 可不算，由 runOcrOrFail 补算） */
  meanConfidence?: number
  /** 是否有低置信度块（provider 可不算，由 runOcrOrFail 补算） */
  hasLowConfidence?: boolean
}

/** OCR provider 契约：输入图像 buffer，输出文本块 */
export interface OcrProvider {
  readonly name: string
  /** 识别单张图像 */
  recognize(input: OcrInput): Promise<OcrResult>
  /** provider 是否就绪（程序可执行 / 模型加载） */
  isAvailable(): Promise<boolean>
}

/** 校验 OCR 输出契约，非法则 fail-loud */
export function assertValidOcrResult(result: OcrResult): void {
  if (!result || !Array.isArray(result.blocks)) {
    throw new Error('OCR result missing blocks array')
  }
  for (const block of result.blocks) {
    if (typeof block.text !== 'string') throw new Error('OCR block.text must be string')
    if (typeof block.confidence !== 'number' || block.confidence < 0 || block.confidence > 1) {
      throw new Error(`OCR block.confidence out of range [0,1]: ${block.confidence}`)
    }
    if (!Array.isArray(block.box) || block.box.length !== 4) {
      throw new Error('OCR block.box must be [x,y,w,h]')
    }
    if (!block.box.every((v) => typeof v === 'number' && Number.isFinite(v))) {
      throw new Error('OCR block.box contains non-finite value')
    }
    if (typeof block.pageIndex !== 'number') throw new Error('OCR block.pageIndex must be number')
  }
}

/**
 * fail-loud OCR 执行器：provider 未配置 / 不可用 → 抛错暂停。
 * 这是设计文档 §8.3 的硬要求：OCR 失败时保存 LIST_DOM_FALLBACK 并暂停人工复核，不静默。
 */
export async function runOcrOrFail(provider: OcrProvider | null, input: OcrInput, lowConfThreshold = 0.6): Promise<OcrResult> {
  if (!provider) {
    throw new Error('OCR provider not configured — install a local OCR engine (offline only)')
  }
  const available = await provider.isAvailable()
  if (!available) {
    throw new Error(`OCR provider "${provider.name}" is not available (executable/model missing)`)
  }
  const result = await provider.recognize(input)
  assertValidOcrResult(result)
  // 计算 meanConfidence / hasLowConfidence（若 provider 未算）
  if (result.blocks.length > 0) {
    const confs = result.blocks.map((b) => b.confidence)
    const mean = confs.reduce((a, b) => a + b, 0) / confs.length
    if (result.meanConfidence === undefined || result.meanConfidence === null) result.meanConfidence = mean
    if (result.hasLowConfidence === undefined) result.hasLowConfidence = confs.some((c) => c < lowConfThreshold)
  } else {
    if (result.meanConfidence === undefined || result.meanConfidence === null) result.meanConfidence = 0
    if (result.hasLowConfidence === undefined) result.hasLowConfidence = false
  }
  return result
}
