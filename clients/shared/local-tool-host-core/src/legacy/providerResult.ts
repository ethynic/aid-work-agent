/** Decode the existing first-party OperationResult contract without trusting arbitrary JSON. */
function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function isOperationResult(value: unknown): value is Record<string, unknown> {
  if (!isRecord(value)) return false
  return typeof value.success === 'boolean'
    && typeof value.code === 'string' && /^[A-Z][A-Z0-9_]*$/.test(value.code)
    && (value.success ? value.code === 'OK' : value.code !== 'OK')
    && typeof value.message === 'string'
    && ['none', 'applied', 'partial', 'unknown'].includes(value.effect as string)
    && (isRecord(value.data) || value.data === null)
    && (typeof value.retryable === 'boolean'
      || (typeof value.safe_to_retry === 'boolean'
        && ['prepared', 'may_have_started', 'submitted', 'verified', 'unknown'].includes(value.phase as string)))
    && typeof value.run_id === 'string' && value.run_id.trim().length > 0
}

export interface ProviderResultDecode {
  result: Record<string, unknown>
  /** Only fixed categories/counts: never raw Provider text, keys, errors or result data. */
  diagnostic?: {
    reason: 'mcp_error' | 'invalid_envelope' | 'invalid_structured' | 'missing_result' | 'ambiguous_result'
    structuredPresent: boolean
    contentCount: number
    textCount: number
    textChars: number
  }
}

export function decodeProviderResult(reply: unknown): ProviderResultDecode {
  const envelope = isRecord(reply) ? reply : {}
  const content = Array.isArray(envelope.content) ? envelope.content : []
  const texts = content.filter((block): block is Record<string, unknown> & { text: string } =>
    isRecord(block) && block.type === 'text' && typeof block.text === 'string')
  const structuredPresent = envelope.structuredContent !== undefined
  const failure = (reason: NonNullable<ProviderResultDecode['diagnostic']>['reason']): ProviderResultDecode => ({
    result: {
      success: false, code: 'INTERNAL_ERROR', effect: 'unknown', retryable: false,
      message: reason === 'mcp_error'
        ? 'Provider 报告工具执行错误，执行结果尚未确认'
        : 'Provider 返回无法解析，执行结果尚未确认',
    },
    diagnostic: {
      reason, structuredPresent, contentCount: content.length, textCount: texts.length,
      textChars: texts.reduce((total, block) => total + block.text.length, 0),
    },
  })

  if (!isRecord(reply) || (envelope.isError !== undefined && typeof envelope.isError !== 'boolean')) {
    return failure('invalid_envelope')
  }
  // The MCP error flag must never be overridden by a success-looking payload.
  if (envelope.isError === true) return failure('mcp_error')
  if (structuredPresent) {
    return isOperationResult(envelope.structuredContent)
      ? { result: envelope.structuredContent }
      : failure('invalid_structured')
  }

  let candidate: Record<string, unknown> | undefined
  for (const block of texts) {
    let value: unknown
    try { value = JSON.parse(block.text) } catch { continue }
    if (!isOperationResult(value)) continue
    // Multiple receipts are ambiguous; do not guess which action's result is authoritative.
    if (candidate) return failure('ambiguous_result')
    candidate = value
  }
  return candidate ? { result: candidate } : failure('missing_result')
}
