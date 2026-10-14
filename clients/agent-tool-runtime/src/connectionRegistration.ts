/** Trusted composition only. A pending identity never owns a Provider or claims work. */
import { randomUUID } from 'node:crypto'
import { existsSync, lstatSync, mkdirSync, readFileSync, realpathSync, renameSync } from 'node:fs'
import { dirname, isAbsolute, join, resolve } from 'node:path'
import { acquireHostLease, ManagementError } from '@aid/local-tool-host-core'
import { ApiClient, ApiError, DeviceRevokedError } from './apiClient.js'
import { PLATFORM, RUNTIME_VERSION, deviceCapabilities, machineFingerprint, type RuntimeConfig } from './config.js'
import { dpapiProtect, dpapiUnprotect } from './dpapi.js'
import { atomicWrite } from './pairingStore.js'
import { diagnosis, connectionFailure, identityDigest, validServer, type ConnectionDiagnosis } from './connectionDiagnosis.js'
import { archiveForRegistration } from './connectionRecoveryStore.js'

interface Registration {
  version: 1; home: string; id: string; archive_id: string; phase: 'requested' | 'paired' | 'activating' | 'active'
  config: RuntimeConfig | null; token: string | null; replacement_pending: boolean
  installed_digest: string | null; action: ConnectionDiagnosis | null
}
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/
const same = (a: string, b: string) => process.platform === 'win32' ? resolve(a).toLowerCase() === resolve(b).toLowerCase() : resolve(a) === resolve(b)
function directory(home: string): string {
  if (!isAbsolute(home) || dirname(home) === home || !same(realpathSync(dirname(home)), dirname(home))) throw new ManagementError(11, '新注册目录无效，请修复当前用户配置目录')
  const root = home + '.connection-registration'
  if (existsSync(root) && (lstatSync(root).isSymbolicLink() || !lstatSync(root).isDirectory() || !same(realpathSync(root), root))) throw new ManagementError(11, '新注册目录异常，请修复目录权限后重试')
  return root
}
function stateFile(home: string) { return join(directory(home), 'state.bin') }
async function load(home: string): Promise<Registration | null> {
  const file = stateFile(home)
  if (!existsSync(file)) return null
  try {
    const metadata = lstatSync(file)
    if (!metadata.isFile() || metadata.isSymbolicLink() || metadata.size > 65536) throw new Error()
    const r = JSON.parse(await dpapiUnprotect(readFileSync(file, 'utf8'))) as Registration
    if (!r || Object.keys(r).sort().join(',') !== 'action,archive_id,config,home,id,installed_digest,phase,replacement_pending,token,version'
      || r.version !== 1 || typeof r.home !== 'string' || !same(r.home, home) || !uuid.test(r.id) || !uuid.test(r.archive_id)
      || !['requested', 'paired', 'activating', 'active'].includes(r.phase) || typeof r.replacement_pending !== 'boolean'
      || !(r.installed_digest === null || typeof r.installed_digest === 'string' && /^[0-9a-f]{64}$/.test(r.installed_digest))) throw new Error()
    if (r.phase === 'requested') { if (r.config !== null || r.token !== null) throw new Error() }
    else if (!r.config || Object.keys(r.config).sort().join(',') !== 'device_id,name,server' || !validServer(r.config.server)
      || !uuid.test(r.config.device_id) || typeof r.config.name !== 'string' || !r.config.name || r.config.name.length > 100
      || typeof r.token !== 'string' || !r.token || r.token.length > 4096) throw new Error()
    if (r.phase === 'active' && !r.installed_digest) throw new Error()
    if (r.action !== null && (!r.action || r.action.version !== 1 || typeof r.action.category !== 'string' || typeof r.action.summary !== 'string'
      || !Array.isArray(r.action.steps) || r.action.steps.some((s: unknown) => typeof s !== 'string') || typeof r.action.requires_reboot !== 'boolean')) throw new Error()
    return r
  } catch { throw new ManagementError(11, '新注册记录不可读取，未自动发送配对请求；可用新配对码重新注册') }
}
async function save(home: string, r: Registration) { atomicWrite(stateFile(home), await dpapiProtect(JSON.stringify(r))) }
export async function withConnectionRegistration<T>(home: string, action: () => Promise<T>): Promise<T> {
  const root = directory(home); mkdirSync(root, { recursive: true })
  const lease = await acquireHostLease(root)
  if (!lease) throw new ManagementError(11, '另一客户端正在配置新连接，请等待或退出另一实例')
  try { return await action() } finally { await lease.release() }
}
export async function registrationBlocksExecution(home: string): Promise<boolean> {
  const r = await load(home)
  return !!r && r.phase !== 'active'
}
export async function registrationStatus(home: string): Promise<ConnectionDiagnosis | null> {
  let r: Registration | null
  try { r = await load(home) }
  catch { return diagnosis('REGISTRATION_UNCONFIRMED', '新注册记录不可读取，未自动发送配对请求', ['使用当前Windows用户，确认目录权限正常。', '使用网页新生成的配对码重新注册；原记录会保留。'], { can_reconfigure: true }) }
  if (!r) return null
  if (r.phase === 'requested') return diagnosis('REGISTRATION_UNCONFIRMED', '上次配对结果尚未确认，未自动重复请求', ['在网页生成新的配对码，明确提交一次重新配对。', '服务器可能已登记设备；新连接成功后可在网页清理无用设备。'], { can_reconfigure: true })
  if (r.phase === 'active') {
    if (r.action?.category === 'REPLACEMENT_TARGET_UNAVAILABLE') return { ...r.action, service_verified: false }
    return diagnosis('REGISTRATION_CLEANUP_PENDING', '新连接配置已保存，旧设备注销尚未确认', ['点击完成连接配置，重新验证连接并重试指定旧设备注销。', '不会重新配对或覆盖配置；可正常退出。'])
  }
  return diagnosis('REGISTRATION_PENDING', r.action ? '新设备凭据已保存，旧执行资源尚需处理' : '新设备凭据已保存，请完成连接配置',
    [...(r.action?.steps ?? ['点击完成连接配置，核验旧执行已停止后启用新连接。']), '配对码已提交，无需再次输入；新设备尚未接管旧执行资源。'].slice(0, 12), { requires_reboot: r.action?.requires_reboot ?? false })
}
/** Caller holds the registration lease. No old home writes, execution inspection, or claim. */
export async function registerConnection(home: string, input: { server: string; device_name: string; pairing_code: string }): Promise<ConnectionDiagnosis> {
  let previous: Registration | null = null
  try { previous = await load(home) } catch { /* Explicit new code can supersede unreadable local registration; retain bytes. */ }
  if (previous && previous.phase !== 'requested' && !(previous.phase === 'active' && (identityDigest(home) !== previous.installed_digest || previous.action?.category === 'REPLACEMENT_TARGET_UNAVAILABLE'))) {
    try {
      await new ApiClient(previous.config!.server, previous.token!).checkConnection()
      return (await registrationStatus(home))!
    } catch (error) {
      if (!(error instanceof DeviceRevokedError || error instanceof ApiError && error.status === 401)) return await verifyRegisteredConnection(home)
      // A confirmed revoked staged identity can be explicitly superseded. An
      // interrupted archive retains its protected lineage, not a second rename.
    }
  }
  const file = stateFile(home)
  if (existsSync(file)) {
    if (lstatSync(file).isSymbolicLink() || !lstatSync(file).isFile()) throw new ManagementError(11, '新注册记录路径异常')
    // Keep the authoritative gate until the replacement requested record is
    // durably published. Protection/disk failures must not reopen execution.
    atomicWrite(join(directory(home), `${previous?.id ?? randomUUID()}.unconfirmed.bin`), readFileSync(file, 'utf8'))
  }
  // Both protection and durable storage are checked before consuming a ticket.
  const id = randomUUID()
  const archiveId = previous && previous.phase !== 'active' && (previous.phase === 'activating' || previous.archive_id !== previous.id) ? previous.archive_id : id
  const r: Registration = { version: 1, home: resolve(home), id, archive_id: archiveId,
    phase: 'requested', config: null, token: null, replacement_pending: false, installed_digest: null, action: null }
  await save(home, r)
  if ((await load(home))?.id !== r.id) throw new ManagementError(11, '新注册记录未通过保存检查')
  try {
    const response = await new ApiClient(input.server).pair({ code: input.pairing_code, name: input.device_name,
      platform: PLATFORM, runtime_version: RUNTIME_VERSION, capabilities: deviceCapabilities(null), machine_fingerprint: machineFingerprint(), pairing_protocol: 2 })
    r.config = { server: input.server, device_id: response.device_id, name: input.device_name }
    r.token = response.device_token; r.replacement_pending = response.replacement_pending === true; r.phase = 'paired'
    await save(home, r)
    return await verifyRegisteredConnection(home)
  } catch (error) {
    if (r.phase === 'paired') return diagnosis('REGISTRATION_UNCONFIRMED', '服务器可能已登记新设备，本机保存或验证未完成', ['点击检查连接核对已经保存的注册进度。', '若显示未保存，再使用网页新生成的配对码；未自动重复请求。'], { can_reconfigure: true })
    if (error instanceof ApiError && error.code === 'PAIRING_PROTOCOL_REQUIRED') return diagnosis('REGISTRATION_UNCONFIRMED', '此替换码需要支持设备替换的客户端', ['安装最新客户端后使用有效配对码。'], { can_reconfigure: true })
    if (error instanceof ApiError && error.code === 'REPLACEMENT_TARGET_NOT_FOUND') return diagnosis('REGISTRATION_UNCONFIRMED', '配对码指定的旧设备不存在或归属已变化', ['在已登录的网页刷新设备列表，重新选择更换目标并生成配对码。', '也可生成普通配对码建立新连接，旧设备随后在网页处理。'], { can_reconfigure: true })
    const failure = connectionFailure(error, true)
    return { ...failure, summary: failure.summary + '；未自动重复配对请求', can_reconfigure: true }
  }
}
export async function verifyRegisteredConnection(home: string): Promise<ConnectionDiagnosis> {
  let r: Registration | null
  try { r = await load(home) } catch { return (await registrationStatus(home))! }
  if (!r || !r.config || !r.token) return (await registrationStatus(home)) ?? diagnosis('CONFIG_MISSING', '尚未建立新连接', ['使用新的配对码重新配置连接。'], { can_reconfigure: true })
  try { await new ApiClient(r.config.server, r.token).checkConnection() }
  catch (error) {
    const failure = connectionFailure(error)
    return { ...failure, can_reconfigure: failure.category === 'CREDENTIALS_REVOKED', requires_reboot: r.action?.requires_reboot ?? false,
      steps: [...failure.steps, ...(r.action?.steps ?? []), failure.category === 'CREDENTIALS_REVOKED' ? '当前新凭据已失效，可明确提交新码重新注册；旧记录保留。' : '新凭据已保存，不要重复配对；连接恢复后继续完成配置。'].slice(0, 12) }
  }
  return { ...(await registrationStatus(home))!, service_verified: true }
}
export async function rememberRegistrationAction(home: string, action: ConnectionDiagnosis): Promise<void> {
  const r = await load(home)
  if (r && r.phase !== 'requested' && r.phase !== 'active') { r.action = action; await save(home, r) }
}
export async function registrationAlreadyActive(home: string): Promise<boolean> { return (await load(home))?.phase === 'active' }
/** Holds both registration lease and original home lease. Resume is tied to protected epoch. */
export async function installRegisteredConnection(home: string, node: string): Promise<void> {
  const r = await load(home)
  if (!r?.config || !r.token) throw new ManagementError(11, '新注册尚未保存，请先检查连接')
  if (r.phase === 'active') {
    if (identityDigest(home) !== r.installed_digest) throw new ManagementError(11, '活动配置已变化，未注销旧设备')
    return
  }
  r.phase = 'activating'; await save(home, r)
  await archiveForRegistration(home, node, r.archive_id)
  atomicWrite(join(home, 'credentials.bin'), await dpapiProtect(r.token))
  atomicWrite(join(home, 'config.json'), JSON.stringify(r.config, null, 2))
  r.installed_digest = identityDigest(home)
  if (!r.installed_digest) throw new ManagementError(11, '新连接资料尚未安全保存')
  r.phase = 'active'; r.action = null; await save(home, r)
}
export async function finishRegisteredConnection(home: string): Promise<ConnectionDiagnosis> {
  const r = await load(home)
  if (!r || r.phase !== 'active' || !r.config || !r.token || identityDigest(home) !== r.installed_digest) throw new ManagementError(11, '新连接尚未安装或活动资料发生变化，未注销旧设备')
  const api = new ApiClient(r.config.server, r.token)
  let selected: boolean
  try { selected = (await api.checkConnection()).selected }
  catch (error) { return { ...connectionFailure(error), steps: ['新连接已保存，恢复网络后点击完成连接配置。', '旧设备注销未执行；不会重新配对。'] } }
  if (r.replacement_pending) {
    try { await api.completeReplacement() }
    catch (error) {
      if (error instanceof ApiError && error.code === 'REPLACEMENT_ALREADY_COMPLETED') {
        renameSync(stateFile(home), join(directory(home), `${r.id}.complete.bin`))
        return diagnosis('DEVICE_NOT_SELECTED', '新设备连接已验证，旧设备已由另一次替换注销', ['在网页选定要使用的新设备；不会撤销另一台新设备。', '旧任务记录保留，不会再次配对。'], { service_verified: true, can_preserve: true, can_reconfigure: true })
      }
      if (error instanceof ApiError && error.code === 'REPLACEMENT_TARGET_NOT_FOUND') {
        r.action = diagnosis('REPLACEMENT_TARGET_UNAVAILABLE', '新设备连接已验证，旧设备替换关系无法核验', ['在已登录网页刷新设备列表，确认旧设备是否仍属于当前账号；无法确认时请管理员处理。', '新连接可以继续使用，不会自动注销其他设备或反复提交注销。'], { service_verified: true, can_reconfigure: true })
        await save(home, r)
        return r.action
      }
      return diagnosis('REGISTRATION_CLEANUP_PENDING', '新设备连接已验证，旧设备注销尚未确认',
        error instanceof ApiError && error.status === 404 ? ['服务器尚未提供设备替换接口，请管理员更新服务端后再完成连接配置。', '当前新连接保留；不会再次配对或注销其他设备。']
          : ['检查服务器连接与权限，恢复后点击完成连接配置重试注销；不会再次配对。', '当前新连接保留；可以正常退出或使用已配置的新设备。'], { service_verified: true })
    }
  }
  renameSync(stateFile(home), join(directory(home), `${r.id}.complete.bin`))
  return diagnosis(selected ? 'VERIFIED' : 'DEVICE_NOT_SELECTED', '新设备连接已验证' + (r.replacement_pending ? '，指定旧设备已注销' : ''),
    [...(!selected ? ['在网页本地工具中选定新设备。'] : []), '安装BOSS插件并完成登录后，点击启动执行。旧任务记录保留。'], { service_verified: true, can_preserve: true, can_reconfigure: true })
}
