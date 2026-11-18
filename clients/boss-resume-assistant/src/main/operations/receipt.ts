/**
 * 执行回执标准 1.1 的共享构造（docs/system/first-party-cli-mcp-provider-standard.md §5.3.1）。
 *
 * - 仅协商调用（OpContext.executionReceipt === '1.1'）的失败/部分完成/效果未知终态附加；
 *   成功不附加、不截图。写入既有 OperationResult.data（业务字段全保留）。
 * - known_effect 必须与外层 effect 一致：helper 以调用方传入的 effect 为准生成，
 *   并断言其为合法枚举（不一致属调用方编程错误，抛出由调用方兜底保留原结果）。
 * - 截图采用 P10 已有 MCP image 块形状 {type:"image", mimeType, data}（Base64），
 *   原始字节单张 5 MiB 上限，与云端 wecom_attachments.MAX_IMAGE_BYTES 对齐。
 * - 本 helper 不做通用回执框架：字段表固定、无扩展点（标准为 BOSS 首版专用）。
 */
import type { Effect, OperationResult } from './types.js'

/** 回执版本固定字符串（标准字段表：识别本次调用使用的扩展格式） */
export const RECEIPT_VERSION = '1.1'

/** P10 单张原始字节上限（云端 _decode_image 同值；超限省略截图、保留业务结果） */
export const MAX_SCREENSHOT_BYTES = 5 * 1024 * 1024

const EFFECTS: ReadonlySet<Effect> = new Set(['none', 'applied', 'partial', 'unknown'])

/** P10 MCP image 块形状（Base64；BOSS 首版 PNG 或 JPEG） */
export interface ReceiptImageBlock {
  type: 'image'
  mimeType: string
  data: string
}

/** 批量部分完成的已知逐项结果（标准字段表 partial_results 项形状） */
export interface ReceiptPartialItem {
  /** 稳定业务引用；无引用时用调用内冻结尝试序号（attempt:N，限本调用对账） */
  item_ref: string
  status: 'completed' | 'failed' | 'unknown' | 'skipped'
  known_effect: Effect
  reason?: { code: string; message: string }
}

export interface ReceiptInput {
  /** 外层 OperationResult.effect（known_effect 与其一致，由本 helper 保证） */
  effect: Effect
  /** 稳定的工具内诊断步骤标识（如 validate_input / connect / prepared / input / postcheck） */
  step: string
  /** 已识别的失败或部分完成原因 */
  reason?: { code: string; message: string }
  partialResults?: ReceiptPartialItem[]
  screenshot?: ReceiptImageBlock
}

export interface ReceiptData {
  receipt_version: string
  step: string
  reason?: { code: string; message: string }
  known_effect: Effect
  partial_results?: ReceiptPartialItem[]
  screenshot?: ReceiptImageBlock
}

/** 构造 data 增量字段（不含业务字段）。effect 与外层不一致/非法时抛出（编程错误）。 */
export function buildReceipt1_1(input: ReceiptInput): ReceiptData {
  if (!EFFECTS.has(input.effect)) {
    throw new Error(`known_effect 与外层 effect 必须一致且为合法枚举，收到 ${String(input.effect)}`)
  }
  if (typeof input.step !== 'string' || !input.step) {
    throw new Error('执行回执 step 必须是非空字符串')
  }
  return {
    receipt_version: RECEIPT_VERSION,
    step: input.step,
    ...(input.reason ? { reason: input.reason } : {}),
    known_effect: input.effect,
    ...(input.partialResults && input.partialResults.length > 0 ? { partial_results: input.partialResults } : {}),
    ...(input.screenshot ? { screenshot: input.screenshot } : {}),
  }
}

/** 尽力截图：失败/超限/空图一律 undefined（业务结果保留），调用方不得据此改变终态 */
export function toReceiptScreenshot(png: Buffer | undefined): ReceiptImageBlock | undefined {
  if (!png || png.length === 0 || png.length > MAX_SCREENSHOT_BYTES) return undefined
  return { type: 'image', mimeType: 'image/png', data: png.toString('base64') }
}

/** 截图预算：整页截图异常挂起时不无限拖延终态回传（CDP 网关自身另有 10s 超时兜底） */
const SCREENSHOT_BUDGET_MS = 5_000

/** 有界单张截图：超时/失败/超限/空图一律 undefined，绝不抛出 */
export async function captureBoundedScreenshot(capture: () => Promise<Buffer>): Promise<ReceiptImageBlock | undefined> {
  let timer: ReturnType<typeof setTimeout> | undefined
  try {
    const png = await Promise.race([
      capture(),
      new Promise<never>((_, reject) => {
        timer = setTimeout(() => reject(new Error('screenshot budget exceeded')), SCREENSHOT_BUDGET_MS)
      }),
    ])
    return toReceiptScreenshot(png)
  } catch {
    return undefined
  } finally {
    if (timer) clearTimeout(timer)
  }
}

export interface ReceiptAttachOptions {
  /** 稳定的工具内诊断步骤标识（validate_input / connect / prepared / input / postcheck / execute） */
  step: string
  /** 尽力截图通道（有页面时提供；无页面/失败/超限省略截图） */
  capture?: () => Promise<Buffer>
  reason?: ReceiptInput['reason']
  partialResults?: ReceiptPartialItem[]
}

/**
 * 失败/部分完成/效果未知终态的 1.1 回执附加（仅协商调用进入；成功结果不值得回执原样返回）。
 * 回执是既有业务结果的增强信息：任何构造不一致保留原回执（fail-to-legacy，与
 * filter_evidence「形状异常按旧回执处理」同哲学），绝不因回执失败改变终态或丢字段。
 */
export async function attachReceipt1_1(result: OperationResult, opts: ReceiptAttachOptions): Promise<OperationResult> {
  if (result.data.receipt_version === RECEIPT_VERSION) return result
  if (result.success && result.effect !== 'partial' && result.effect !== 'unknown'
      && !opts.reason && !opts.partialResults?.length) return result
  try {
    const screenshot = opts.capture ? await captureBoundedScreenshot(opts.capture) : undefined
    const receipt = buildReceipt1_1({
      effect: result.effect,
      step: opts.step,
      reason: opts.reason ?? { code: result.code, message: result.message },
      partialResults: opts.partialResults,
      screenshot,
    })
    return { ...result, data: { ...result.data, ...receipt } }
  } catch {
    process.stderr.write('[boss-op] 执行回执 1.1 构造失败，保留原回执\n')
    return result
  }
}
