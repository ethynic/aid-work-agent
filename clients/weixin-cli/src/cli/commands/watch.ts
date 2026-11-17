import { randomUUID } from 'node:crypto'
import { setTimeout as delay } from 'node:timers/promises'
import { createNameOperations } from '../../operations/nameOperations.js'
import type { WeixinOperation } from '../../operations/types.js'
import { NamedMutex, resolveMutexScope } from '../../platform/namedMutex.js'
import {
  classifySessionObservation,
  validateSessionObservation,
  type SessionObserverRequest,
} from '../../platform/sessionObserver.js'
import { flagString, type ParsedArgs } from '../args.js'

const DEFAULT_INTERVAL_SECONDS = 10
const MAX_TIMER_MS = 2_147_483_647

type WatchRequest = SessionObserverRequest & { target_name: string }
type WatchEvent = { event: 'new_messages'; count: number }

interface WatchDependencies {
  observe: Pick<WeixinOperation<WatchRequest>, 'execute'>
  mutex: Pick<NamedMutex, 'acquire' | 'release'>
  emit: (event: WatchEvent) => void
  report: (message: string) => void
  wait?: (intervalMs: number, signal: AbortSignal) => Promise<unknown>
}

/** 每轮只把确认新增的条数投影到终端，水位与观察数据仅在进程内消费。 */
export async function watchCurrentConversation(
  targetName: string,
  intervalMs: number,
  signal: AbortSignal,
  deps: WatchDependencies,
): Promise<void> {
  const request: WatchRequest = {
    target_name: targetName,
    conversation_binding_id: randomUUID(),
    binding_version: 1,
    account_identity_version: 0,
    watermark: null,
  }
  const wait = deps.wait ?? ((ms, s) => delay(ms, undefined, { signal: s }))

  while (!signal.aborted) {
    if (await deps.mutex.acquire()) {
      try {
        if (signal.aborted) break
        const result = await deps.observe.execute({ ...request }, { signal, progress() {} })
        if (signal.aborted || result.code === 'CANCELLED') break
        if (!result.success) {
          deps.report('watch 本轮读取失败（' + result.code + '），保留原水位')
        } else {
          const observation = validateSessionObservation(result.data)
          if (!observation.ok) {
            deps.report('watch 本轮观察格式无效（' + observation.code + '），保留原水位')
          } else {
            const outcome = classifySessionObservation(request, observation.value)
            switch (outcome.kind) {
              case 'baseline':
                request.watermark = outcome.watermark
                deps.report('watch 已建立会话水位。存量消息不回放')
                break
              case 'new_messages':
                request.watermark = outcome.watermark
                deps.emit({ event: 'new_messages', count: outcome.count })
                break
              case 'no_change':
                request.watermark = outcome.watermark
                break
              case 'gap':
                deps.report('watch 本轮观察断层（' + outcome.reason + '），保留原水位')
                break
              case 'unavailable':
                deps.report('watch 本轮观察不可用（' + outcome.reason + '），保留原水位')
                break
              case 'binding_mismatch':
              case 'identity_drift':
                deps.report('watch 本轮观察身份不一致，保留原水位')
            }
          }
        }
      } finally {
        await deps.mutex.release()
      }
    } else {
      deps.report('watch 本轮跳过（BUSY），保留原水位')
    }

    if (signal.aborted) break
    try { await wait(intervalMs, signal) }
    catch (error) { if (!signal.aborted) throw error }
  }
}

export async function watchCommand(args: ParsedArgs): Promise<number> {
  const targetName = flagString(args, 'target-name')
  const interval = Number(flagString(args, 'interval') ?? DEFAULT_INTERVAL_SECONDS)
  const invalidFlags = [...args.flags].some(([key, value]) =>
    (key !== 'target-name' && key !== 'interval') || typeof value !== 'string')
  if (args.positional.length !== 1 || args.duplicateFlags.size || invalidFlags
    || !targetName?.trim() || targetName.length > 128
    || !Number.isFinite(interval) || interval < 1 || interval * 1000 > MAX_TIMER_MS) {
    console.error('用法：watch --target-name <当前会话全名> [--interval <秒，至少 1>]')
    return 2
  }
  if (process.platform !== 'win32') {
    console.error('watch 需要 Windows 微信桌面会话（WINDOWS_REQUIRED）')
    return 1
  }

  const controller = new AbortController()
  const stop = () => controller.abort()
  process.on('SIGINT', stop)
  process.on('SIGTERM', stop)
  try {
    console.error('watch 已启动，每 ' + interval + ' 秒轮询。请保持目标会话打开并停在最新消息，Ctrl+C 退出')
    await watchCurrentConversation(targetName, interval * 1000, controller.signal, {
      observe: createNameOperations({ observeCurrentOnly: true }).observe,
      mutex: new NamedMutex(resolveMutexScope()),
      emit: (event) => { process.stdout.write(JSON.stringify(event) + '\n') },
      report: (message) => console.error(message),
    })
    console.error('watch 已退出')
    return 0
  } catch {
    console.error('watch 无法继续（INTERNAL_ERROR）')
    return 1
  } finally {
    process.removeListener('SIGINT', stop)
    process.removeListener('SIGTERM', stop)
  }
}
