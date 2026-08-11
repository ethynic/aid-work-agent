/**
 * operation 执行期任意错误 → 统一错误码（设计 §4.3）。
 *
 * 匹配顺序即判定优先级：取消 > 主动 code > 系统命令缺失 > 兜底。
 * M1 只有只读探测，消息标记表随 M2+ 的 driver/executor 落地再扩充。
 */
import { CancelledError, CodedOperationError, type ErrorCode } from './types.js'

export interface MappedError {
  code: ErrorCode
  message: string
}

/** 系统命令缺失标记（tasklist/where 等只读命令不可执行，属实现/部署错误而非环境问题） */
const COMMAND_MISSING_MARKERS = ['ENOENT', '不是内部或外部命令', 'is not recognized']

export function mapExecutorError(err: unknown): MappedError {
  if (err instanceof CancelledError) {
    return { code: 'CANCELLED', message: err.message }
  }
  if (err instanceof CodedOperationError) {
    return { code: err.code, message: err.message }
  }
  const message = err instanceof Error ? err.message : String(err)
  const causeCode = (err as { cause?: { code?: unknown } } | null)?.cause?.code
  if (causeCode === 'ENOENT' || COMMAND_MISSING_MARKERS.some((m) => message.includes(m))) {
    return { code: 'INTERNAL_ERROR', message: `系统命令缺失或不可执行：${message}` }
  }
  return { code: 'INTERNAL_ERROR', message: `未预期错误：${message}` }
}
