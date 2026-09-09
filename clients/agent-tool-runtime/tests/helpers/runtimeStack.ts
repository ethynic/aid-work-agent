/**
 * 测试栈：FakeCloud + ApiClient + ProviderSet(fakeProvider) + PollLoop 一键搭建/回收。
 */
import { randomUUID } from 'node:crypto'
import { mkdtempSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { ApiClient } from '../../src/apiClient.js'
import type { ProviderManifest } from '../../src/providers.js'
import { deriveResourceKey } from '../../src/desktopLock.js'
import { PollLoop } from '../../src/pollLoop.js'
import { ProviderManager, ProviderSet } from '../../src/providerManager.js'
import { ResultOutbox, resultOutboxDir } from '../../src/resultOutbox.js'
import { FakeCloud } from './fakeCloud.js'

const here = path.dirname(fileURLToPath(import.meta.url))

export function fakeProviderEntry(): string {
  return path.join(here, 'fakeProvider.js')
}

/** 双消费方 v2 契约假 Provider 入口（P1-D：weixin_message_send_v2 / boss_send_to_v2） */
export function fakeV2ProviderEntry(): string {
  return path.join(here, 'fakeV2Provider.js')
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
  /** 额外 Provider 入口（如 weixin → fakeProvider；默认仅 boss-recruiting） */
  providerEntries?: Record<string, string>
  /** 桌面锁 resource_key（默认该 stack device_id 派生，天然每栈唯一） */
  desktopResourceKey?: string
  /** 仲裁等待超时（默认 desktopLock 常量 120s；超时类测试注入短值） */
  desktopLockTimeoutMs?: number
  /** v2 数据根目录（journal/ + result-outbox/；默认每栈独立 tmpdir；重启重投测试传共享目录） */
  runtimeDataDir?: string
  /** 测试注入 manifest 覆盖（v2 契约样例：protocol_version 2 + shared_lock_capable） */
  manifests?: Record<string, ProviderManifest>
  /** 本地许可有效期上限 ms（默认 90s；过期类测试注入短值） */
  permitLocalCapMs?: number
  /** 复用已有设备身份（重启重投测试：新栈沿用原 device token，等价生产 credentials.bin 不变） */
  device?: { device_id: string; token: string }
  /** 复用已有 FakeCloud（重启重投测试：同一云端实例 + 同一 invocation 现场） */
  cloud?: FakeCloud
  /** stop() 不关闭自有 cloud（重启重投测试：栈1 停止后 cloud 供栈2 复用，由测试负责关闭） */
  keepCloudOnStop?: boolean
}

export interface TestStack {
  cloud: FakeCloud
  api: ApiClient
  /** boss-recruiting 的 Manager（与 ProviderSet 内同一实例；既有测试观测 pid/isRunning 用） */
  provider: ProviderManager
  providers: ProviderSet
  loop: PollLoop
  runPromise: Promise<void>
  deviceId: string
  token: string
  /** v2 数据根目录（journal/<id>.jsonl 与 result-outbox/<id>.json 所在） */
  dataDir: string
  /** v2 结果 outbox 实例（与 runner 共用） */
  outbox: ResultOutbox
  /** 停 loop + 等退出 + 回收全部 Provider 子进程 + 关 cloud（不删 dataDir——共享目录场景由测试自理） */
  stop: () => Promise<void>
}

export async function startTestStack(opts: TestStackOptions = {}): Promise<TestStack> {
  // 复用云端时 server 已在监听（重启重投测试共享 invocation 现场）；否则新起
  const cloud = opts.cloud ?? new FakeCloud()
  if (!opts.cloud) await cloud.start()
  const device = opts.device ?? cloud.createDeviceDirectly()
  const { device_id, token } = device
  const api = new ApiClient(cloud.baseUrl, token)
  const providers = new ProviderSet(
    { 'boss-recruiting': fakeProviderEntry(), ...opts.providerEntries },
    { shutdownTimeoutMs: 3_000 },
  )
  const provider = providers.get('boss-recruiting')
  const dataDir = opts.runtimeDataDir ?? mkdtempSync(path.join(os.tmpdir(), 'aidwork-rt-test-'))
  const outbox = new ResultOutbox(resultOutboxDir(dataDir))
  const loop = new PollLoop({
    api,
    runnerDeps: {
      providers,
      desktopCheck: opts.desktopCheck ?? (async () => true),
      progressIntervalMs: opts.progressIntervalMs ?? 100,
      retryBaseMs: 50,
      retryMaxMs: 200,
      // R24：锁标识为机器作用域（hostname|username|session）。测试默认注入独立 sessionId
      // 隔离各栈（真实部署同机同会话恒同锁）；需要验证同机互斥的测试显式传 deriveResourceKey()
      desktopResourceKey: opts.desktopResourceKey ?? deriveResourceKey(randomUUID()),
      desktopLockTimeoutMs: opts.desktopLockTimeoutMs,
      runtimeDataDir: dataDir,
      resultOutbox: outbox,
      manifests: opts.manifests,
      permitLocalCapMs: opts.permitLocalCapMs,
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
    await providers.shutdownAll()
    if (!opts.cloud && !opts.keepCloudOnStop) await cloud.stop()
  }
  return { cloud, api, provider, providers, loop, runPromise, deviceId: device_id, token, dataDir, outbox, stop }
}

export function isProcessAlive(pid: number): boolean {
  try {
    process.kill(pid, 0)
    return true
  } catch {
    return false
  }
}
