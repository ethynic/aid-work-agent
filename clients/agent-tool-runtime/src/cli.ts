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
import { hostname } from 'node:os'
import { setTimeout as delay } from 'node:timers/promises'
import { ManagementError, type ManagementOperation, type RuntimeHost } from '@aid/local-tool-host-core'
import { openRuntimeHost } from './runtimeHost.js'
import { ApiClient } from './apiClient.js'
import { configPath, credentialsPath, deviceCapabilities, loadConfig, resolveProviderEntries, RUNTIME_VERSION, runtimeHomeDir } from './config.js'
import { hasDeviceToken, loadDeviceToken } from './credentials.js'
import { checkDesktopInteractive } from './desktopCheck.js'
import { dpapiProtect, dpapiUnprotect } from './dpapi.js'
import { manifestDigest } from './manifestVerifier.js'
import { clearPairing } from './pairingStore.js'

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

async function runOperation(host: RuntimeHost, method: 'pair' | 'start' | 'stop', params: Record<string, string> = {}): Promise<void> {
  const response = await host.request({ request_id: randomUUID(), method, params: { ...params, request_key: randomUUID() } })
  if (response.code !== 0) throw new ManagementError(Number(response.code), String(response.error))
  let operation = response.result as ManagementOperation
  while (operation.status === 'running') {
    await delay(25)
    const update = await host.request({ request_id: randomUUID(), method: 'operations.get', params: { operation_id: operation.operation_id } })
    if (update.code !== 0) throw new ManagementError(Number(update.code), String(update.error))
    operation = update.result as ManagementOperation
  }
  if (operation.status !== 'succeeded') throw new ManagementError(operation.code || 11, operation.error || operation.message || '执行事实尚需核对')
}

async function disposeIfStopped(host: RuntimeHost): Promise<void> {
  if (host.getState().state === 'stopped') await host.dispose()
}

async function cmdPair(args: ParsedArgs): Promise<number> {
  const code = flagString(args, 'code')
  if (!code?.trim()) {
    console.error('缺少 --code <8位配对码>（在 Web 端「本地工具」页面生成）')
    return 1
  }
  const existing = loadConfig()
  const server = flagString(args, 'server') ?? existing?.server
  if (!server) {
    console.error('缺少 --server <云端地址>（首次 pair 必须提供，之后可从 config.json 继承）')
    return 1
  }
  const host = await openRuntimeHost()
  try {
    await runOperation(host, 'pair', { server, device_name: flagString(args, 'name') || existing?.name || hostname(), pairing_code: code.trim() })
    const device = host.getState().device as { device_id: string }
    console.log(`配对成功：device_id=${device.device_id} server=${server}`)
    console.log(`凭证已加密保存到 ${credentialsPath()}（DPAPI CurrentUser）`)
    return 0
  } catch (err) {
    console.error(`配对失败: ${err instanceof ManagementError ? err.message : '本机配对事务未完成，需核对'}`)
    return 1
  } finally { await disposeIfStopped(host) }
}

async function cmdStart(args: ParsedArgs): Promise<number> {
  const serverOverride = flagString(args, 'server')
  if (serverOverride && serverOverride !== loadConfig()?.server) {
    console.error('启动不能切换配对服务地址，请停止并重新配对')
    return 1
  }
  const host = await openRuntimeHost()
  let stopPromise: Promise<void> | null = null
  const stop = () => {
    if (stopPromise) return
    stopPromise = (async () => {
      await runOperation(host, 'stop')
      await host.dispose()
      process.removeListener('SIGINT', stop)
      process.removeListener('SIGTERM', stop)
    })()
    void stopPromise.catch(() => {
      process.exitCode = 1
      console.error('停止事实尚需核对，保留实例保护')
    })
  }
  // Keep listeners through the complete drain, including repeated signals. A second
  // signal must not revert to Node's default forced termination while work is accepted.
  process.on('SIGINT', stop)
  process.on('SIGTERM', stop)
  try {
    await runOperation(host, 'start')
    return 0
  } catch (err) {
    console.error(`启动失败: ${err instanceof ManagementError ? err.message : '本机启动事实尚需核对'}`)
    if (host.getState().state === 'stopped') {
      await host.dispose()
      process.removeListener('SIGINT', stop)
      process.removeListener('SIGTERM', stop)
    }
    return 1
  }
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
  const host = await openRuntimeHost()
  try {
    let hadIdentity = false
    await host.runLocalIdentityChange(async () => {
      hadIdentity = hasDeviceToken() || existsSync(configPath())
      await clearPairing()
    })
    console.log(hadIdentity ? '已清除本地凭证与配置' : '本地无配对信息，无需解除')
    console.log('注意：本命令仅清除本地数据；请在 Web 端「本地工具设备管理」中删除设备以真正撤销 token')
    return 0
  } catch (err) {
    console.error(`解除配对失败: ${err instanceof ManagementError ? err.message : '本机身份事务未完成，需核对'}`)
    return 1
  } finally { await disposeIfStopped(host) }
}

export async function main(argv: string[] = process.argv.slice(2)): Promise<number> {
  const args = parseArgs(argv)
  try {
    switch (args.command) {
      case 'pair':
        return await cmdPair(args)
      case 'start':
        return await cmdStart(args)
      case 'status':
        return await cmdStatus()
      case 'doctor':
        return await cmdDoctor()
      case 'unpair':
        return await cmdUnpair()
      case null:
      case 'help':
        console.log(USAGE)
        return args.command === null ? 0 : 0
      default:
        console.error(`未知命令: ${args.command}\n`)
        console.log(USAGE)
        return 1
    }
  } catch (err) {
    console.error(`运行时错误: ${err instanceof ManagementError ? err.message : '本机操作未完成，需核对'}`)
    return 1
  }
}

// 只有 node 直接执行本文件（cli.js）才进入 main；被测试 import 时不触发
if (process.argv[1] && /cli\.js$/.test(process.argv[1].replace(/\\/g, '/'))) {
  main().then((code) => { process.exitCode = code }).catch(() => {
    console.error('运行时错误: 本机操作未完成，需核对')
    process.exitCode = 1
  })
}
