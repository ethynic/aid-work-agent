/** User-confirmed recovery journal. Call only while holding this home's OS lease. */
import { createHash, randomUUID } from 'node:crypto'
import { existsSync, lstatSync, mkdirSync, readFileSync, readlinkSync, readdirSync, realpathSync, renameSync } from 'node:fs'
import { dirname, isAbsolute, join, resolve } from 'node:path'
import { ManagementError } from '@aid/local-tool-host-core'
import { atomicWrite } from './pairingStore.js'
import { dpapiProtect, dpapiUnprotect } from './dpapi.js'
import { checkConnectionRecoveryResources, recordFreshConnectionEpoch } from './connectionRecoveryResources.js'
import { diagnoseConnection, identityDigest } from './connectionDiagnosis.js'

type Mode = 'preserve' | 'new_device'
interface RecoveryIntent {
  version: 1; home: string; epoch: string; mode: Mode; stage: 'prepared' | 'archived' | 'complete'
  identity_digest: string | null; controls: string[]; control_hashes: Record<string, string>; retired_keys: string[]; anchor_sha256: string
}
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/
const hash = (bytes: string | Buffer) => createHash('sha256').update(bytes).digest('hex')
const controlNames = ['management-secret.bin', 'management-operations.json', 'management-retired-request-keys.bin']
const same = (a: string, b: string) => process.platform === 'win32' ? resolve(a).toLowerCase() === resolve(b).toLowerCase() : resolve(a) === resolve(b)
function present(file: string) {
  try { lstatSync(file); return true } catch (error) { if ((error as NodeJS.ErrnoException).code === 'ENOENT') return false; throw error }
}
function fingerprint(file: string): string {
  const stat = lstatSync(file)
  if (stat.isSymbolicLink()) return 'link:' + hash(readlinkSync(file))
  if (stat.isDirectory()) return 'directory:' + String(stat.ino)
  if (!stat.isFile() || stat.size > 16 * 1024 * 1024) throw new ManagementError(11, '管理记录过大或异常，请选择重新配置并保留原资料')
  return 'file:' + hash(readFileSync(file))
}
function paths(home: string) {
  if (!isAbsolute(home) || dirname(resolve(home)) === resolve(home)) throw new ManagementError(11, '连接恢复目录无效，请重新安装官方客户端')
  const parent = realpathSync(dirname(home))
  if (!same(resolve(dirname(home)), parent)) throw new ManagementError(11, '配置父目录是链接，需管理员恢复标准用户目录')
  if (existsSync(home) && (lstatSync(home).isSymbolicLink() || !lstatSync(home).isDirectory() || !same(realpathSync(home), home))) throw new ManagementError(11, '配置目录是链接或异常文件，需管理员恢复标准目录')
  const control = home + '.connection-recovery'
  const history = home + '.connection-history'
  for (const directory of [control, history]) if (existsSync(directory) && (lstatSync(directory).isSymbolicLink() || !lstatSync(directory).isDirectory())) throw new ManagementError(11, '连接恢复记录目录异常，请管理员修复目录访问后重试')
  return { control, history, state: join(control, 'state.bin') }
}
function archiveDirectory(home: string, intent: RecoveryIntent): string {
  const directory = join(paths(home).history, intent.epoch)
  if (existsSync(directory) && (lstatSync(directory).isSymbolicLink() || !lstatSync(directory).isDirectory())) throw new ManagementError(11, '连接历史归档目录异常，原资料未清空')
  return directory
}
async function load(home: string): Promise<RecoveryIntent | null> {
  const file = paths(home).state
  if (!present(file)) return null
  if (lstatSync(file).isSymbolicLink() || !lstatSync(file).isFile() || lstatSync(file).size > 4 * 1024 * 1024) throw new ManagementError(11, '连接恢复记录不可读取，请选择重新配置并保留旧资料')
  try {
    const value = JSON.parse(await dpapiUnprotect(readFileSync(file, 'utf8'))) as RecoveryIntent
    if (Object.keys(value).sort().join(',') !== 'anchor_sha256,control_hashes,controls,epoch,home,identity_digest,mode,retired_keys,stage,version'
      || value.version !== 1 || !same(value.home, home) || !uuid.test(value.epoch) || !['preserve', 'new_device'].includes(value.mode)
      || !['prepared', 'archived', 'complete'].includes(value.stage) || !/^[0-9a-f]{64}$/.test(value.anchor_sha256)
      || value.identity_digest !== null && !/^[0-9a-f]{64}$/.test(value.identity_digest)
      || value.mode === 'preserve' && value.identity_digest === null
      || !Array.isArray(value.controls) || value.controls.length > controlNames.length || value.controls.some(name => !controlNames.includes(name))
      || new Set(value.controls).size !== value.controls.length || !Array.isArray(value.retired_keys) || value.retired_keys.length > 100000
      || !value.control_hashes || typeof value.control_hashes !== 'object' || Array.isArray(value.control_hashes)
      || Object.keys(value.control_hashes).sort().join(',') !== [...value.controls].sort().join(',')
      || Object.values(value.control_hashes).some(item => typeof item !== 'string' || !/^(?:file:|link:)[0-9a-f]{64}$|^directory:[0-9]+$/.test(item))
      || value.retired_keys.some(key => typeof key !== 'string' || !/^[0-9a-f]{64}$/.test(key))) throw new Error('invalid')
    return value
  } catch { throw new ManagementError(11, '连接恢复记录无法验证，请选择重新配置；旧记录会继续保留') }
}
async function save(home: string, intent: RecoveryIntent) {
  const p = paths(home); mkdirSync(p.control, { recursive: true })
  atomicWrite(p.state, await dpapiProtect(JSON.stringify(intent)))
}
async function retiredKeys(home: string, mode: Mode, inherited: string[] = []): Promise<string[]> {
  const result = new Set<string>(inherited)
  try {
    const file = join(home, 'management-operations.json')
    if (existsSync(file) && lstatSync(file).isFile() && !lstatSync(file).isSymbolicLink()) {
      if (lstatSync(file).size > 16 * 1024 * 1024) throw new ManagementError(11, '旧管理记录超出安全恢复容量，请管理员归档处理后重试')
      const value = JSON.parse(readFileSync(file, 'utf8').replace(/^\uFEFF/, ''))
      if (value && typeof value === 'object' && !Array.isArray(value)) for (const key of Object.keys(value)) result.add(hash(key))
    }
  } catch (error) { if (error instanceof ManagementError) throw error /* Invalid old operations remain archived and are never replayed. */ }
  try {
    const previous = join(home, 'management-retired-request-keys.bin')
    if (existsSync(previous)) {
      if (!lstatSync(previous).isFile() || lstatSync(previous).isSymbolicLink() || lstatSync(previous).size > 4 * 1024 * 1024) throw new Error('invalid')
      const value = JSON.parse(await dpapiUnprotect(readFileSync(previous, 'utf8')))
      if (value.version !== 1 || !Array.isArray(value.keys) || value.keys.some((key: unknown) => typeof key !== 'string' || !/^[0-9a-f]{64}$/.test(key))) throw new Error('invalid')
      for (const key of value.keys) result.add(key)
    }
  } catch { if (mode === 'preserve') throw new ManagementError(11, '旧操作保护记录损坏，不能保留原管理身份；请重新配置并保留旧资料') }
  if (Buffer.byteLength(JSON.stringify([...result]), 'utf8') > 2800000) throw new ManagementError(11, '旧操作保护记录超出安全恢复容量，请管理员归档处理后重试')
  return [...result]
}
/** Preflight is repeated after actual old Host exit and acquisition of the Main lease. */
export async function beginConnectionRecovery(home: string, node: string, mode: Mode, registrationId?: string): Promise<void> {
  if (typeof mode !== 'string' || !['preserve', 'new_device'].includes(mode)) throw new ManagementError(1, '连接恢复模式无效，未修改任何资料')
  const p = paths(home)
  let inherited: string[] = []
  try { inherited = (await load(home))?.retired_keys ?? [] }
  catch { if (mode === 'preserve') throw new ManagementError(11, '原恢复记录损坏，不能保留原管理身份；请重新配置并保留旧资料') }
  const resources = await checkConnectionRecoveryResources(home, node, { prepareReboot: true })
  if (!resources.safe) throw new ManagementError(11, resources.needs_reboot ? '旧执行资源尚需确认，请按页面登记并正常重启Windows后继续' : '执行资源或系统保护尚未恢复，请按连接检查步骤处理')
  // Writing the archive anchor makes an empty home nonempty. Preserve the
  // proved-empty scope before that write so the second check remains truthful.
  if (resources.category === 'CONNECTION_RESOURCE_NEW_HOME') await recordFreshConnectionEpoch(home, node)
  const identity = mode === 'preserve' ? await diagnoseConnection(home) : null
  if (mode === 'preserve' && !identity?.can_preserve) throw new ManagementError(11, '原身份尚未通过服务器验证，请选择重新配置或先恢复原服务')
  const retired = await retiredKeys(home, mode, inherited)
  mkdirSync(p.control, { recursive: true }); mkdirSync(p.history, { recursive: true })
  if (existsSync(p.state)) {
    // Superseding is explicit. Preserve even an unreadable earlier recovery record.
    if (lstatSync(p.state).isSymbolicLink() || !lstatSync(p.state).isFile()) throw new ManagementError(11, '恢复记录路径异常，请管理员修复后重试')
    // Keep the authoritative gate until atomic replacement. A failed DPAPI or
    // disk write must never turn an incomplete transaction into normal startup.
    atomicWrite(join(p.history, `previous-${randomUUID()}.bin`), readFileSync(p.state, 'utf8'))
  }
  if (registrationId !== undefined && !uuid.test(registrationId)) throw new ManagementError(11, '新注册事务标识无效')
  const epoch = registrationId ?? randomUUID()
  const anchor = await dpapiProtect(JSON.stringify({ version: 1, home, epoch, mode }))
  atomicWrite(join(home, '.connection-recovery-anchor.bin'), anchor)
  const controls = mode === 'preserve' ? controlNames.filter(name => existsSync(join(home, name))) : []
  const intent: RecoveryIntent = { version: 1, home, epoch, mode, stage: 'prepared', identity_digest: mode === 'preserve' ? identityDigest(home) : null,
    controls, control_hashes: Object.fromEntries(controls.map(name => [name, fingerprint(join(home, name))])), retired_keys: retired, anchor_sha256: hash(anchor) }
  await save(home, intent)
  await resumeConnectionRecovery(home, node)
}

