import { readFileSync } from 'node:fs'
import path from 'node:path'

export const UPDATE_CONFIG_RELATIVE_PATH = path.join('config', 'update-config.json')

export type UpdateDisabledReason = 'not-packaged' | 'smoke-mode' | 'development-unsigned' | 'missing-configuration' | 'invalid-configuration'

export type DesktopUpdateConfiguration =
  | { enabled: true; updateBaseUrl: string }
  | { enabled: false; reason: UpdateDisabledReason }

export function normalizeUpdateBaseUrl(value: string): string {
  const trimmed = value.trim()
  if (!trimmed) throw new Error('update base URL is missing')
  const url = new URL(trimmed)
  if (url.protocol !== 'https:') throw new Error('update base URL must use HTTPS')
  if (url.username || url.password) throw new Error('update base URL must not contain credentials')
  if (url.search || url.hash) throw new Error('update base URL must not contain query or fragment')
  return url.toString().replace(/\/$/, '')
}

export function resolveUpdateConfiguration(input: {
  isPackaged: boolean
  smokeMode: boolean
  resourcesPath: string
}): DesktopUpdateConfiguration {
  if (!input.isPackaged) return { enabled: false, reason: 'not-packaged' }
  if (input.smokeMode) return { enabled: false, reason: 'smoke-mode' }

  let raw: string
  try {
    raw = readFileSync(path.join(input.resourcesPath, UPDATE_CONFIG_RELATIVE_PATH), 'utf8')
  } catch {
    return { enabled: false, reason: 'missing-configuration' }
  }
  let value: unknown
  try {
    value = JSON.parse(raw)
  } catch {
    return { enabled: false, reason: 'invalid-configuration' }
  }
  if (!value || typeof value !== 'object' || Array.isArray(value)) return { enabled: false, reason: 'invalid-configuration' }
  const record = value as Record<string, unknown>
  const keys = Object.keys(record).sort()
  if (keys.join(',') !== 'channel,schemaVersion,updateBaseUrl' || record.schemaVersion !== 1) {
    return { enabled: false, reason: 'invalid-configuration' }
  }
  if (record.channel !== 'release') return { enabled: false, reason: 'development-unsigned' }
  if (typeof record.updateBaseUrl !== 'string') return { enabled: false, reason: 'missing-configuration' }
  try {
    return { enabled: true, updateBaseUrl: normalizeUpdateBaseUrl(record.updateBaseUrl) }
  } catch {
    return { enabled: false, reason: 'invalid-configuration' }
  }
}
