import { mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import type { SafeStorage } from 'electron'

const CREDENTIAL_KEY = /^(desktop_auth|demo_token|user_info|saas_(token|admin|tenant)(_[a-zA-Z0-9_-]{1,128})?)$/
const MAX_CREDENTIAL_VALUE_LENGTH = 64 * 1024

export function isAllowedCredentialKey(key: string): boolean {
  return CREDENTIAL_KEY.test(key) && !key.startsWith('portal_')
}

export function isAllowedCredentialValue(value: unknown): value is string {
  return typeof value === 'string' && value.length <= MAX_CREDENTIAL_VALUE_LENGTH
}

function parseEncryptedCredentials(value: string): Record<string, string> {
  const parsed: unknown = JSON.parse(value)
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('encrypted credential file is invalid')
  const result: Record<string, string> = {}
  for (const [key, encrypted] of Object.entries(parsed)) {
    if (!isAllowedCredentialKey(key) || typeof encrypted !== 'string') throw new Error('encrypted credential file is invalid')
    result[key] = encrypted
  }
  return result
}

function decodeEncryptedValue(value: string): Buffer {
  if (!/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(value) || value.length === 0) {
    throw new Error('encrypted credential cannot be decrypted')
  }
  return Buffer.from(value, 'base64')
}

export class EncryptedCredentialStore {
  constructor(private readonly filePath: string, private readonly encryption: Pick<SafeStorage, 'isEncryptionAvailable' | 'encryptString' | 'decryptString'>) {}

  load(): Record<string, string> {
    if (!this.encryption.isEncryptionAvailable()) throw new Error('secure credential encryption unavailable')
    let stored: Record<string, string>
    try {
      stored = parseEncryptedCredentials(readFileSync(this.filePath, 'utf8'))
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') return {}
      throw new Error('encrypted credential file is invalid')
    }
    const result: Record<string, string> = {}
    for (const [key, encrypted] of Object.entries(stored)) {
      try {
        result[key] = this.encryption.decryptString(decodeEncryptedValue(encrypted))
      } catch {
        throw new Error('encrypted credential cannot be decrypted')
      }
    }
    return result
  }

  set(key: string, value: string): void {
    if (!isAllowedCredentialKey(key) || !isAllowedCredentialValue(value)) throw new Error('credential request is not allowed')
    const current = this.readEncrypted()
    current[key] = this.encryption.encryptString(value).toString('base64')
    this.writeEncrypted(current)
  }

  delete(key: string): void {
    if (!isAllowedCredentialKey(key)) throw new Error('credential key is not allowed')
    const current = this.readEncrypted()
    delete current[key]
    this.writeEncrypted(current)
  }

  private readEncrypted(): Record<string, string> {
    if (!this.encryption.isEncryptionAvailable()) throw new Error('secure credential encryption unavailable')
    try {
      return parseEncryptedCredentials(readFileSync(this.filePath, 'utf8'))
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') return {}
      throw new Error('encrypted credential file is invalid')
    }
  }

  private writeEncrypted(value: Record<string, string>): void {
    mkdirSync(path.dirname(this.filePath), { recursive: true })
    const temporary = `${this.filePath}.tmp`
    writeFileSync(temporary, JSON.stringify(value), { encoding: 'utf8', mode: 0o600 })
    renameSync(temporary, this.filePath)
  }
}