/** A registration always resumes its own archive; never supersedes another unfinished recovery. */
export async function archiveForRegistration(home: string, node: string, registrationId: string): Promise<void> {
  if (!uuid.test(registrationId)) throw new ManagementError(11, '新注册事务标识无效')
  const intent = await load(home)
  if (intent?.epoch === registrationId) {
    if (intent.mode !== 'new_device') throw new ManagementError(11, '新注册与恢复记录不匹配')
    await resumeConnectionRecovery(home, node)
    if (!await hasArchivedConnectionRecovery(home)) throw new ManagementError(11, '新注册归档尚未确认')
    return
  }
  if (intent && intent.stage !== 'complete') throw new ManagementError(11, '另一恢复事务尚未完成，请先完成原恢复')
  await beginConnectionRecovery(home, node, 'new_device', registrationId)
}
/** Common CLI and managed Host startup gate; never consumes pairing codes. */
export async function resumeConnectionRecovery(home: string, node: string): Promise<void> {
  const intent = await load(home)
  if (!intent) return
  if (intent.stage === 'complete') {
    const file = join(home, 'management-retired-request-keys.bin')
    if (!present(file)) {
      atomicWrite(file, await dpapiProtect(JSON.stringify({ version: 1, keys: intent.retired_keys })))
    } else {
      try {
        const metadata = lstatSync(file)
        if (metadata.isSymbolicLink() || !metadata.isFile() || metadata.size > 4 * 1024 * 1024) throw new Error('invalid')
        const value = JSON.parse(await dpapiUnprotect(readFileSync(file, 'utf8')))
        if (!value || typeof value !== 'object' || Object.keys(value).sort().join(',') !== 'keys,version' || value.version !== 1 || !Array.isArray(value.keys) || value.keys.length > 100000 || value.keys.some((key: unknown) => typeof key !== 'string' || !/^[0-9a-f]{64}$/.test(key))) throw new Error('invalid')
        const keys = new Set<string>(value.keys)
        if (intent.retired_keys.some(key => !keys.has(key))) throw new Error('incomplete')
      } catch { throw new ManagementError(11, '已归档操作保护记录缺失或损坏，请使用连接恢复重新配置并保留旧资料') }
    }
    return
  }
  const p = paths(home); const archive = archiveDirectory(home, intent)
  const anchorName = '.connection-recovery-anchor.bin'
  const matchesAnchor = (directory: string) => {
    const file = join(directory, anchorName)
    return existsSync(file) && !lstatSync(file).isSymbolicLink() && lstatSync(file).isFile() && lstatSync(file).size < 65536 && hash(readFileSync(file)) === intent.anchor_sha256
  }
  if (intent.mode === 'new_device') {
    if (!existsSync(archive)) {
      if (!matchesAnchor(home)) throw new ManagementError(11, '旧资料发生变化，请重新检查连接后重新配置')
      const resources = await checkConnectionRecoveryResources(home, node, { prepareReboot: true })
      if (!resources.safe) throw new ManagementError(11, '恢复需正常重启Windows后继续，原资料仍保留')
      renameSync(home, archive)
    } else if (!matchesAnchor(archive)) throw new ManagementError(11, '旧资料归档未能验证，请重新配置并保留原记录')
    if (intent.stage === 'prepared') { intent.stage = 'archived'; await save(home, intent) }
    if (existsSync(home) && readdirSync(home).length > 0 && !existsSync(join(home, 'connection-recovery-epoch.bin'))) throw new ManagementError(4, '恢复目录被另一实例使用，请先退出旧实例后继续')
    mkdirSync(home, { recursive: true })
    await recordFreshConnectionEpoch(home, node)
  } else {
    if (identityDigest(home) !== intent.identity_digest || !matchesAnchor(home)) throw new ManagementError(11, '原身份资料发生变化，保留原记录，请重新检查连接')
    const resources = await checkConnectionRecoveryResources(home, node, { prepareReboot: true })
    if (!resources.safe) throw new ManagementError(11, '旧执行资源仍待确认，请按页面正常重启Windows')
    mkdirSync(archive, { recursive: true })
    for (const name of intent.controls) {
      const source = join(home, name); const target = join(archive, name)
      if (existsSync(source) && existsSync(target)) throw new ManagementError(11, '管理记录归档出现冲突，请重新配置并保留旧资料')
      if (existsSync(source)) {
        if (fingerprint(source) !== intent.control_hashes[name]) throw new ManagementError(11, '原管理记录发生变化，请重新检查后配置连接')
        renameSync(source, target)
      }
      else if (!existsSync(target)) throw new ManagementError(11, '管理记录归档不完整，请重新配置并保留旧资料')
      if (fingerprint(target) !== intent.control_hashes[name]) throw new ManagementError(11, '归档管理记录未通过验证，请重新配置并保留旧资料')
    }
    intent.stage = 'archived'; await save(home, intent)
  }
  atomicWrite(join(home, 'management-retired-request-keys.bin'), await dpapiProtect(JSON.stringify({ version: 1, keys: intent.retired_keys })))
  intent.stage = 'complete'; await save(home, intent)
}

