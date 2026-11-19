/**
 * 执行回执标准 1.1 的 Runtime 侧形状验证（docs/system/first-party-cli-mcp-provider-standard.md
 * §5.3.1「MCP、Runtime 与云端结果通路」第 3 条）。
 *
 * - 仅协商调用（领取响应附加 receipt_version）接受 1.1 扩展；未协商调用 data 含
 *   receipt_version 一律剥离（防伪造，同 _runtime_completion 保留键先例）。
 * - 验证失败（未知版本/known_effect 缺失或非法/step/reason/partial_results/screenshot
 *   形状坏）→ 整块扩展按旧回执语义处理：剥离全部 1.1 键、保留业务字段、记脱敏诊断
 *  （与 filter_evidence「版本未知或形状异常一律按旧回执处理」同哲学）。
 * - 已协商且两个合法效果值相互矛盾（known_effect ≠ 外层 effect）→ 保留原结果原扩展，
 *   记脱敏诊断；自动恢复按 unknown 判断是 Runner/服务端职责，Runtime 不改写不重试。
 * - 诊断只记键名与形状事实，不记 payload 值（脱敏）。
 */

/** 1.1 扩展占用的 data 键（剥离集合；业务字段永不触碰） */
export const RECEIPT_DATA_KEYS: readonly string[] = [
  'receipt_version', 'step', 'reason', 'known_effect', 'partial_results', 'screenshot',
]

/** 执行回执效果枚举（与外层 Effect 同集） */
const RECEIPT_EFFECTS: ReadonlySet<string> = new Set(['none', 'applied', 'partial', 'unknown'])

/** 批量逐项状态枚举（标准字段表） */
const PARTIAL_STATUSES: ReadonlySet<string> = new Set(['completed', 'failed', 'unknown', 'skipped'])

export const EXECUTION_RECEIPT_META_KEY = 'aidwork/receipt_version'

export interface ReceiptValidation {
  /** 是否从 data 剥离全部 1.1 键（验证失败/未协商防伪造） */
  strip: boolean
  /** 脱敏诊断（只含形状事实，不含 payload 值） */
  diagnostics: string[]
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

/** reason 合法形状：{code: string, message: string}（标准：稳定大写标识 + 可展示简短原因） */
const validReason = (value: unknown): boolean =>
  isRecord(value) && typeof value['code'] === 'string' && value['code'].length > 0
    && typeof value['message'] === 'string' && value['message'].length > 0

/** partial_results 项合法形状：{item_ref: string, status, known_effect, reason?} */
const validPartialItem = (value: unknown): boolean => {
  if (!isRecord(value)) return false
  if (typeof value['item_ref'] !== 'string' || value['item_ref'].length === 0) return false
  if (typeof value['status'] !== 'string' || !PARTIAL_STATUSES.has(value['status'])) return false
  if (typeof value['known_effect'] !== 'string' || !RECEIPT_EFFECTS.has(value['known_effect'])) return false
  return value['reason'] === undefined || validReason(value['reason'])
}

/** screenshot 合法形状：P10 MCP image 块 {type:"image", mimeType, data} */
const validScreenshot = (value: unknown): boolean =>
  isRecord(value) && value['type'] === 'image' && typeof value['mimeType'] === 'string'
  && value['mimeType'].startsWith('image/') && typeof value['data'] === 'string' && value['data'].length > 0

/**
 * 验证 settled.data 中的 1.1 扩展。negotiated=本次调用领取响应是否附加 receipt_version。
 * 返回剥离决策与脱敏诊断；调用方据此 delete data[key] 并 logError。
 */
export function validateExecutionReceipt(data: Record<string, unknown>, negotiated: boolean): ReceiptValidation {
  const diagnostics: string[] = []
  // Versionless receipts belong to the original plugin contract, including
  // business fields that happen to share names with 1.1 extensions.
  if (data['receipt_version'] === undefined) return { strip: false, diagnostics }
  if (!negotiated) {
    diagnostics.push('未协商调用携带 receipt_version（防伪造），已剥离扩展键')
    return { strip: true, diagnostics }
  }

  const version = data['receipt_version']
  if (version !== '1.1') {
    diagnostics.push(`未知执行回执版本（非 "1.1"），按旧回执语义剥离扩展键`)
    return { strip: true, diagnostics }
  }

  const knownEffect = data['known_effect']
  if (typeof knownEffect !== 'string' || !RECEIPT_EFFECTS.has(knownEffect)) {
    diagnostics.push('known_effect 缺失或非合法枚举，按旧回执语义剥离扩展键')
    return { strip: true, diagnostics }
  }
  if (data['step'] !== undefined && (typeof data['step'] !== 'string' || data['step'].length === 0)) {
    diagnostics.push('step 非法，按旧回执语义剥离扩展键')
    return { strip: true, diagnostics }
  }
  if (data['reason'] !== undefined && !validReason(data['reason'])) {
    diagnostics.push('reason 形状非法（需 {code,message} 字符串），按旧回执语义剥离扩展键')
    return { strip: true, diagnostics }
  }
  const partial = data['partial_results']
  if (partial !== undefined && (!Array.isArray(partial) || !partial.every(validPartialItem))) {
    diagnostics.push('partial_results 形状非法（项需 item_ref/status/known_effect），按旧回执语义剥离扩展键')
    return { strip: true, diagnostics }
  }
  if (data['screenshot'] !== undefined && !validScreenshot(data['screenshot'])) {
    diagnostics.push('screenshot 形状非法（需 P10 image 块），按旧回执语义剥离扩展键')
    return { strip: true, diagnostics }
  }

  // 形状全部合法：扩展随结果透传。known_effect 与外层 effect 的矛盾判定需 settled 外层值
  //（不在 data 内），由调用方用 receiptEffectContradiction 单独判定并记诊断。
  return { strip: false, diagnostics }
}

/**
 * 矛盾判定独立入口：settled 外层 effect 与 known_effect 均为合法值且不同 → true。
 * Runtime 保留原结果与扩展，只记脱敏诊断；自动恢复按 unknown 是消费者职责。
 */
export function receiptEffectContradiction(outerEffect: unknown, knownEffect: unknown): boolean {
  return typeof outerEffect === 'string' && RECEIPT_EFFECTS.has(outerEffect)
    && typeof knownEffect === 'string' && RECEIPT_EFFECTS.has(knownEffect)
    && outerEffect !== knownEffect
}
