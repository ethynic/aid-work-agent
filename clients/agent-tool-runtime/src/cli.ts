#!/usr/bin/env node
/**
 * agent-tool-runtime CLI 入口。
 *
 * 用法：
 *   aid-runtime pair --code <8位配对码> [--server https://...] [--name 设备名]
 *   aid-runtime start [--server https://...]
 *   aid-runtime status
 *   aid-runtime doctor
 *   aid-runtime unpair
 *
 * 约束：不打印 token/claim_token；pair 后 config.json 不含 token（DPAPI 密文存 credentials.bin）。
 */
import { existsSync } from 'node:fs'
import { randomUUID } from 'node:crypto'
import { execFile } from 'node:child_process'
import { ApiClient } from './apiClient.js'
import {
  configPath,
  credentialsPath,
  deviceCapabilities,
  loadConfig,
  machineFingerprint,
  PLATFORM,
  resolveProviderEntries,
  RUNTIME_VERSION,
  runtimeHomeDir,
  saveConfig,
} from './config.js'
import { clearCredentials, hasDeviceToken, loadDeviceToken, saveDeviceToken } from './credentials.js'
import { checkDesktopInteractive } from './desktopCheck.js'
import { deriveResourceKey } from './desktopLock.js'
import { PollLoop } from './pollLoop.js'
import { ProviderSet } from './providerManager.js'
import { ResultOutbox, resultOutboxDir } from './resultOutbox.js'
import { SessionTaskEngine, type ObserverResult, type ObserverWatermark } from './sessionTasks/engine.js'
import { acquireSessionTasksSingleInstance, type SingleInstanceGuard } from './sessionTasks/singleInstance.js'
import { enforceRetention } from './sessionTasks/retention.js'
import { dpapiProtect, dpapiUnprotect } from './dpapi.js'
import { withDesktopLock, desktopLockName } from './desktopLock.js'
import { manifestDigest } from './manifestVerifier.js'
import { logError, logInfo } from './log.js'
import { rmSync } from 'node:fs'

const USAGE = `agent-tool-runtime — 本地工具 Runtime（云端 invocation → 本地 MCP Provider 执行）

命令：
  pair --code <8位配对码> [--server URL] [--name 设备名]   配对设备（token DPAPI 加密存 credentials.bin）
  start [--server URL]                                     启动主循环（心跳 + 领取任务 + 执行）
  status                                                   本地配置/配对状态（offline 视图）
  doctor                                                   只读环境检查（配置/凭证/网络/boss CLI/桌面）
  unpair                                                   本地解除配对（清除凭证与配置；Web 端需另行解绑）

全局说明：
  配置目录 %APPDATA%/aidwork-tool-runtime/（可用 AIDWORK_RUNTIME_HOME 覆盖）
  config.json 不含 token；日志绝不输出 token/配对码明文
  多 Provider：config.json providers.<key>.entry 配置各 Provider 入口（boss 可用 bossCliEntry 简写），
  未配置 entry 的 Provider 视为未安装（不上报能力、不领取其 invocation）
`

interface ParsedArgs {
  command: string | null
  flags: Map<string, string | boolean>
}

function parseArgs(argv: string[]): ParsedArgs {
  const flags = new Map<string, string | boolean>()
  let command: string | null = null
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i]!
    if (arg.startsWith('--')) {
      const key = arg.slice(2)
      const next = argv[i + 1]
      if (next !== undefined && !next.startsWith('--')) {
        flags.set(key, next)
        i++
      } else {
        flags.set(key, true)
      }
    } else if (command === null) {
      command = arg
    }
  }
  return { command, flags }
}

function flagString(args: ParsedArgs, key: string): string | undefined {
  const v = args.flags.get(key)
  return typeof v === 'string' ? v : undefined
}

async function cmdPair(args: ParsedArgs): Promise<number> {
  const code = flagString(args, 'code')
  if (!code) {
    console.error('缺少 --code <8位配对码>（在 Web 端「本地工具」页面生成）')
    return 1
  }
  const existing = loadConfig()
  const server = flagString(args, 'server') ?? existing?.server
  if (!server) {
    console.error('缺少 --server <云端地址>（首次 pair 必须提供，之后可从 config.json 继承）')
    return 1
  }
  const name = flagString(args, 'name') ?? undefined

  const api = new ApiClient(server)
  let pairResult
  try {
    pairResult = await api.pair({
      code: code.trim(),
      name,
      platform: PLATFORM,
      runtime_version: RUNTIME_VERSION,
      capabilities: deviceCapabilities(),
      machine_fingerprint: machineFingerprint(),
    })
  } catch (err) {
    console.error(`配对失败: ${err instanceof Error ? err.message : String(err)}`)
    return 1
  }

  await saveDeviceToken(pairResult.device_token)
  saveConfig({ server, device_id: pairResult.device_id, name })
  console.log(`配对成功：device_id=${pairResult.device_id} server=${server}`)
  console.log(`凭证已加密保存到 ${credentialsPath()}（DPAPI CurrentUser）`)
  return 0
}

