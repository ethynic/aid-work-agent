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

export const RUNTIME_VERSION = '0.1.0'
export const PLATFORM = 'win32-x64'

export interface RuntimeConfig {
  server: string
  device_id: string
  name?: string
  /** boss CLI 入口绝对路径（本地管理员配置，禁止云端下发） */
  bossCliEntry?: string
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

/** 默认 boss CLI 入口：相对 runtime 包目录的 ../boss-resume-assistant/dist/src/cli/index.js */
export function defaultBossCliEntry(): string {
  const here = path.dirname(fileURLToPath(import.meta.url))
  // dist/src/config.js → 包根是上两级
  const pkgRoot = path.resolve(here, '..', '..')
  return path.resolve(pkgRoot, '..', 'boss-resume-assistant', 'dist', 'src', 'cli', 'index.js')
}

export function resolveBossCliEntry(config: RuntimeConfig | null): string {
  return config?.bossCliEntry ?? defaultBossCliEntry()
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

/** 设备能力：providers 清单（规格 §2）+ provider_id（云端 catalog 解析用，M0.3 契约） */
export function deviceCapabilities(): Record<string, unknown> {
  return {
    providers: ['boss-recruiting'],
    provider_id: 'ai.aidwork.boss-recruiting',
  }
}
