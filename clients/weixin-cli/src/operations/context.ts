/**
 * operation 执行骨架（设计 §4.2：永不 throw、参数校验 fail-fast、统一结果）。
 *
 * 与 BOSS reference 的 runBossOperation 同范式；M1 尚无 driver/session 概念，
 * body 直接接收 OpContext，M2 引入 PowerShell driver 后在此扩展会话工厂。
 */
import { randomUUID } from 'node:crypto'
import {
  CancelledError,
  failResult,
  okResult,
  writeEffect,
  type Effect,
  type OperationResult,
  type OpContext,
} from './types.js'
import { mapExecutorError } from './errorMapping.js'

/** signal 触发时让挂起的 Promise 立即以 CancelledError 拒绝（子进程等待阶段也需要可取消） */
export function withAbort<T>(promise: Promise<T>, signal: AbortSignal): Promise<T> {
  if (signal.aborted) return Promise.reject(new CancelledError())
  return new Promise<T>((resolve, reject) => {
    const onAbort = () => reject(new CancelledError())
    signal.addEventListener('abort', onAbort, { once: true })
    promise.then(
      (v) => {
        signal.removeEventListener('abort', onAbort)
        resolve(v)
      },
      (e) => {
        signal.removeEventListener('abort', onAbort)
        reject(e instanceof Error ? e : new Error(String(e)))
      },
    )
  })
}

/**
 * 测试 hook：永久悬挂直到 signal abort（配合 AID_WEIXIN_TEST_HANG=1，
 * 用于并发单飞 BUSY / 取消后锁释放的契约检查）。正式路径不得调用。
 */
export function hangUntilAbort(signal: AbortSignal): Promise<never> {
  return withAbort(new Promise<never>(() => {}), signal)
}

/** body 的成功产出；effect 缺省时按 kind+completed 自动计算 */
export interface OperationOutcome {
  message: string
  data?: Record<string, unknown>
  effect?: Effect
}

/** 写动作完成量追踪（M2+ 写 operation 使用）：失败时用于 partial/unknown 判定 */
export interface CompletedTracker {
  completed: number
}

/**
 * 参数校验（validate）→ signal 入口检查 → body → 统一结果。
 * 任何异常经 errorMapping 映射为结构化失败结果；写动作失败按 tracker.completed 计算 effect。
 */
export async function runWeixinOperation(
  kind: 'write' | 'readonly',
  ctx: OpContext,
  validate: () => string | null,
  body: (ctx: OpContext, tracker: CompletedTracker) => Promise<OperationOutcome>,
): Promise<OperationResult> {
  const runId = randomUUID()
  const invalid = validate()
  if (invalid) return failResult(runId, 'INVALID_ARGUMENT', invalid, 'none')

  const tracker: CompletedTracker = { completed: 0 }
  try {
    if (ctx.signal.aborted) throw new CancelledError()
    const outcome = await body(ctx, tracker)
    const effect = outcome.effect ?? (kind === 'readonly' ? 'none' : writeEffect(true, 'OK', tracker.completed))
    ctx.progress({ stage: 'done', message: outcome.message })
    return okResult(runId, outcome.message, effect, outcome.data ?? {})
  } catch (err) {
    const mapped = mapExecutorError(err)
    const effect = kind === 'readonly' ? 'none' : writeEffect(false, mapped.code, tracker.completed)
    const data: Record<string, unknown> = {}
    if (kind === 'write' && tracker.completed > 0) data.completed = tracker.completed
    return failResult(runId, mapped.code, mapped.message, effect, data)
  }
}