/**
 * 会话观察器（C2）：经 ProviderSet 的 weixin Provider 调用 session_observer_v1
 * 工具。评审 P1-3：callTool 返回 {success, code, data, ...} envelope——须检查
 * success、解包 data 并校验观察契约最小字段，身份版本用领取的实际值（不写死）。
 */
function makeProviderSessionObserver(providers: ProviderSet): (task: { taskId: string; conversationBindingId: string; expectedBindingVersion: number; expectedAccountIdentityVersion: number }, request: { watermark: ObserverWatermark | null }) => Promise<ObserverResult> {
  return async (task, request) => {
    const provider = providers.get('weixin')
    const envelope = (await provider.callTool('weixin_session_observe', {
      conversation_binding_id: task.conversationBindingId,
      binding_version: task.expectedBindingVersion,
      account_identity_version: task.expectedAccountIdentityVersion,
      watermark: request.watermark,
    }, { timeoutMs: 15_000 })) as Record<string, unknown>
    if (envelope['success'] !== true) {
      throw new Error(`观察工具返回失败: code=${String(envelope['code'] ?? '')} message=${String((envelope as { message?: string }).message ?? '')}`)
    }
    const data = (envelope['data'] ?? {}) as Record<string, unknown>
    // 契约最小校验（session_observer_v1）
    if (typeof data['observation_id'] !== 'string' || !data['observation_id']) throw new Error('观察结果缺少 observation_id')
    if (data['coverage'] !== 'complete_window' && data['coverage'] !== 'gap' && data['coverage'] !== 'unavailable') {
      throw new Error(`观察结果 coverage 非法: ${String(data['coverage'])}`)
    }
    return {
      observation_id: String(data['observation_id']),
      account_identity_version: Number(data['account_identity_version'] ?? 0),
      conversation_binding_id: String(data['conversation_binding_id'] ?? ''),
      binding_version: Number(data['binding_version'] ?? 0),
      observed_at: String(data['observed_at'] ?? new Date().toISOString()),
      coverage: data['coverage'] as ObserverResult['coverage'],
      ordered_messages: (data['ordered_messages'] as ObserverResult['ordered_messages']) ?? [],
      window_fingerprint: (data['window_fingerprint'] as string | null) ?? null,
      gap_reason: (data['gap_reason'] as string | null) ?? null,
    }
  }
}

