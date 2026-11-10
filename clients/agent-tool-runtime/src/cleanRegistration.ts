/** Durable Runtime pairing state. OTP is never persisted; private state uses DPAPI. */
import { randomBytes, randomUUID } from 'node:crypto'
import { existsSync, lstatSync, readFileSync, renameSync, unlinkSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { acquireHostLease, ManagementError } from '@aid/local-tool-host-core'
import { ApiClient, type CleanPairRequest, type PairingPreparation, type PairingProof, type PairingStatus } from './apiClient.js'
import { dpapiProtect, dpapiUnprotect } from './dpapi.js'
import { atomicWrite } from './pairingStore.js'
import { PLATFORM, RUNTIME_VERSION, machineFingerprint } from './config.js'
import { validServer } from './connectionDiagnosis.js'

export interface CleanRegistration {
  version: 1 | 2; server: string; request: Omit<CleanPairRequest, 'code'>
  phase: 'requested' | 'prepared' | 'confirming' | 'paired' | 'installed'; device_id: string | null; home: string | null
  preparation?: PairingPreparation
}
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const file = (metadata: string) => join(metadata, 'clean-registration.bin')
export async function readCleanRegistration(metadata: string): Promise<CleanRegistration | null> {
  if (!existsSync(file(metadata))) return null
  try {
    const stat = lstatSync(file(metadata))
    if (!stat.isFile() || stat.isSymbolicLink() || stat.size > 65536) throw new Error()
    const r = JSON.parse(await dpapiUnprotect(readFileSync(file(metadata), 'utf8'))) as CleanRegistration
    const keys = Object.keys(r ?? {}).sort().join(',')
    if (!r || (r.version === 1
      ? keys !== 'device_id,home,phase,request,server,version' || ['prepared', 'confirming'].includes(r.phase)
      : r.version !== 2 || keys !== 'device_id,home,phase,preparation,request,server,version' || r.phase === 'requested'
        || !r.preparation || Object.keys(r.preparation).sort().join(',') !== 'preparation_id,preparation_token'
        || !uuid.test(r.preparation.preparation_id) || !/^[0-9a-f]{64}$/.test(r.preparation.preparation_token))
      || !validServer(r.server) || !['requested', 'prepared', 'confirming', 'paired', 'installed'].includes(r.phase)
      || !r.request || r.request.pairing_protocol !== 3 || !uuid.test(r.request.registration_id)
      || !/^[0-9a-f]{64}$/.test(r.request.registration_token) || typeof r.request.name !== 'string' || !r.request.name
      || r.request.name.length > 100 || typeof r.request.platform !== 'string' || typeof r.request.runtime_version !== 'string'
      || !r.request.capabilities || typeof r.request.capabilities !== 'object' || Array.isArray(r.request.capabilities)
      || typeof r.request.machine_fingerprint !== 'string' || !/^[0-9a-f]{64}$/.test(r.request.machine_fingerprint)
      || r.request.previous_device_token !== undefined && !/^[0-9a-f]{64}$/.test(r.request.previous_device_token)
      || Object.keys(r.request).some(key => !['pairing_protocol', 'registration_id', 'registration_token', 'name', 'platform', 'runtime_version', 'capabilities', 'machine_fingerprint', 'previous_device_token'].includes(key))
      || Object.hasOwn(r.request, 'code') || ![null, 'string'].includes(r.home === null ? null : typeof r.home)
      || r.home !== null && resolve(r.home) !== r.home
      || ['requested', 'prepared', 'confirming'].includes(r.phase) && r.device_id !== null || r.phase === 'installed' && r.home === null
      || ['prepared', 'confirming'].includes(r.phase) && r.home !== null
      || ['paired', 'installed'].includes(r.phase) && (!r.device_id || !uuid.test(r.device_id))) throw new Error()
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
/** Remove legacy evidence from the active entry without reading it as a new submission. */
export function retireCleanRegistration(metadata: string): void {
  if (existsSync(file(metadata))) {
    const stat = lstatSync(file(metadata))
    if (!stat.isFile() || stat.isSymbolicLink()) throw new ManagementError(11, '注册文件路径异常，请检查当前用户目录权限')
    renameSync(file(metadata), join(metadata, `previous-registration-${randomUUID()}.bin`))
  }
}
export async function beginCleanRegistration(metadata: string, input: { server: string; device_name: string }, previousHomes: string[]): Promise<CleanRegistration> {
  if (!validServer(input.server) || !input.device_name.trim() || input.device_name.length > 100) throw new ManagementError(3, '请填写有效的企业服务地址和设备名称')
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
  return { version: 1, server: input.server.replace(/\/+$/, ''), phase: 'requested', device_id: null, home: null,
    request: { pairing_protocol: 3, registration_id: randomUUID(), registration_token: randomBytes(32).toString('hex'), name: input.device_name,
      platform: PLATFORM, runtime_version: RUNTIME_VERSION, capabilities: { providers: [] }, machine_fingerprint: machineFingerprint(),
      ...(previous ? { previous_device_token: previous } : {}) } }
}
export async function pairCleanRegistration(metadata: string, r: CleanRegistration, code: string): Promise<void> {
  if (r.phase !== 'requested' || !code) throw new ManagementError(3, '请使用有效配对码明确发起配对')
  const response = await new ApiClient(r.server).pairClean({ ...r.request, code })
  // Only a validated server success becomes a protected local transaction.
  r.device_id = response.device_id; r.phase = 'paired'
  await saveCleanRegistration(metadata, r)
}
type PairingOutcome = 'started' | 'succeeded' | 'failed' | PairingStatus['state']
type ObservePairing = (outcome: PairingOutcome, error?: unknown) => void
async function observeRequest<T>(observe: ObservePairing | undefined, request: () => Promise<T>, outcome?: (value: T) => PairingOutcome): Promise<T> {
  observe?.('started')
  try {
    const result = await request()
    observe?.(outcome?.(result) ?? 'succeeded')
    return result
  } catch (error) { observe?.('failed', error); throw error }
}
export async function prepareCleanRegistration(metadata: string, r: CleanRegistration, code: string, observe?: ObservePairing): Promise<void> {
  if (r.phase !== 'requested' || !code) throw new ManagementError(3, '请使用有效配对码明确发起配对')
  const preparation = await observeRequest(observe, () => new ApiClient(r.server).preparePairing(r.request, code))
  const request = { ...r.request }
  delete request.previous_device_token // The old identity proof is only used for prepare.
  const prepared: CleanRegistration = { ...r, request, version: 2, phase: 'prepared', preparation }
  await saveCleanRegistration(metadata, prepared)
  Object.assign(r, prepared)
}
function pairingProof(r: CleanRegistration): PairingProof {
  if (!['prepared', 'confirming'].includes(r.phase) || !r.preparation) throw new ManagementError(11, '本机没有可核对的配对候选')
  return { ...r.preparation, registration_id: r.request.registration_id, registration_token: r.request.registration_token }
}
async function saveConfirmedRegistration(metadata: string, r: CleanRegistration, deviceId: string): Promise<void> {
  r.device_id = deviceId; r.phase = 'paired'
  await saveCleanRegistration(metadata, r)
}
export async function confirmCleanRegistration(metadata: string, r: CleanRegistration, observe?: ObservePairing): Promise<void> {
  const proof = pairingProof(r)
  const saved = await readCleanRegistration(metadata)
  if (saved?.phase !== 'prepared' || saved.server !== r.server || JSON.stringify(pairingProof(saved)) !== JSON.stringify(proof)) {
    throw new ManagementError(11, '配对候选未安全保存，本次未发送确认请求')
  }
  await saveCleanRegistration(metadata, { ...r, phase: 'confirming' })
  r.phase = 'confirming'
  const deviceId = await observeRequest(observe, () => new ApiClient(saved.server).confirmPairing(proof))
  await saveConfirmedRegistration(metadata, r, deviceId)
}
export async function checkCleanRegistration(metadata: string, r: CleanRegistration, observe?: ObservePairing): Promise<PairingStatus['state']> {
  const proof = pairingProof(r)
  const status = await observeRequest(observe, () => new ApiClient(r.server).readPairingStatus(proof), result => result.state)
  if (status.state === 'committed') await saveConfirmedRegistration(metadata, r, status.device_id)
  return status.state
}
export function discardPreparedCleanRegistration(metadata: string): void {
  unlinkSync(file(metadata))
}
export async function installCleanRegistration(metadata: string, r: CleanRegistration, home: string): Promise<void> {
  if (!r.device_id || !['paired', 'installed'].includes(r.phase)) throw new ManagementError(11, '服务器尚未确认连接注册')
  r.home = resolve(home)
  await saveCleanRegistration(metadata, r)
  // Target is a new generation allocated by Main, never the old execution home.
  atomicWrite(join(home, 'credentials.bin'), await dpapiProtect(r.request.registration_token))
  atomicWrite(join(home, 'config.json'), JSON.stringify({ server: r.server, device_id: r.device_id, name: r.request.name }, null, 2))
  r.phase = 'installed'
  await saveCleanRegistration(metadata, r)
}
export async function matchesInstalledCleanRegistration(r: CleanRegistration, home: string): Promise<boolean> {
  if (!['paired', 'installed'].includes(r.phase)) return false
  const configFile = join(home, 'config.json')
  try {
    const stat = lstatSync(configFile)
    if (!stat.isFile() || stat.isSymbolicLink() || stat.size > 65536) return false
    const config = JSON.parse(readFileSync(configFile, 'utf8').replace(/^\uFEFF/, ''))
    return config.device_id === r.device_id && await previousToken(home, r.server) === r.request.registration_token
  } catch { return false }
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
