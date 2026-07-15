import { mkdirSync, readFileSync, renameSync, rmSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import { normalizeApiBaseUrl } from './security.js'

export const DESKTOP_CONFIG_FILE_NAME = 'desktop-config.json'
export const PACKAGED_CONFIG_RELATIVE_PATH = path.join('config', DESKTOP_CONFIG_FILE_NAME)

export interface DesktopApiConfiguration {
  schemaVersion: 1
  apiBaseUrl: string
}

export interface ResolvedApiConfiguration {
  apiBaseUrl: string
  source: 'environment' | 'user' | 'packaged'
  userConfigPath: string
}

function parseConfigFile(file: string, label: string): DesktopApiConfiguration {
  let value: unknown
  try {
    value = JSON.parse(readFileSync(file, 'utf8'))
  } catch (error) {
    throw new Error(`${label} unreadable`, { cause: error })
  }
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(`${label} must be a JSON object`)
  const record = value as Record<string, unknown>
  const keys = Object.keys(record).sort()
  if (keys.length !== 2 || keys[0] !== 'apiBaseUrl' || keys[1] !== 'schemaVersion') {
    throw new Error(`${label} schema is invalid`)
  }
  if (record.schemaVersion !== 1 || typeof record.apiBaseUrl !== 'string') throw new Error(`${label} schema is invalid`)
  return { schemaVersion: 1, apiBaseUrl: normalizeApiBaseUrl(record.apiBaseUrl) }
}

function writeConfigAtomic(file: string, configuration: DesktopApiConfiguration): void {
  mkdirSync(path.dirname(file), { recursive: true })
  const temporary = `${file}.${process.pid}.tmp`
  try {
    writeFileSync(temporary, `${JSON.stringify(configuration, null, 2)}\n`, { flag: 'wx', mode: 0o600 })
    renameSync(temporary, file)
  } finally {
    rmSync(temporary, { force: true })
  }
}

export function resolveApiConfiguration(input: {
  environmentValue?: string
  userDataPath: string
  resourcesPath: string
}): ResolvedApiConfiguration {
  const userConfigPath = path.join(input.userDataPath, DESKTOP_CONFIG_FILE_NAME)
  if (input.environmentValue !== undefined && input.environmentValue.trim() !== '') {
    return {
      apiBaseUrl: normalizeApiBaseUrl(input.environmentValue),
      source: 'environment',
      userConfigPath,
    }
  }

  try {
    const user = parseConfigFile(userConfigPath, 'user desktop config')
    return { apiBaseUrl: user.apiBaseUrl, source: 'user', userConfigPath }
  } catch (error) {
    const cause = error as NodeJS.ErrnoException
    if (cause.cause && (cause.cause as NodeJS.ErrnoException).code !== 'ENOENT') throw error
    if (!cause.cause) throw error
  }

  const packagedPath = path.join(input.resourcesPath, PACKAGED_CONFIG_RELATIVE_PATH)
  const packaged = parseConfigFile(packagedPath, 'packaged desktop config')
  writeConfigAtomic(userConfigPath, packaged)
  return { apiBaseUrl: packaged.apiBaseUrl, source: 'packaged', userConfigPath }
}