async function cmdStart(args: ParsedArgs): Promise<number> {
  const config = loadConfig()
  const server = flagString(args, 'server') ?? config?.server
  if (!server || !config) {
    console.error('未配对或缺少配置，请先执行 pair --code <配对码> --server <云端地址>')
    return 1
  }
  let token: string
  try {
    token = await loadDeviceToken()
  } catch (err) {
    console.error(err instanceof Error ? err.message : String(err))
    return 1
  }

  const entries = resolveProviderEntries(config)
  const missing = Object.entries(entries).filter(([, entry]) => !existsSync(entry))
  if (missing.length > 0) {
    for (const [key, entry] of missing) {
      if (key === 'boss-recruiting') {
        console.error(`boss CLI 入口不存在: ${entry}（可在 config.json 配置 bossCliEntry 绝对路径）`)
      } else {
        console.error(`Provider ${key} 入口不存在: ${entry}（可在 config.json providers.${key}.entry 配置绝对路径）`)
      }
    }
    return 1
  }

  const api = new ApiClient(server, token)
  const providers = new ProviderSet(entries)
  console.log(`[runtime] providers: ${Object.keys(entries).join(', ')}`)
  // v2 写路径数据目录（journal/ + result-outbox/，跟随 runtime home；启动重投共用同一 outbox 实例）
  const dataDir = runtimeHomeDir()
  const loop = new PollLoop({
    api,
    runnerDeps: {
      providers,
      desktopCheck: async () => (await checkDesktopInteractive()).interactive,
      desktopResourceKey: deriveResourceKey(),
      runtimeDataDir: dataDir,
      resultOutbox: new ResultOutbox(resultOutboxDir(dataDir)),
    },
    // 2026-09-10 排障整改：invocation 生命周期行（领取/开始/终态回传/重试）此前只 console.log 到
    // stdout，服务化启动无重定向时文件日志全无回传链路痕迹（2026-09-10 客户现场诊断包实证）。
    // logInfo = stderr + %APPDATA% 文件双写，前缀格式不变。
    onEvent: (msg) => logInfo(msg),
  })

  // ----- 端侧会话任务引擎（C2）：与 pollLoop 共存，共享 Provider 与桌面锁 -----
  let sessionEngine: SessionTaskEngine | null = null
  let sessionGuard: SingleInstanceGuard | null = null
  if (config.sessionTasks === true) {
    sessionGuard = await acquireSessionTasksSingleInstance(dataDir)
    if (sessionGuard === null) {
      logInfo('[session-tasks] 已有实例持有单实例锁，本进程不启动会话引擎（standard lane 正常运行）')
    } else {
      const retention = enforceRetention(dataDir, [], {}) // 启动时评估磁盘水位（终态清理由引擎周期执行）
      if (retention.stopNew) logInfo('[session-tasks] 本地会话日志已达上限（256MiB），停止新观察持久化')
      else if (retention.warn80) logInfo('[session-tasks] 本地会话日志超 80% 水位')
      sessionEngine = new SessionTaskEngine({
        api,
        runtimeHome: dataDir,
        crypto: { protect: dpapiProtect, unprotect: dpapiUnprotect },
        runtimeInstanceId: `rt-${RUNTIME_VERSION}-${randomUUID().slice(0, 8)}`,
        withLock: <T,>(fn: () => Promise<T>) => withDesktopLock(desktopLockName(deriveResourceKey()), fn),
        observer: makeProviderSessionObserver(providers),
        emit: (msg) => logInfo(`[session-tasks] ${msg}`),
      })
      logInfo('[session-tasks] 会话任务引擎已启动（共享桌面锁；observer 经 weixin Provider）')
    }
  }

  const shutdown = (signal: string) => {
    logInfo(`收到 ${signal}，停止领取新任务并回收 Provider…`)
    loop.shutdown()
    sessionEngine?.shutdown()
    void sessionGuard?.release()
    // 兜底：runner 协作式中止 + provider 回收最长给 20s，超时强退
    setTimeout(() => {
      logError('关闭超时，强制退出')
      process.exit(2)
    }, 20_000).unref()
  }
  process.on('SIGINT', () => shutdown('SIGINT'))
  process.on('SIGTERM', () => shutdown('SIGTERM'))

  console.log(`[runtime] 已启动 device_id=${config.device_id} server=${server} version=${RUNTIME_VERSION}`)
  try {
    // 等待全部运行循环退出（评审 P1-1）：未开启 sessionTasks 或未取得单实例锁时
    // 只有 pollLoop 在跑；race 会让已完成的 Promise.resolve() 立即结束、误关 Provider
    const loops: Array<Promise<void>> = [loop.run()]
    if (sessionEngine) loops.push(sessionEngine.run())
    await Promise.all(loops)
  } finally {
    sessionEngine?.shutdown()
    await providers.shutdownAll()
    await sessionGuard?.release()
  }
  console.log('[runtime] 已退出')
  return 0
}

async function cmdStatus(): Promise<number> {
  const config = loadConfig()
  console.log(`配置目录: ${runtimeHomeDir()}`)
  if (!config) {
    console.log('配对状态: 未配对（config.json 不存在或不完整）')
    console.log(`凭证文件: ${hasDeviceToken() ? '存在' : '不存在'} (${credentialsPath()})`)
    return 0
  }
  console.log(`配对状态: 已配对`)
  console.log(`  server:    ${config.server}`)
  console.log(`  device_id: ${config.device_id}`)
  console.log(`  name:      ${config.name ?? '-'}`)
  const entries = resolveProviderEntries(config)
  console.log(`  providers: ${Object.keys(entries).join(', ')}`)
  for (const [key, entry] of Object.entries(entries)) {
    console.log(`  ${key} 入口: ${entry}${existsSync(entry) ? '' : '（不存在！）'}`)
  }
  console.log(`凭证文件: ${hasDeviceToken() ? '存在' : '不存在！请重新 pair'}`)
  return 0
}

