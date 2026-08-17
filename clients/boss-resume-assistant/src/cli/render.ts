/**
 * CLI 薄 renderer 公共段（实施规格 m02 §5）。
 *
 * 每个 command：打印 intro/⚠️ 提示（command 层保留）→ runCliOperation：
 * AbortController（Ctrl+C 触发 abort）→ progress 打印「⠿ stage message」→
 * 调 operation → 按 result 打印成功/失败 → 退出码（成功 0 / 参数 2 / 其余 1）。
 */
import type { BossOperation, OperationResult, OpContext } from '../main/operations/types.js'

export interface CliRunOptions {
  cdpPort?: number
  /** 成功时的附加渲染（在 ✅ 结果行之后调用，如 read-resume 打印简历全文） */
  onSuccess?: (result: OperationResult) => void
}

/**
 * 运行 operation 并渲染到终端。返回进程退出码。
 */
export async function runCliOperation<Args>(
  operation: BossOperation<Args>,
  args: Args,
  opts: CliRunOptions = {},
): Promise<number> {
  const ac = new AbortController()
  const onSigint = () => {
    if (!ac.signal.aborted) {
      console.error('\n收到 Ctrl+C，正在取消（协作式，当前步骤收尾后停止）…')
      ac.abort()
    }
  }
  process.on('SIGINT', onSigint)
  try {
    const ctx: OpContext = {
      signal: ac.signal,
      cdpPort: opts.cdpPort,
      progress: (p) => {
        const counter = p.current !== undefined ? ` ${p.current}/${p.total ?? '?'}` : ''
        console.log(`⠿ ${p.stage}${counter} ${p.message}`)
      },
    }
    const result = await operation.execute(args, ctx)
    if (result.success) {
      console.log(`✅ ${result.message}`)
      opts.onSuccess?.(result)
      return 0
    }
    console.error(`❌ ${result.message}（code=${result.code}${result.retryable ? '，可重试' : '，不可自动重试'}）`)
    return result.code === 'INVALID_ARGUMENT' ? 2 : 1
  } finally {
    process.removeListener('SIGINT', onSigint)
  }
}
