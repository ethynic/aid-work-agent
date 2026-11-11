/**
 * 配置解析与持久化。
 *
 * - 目录：%APPDATA%/aidwork-tool-runtime/（可用 AIDWORK_RUNTIME_HOME 覆盖，测试隔离用）
 * - config.json：server / device_id / name / bossCliEntry（不含 token）
 * - machine_fingerprint：machineGuid + hostname 的 sha256，只发 hash
 */
import { createHash } from 'node:crypto'
import { execFileSync } from 'node:child_process'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { effectiveManifestFor, manifestDigestOf, TRUSTED_MANIFESTS, type ProviderManifest } from './providers.js'

/** 运行时版本（读 package.json，src/dist 两种布局兜底；不手写字符串防漂移误导排障） */
export const RUNTIME_VERSION: string = (() => {
  const here = path.dirname(fileURLToPath(import.meta.url))
  for (const p of [path.join(here, '..', 'package.json'), path.join(here, '..', '..', 'package.json')]) {
    try {
      if (existsSync(p)) return String(JSON.parse(readFileSync(p, 'utf8')).version ?? '0.0.0')
    } catch {
      // 读失败试下一个候选
    }
  }
  return '0.0.0'
})()
export const PLATFORM = 'win32-x64'

export interface RuntimeConfig {
  server: string
  device_id: string
  name?: string
  /** boss CLI 入口绝对路径（本地管理员配置，禁止云端下发；折算为 providers['boss-recruiting'].entry 的高优先级来源） */
  bossCliEntry?: string
  /** 各 Provider 入口（key → entry 绝对路径，本地管理员配置，禁止云端下发；无 entry 的 Provider 视为未安装）。
   * v2Send（可选，显式协商）：仅隔离测试/真实 v2 Provider 具备 weixin_message_send_v2
   * 时开启——开启后受信 manifest 升级 v2 变体并上报 v2 发送能力（#6 能力真实性） */
  providers?: Record<string, { entry: string; v2Send?: boolean }>
  /** 端侧会话任务引擎开关（C2；默认关闭，显式开启后与 pollLoop 并存运行） */
  sessionTasks?: boolean
}

export function runtimeHomeDir(): string {
  if (configuredHome) return configuredHome
  const override = process.env.AIDWORK_RUNTIME_HOME
  if (override) return override
  const appData = process.env.APPDATA ?? path.join(os.homedir(), 'AppData', 'Roaming')
  return path.join(appData, 'aidwork-tool-runtime')
}

let configuredHome: string | undefined
/** Trusted composition only; never exposed through a management request. */
export function configureRuntimeHome(home: string | undefined): void { configuredHome = home }

export function configPath(): string {
  return path.join(runtimeHomeDir(), 'config.json')
}

export function credentialsPath(): string {
  return path.join(runtimeHomeDir(), 'credentials.bin')
}

export function loadConfig(): RuntimeConfig | null {
  const file = configPath()
  if (!existsSync(file)) return null
  try {
    const parsed = JSON.parse(readFileSync(file, 'utf8').replace(/^\uFEFF/, '')) as RuntimeConfig
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed) || typeof parsed.server !== 'string' || !parsed.server || typeof parsed.device_id !== 'string' || !parsed.device_id) return null
    return parsed
  } catch {
    return null
  }
}

export function saveConfig(config: RuntimeConfig): void {
  const dir = runtimeHomeDir()
  mkdirSync(dir, { recursive: true })
  writeFileSync(configPath(), JSON.stringify(config, null, 2), 'utf8')
}

/**
 * 解析全部 Provider 入口（key → entry 绝对路径）。
 *
 * - boss-recruiting 仅显式 legacy 配置可用：bossCliEntry > providers['boss-recruiting'].entry。
 * - 其余 Provider 仅在 config.providers 显式配置 entry 时可用（未配置=未安装，不上报不启用）。
 */
