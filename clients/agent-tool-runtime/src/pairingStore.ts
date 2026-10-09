/** Recoverable local identity changes. Call only while holding the Runtime Host lease. */
import { randomUUID } from 'node:crypto'
import { closeSync, existsSync, fsyncSync, mkdirSync, openSync, readFileSync, renameSync, rmSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { configPath, credentialsPath, runtimeHomeDir, type RuntimeConfig } from './config.js'
import { dpapiUnprotect } from './dpapi.js'

export function atomicWrite(file: string, contents: string): void {
  mkdirSync(dirname(file), { recursive: true })
  const temporary = `${file}.${randomUUID()}.tmp`
  try {
    writeFileSync(temporary, contents, { encoding: 'utf8', mode: 0o600, flag: 'wx' })
    const fd = openSync(temporary, 'r+')
    try { fsyncSync(fd) } finally { closeSync(fd) }
    renameSync(temporary, file)
  } finally {
    rmSync(temporary, { force: true })
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}
function exactKeys(value: Record<string, unknown>, allowed: string[]): boolean {
  return Object.keys(value).every(key => allowed.includes(key))
}
function validConfig(value: unknown): value is RuntimeConfig {
  if (!isRecord(value) || !exactKeys(value, ['server', 'device_id', 'name', 'bossCliEntry', 'providers', 'sessionTasks', 'skills'])
    || typeof value.server !== 'string' || !value.server || typeof value.device_id !== 'string' || !value.device_id) return false
  try {
    const url = new URL(value.server)
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) return false
  } catch { return false }
  if (['name', 'bossCliEntry'].some(key => value[key] !== undefined && typeof value[key] !== 'string')
    || (value.sessionTasks !== undefined && typeof value.sessionTasks !== 'boolean')) return false
  if (value.providers !== undefined && (!isRecord(value.providers) || Object.values(value.providers).some(provider =>
    !isRecord(provider) || !exactKeys(provider, ['entry', 'v2Send']) || typeof provider.entry !== 'string'
    || (provider.v2Send !== undefined && typeof provider.v2Send !== 'boolean')))) return false
  if (value.skills !== undefined && (!isRecord(value.skills) || !exactKeys(value.skills, ['dir', 'python'])
    || Object.values(value.skills).some(item => typeof item !== 'string'))) return false
  return true
}

export async function recoverPairing(unprotect: (value: string) => Promise<string> = dpapiUnprotect): Promise<void> {
  const pending = join(runtimeHomeDir(), 'pairing.pending.json')
  if (!existsSync(pending)) return
  // Validate the entire transaction before touching either identity file. Error messages
  // deliberately contain neither serialized input nor decrypted credentials.
  let transaction: unknown
  try { transaction = JSON.parse(readFileSync(pending, 'utf8')) }
  catch { throw new Error('配对事务无法读取，保留旧身份并等待本机核对') }
  if (isRecord(transaction) && transaction.action === 'unpair' && exactKeys(transaction, ['action'])) {
    rmSync(credentialsPath(), { force: true })
    rmSync(configPath(), { force: true })
  } else {
    if (!isRecord(transaction) || !exactKeys(transaction, ['config', 'encrypted_token']) || !validConfig(transaction.config)
      || typeof transaction.encrypted_token !== 'string' || !transaction.encrypted_token
      || Buffer.from(transaction.encrypted_token, 'base64').toString('base64') !== transaction.encrypted_token) {
      throw new Error('配对事务格式无效，保留旧身份并等待本机核对')
    }
    try {
      if (!(await unprotect(transaction.encrypted_token))) throw new Error('empty credential')
    } catch { throw new Error('配对事务凭证无法解密，保留旧身份并等待本机核对') }
    atomicWrite(credentialsPath(), transaction.encrypted_token)
    atomicWrite(configPath(), JSON.stringify(transaction.config, null, 2))
  }
  rmSync(pending)
}

export async function clearPairing(): Promise<void> {
  atomicWrite(join(runtimeHomeDir(), 'pairing.pending.json'), JSON.stringify({ action: 'unpair' }))
  await recoverPairing()
}
