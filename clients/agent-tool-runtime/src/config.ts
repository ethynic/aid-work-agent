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
import { manifestDigestFor, TRUSTED_MANIFESTS } from './providers.js'

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
  /** 各 Provider 入口（key → entry 绝对路径，本地管理员配置，禁止云端下发；无 entry 的 Provider 视为未安装） */
  providers?: Record<string, { entry: string }>
  /** 端侧会话任务引擎开关（C2；默认关闭，显式开启后与 pollLoop 并存运行） */
  sessionTasks?: boolean
}

export function runtimeHomeDir(): string {
  const override = process.env.AIDWORK_RUNTIME_HOME
  if (override) return override
  const appData = process.env.APPDATA ?? path.join(os.homedir(), 'AppData', 'Roaming')
  return path.join(appData, 'aidwork-tool-runtime')
}

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
    const parsed = JSON.parse(readFileSync(file, 'utf8')) as RuntimeConfig
    if (!parsed.server || !parsed.device_id) return null
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
 * 默认 boss CLI 入口。优先级：
 * 1. 随包捆绑的依赖：node_modules/boss-resume-assistant（npm 全局安装 tgz 的场景）
 * 2. 开发仓库兄弟目录：../boss-resume-assistant（源码构建直接跑的场景）
 */
export function defaultBossCliEntry(): string {
  const here = path.dirname(fileURLToPath(import.meta.url))
  // dist/src/config.js → 包根是上两级
  const pkgRoot = path.resolve(here, '..', '..')
  const bundled = path.resolve(pkgRoot, 'node_modules', 'boss-resume-assistant', 'dist', 'src', 'cli', 'index.js')
  if (existsSync(bundled)) return bundled
  return path.resolve(pkgRoot, '..', 'boss-resume-assistant', 'dist', 'src', 'cli', 'index.js')
}

export function resolveBossCliEntry(config: RuntimeConfig | null): string {
  return config?.bossCliEntry ?? defaultBossCliEntry()
}

/**
 * 解析全部 Provider 入口（key → entry 绝对路径）。
 *
 * - boss-recruiting 始终可用（现状兼容）：bossCliEntry > providers['boss-recruiting'].entry > 默认入口。
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
  // boss 恒可用（现状兼容）：bossCliEntry > providers['boss-recruiting'].entry > 默认入口
  entries['boss-recruiting'] = config?.bossCliEntry ?? entries['boss-recruiting'] ?? defaultBossCliEntry()
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
 *
 * 键序契约：providers 数组与 provider_manifests 键序固定为 boss-recruiting 恒首位、其余
 * 按字典序——首个可用 provider 即旧 provider_id 兼容字段的取值来源，顺序漂移会改变
 * 上报字节与云端 catalog 归属，不可依赖对象插入顺序的隐式行为。
 */
export function deviceCapabilities(config?: RuntimeConfig | null): Record<string, unknown> {
  const cfg = config === undefined ? loadConfig() : config
  const entries = resolveProviderEntries(cfg)
  const available = Object.keys(TRUSTED_MANIFESTS)
    .filter((key) => Boolean(entries[key]))
    // 键序契约：boss 恒首位（provider_id 兼容字段来源），其余字典序稳定
    .sort((a, b) => {
      if (a === 'boss-recruiting') return b === 'boss-recruiting' ? 0 : -1
      if (b === 'boss-recruiting') return 1
      return a < b ? -1 : a > b ? 1 : 0
    })
  const manifests: Record<string, { provider_id: string; manifest_digest: string; protocol_version: number }> = {}
  for (const key of available) {
    const manifest = TRUSTED_MANIFESTS[key]!
    manifests[key] = {
      provider_id: manifest.provider_id,
      manifest_digest: manifestDigestFor(key),
      protocol_version: manifest.protocol_version,
    }
  }
  const first = available[0]
  return {
    providers: available,
    protocol_version: 2,
    provider_manifests: manifests,
    ...(first ? { provider_id: TRUSTED_MANIFESTS[first]!.provider_id } : {}),
  }
}