export function resolveProviderEntries(config: RuntimeConfig | null): Record<string, string> {
  const entries: Record<string, string> = {}
  const providerConfig = config?.providers
  if (providerConfig && typeof providerConfig === 'object') {
    for (const [key, value] of Object.entries(providerConfig)) {
      if (typeof value?.entry === 'string' && value.entry) entries[key] = value.entry
    }
  }
  // Empty hosts publish no business provider; explicit legacy entries remain compatible.
  if (config?.bossCliEntry) entries['boss-recruiting'] = config.bossCliEntry
  return entries
}

function readMachineGuid(): string | null {
  try {
    const out = execFileSync(
      'reg.exe',
      ['query', 'HKLM\\SOFTWARE\\Microsoft\\Cryptography', '/v', 'MachineGuid'],
      { encoding: 'utf8', timeout: 10_000, windowsHide: true },
    )
    const match = out.match(/MachineGuid\s+REG_SZ\s+(\S+)/)
    return match?.[1] ?? null
  } catch {
    return null
  }
}

/** 机器指纹 sha256（只发 hash，不发原始 machineGuid） */
export function machineFingerprint(): string {
  const guid = readMachineGuid() ?? 'unknown-machine'
  return createHash('sha256').update(`${guid}|${os.hostname()}`).digest('hex')
}

/**
 * 设备能力上报。显式传 config（cli/测试）；缺省读当前 config.json。
 *
 * - providers：已配置入口的可用 provider key 数组（无 entry 的 manifest 不进数组，如默认未安装的 weixin）
 * - protocol_version：Runtime 侧统一操作协议版本（v2 支持按 invocation 级 provider 路由）
 * - provider_manifests：各可用 provider 的 manifest 摘要（provider_id/manifest_digest/protocol_version）
 * - provider_id：第一个可用 provider 的 id（云端 catalog 兼容字段；boss-only 环境与历史一致）
 * - store（契约 2.20，可选）：商店插件派生 manifest 与调度声明上报——manifests 优先于
 *   TRUSTED 回退；parallel_tools 仅列声明 parallel 的工具（legacy 直配 entry 不参与）；
 *   存在任一有效声明时 capabilities 追加 tool_declarations_v1
 *
 * 键序契约：providers 数组与 provider_manifests 键序固定为 boss-recruiting 恒首位、其余
 * 按字典序——首个可用 provider 即旧 provider_id 兼容字段的取值来源，顺序漂移会改变
 * 上报字节与云端 catalog 归属，不可依赖对象插入顺序的隐式行为。
 */