/** Same marker and ledger stay authoritative throughout management-only repair.
 * Core archives the marker only after this independently verified resource gate.
 */
export async function recoverManagementRepairExecution(home: string, node: string): Promise<boolean> {
  const intent = await load(home)
  if (!intent || intent.stage !== 'complete' || intent.mode !== 'preserve' || identityDigest(home) !== intent.identity_digest) return false
  const anchor = join(home, '.connection-recovery-anchor.bin')
  if (!existsSync(anchor) || lstatSync(anchor).isSymbolicLink() || !lstatSync(anchor).isFile() || hash(readFileSync(anchor)) !== intent.anchor_sha256) return false
  if (!(await checkConnectionRecoveryResources(home, node, { prepareReboot: false })).safe) return false
  if (!existsSync(join(home, 'identity-reconciliation.bin'))) atomicWrite(join(home, 'identity-reconciliation.bin'), await dpapiProtect(JSON.stringify({ version: 1, recovery_epoch: intent.epoch, resources_stopped: true })))
  return true
}

/** Main error projection only; protected intent + original archive anchor. */
export async function hasArchivedConnectionRecovery(home: string): Promise<boolean> {
  const intent = await load(home)
  if (!intent || intent.mode !== 'new_device') return false
  const archive = archiveDirectory(home, intent); const anchor = join(archive, '.connection-recovery-anchor.bin')
  return present(anchor) && !lstatSync(anchor).isSymbolicLink() && lstatSync(anchor).isFile() && lstatSync(anchor).size < 65536 && hash(readFileSync(anchor)) === intent.anchor_sha256
}
