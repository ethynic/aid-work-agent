/**
 * CLI 子命令 watch：新消息跟踪循环（本 CLI 的核心价值——无会话归档时的新消息跟踪）。
 *
 * 用法：
 *   aid-wecom watch [--interval 秒] [--once]
 *
 * 每轮调用 wecom_watch_poll operation（未读快照 diff → 读候选会话当前屏 → 取增量），
 * 事件以 NDJSON 逐行写 stdout：
 *   {"type":"new_messages","session":"...","unread_count":N,"messages":[{"side","text"},...]}
 *   {"type":"tick","unread_total":N}（本轮无新消息）
 * 进度/告警只写 stderr，stdout 恒为纯净 NDJSON（可管道给 jq/其它进程）。
 * Ctrl+C（AbortSignal）干净退出（退出码 0）；--once 单轮（测试/手动）；
 * 循环期间持有跨进程互斥（与 send/search 等命令同一 scope，不并行操作企微）。
 * 单轮失败不终止循环（stderr 告警后等下一轮）；--once 失败退出码 1。
 */
import { getOperationEntry } from '../../operations/registry.js'
import type { OpContext } from '../../operations/types.js'
import type { WatchEvent, WecomWatchPollArgs } from '../../operations/watchPoll.js'
import { NamedMutex, resolveMutexScope } from '../../platform/namedMutex.js'

export interface WatchCommandOptions {
  interval?: string
  once?: boolean
}

const DEFAULT_INTERVAL_SECONDS = 5

/** 可取消睡眠：abort 时立即返回 false（否则 true） */
function sleepAbortable(ms: number, signal: AbortSignal): Promise<boolean> {
  if (signal.aborted) return Promise.resolve(false)
  return new Promise((resolve) => {
    const timer = setTimeout(() => {
      signal.removeEventListener('abort', onAbort)
      resolve(true)
    }, ms)
    const onAbort = () => {
      clearTimeout(timer)
      resolve(false)
    }
    signal.addEventListener('abort', onAbort, { once: true })
  })
}

function emitEvents(events: WatchEvent[]): void {
  for (const e of events) {
    process.stdout.write(JSON.stringify(e) + '\n')
  }
}

export async function watchCommand(opts: WatchCommandOptions): Promise<number> {
  let intervalSec = DEFAULT_INTERVAL_SECONDS
  if (opts.interval !== undefined) {
    intervalSec = Number(opts.interval)
    if (!Number.isFinite(intervalSec) || intervalSec < 1) {
      console.error(`非法 --interval 值「${opts.interval}」（必须是 ≥1 的数字，单位秒）`)
      return 2
    }
  }

  const ac = new AbortController()
  const onSigint = () => {
    if (!ac.signal.aborted) {
      console.error('\n收到 Ctrl+C，正在退出 watch（当前轮收尾后停止）…')
      ac.abort()
    }
  }
  process.on('SIGINT', onSigint)

  // 循环期间持有跨进程互斥：watch 长循环与 send/search 等命令不得并行操作企微
  const mutex = new NamedMutex(resolveMutexScope())
  try {
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

    const { operation } = getOperationEntry('wecom_watch_poll')
    const ctx: OpContext = {
      signal: ac.signal,
      progress: (p) => {
        const counter = p.current !== undefined ? ` ${p.current}/${p.total ?? '?'}` : ''
        console.error(`⠿ ${p.stage}${counter} ${p.message}`)
      },
    }
    const args: WecomWatchPollArgs = {}

    console.error(`watch 已启动：每 ${intervalSec}s 一轮（Ctrl+C 退出；事件 NDJSON 写 stdout）`)
    for (;;) {
      const result = await operation.execute(args, ctx)
      if (result.success) {
        emitEvents((result.data.events as WatchEvent[] | undefined) ?? [])
      } else {
        if (result.code === 'CANCELLED') break
        console.error(`⚠️ 本轮失败（${result.code}）：${result.message}${opts.once ? '' : '（等下一轮重试）'}`)
        if (opts.once) return 1
      }
      if (opts.once || ac.signal.aborted) break
      if (!(await sleepAbortable(intervalSec * 1000, ac.signal))) break
    }
    console.error('watch 已退出')
    return 0
  } finally {
    process.removeListener('SIGINT', onSigint)
    await mutex.release()
  }
}