/** 商店插件声明上报输入（契约 2.20）：派生 manifest、并行工具与存在性，来自 PluginStore */
export interface StoreDeclarationReport {
  parallelTools?: Record<string, readonly string[]>
  hasDeclarations?: boolean
  manifests?: Record<string, ProviderManifest>
}
export function deviceCapabilities(config?: RuntimeConfig | null, wecomBusinessReady = false, wecomDirectoryReady = false,
  wecomDirectorySearchReady = false, wecomConfigDoctorReady = false, wecomConfigCalibrateReady = false,
  store?: StoreDeclarationReport): Record<string, unknown> {
  const cfg = config === undefined ? loadConfig() : config
  const entries = resolveProviderEntries(cfg)
  const storeManifests = store?.manifests ?? {}
  const available = [...new Set([...Object.keys(TRUSTED_MANIFESTS), ...Object.keys(storeManifests)])]
    .filter((key) => Boolean(entries[key] && existsSync(entries[key]!)))
    // 键序契约：boss 恒首位（provider_id 兼容字段来源），其余字典序稳定
    .sort((a, b) => {
      if (a === 'boss-recruiting') return b === 'boss-recruiting' ? 0 : -1
      if (b === 'boss-recruiting') return 1
      return a < b ? -1 : a > b ? 1 : 0
    })
  // 实际生效 manifest：商店派生 > v2Send 显式协商覆盖 > TRUSTED 回退
  const currentManifestFor = (key: string) => storeManifests[key] ?? effectiveManifestFor(key) ?? TRUSTED_MANIFESTS[key]!
  const manifests: Record<string, { provider_id: string; manifest_digest: string; protocol_version: number }> = {}
  for (const key of available) {
    // D5：上报/摘要统一读**实际生效** manifest（含 v2Send 显式协商变体）——
    // 运行时校验、能力上报与摘要计算三者同源，显式开启 v2 后不再上报旧 v1 摘要
    const current = currentManifestFor(key)
    const manifest = key === 'wecom'
      ? { ...current, tools: current.tools.filter(name =>
          (name !== 'wecom_read_sessions' || wecomBusinessReady) &&
          (name !== 'wecom_session_directory' || wecomDirectoryReady) &&
          (name !== 'wecom_config_doctor' || wecomConfigDoctorReady) &&
          (name !== 'wecom_config_calibrate' || wecomConfigCalibrateReady)) } : current
    manifests[key] = {
      provider_id: manifest.provider_id,
      manifest_digest: manifestDigestOf(manifest),
      protocol_version: manifest.protocol_version,
    }
  }
  const first = available[0]
  // 会话任务能力名（C1 REQUIRED_DEVICE_CAPABILITIES：session_task_v1 +
  // session_observer_v1 + v2 写链）。weixin Provider 安装才上报——未安装的设备
  // 不会通过会话任务的发布/分配能力检查（设计 §10 fail-closed）
  const capabilities: string[] = ['local_v2']
  if (available.includes('weixin')) {
    capabilities.push('session_task_v1', 'session_observer_v1')
    // #6/D5 能力真实性：v2 发送能力与实际生效 manifest 的工具集合同源判定——
    // 显式协商（v2Send）使 manifest 升级 v2 变体后才上报；旧 Provider 不得宣称
    const effectiveWeixin = currentManifestFor('weixin')
    if (effectiveWeixin.tools.includes('weixin_message_send_v2')) {
      capabilities.push('weixin_message_send_v2')
    }
  }
  // wecom 工具能力（M11b Phase 1）：粒度对齐 weixin（能力串 = MCP 工具名，云端按
  // 工具名做发布/分配门禁）——与实际生效 manifest 同源（真实能力上报，未接入的
  // search/select/watch_poll/add_customer 不出现）；Provider 未安装（无 entry）不上报
  if (available.includes('wecom')) {
    const effectiveWecom = currentManifestFor('wecom')
    capabilities.push(...effectiveWecom.tools.filter(name =>
      (name !== 'wecom_read_sessions' || wecomBusinessReady) &&
      (name !== 'wecom_session_directory' || wecomDirectoryReady) &&
      (name !== 'wecom_config_doctor' || wecomConfigDoctorReady) &&
      (name !== 'wecom_config_calibrate' || wecomConfigCalibrateReady)))
    if (wecomDirectoryReady && wecomDirectorySearchReady && effectiveWecom.tools.includes('wecom_session_directory')) {
      capabilities.push('wecom_session_directory_search')
    }
  }
  if (store?.hasDeclarations) capabilities.push('tool_declarations_v1')
  // parallel_tools：仅已验签、启用且带有效声明的商店插件（store 来源），且 provider
  // 实际可用（有 entry）；无并行工具的 provider 不出现键，全空则整个字段省略
  const parallel: Record<string, string[]> = {}
  for (const [key, tools] of Object.entries(store?.parallelTools ?? {})) {
    if (tools.length > 0 && available.includes(key)) parallel[key] = [...tools]
  }
  return {
    providers: available,
    protocol_version: 2,
    provider_manifests: manifests,
    capabilities,
    ...(Object.keys(parallel).length > 0 ? { parallel_tools: parallel } : {}),
    ...(first ? { provider_id: (TRUSTED_MANIFESTS[first] ?? storeManifests[first])!.provider_id } : {}),
  }
}
