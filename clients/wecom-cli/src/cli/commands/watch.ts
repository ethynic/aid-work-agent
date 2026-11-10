/**
 * CLI watch：本地库新消息跟踪循环，按轮获取互斥，水位异常轮不推进。
 * 每轮输出 {channel:'local-db',new_messages,last_sender,failed?} NDJSON。
 * --channel ui 已停用；本地库失败保留水位，下轮重试。
 */
import { getOperationEntry } from '../../operations/registry.js'
import { okResult, type OperationResult, type OpContext } from '../../operations/types.js'
import type { LocaldbWatchEvent, WecomWatchPollArgs } from '../../operations/watchPoll.js'
import { NamedMutex, resolveMutexScope } from '../../platform/namedMutex.js'

export interface WatchCommandOptions {
  interval?: string
  once?: boolean
  /** 显式通道：local-db | ui（缺省按 doctor 状态分派） */
  channel?: string
  /** 逗号分隔的会话名列表（--focus 名1,名2；仅本地库通道生效） */
  focus?: string
}

const DEFAULT_INTERVAL_SECONDS = 5

/**
 * 测试 hook（对齐 probe.ts 的 AID_WECOM_TEST_DRIVER_STUB 先例）：置 1 时每轮不启动真实
 * unread-list/read-session 驱动，返回固定 tick 桩——互斥按轮获取/释放的集成测试以子进程
 * 真实跑本循环，不得触达真实企微窗口。
 */
const TEST_DRIVER_STUB_ENV = 'AID_WECOM_TEST_DRIVER_STUB'

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

/** 本地库通道单轮事件（冻结点 4 形态）整行输出（每轮一行，含空轮——NDJSON 心跳语义） */
function emitLocaldbEvent(event: LocaldbWatchEvent): void {
  process.stdout.write(JSON.stringify(event) + '\n')
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
  if (opts.channel !== undefined && opts.channel !== 'local-db' && opts.channel !== 'ui') {
    console.error(`非法 --channel 值「${opts.channel}」（必须是 local-db 或 ui）`)
    return 2
  }
  if (opts.channel === 'ui') {
    console.error('watch 仅支持本地库通道；请升级到已验证的企业微信版本')
    return 2
  }
  const focus = opts.focus !== undefined
    ? opts.focus.split(',').map((s) => s.trim()).filter((s) => s.length > 0)
    : undefined
  if (focus !== undefined && focus.length === 0) {
    console.error('非法 --focus 值：至少需要一个会话名（逗号分隔；如无关注目标请不要传 --focus）')
    return 2
  }

  const ac = new AbortController()
  const onSigint = () => {
    if (!ac.signal.aborted) {
      console.error('\n收到 Ctrl+C，正在退出 watch（当前轮收尾后停止）…')
      ac.abort()
    }
  }
  process.on('SIGINT', onSigint)

  const mutex = new NamedMutex(resolveMutexScope())
  try {
    const { operation } = getOperationEntry('wecom_watch_poll')
    const ctx: OpContext = {
      signal: ac.signal,
      progress: (p) => {
        const counter = p.current !== undefined ? ` ${p.current}/${p.total ?? '?'}` : ''
        console.error(`⠿ ${p.stage}${counter} ${p.message}`)
      },
    }
    const args: WecomWatchPollArgs = {
      ...(opts.channel !== undefined ? { channel: opts.channel as 'local-db' | 'ui' } : {}),
      ...(focus !== undefined ? { focus } : {}),
    }
    /** 冻结点 4 调用方初始化语义：本地库首轮建立关注基线时输出一次提示（存量不回放） */
    let baselineHintShown = false
    const executeRound = (): Promise<OperationResult> => {
      if (process.env[TEST_DRIVER_STUB_ENV] === '1') {
        return Promise.resolve(
          okResult('watch-stub-round', '测试桩轮（未触达真实驱动）', 'none', {
            channel: 'local-db', new_messages: [], last_sender: {},
          }),
        )
      }
      return operation.execute(args, ctx)
    }

    console.error(`watch 已启动：每 ${intervalSec}s 一轮（Ctrl+C 退出；事件 NDJSON 写 stdout）`)
    for (;;) {
      // 每轮获取/释放跨进程互斥：轮询间隔不持锁（与 MCP runExclusive 每调用独占的粒度对齐）。
      // 本地库轮同样过锁（设计决策 #39「DB 读取绕过 UI 锁」延后——共享状态多进程读改写
      // 需要串行协调，不提前建读写锁；批次 A 已就位的按轮锁语义对 DB 轮原样适用）
      let acquired = false
      try {
        try {
          acquired = await mutex.acquire()
        } catch (err) {
          // 互斥占用本身失败（系统错误）：重试无意义，退出循环
          console.error(`❌ 跨进程互斥占用失败：${err instanceof Error ? err.message : String(err)}（code=INTERNAL_ERROR）`)
          return 1
        }
        if (!acquired) {
          // 占用中被跳过的轮不终止循环：等下一轮再获取（--once 保持既有失败退出码 1）
          console.error('⚠️ 本轮跳过：另一个 aid-wecom 进程正在操作企业微信（跨进程互斥，code=BUSY），等下一轮重试')
          if (opts.once) return 1
        } else {
          const result = await executeRound()
          if (result.success) {
            emitLocaldbEvent(result.data as unknown as LocaldbWatchEvent)
            if (!baselineHintShown) {
              baselineHintShown = true
              console.error('ℹ️ 本地库 watch 关注基线已按当前水位建立（基线 = 各会话当前最新消息，存量消息不回放）；存量待回复请先运行 aid-wecom unread 获取快照')
            }
          } else {
            if (result.code === 'CANCELLED') break
            const dbUnavailable = result.data.db_unavailable === true ? '；DB 不可用（db_unavailable，水位已保留，不落 UI）' : ''
            console.error(`⚠️ 本轮失败（${result.code}）：${result.message}${dbUnavailable}${opts.once ? '' : '（等下一轮重试）'}`)
            if (opts.once) return 1
          }
        }
      } finally {
        // 轮结束（含失败轮/取消轮）立即释放；释放失败不掩盖轮内结果（release 幂等不抛）
        if (acquired) await mutex.release()
      }
      if (opts.once || ac.signal.aborted) break
      if (!(await sleepAbortable(intervalSec * 1000, ac.signal))) break
    }
    console.error('watch 已退出')
    return 0
  } finally {
    process.removeListener('SIGINT', onSigint)
  }
}