async function cmdDoctor(): Promise<number> {
  let failures = 0
  const ok = (msg: string) => console.log(`  [OK]   ${msg}`)
  const fail = (msg: string) => {
    failures++
    console.log(`  [FAIL] ${msg}`)
  }

  console.log('doctor：只读环境检查')

  // 1. 配置
  const config = loadConfig()
  if (config) ok(`config.json 可解析（server=${config.server} device_id=${config.device_id}）`)
  else fail(`config.json 不存在或不完整（${configPath()}），请先 pair`)

  // 2. 凭证（DPAPI round-trip + 实际解密）
  try {
    const probe = await dpapiProtect('doctor-probe')
    const back = await dpapiUnprotect(probe)
    if (back !== 'doctor-probe') fail('DPAPI round-trip 结果不一致')
    else ok('DPAPI 加解密可用（CurrentUser）')
  } catch (err) {
    fail(`DPAPI 不可用: ${err instanceof Error ? err.message : String(err)}`)
  }
  if (!hasDeviceToken()) {
    fail('credentials.bin 不存在，请先 pair')
  } else {
    try {
      await loadDeviceToken()
      ok('设备凭证可解密')
    } catch (err) {
      fail(err instanceof Error ? err.message : String(err))
    }
  }

  // 3. 云端可达（已配对才检：heartbeat 全链路）
  if (config && hasDeviceToken()) {
    try {
      const token = await loadDeviceToken()
      const api = new ApiClient(config.server, token)
      const hb = await api.heartbeat({
        runtime_version: RUNTIME_VERSION,
        capabilities: deviceCapabilities(),
        manifest_digest: manifestDigest(),
      })
      ok(`云端可达且设备有效（selected=${hb.selected}）`)
    } catch (err) {
      fail(`云端心跳失败: ${err instanceof Error ? err.message : String(err)}`)
    }
  }

  // 4/5. 各 Provider 入口存在性 + 逐 Provider doctor（boss 输出保持原格式）
  const entries = resolveProviderEntries(config)
  for (const [key, entry] of Object.entries(entries)) {
    const isBoss = key === 'boss-recruiting'
    if (existsSync(entry)) {
      ok(isBoss ? `boss CLI 入口存在: ${entry}` : `Provider ${key} 入口存在: ${entry}`)
    } else {
      fail(isBoss
        ? `boss CLI 入口不存在: ${entry}（先构建 boss-resume-assistant，或在 config.json 配 bossCliEntry）`
        : `Provider ${key} 入口不存在: ${entry}（在 config.json providers.${key}.entry 配置绝对路径）`)
      continue
    }
    const result = await new Promise<{ code: number | null; tail: string }>((resolve) => {
      execFile(process.execPath, [entry, 'doctor'], { timeout: 60_000 }, (err, stdout, stderr) => {
        const out = `${stdout}\n${stderr}`.trim()
        const tail = out.split('\n').slice(-3).join(' | ')
        resolve({ code: err ? (typeof err.code === 'number' ? err.code : 1) : 0, tail })
      })
    })
    if (result.code === 0) ok(isBoss ? 'boss doctor 通过' : `Provider ${key} doctor 通过`)
    else fail(isBoss
      ? `boss doctor 未通过（exit=${result.code}）: ${result.tail}`
      : `Provider ${key} doctor 未通过（exit=${result.code}）: ${result.tail}`)
  }

  // 6. 桌面可交互
  const desktop = await checkDesktopInteractive()
  if (desktop.interactive) ok('Windows 桌面可交互（未锁屏）')
  else fail(`桌面不可交互或锁屏${desktop.error ? `: ${desktop.error}` : '（请解锁后重试）'}`)

  console.log(failures === 0 ? 'doctor 通过：环境就绪' : `doctor 发现 ${failures} 个问题`)
  return failures === 0 ? 0 : 1
}

async function cmdUnpair(): Promise<number> {
  const hadCreds = clearCredentials()
  const hadConfig = existsSync(configPath())
  if (hadConfig) rmSync(configPath())
  if (!hadCreds && !hadConfig) {
    console.log('本地无配对信息，无需解除')
  } else {
    console.log('已清除本地凭证与配置')
  }
  console.log('注意：本命令仅清除本地数据；请在 Web 端「本地工具设备管理」中删除设备以真正撤销 token')
  return 0
}

export async function main(argv: string[] = process.argv.slice(2)): Promise<number> {
  const args = parseArgs(argv)
  switch (args.command) {
    case 'pair':
      return cmdPair(args)
    case 'start':
      return cmdStart(args)
    case 'status':
      return cmdStatus()
    case 'doctor':
      return cmdDoctor()
    case 'unpair':
      return cmdUnpair()
    case null:
    case 'help':
      console.log(USAGE)
      return args.command === null ? 0 : 0
    default:
      console.error(`未知命令: ${args.command}\n`)
      console.log(USAGE)
      return 1
  }
}

// 只有 node 直接执行本文件（cli.js）才进入 main；被测试 import 时不触发
if (process.argv[1] && /cli\.js$/.test(process.argv[1].replace(/\\/g, '/'))) {
  main().then((code) => process.exit(code)).catch((err) => {
    console.error(`运行时错误: ${err instanceof Error ? err.message : String(err)}`)
    process.exit(1)
  })
}
