/** Protocol 3 transaction. OTP is never persisted; all private state uses existing DPAPI. */
import { randomBytes, randomUUID } from 'node:crypto'
import { existsSync, lstatSync, readFileSync, renameSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { acquireHostLease, ManagementError } from '@aid/local-tool-host-core'
import { ApiClient, type CleanPairRequest } from './apiClient.js'
import { dpapiProtect, dpapiUnprotect } from './dpapi.js'
import { atomicWrite } from './pairingStore.js'
import { PLATFORM, RUNTIME_VERSION, machineFingerprint } from './config.js'
import { validServer } from './connectionDiagnosis.js'

export interface CleanRegistration {
  version: 1; server: string; request: Omit<CleanPairRequest, 'code'>
  phase: 'requested' | 'paired' | 'installed'; device_id: string | null; home: string | null
}
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const file = (metadata: string) => join(metadata, 'clean-registration.bin')
export async function readCleanRegistration(metadata: string): Promise<CleanRegistration | null> {
  if (!existsSync(file(metadata))) return null
  try {
    const stat = lstatSync(file(metadata))
    if (!stat.isFile() || stat.isSymbolicLink() || stat.size > 65536) throw new Error()
    const r = JSON.parse(await dpapiUnprotect(readFileSync(file(metadata), 'utf8'))) as CleanRegistration
    if (!r || Object.keys(r).sort().join(',') !== 'device_id,home,phase,request,server,version' || r.version !== 1
      || !validServer(r.server) || !['requested', 'paired', 'installed'].includes(r.phase)
      || !r.request || r.request.pairing_protocol !== 3 || !uuid.test(r.request.registration_id)
      || !/^[0-9a-f]{64}$/.test(r.request.registration_token) || typeof r.request.name !== 'string' || !r.request.name
      || r.request.name.length > 100 || typeof r.request.platform !== 'string' || typeof r.request.runtime_version !== 'string'
      || !r.request.capabilities || typeof r.request.capabilities !== 'object' || Array.isArray(r.request.capabilities)
      || typeof r.request.machine_fingerprint !== 'string' || !/^[0-9a-f]{64}$/.test(r.request.machine_fingerprint)
      || r.request.previous_device_token !== undefined && !/^[0-9a-f]{64}$/.test(r.request.previous_device_token)
      || Object.keys(r.request).some(key => !['pairing_protocol', 'registration_id', 'registration_token', 'name', 'platform', 'runtime_version', 'capabilities', 'machine_fingerprint', 'previous_device_token'].includes(key))
      || Object.hasOwn(r.request, 'code') || ![null, 'string'].includes(r.home === null ? null : typeof r.home)
      || r.home !== null && resolve(r.home) !== r.home
      || r.phase === 'requested' && r.device_id !== null || r.phase === 'installed' && r.home === null
      || r.phase !== 'requested' && (!r.device_id || !uuid.test(r.device_id))) throw new Error()
    return r
  } catch { throw new ManagementError(11, '本次连接注册记录损坏或无法解密，请使用新配对码重新连接') }
}
export async function saveCleanRegistration(metadata: string, value: CleanRegistration): Promise<void> {
  atomicWrite(file(metadata), await dpapiProtect(JSON.stringify(value)))
}
export async function withCleanRegistration<T>(metadata: string, action: () => Promise<T>): Promise<T> {
  // The validated stable root defines the OS lease. No leftover lock file or
  // directory from an older installation is consulted.
  const lease = await acquireHostLease(metadata)
  if (!lease) throw new ManagementError(11, '另一客户端正在配置连接，请退出重复打开的本产品客户端')
  try { return await action() } finally { await lease.release() }
}
async function previousToken(home: string, server: string): Promise<string | undefined> {
  try {
    const configFile = join(home, 'config.json'), tokenFile = join(home, 'credentials.bin')
    for (const location of [configFile, tokenFile]) {
      const stat = lstatSync(location)
      if (!stat.isFile() || stat.isSymbolicLink() || stat.size > 65536) return undefined
    }
    const config = JSON.parse(readFileSync(configFile, 'utf8').replace(/^\uFEFF/, ''))
    if (typeof config.server !== 'string' || config.server.replace(/\/+$/, '') !== server.replace(/\/+$/, '')) return undefined
    const token = await dpapiUnprotect(readFileSync(tokenFile, 'utf8'))
    return /^[0-9a-f]{64}$/.test(token) ? token : undefined
  } catch { return undefined }
}
export async function beginCleanRegistration(metadata: string, input: { server: string; device_name: string }, previousHomes: string[]): Promise<CleanRegistration> {
  if (!validServer(input.server) || !input.device_name.trim() || input.device_name.length > 100) throw new ManagementError(3, '请填写有效的企业服务地址和设备名称')
  if (existsSync(file(metadata))) {
    const stat = lstatSync(file(metadata))
    if (!stat.isFile() || stat.isSymbolicLink()) throw new ManagementError(11, '注册文件路径异常，请检查当前用户目录权限')
    renameSync(file(metadata), join(metadata, `previous-registration-${randomUUID()}.bin`))
  }
  let previous: string | undefined
  try {
    const proofFile = join(metadata, 'previous-identity.bin')
    const stat = lstatSync(proofFile)
    if (stat.isFile() && !stat.isSymbolicLink() && stat.size <= 65536) {
      const proof = JSON.parse(await dpapiUnprotect(readFileSync(proofFile, 'utf8')))
      if (typeof proof.config?.server === 'string' && proof.config.server.replace(/\/+$/, '') === input.server.replace(/\/+$/, '')
        && typeof proof.token === 'string' && /^[0-9a-f]{64}$/.test(proof.token)) previous = proof.token
    }
  } catch { /* Invalid legacy identity proof cannot block a clean registration. */ }
  // Prefer the current active identity over older installation proof.
  for (const home of previousHomes) { const token = await previousToken(home, input.server); if (token) { previous = token; break } }
  const r: CleanRegistration = { version: 1, server: input.server.replace(/\/+$/, ''), phase: 'requested', device_id: null, home: null,
    request: { pairing_protocol: 3, registration_id: randomUUID(), registration_token: randomBytes(32).toString('hex'), name: input.device_name,
      platform: PLATFORM, runtime_version: RUNTIME_VERSION, capabilities: { providers: [] }, machine_fingerprint: machineFingerprint(),
      ...(previous ? { previous_device_token: previous } : {}) } }
  await saveCleanRegistration(metadata, r)
  if ((await readCleanRegistration(metadata))?.request.registration_id !== r.request.registration_id) throw new ManagementError(11, '新连接凭据保存检查失败，请检查当前用户目录权限')
  return r
}
export async function pairCleanRegistration(metadata: string, r: CleanRegistration, code?: string): Promise<void> {
  if (r.phase !== 'requested') {
    await new ApiClient(r.server, r.request.registration_token).checkConnection()
    return
  }
  const response = await new ApiClient(r.server).pairClean({ ...r.request, ...(code ? { code } : {}) })
  r.device_id = response.device_id; r.phase = 'paired'
  await saveCleanRegistration(metadata, r)
}
export async function installCleanRegistration(metadata: string, r: CleanRegistration, home: string): Promise<void> {
  if (!r.device_id || r.phase === 'requested') throw new ManagementError(11, '服务器尚未确认连接注册')
  r.home = resolve(home)
  await saveCleanRegistration(metadata, r)
  // Target is a new generation allocated by Main, never the old execution home.
  atomicWrite(join(home, 'credentials.bin'), await dpapiProtect(r.request.registration_token))
  atomicWrite(join(home, 'config.json'), JSON.stringify({ server: r.server, device_id: r.device_id, name: r.request.name }, null, 2))
  r.phase = 'installed'
  await saveCleanRegistration(metadata, r)
}
export function finishCleanRegistration(metadata: string): void {
  // Keep a protected recovery receipt, but do not make it an execution gate.
  if (existsSync(file(metadata))) renameSync(file(metadata), join(metadata, `completed-registration-${randomUUID()}.bin`))
}

/** Finish a previously saved protocol-2 identity without importing its resource gate. */
export async function finishLegacyRegistration(metadata: string, home: string): Promise<boolean> {
  const marker = join(metadata, 'legacy-replacement-pending.json')
  if (!existsSync(marker)) return false
  try {
    const stat = lstatSync(marker)
    if (!stat.isFile() || stat.isSymbolicLink() || stat.size > 1024) throw new Error()
    const record = JSON.parse(readFileSync(marker, 'utf8'))
    const config = JSON.parse(readFileSync(join(home, 'config.json'), 'utf8'))
    if (record.version !== 1 || !uuid.test(record.device_id) || record.device_id !== config.device_id || !validServer(config.server)) return false
    const token = await dpapiUnprotect(readFileSync(join(home, 'credentials.bin'), 'utf8'))
    const api = new ApiClient(config.server, token)
    if (record.replacement_pending !== false) await api.completeReplacement()
    await api.activateDevice()
    renameSync(marker, join(metadata, `completed-legacy-registration-${randomUUID()}.json`))
    return true
  } catch (error) {
    // HTTP failures remain typed for automatic retry; invalid legacy bytes are
    // a migration miss and cannot prevent a new explicit protocol-3 pair.
    if (error instanceof Error && ['ApiError', 'NetworkError', 'DeviceRevokedError'].includes(error.name)) throw error
    return false
  }
}
