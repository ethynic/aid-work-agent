/**
 * 测试栈：FakeCloud + ApiClient + ProviderManager(fakeProvider) + PollLoop 一键搭建/回收。
 */
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { ApiClient } from '../../src/apiClient.js'
import { PollLoop } from '../../src/pollLoop.js'
import { ProviderManager } from '../../src/providerManager.js'
import { FakeCloud } from './fakeCloud.js'

const here = path.dirname(fileURLToPath(import.meta.url))

export function fakeProviderEntry(): string {
  return path.join(here, 'fakeProvider.js')
}

export interface TestStackOptions {
  heartbeatIntervalMs?: number
  claimWaitSeconds?: number
  backoffBaseMs?: number
  backoffMaxMs?: number
  progressIntervalMs?: number
  desktopCheck?: () => Promise<boolean>
  onHeartbeatAttempt?: (ts: number) => void
  onEvent?: (msg: string) => void
}

export interface TestStack {
  cloud: FakeCloud
  api: ApiClient
  provider: ProviderManager
  loop: PollLoop
  runPromise: Promise<void>
  deviceId: string
  token: string
  /** 停 loop + 等退出 + 回收 provider 子进程 + 关 cloud */
  stop: () => Promise<void>
}

export async function startTestStack(opts: TestStackOptions = {}): Promise<TestStack> {
  const cloud = new FakeCloud()
  await cloud.start()
  const { device_id, token } = cloud.createDeviceDirectly()
  const api = new ApiClient(cloud.baseUrl, token)
  const provider = new ProviderManager(fakeProviderEntry(), { shutdownTimeoutMs: 3_000 })
  const loop = new PollLoop({
    api,
    runnerDeps: {
      provider,
      desktopCheck: opts.desktopCheck ?? (async () => true),
      progressIntervalMs: opts.progressIntervalMs ?? 100,
      retryBaseMs: 50,
      retryMaxMs: 200,
    },
    heartbeatIntervalMs: opts.heartbeatIntervalMs ?? 150,
    claimWaitSeconds: opts.claimWaitSeconds ?? 1,
    backoffBaseMs: opts.backoffBaseMs ?? 50,
    backoffMaxMs: opts.backoffMaxMs ?? 400,
    onHeartbeatAttempt: opts.onHeartbeatAttempt,
    onEvent: opts.onEvent,
  })
  const runPromise = loop.run()
  const stop = async () => {
    loop.shutdown()
    await runPromise
    await provider.shutdown()
    await cloud.stop()
  }
  return { cloud, api, provider, loop, runPromise, deviceId: device_id, token, stop }
}

export function isProcessAlive(pid: number): boolean {
  try {
    process.kill(pid, 0)
    return true
  } catch {
    return false
  }
}
