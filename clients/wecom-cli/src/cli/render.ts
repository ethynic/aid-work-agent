/**
 * CLI 薄 renderer 公共段（与 weixin-cli 同范式）。
 *
 * 每个 command：runCliOperation：AbortController（Ctrl+C 触发 abort）→
 * progress 打印「⠿ stage message」→ 调 operation → 按 result 打印成功/失败 →
 * 退出码（成功 0 / 参数 2 / 其余 1）。
 *
 * exclusive=true（驱动企微 UI 的命令必须开启）：执行前占用跨进程命名互斥
 * （与 MCP server 同一 scope：用户操作与 Provider 调用不得并行控制企业微信）；
 * 被占用打印 BUSY 并以退出码 1 终止。
 */
import type { WecomOperation, OpContext } from '../operations/types.js'
import { NamedMutex, resolveMutexScope } from '../platform/namedMutex.js'

export interface RunCliOperationOptions {
  /** 驱动企微 UI 的命令必须跨进程互斥（与 MCP server 同一 NamedMutex scope） */
  exclusive?: boolean
  /** true 时 progress 走 stderr，stdout 最后一行只输出 OperationResult JSON（AI 组合调用用） */
  json?: boolean
}

/**
 * 运行 operation 并渲染到终端。返回进程退出码。
 */
export async function runCliOperation<Args>(
  operation: WecomOperation<Args>,
  args: Args,
  opts: RunCliOperationOptions = {},
): Promise<number> {
  const ac = new AbortController()
  const onSigint = () => {
    if (!ac.signal.aborted) {
      console.error('\n收到 Ctrl+C，正在取消（协作式，当前步骤收尾后停止）…')
      ac.abort()
    }
  }
  process.on('SIGINT', onSigint)
  let mutex: NamedMutex | null = null
  try {
    if (opts.exclusive) {
      mutex = new NamedMutex(resolveMutexScope())
      let acquired = false
      try {
        acquired = await mutex.acquire()
      } catch (err) {
        console.error(`❌ 跨进程互斥占用失败：${err instanceof Error ? err.message : String(err)}（code=INTERNAL_ERROR）`)
        return 1
      }
      if (!acquired) {
        console.error('❌ 另一个 aid-wecom 进程正在操作企业微信（跨进程互斥），请等其完成后重试（code=BUSY，可重试）')
        return 1
      }
    }
    const ctx: OpContext = {
      signal: ac.signal,
      progress: (p) => {
        const counter = p.current !== undefined ? ` ${p.current}/${p.total ?? '?'}` : ''
        // --json 模式：progress 走 stderr，保持 stdout 纯净
        if (opts.json) console.error(`⠿ ${p.stage}${counter} ${p.message}`)
        else console.log(`⠿ ${p.stage}${counter} ${p.message}`)
      },
    }
    const result = await operation.execute(args, ctx)
    if (opts.json) {
      console.log(JSON.stringify(result))
      return result.success ? 0 : result.code === 'INVALID_ARGUMENT' ? 2 : 1
    }
    if (result.success) {
      console.log(`✅ ${result.message}`)
      return 0
    }
    console.error(`❌ ${result.message}（code=${result.code}${result.retryable ? '，可重试' : '，不可自动重试'}）`)
    return result.code === 'INVALID_ARGUMENT' ? 2 : 1
  } finally {
    process.removeListener('SIGINT', onSigint)
    if (mutex) await mutex.release()
  }
}
