/**
 * CDP 协议审计（设计文档 §6.3）。
 * 脱敏：禁止记录响应正文、Cookie、securityId、完整候选人隐私。
 * 本模块只提供脱敏摘要与 AuditWriter 接口；具体写入实现由调用方注入。
 */

export type CdpAuditStatus = 'OK' | 'ERROR' | 'FORBIDDEN'

export interface CdpAuditEntry {
  method: string
  sessionId?: string
  params: Record<string, unknown>
  durationMs: number
  status: CdpAuditStatus
}

/** 敏感字段名正则（脱敏过滤） */
const SENSITIVE_KEY = /cookie|security|body|data|token|authorization|source/i

/** 对参数做方法相关的摘要（不记录完整 params） */
export function summarizeParams(
  method: string,
  params: Record<string, unknown>,
): Record<string, unknown> {
  if (method === 'Page.captureScreenshot') {
    return {
      format: params['format'],
      captureBeyondViewport: params['captureBeyondViewport'],
    }
  }
  if (method.startsWith('Input.')) {
    return {
      type: params['type'],
      x: params['x'],
      y: params['y'],
      deltaY: params['deltaY'],
      key: params['key'],
    }
  }
  const r = redact(params)
  return typeof r === 'object' && r !== null ? (r as Record<string, unknown>) : {}
}

function redact(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(redact)
  if (!value || typeof value !== 'object') return value
  const obj = value as Record<string, unknown>
  const out: Record<string, unknown> = {}
  for (const [key, nested] of Object.entries(obj)) {
    if (SENSITIVE_KEY.test(key)) continue
    out[key] = redact(nested)
  }
  return out
}

export interface AuditWriter {
  write(entry: CdpAuditEntry): Promise<void> | void
}
