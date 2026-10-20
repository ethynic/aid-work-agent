/** Resource proof only. Caller holds the home OS lease and owns no execution.
 * This never settles a business result, replays a task, or changes a marker/fence.
 */
import { execFile } from 'node:child_process'
import { AsyncLocalStorage } from 'node:async_hooks'
import { createHash } from 'node:crypto'
import { lstatSync, readFileSync, readdirSync } from 'node:fs'
import { dirname, isAbsolute, join, resolve } from 'node:path'
import { dpapiProtect, dpapiUnprotect } from './dpapi.js'
import { assertNoLegacyRuntimeProcess } from './legacyProcessCheck.js'
import { atomicWrite } from './pairingStore.js'
import { ProcessSupervision } from './processSupervision.js'

export interface ConnectionRecoveryResourceResult { safe: boolean; needs_reboot: boolean; category: string }
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const DIGEST = /^[0-9a-f]{64}$/
const WITNESS = 'connection-recovery-witness.bin'
const EPOCH = 'connection-recovery-epoch.bin'
const MAX_FILE = 2 * 1024 * 1024
const MAX_FILES = 2048
const MAX_LEDGERS = 128
const MAX_JOBS = 512
const MAX_READ_BYTES = 16 * 1024 * 1024
const RECOVERY_CONTROLS = new Set(['.connection-recovery-anchor.bin', 'management-retired-request-keys.bin'])
// These can be created after a proved-fresh epoch without executing a task.
const FRESH_METADATA = new Set(['config.json', 'credentials.bin', 'pairing.pending.json',
  'management-secret.bin', 'management-operations.json', 'plugins', 'management-imports', ...RECOVERY_CONTROLS])
interface Kernel { bootId: string; sessionId: number }
interface Witness extends Kernel { schemaVersion: 1; home: string; resourcesDigest: string }
interface Epoch extends Kernel { schemaVersion: 1; home: string }
interface Ledger extends Kernel { schemaVersion: 1; home: string; generation: string; ownerPid: number; jobs: string[]; business_started?: boolean }
interface Snapshot { digest: string; entries: string[]; marker?: string; markerUnreadable: boolean; files: Map<string, string | null>; supervisionEntries: string[] }
class ResourceFailure extends Error { constructor(readonly category: string) { super(category) } }
const inspectionDeadline = new AsyncLocalStorage<number>()
function remaining(): number {
  const milliseconds = (inspectionDeadline.getStore() ?? Infinity) - Date.now()
  if (milliseconds <= 0) throw new ResourceFailure('CONNECTION_RESOURCE_CHECK_TIMEOUT')
  return milliseconds
}
function requireResource(valid: unknown, category = 'CONNECTION_RESOURCE_EVIDENCE_INVALID'): asserts valid {
  if (!valid) throw new ResourceFailure(category)
}
function result(safe: boolean, needs_reboot: boolean, category: string): ConnectionRecoveryResourceResult { return { safe, needs_reboot, category } }
function record(value: unknown): value is Record<string, unknown> { return !!value && typeof value === 'object' && !Array.isArray(value) }
function exact(value: Record<string, unknown>, keys: string[]) { return Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key)) }
function uuid(value: unknown): value is string { return typeof value === 'string' && UUID.test(value) }
function session(value: unknown): value is number { return Number.isSafeInteger(value) && Number(value) >= 0 }
function sameBoot(a: string, b: string) { return a.toLowerCase() === b.toLowerCase() }

function stat(path: string) {
  try {
    const value = lstatSync(path)
    requireResource(!value.isSymbolicLink(), 'CONNECTION_RESOURCE_PATH_INVALID')
    return value
  } catch (error) {
    if (record(error) && error.code === 'ENOENT') return undefined
    if (error instanceof ResourceFailure) throw error
    throw new ResourceFailure('CONNECTION_RESOURCE_PATH_INVALID')
  }
}
function validateHome(home: string, nodeExecutable: string): string {
  requireResource(typeof home === 'string' && isAbsolute(home) && resolve(home) === home, 'CONNECTION_RESOURCE_PATH_INVALID')
  for (let path = home; ; path = dirname(path)) {
    const value = stat(path)
    requireResource(!value || value.isDirectory(), 'CONNECTION_RESOURCE_PATH_INVALID')
    if (dirname(path) === path) break
  }
  requireResource(typeof nodeExecutable === 'string' && isAbsolute(nodeExecutable), 'CONNECTION_RESOURCE_NODE_UNAVAILABLE')
  try { requireResource(lstatSync(nodeExecutable).isFile(), 'CONNECTION_RESOURCE_NODE_UNAVAILABLE') }
  catch { throw new ResourceFailure('CONNECTION_RESOURCE_NODE_UNAVAILABLE') }
  return home
}
function text(path: string): string {
  const value = stat(path)
  requireResource(value?.isFile() && value.size <= MAX_FILE)
  try { return new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(readFileSync(path)) }
  catch { throw new ResourceFailure('CONNECTION_RESOURCE_EVIDENCE_UNREADABLE') }
}
function snapshot(home: string): Snapshot {
  const value = stat(home)
  const entries = value ? readdirSync(home).sort() : []
  requireResource(entries.length <= MAX_FILES)
  for (const name of entries) stat(join(home, name))
  const directory = join(home, 'process-supervision')
  const directoryStat = stat(directory)
  requireResource(!directoryStat || directoryStat.isDirectory(), 'CONNECTION_RESOURCE_PATH_INVALID')
  const supervisionEntries = directoryStat ? readdirSync(directory).sort() : []
  requireResource(supervisionEntries.length <= MAX_FILES)
  const hash = createHash('sha256')
  const files = new Map<string, string | null>()
  // Identity/management repair files are not execution evidence and are never read.
  for (const name of entries.filter(name => !FRESH_METADATA.has(name)
    && ![WITNESS, EPOCH, 'process-supervision', 'identity-reconciliation.bin'].includes(name))) {
    hash.update(JSON.stringify(['entry', name]))
  }
  let marker: string | undefined
  let markerUnreadable = false
  if (entries.includes('host-running.json')) {
    try { marker = text(join(home, 'host-running.json')) }
    catch (error) {
      if (error instanceof ResourceFailure && error.category === 'CONNECTION_RESOURCE_PATH_INVALID') throw error
      markerUnreadable = true
      const metadata = stat(join(home, 'host-running.json'))!
      hash.update(JSON.stringify(['unreadable-marker', metadata.size, metadata.mtimeMs, metadata.ino]))
    }
  }
  if (marker !== undefined) hash.update(JSON.stringify(['marker', marker]))
  let bytes = 0
  for (const name of supervisionEntries) {
    const file = join(directory, name); const fileStat = stat(file)
    requireResource(fileStat !== undefined && fileStat.isFile(), 'CONNECTION_RESOURCE_PATH_INVALID')
    // A completed native receipt can arrive during cleanup. It is not a ledger
    // and is not used to prove stop or make a fresh epoch eligible.
    if (/^AidWorkRuntime\.[0-9a-f-]{36}\.receipt\.json$/i.test(name)) continue
    if (fileStat.size <= MAX_FILE && bytes + fileStat.size <= MAX_READ_BYTES) {
      bytes += fileStat.size
      // Invalid UTF-8 also remains available for a metadata-bound reboot proof.
      try {
        const raw = text(file); files.set(name, raw)
        hash.update(JSON.stringify(['supervision', name, raw])); continue
      } catch (error) {
        if (error instanceof ResourceFailure && error.category === 'CONNECTION_RESOURCE_PATH_INVALID') throw error
      }
    }
    {
      files.set(name, null)
      hash.update(JSON.stringify(['oversized', name, fileStat.size, fileStat.mtimeMs, fileStat.ino]))
    }
  }
  return { digest: hash.digest('hex'), entries, marker, markerUnreadable, files, supervisionEntries }
}
async function protectionAvailable(): Promise<void> {
  remaining()
  try {
    const probe = 'AID.connection-resource-proof.v1'
    requireResource(await dpapiUnprotect(await dpapiProtect(probe)) === probe)
  } catch { throw new ResourceFailure('CONNECTION_RESOURCE_PROTECTION_UNAVAILABLE') }
  remaining()
}
async function command(executable: string, args: string[]): Promise<Record<string, unknown>> {
  requireResource(stat(executable)?.isFile(), 'CONNECTION_RESOURCE_SUPERVISOR_UNAVAILABLE')
  return new Promise((accept, reject) => execFile(executable, args,
    { windowsHide: true, timeout: Math.min(20000, remaining()), maxBuffer: 65536 }, (error, stdout, stderr) => {
      if (error || stderr) { reject(new ResourceFailure('CONNECTION_RESOURCE_SUPERVISOR_UNAVAILABLE')); return }
      try { const parsed: unknown = JSON.parse(stdout); requireResource(record(parsed)); accept(parsed) }
      catch { reject(new ResourceFailure('CONNECTION_RESOURCE_SUPERVISOR_UNAVAILABLE')) }
    }))
}
async function kernel(executable: string): Promise<Kernel> {
  const boot = await command(executable, ['boot-id'])
  const login = await command(executable, ['session-id'])
  requireResource(uuid(boot.bootId) && session(login.sessionId), 'CONNECTION_RESOURCE_SUPERVISOR_UNAVAILABLE')
  return { bootId: boot.bootId, sessionId: login.sessionId }
}
async function protectedRecord(raw: string): Promise<Record<string, unknown>> {
  remaining()
  try { const value: unknown = JSON.parse(await dpapiUnprotect(raw)); requireResource(record(value)); return value }
  catch { throw new ResourceFailure('CONNECTION_RESOURCE_EVIDENCE_UNREADABLE') }
}
async function witness(home: string): Promise<Witness | undefined> {
  if (!stat(join(home, WITNESS))) return undefined
  try {
    const value = await protectedRecord(text(join(home, WITNESS)))
    requireResource(exact(value, ['schemaVersion', 'home', 'bootId', 'sessionId', 'resourcesDigest'])
      && value.schemaVersion === 1 && value.home === home && uuid(value.bootId) && session(value.sessionId)
      && typeof value.resourcesDigest === 'string' && DIGEST.test(value.resourcesDigest))
    return value as unknown as Witness
  } catch { throw new ResourceFailure('CONNECTION_RESOURCE_WITNESS_INVALID') }
}
async function writeWitness(home: string, now: Kernel, resources: Snapshot): Promise<void> {
  // Preserve a valid earlier witness only while the exact resource scope matches.
  let prior: Witness | undefined
  try { prior = await witness(home) } catch (error) {
    if (!(error instanceof ResourceFailure) || error.category !== 'CONNECTION_RESOURCE_WITNESS_INVALID') throw error
  }
  if (prior?.resourcesDigest === resources.digest) return
  requireResource(snapshot(home).digest === resources.digest, 'CONNECTION_RESOURCE_EVIDENCE_CHANGED')
  const encrypted = await dpapiProtect(JSON.stringify({ schemaVersion: 1, home, ...now, resourcesDigest: resources.digest } satisfies Witness))
  requireResource(snapshot(home).digest === resources.digest, 'CONNECTION_RESOURCE_EVIDENCE_CHANGED')
  atomicWrite(join(home, WITNESS), encrypted)
}
function freshResources(resources: Snapshot): boolean {
  return resources.marker === undefined && resources.supervisionEntries.length === 0
    && resources.entries.every(name => FRESH_METADATA.has(name) || name === EPOCH)
}
async function freshEpoch(home: string, now: Kernel, resources: Snapshot): Promise<boolean> {
  if (!stat(join(home, EPOCH))) return false
  const value = await protectedRecord(text(join(home, EPOCH)))
  requireResource(exact(value, ['schemaVersion', 'home', 'bootId', 'sessionId']) && value.schemaVersion === 1
    && value.home === home && uuid(value.bootId) && session(value.sessionId), 'CONNECTION_RESOURCE_FRESH_EPOCH_INVALID')
  if (!sameBoot(value.bootId, now.bootId) || value.sessionId !== now.sessionId || !freshResources(resources)) return false
  // An accepted start is execution history even when someone removed its ledger.
  if (resources.entries.includes('management-operations.json')) {
    let operations: unknown
    try { operations = JSON.parse(text(join(home, 'management-operations.json')).replace(/^\uFEFF/, '')) }
    catch { return false }
    if (!record(operations) || Object.values(operations).some(item => !record(item) || !record(item.operation) || item.operation.operation === 'start')) return false
  }
  return true
}

/** Registration only: usable after owned execution could not drain. No CLI or
 * Job query/kill occurs; later check must prove a different boot and no old CLI.
 */
export async function registerConnectionRecoveryReboot(home: string, nodeExecutable: string): Promise<ConnectionRecoveryResourceResult> {
  try {
    requireResource(process.platform === 'win32', 'CONNECTION_RESOURCE_PLATFORM_UNSUPPORTED')
    validateHome(home, nodeExecutable); await protectionAvailable()
    const now = await kernel(new ProcessSupervision(home, nodeExecutable).executable)
    await writeWitness(home, now, snapshot(home))
    return result(false, true, 'CONNECTION_RESOURCE_REBOOT_PREPARED')
  } catch (error) { return result(false, error instanceof ResourceFailure && error.category === 'CONNECTION_RESOURCE_EVIDENCE_CHANGED', error instanceof ResourceFailure ? error.category : 'CONNECTION_RESOURCE_EVIDENCE_UNREADABLE') }
}

/** Only after Main has archived the old home under a proved resource gate and
 * holds the new home's lease. No existing identity/history may be overwritten.
 */
export async function recordFreshConnectionEpoch(home: string, nodeExecutable: string): Promise<void> {
  try {
    requireResource(process.platform === 'win32', 'CONNECTION_RESOURCE_PLATFORM_UNSUPPORTED')
    validateHome(home, nodeExecutable); await protectionAvailable()
    const now = await kernel(new ProcessSupervision(home, nodeExecutable).executable)
    const resources = snapshot(home)
    requireResource(resources.entries.every(name => name === EPOCH || RECOVERY_CONTROLS.has(name)), 'CONNECTION_RESOURCE_FRESH_EPOCH_NOT_EMPTY')
    if (resources.entries.includes(EPOCH)) {
      const value = await protectedRecord(text(join(home, EPOCH)))
      requireResource(exact(value, ['schemaVersion', 'home', 'bootId', 'sessionId']) && value.schemaVersion === 1 && value.home === home
        && uuid(value.bootId) && session(value.sessionId), 'CONNECTION_RESOURCE_FRESH_EPOCH_INVALID')
      if (sameBoot(value.bootId, now.bootId) && value.sessionId === now.sessionId) return
      // Verified Main transaction may have crashed between archiving old home
      // and completing the new epoch. Only this still-empty transaction scope
      // can renew an authenticated epoch after another boot/login session.
    }
    requireResource(snapshot(home).digest === resources.digest, 'CONNECTION_RESOURCE_EVIDENCE_CHANGED')
    const encrypted = await dpapiProtect(JSON.stringify({ schemaVersion: 1, home, ...now } satisfies Epoch))
    const latest = snapshot(home)
    requireResource(latest.entries.every(name => name === EPOCH || RECOVERY_CONTROLS.has(name)) && latest.digest === resources.digest, 'CONNECTION_RESOURCE_EVIDENCE_CHANGED')
    atomicWrite(join(home, EPOCH), encrypted)
  } catch (error) { throw new Error(error instanceof ResourceFailure ? error.category : 'CONNECTION_RESOURCE_EVIDENCE_UNREADABLE') }
}

export async function checkConnectionRecoveryResources(home: string, nodeExecutable: string,
  options: { prepareReboot: boolean }): Promise<ConnectionRecoveryResourceResult> {
  // Every child helper has its own timeout. Stop starting further ledger probes
  // once the whole inspection expires, before Main's 120s IPC deadline.
  return inspectionDeadline.run(Date.now() + 55_000, () => checkResources(home, nodeExecutable, options))
}
async function checkResources(home: string, nodeExecutable: string,
  options: { prepareReboot: boolean }): Promise<ConnectionRecoveryResourceResult> {
  try {
    requireResource(process.platform === 'win32', 'CONNECTION_RESOURCE_PLATFORM_UNSUPPORTED')
    requireResource(options && typeof options.prepareReboot === 'boolean', 'CONNECTION_RESOURCE_INPUT_INVALID')
    validateHome(home, nodeExecutable); await protectionAvailable()
    let legacy = false
    try { await assertNoLegacyRuntimeProcess() } catch (error) {
      if (record(error) && error.code === 4) legacy = true
      else throw new ResourceFailure('CONNECTION_RESOURCE_LEGACY_CHECK_UNAVAILABLE')
    }
    const executable = new ProcessSupervision(home, nodeExecutable).executable
    const now = await kernel(executable)
    const resources = snapshot(home)
    const blocked = async (category: string) => {
      if (options.prepareReboot) {
        await writeWitness(home, now, snapshot(home))
        return result(false, true, 'CONNECTION_RESOURCE_REBOOT_PREPARED')
      }
      return result(false, true, category)
    }
    if (legacy) return blocked('CONNECTION_RESOURCE_LEGACY_ACTIVE')
    let prior: Witness | undefined
    try { prior = await witness(home) } catch { return blocked('CONNECTION_RESOURCE_WITNESS_INVALID') }
    if (prior && prior.resourcesDigest !== resources.digest) return blocked('CONNECTION_RESOURCE_EVIDENCE_CHANGED')
    const rebootProved = !!prior && !sameBoot(prior.bootId, now.bootId)
    if (rebootProved) {
      // The protected witness binds every old execution file to a real earlier
      // kernel boot. A reboot stops those processes even if an old ledger cannot
      // decrypt. Do not repeatedly decrypt old ledgers to rediscover that fact.
      remaining()
      try { await assertNoLegacyRuntimeProcess() } catch (error) {
        if (record(error) && error.code === 4) return blocked('CONNECTION_RESOURCE_LEGACY_ACTIVE')
        throw new ResourceFailure('CONNECTION_RESOURCE_LEGACY_CHECK_UNAVAILABLE')
      }
      if (snapshot(home).digest !== resources.digest) return blocked('CONNECTION_RESOURCE_EVIDENCE_CHANGED')
      return result(true, false, 'CONNECTION_RESOURCE_REBOOT_CONFIRMED')
    }
    let incomplete: string | undefined = resources.markerUnreadable ? 'CONNECTION_RESOURCE_EVIDENCE_UNREADABLE' : undefined
    let currentBootUnproved = false
    let markerProof: Record<string, unknown> | undefined
    let markerId: string | undefined
    let legacyMarker = false
    if (resources.marker !== undefined) {
      try {
        const parsed: unknown = JSON.parse(resources.marker.replace(/^\uFEFF/, ''))
        requireResource(record(parsed) && uuid(parsed.instance_id)); markerId = parsed.instance_id
        if (exact(parsed, ['instance_id', 'schemaVersion', 'supervision']) && parsed.schemaVersion === 2 && typeof parsed.supervision === 'string') {
          markerProof = await protectedRecord(parsed.supervision)
          requireResource(exact(markerProof, ['generation', 'home', 'bootId', 'sessionId', 'rebootOnly']) && markerProof.generation === markerId
            && markerProof.home === home && uuid(markerProof.bootId) && session(markerProof.sessionId) && typeof markerProof.rebootOnly === 'boolean')
          if (sameBoot(markerProof.bootId, now.bootId) && (markerProof.rebootOnly || markerProof.sessionId !== now.sessionId)) {
            incomplete = 'CONNECTION_RESOURCE_REBOOT_REQUIRED'; currentBootUnproved = true
          }
        } else {
          requireResource(/^\s*\{\s*"instance_id"\s*:\s*"[0-9a-f-]{36}"\s*\}\s*$/i.test(resources.marker.replace(/^\uFEFF/, '')))
          legacyMarker = true; incomplete = 'CONNECTION_RESOURCE_REBOOT_REQUIRED'
        }
      } catch { incomplete = 'CONNECTION_RESOURCE_EVIDENCE_INVALID'; markerProof = undefined }
    }
    const ledgers: Ledger[] = []; const jobs = new Set<string>()
    let legacyBootProof = false
    let attempted = 0
    for (const [name, raw] of resources.files) {
      if (++attempted > MAX_LEDGERS) { incomplete = 'CONNECTION_RESOURCE_EVIDENCE_UNREADABLE'; break }
      try {
        requireResource(raw !== null && ledgers.length < MAX_LEDGERS)
        const value = await protectedRecord(raw)
        if (/^legacy-[0-9a-f]{64}\.bin$/.test(name)) {
          requireResource(exact(value, ['home', 'digest', 'bootId']) && value.home === home && value.digest === name.slice(7, -4) && uuid(value.bootId))
          if (legacyMarker && resources.marker !== undefined && value.digest === createHash('sha256').update(resources.marker.replace(/^\uFEFF/, '')).digest('hex') && !sameBoot(value.bootId, now.bootId)) legacyBootProof = true
          else if (sameBoot(value.bootId, now.bootId)) { incomplete = 'CONNECTION_RESOURCE_REBOOT_REQUIRED'; currentBootUnproved = true }
          continue
        }
        requireResource(UUID.test(name.slice(0, -4)) && name.endsWith('.bin')
          && exact(value, ['schemaVersion', 'generation', 'home', 'bootId', 'sessionId', 'ownerPid', 'jobs', ...(Object.hasOwn(value, 'business_started') ? ['business_started'] : [])])
          && (!Object.hasOwn(value, 'business_started') || typeof value.business_started === 'boolean')
          && value.schemaVersion === 1 && value.home === home && uuid(value.generation) && name.toLowerCase() === `${value.generation}.bin`.toLowerCase()
          && uuid(value.bootId) && session(value.sessionId) && Number.isSafeInteger(value.ownerPid) && Number(value.ownerPid) > 0
          && Array.isArray(value.jobs) && value.jobs.length <= 10000 && value.jobs.every(job => typeof job === 'string' && job.startsWith('AidWorkRuntime.') && UUID.test(job.slice(15))))
        const ledger = value as unknown as Ledger
        requireResource(new Set(ledger.jobs).size === ledger.jobs.length)
        for (const job of ledger.jobs) { requireResource(!jobs.has(job)); jobs.add(job) }
        requireResource(jobs.size <= MAX_JOBS)
        ledgers.push(ledger)
        if (sameBoot(ledger.bootId, now.bootId) && (ledger.sessionId !== now.sessionId || (markerId && ledger.generation !== markerId))) {
          incomplete = 'CONNECTION_RESOURCE_REBOOT_REQUIRED'; currentBootUnproved = true
        }
      } catch { incomplete = 'CONNECTION_RESOURCE_EVIDENCE_UNREADABLE' }
    }
    if (markerProof) {
      const ledger = ledgers.find(item => item.generation === markerId)
      if (!ledger || !sameBoot(ledger.bootId, markerProof.bootId as string) || ledger.sessionId !== markerProof.sessionId) incomplete = 'CONNECTION_RESOURCE_EVIDENCE_INVALID'
    }
    // All decoded same-session current-boot jobs are checked, not only marker's.
    for (const ledger of ledgers) {
      if (!sameBoot(ledger.bootId, now.bootId) || ledger.sessionId !== now.sessionId) continue
      for (const job of ledger.jobs) {
        try {
          const state = await command(executable, [options.prepareReboot ? 'terminate' : 'probe', '--job-name', job])
          requireResource(typeof state.exists === 'boolean' && Number.isSafeInteger(state.activeProcesses) && Number(state.activeProcesses) >= 0)
          if (state.activeProcesses !== 0) return blocked('CONNECTION_RESOURCE_ACTIVE')
        } catch { remaining(); return blocked('CONNECTION_RESOURCE_STOP_UNCONFIRMED') }
      }
    }
    if (snapshot(home).digest !== resources.digest) return blocked('CONNECTION_RESOURCE_EVIDENCE_CHANGED')
    remaining()
    try { await assertNoLegacyRuntimeProcess() } catch (error) {
      if (record(error) && error.code === 4) return blocked('CONNECTION_RESOURCE_LEGACY_ACTIVE')
      throw new ResourceFailure('CONNECTION_RESOURCE_LEGACY_CHECK_UNAVAILABLE')
    }
    if (currentBootUnproved) return blocked('CONNECTION_RESOURCE_REBOOT_REQUIRED')
    if (rebootProved) return result(true, false, 'CONNECTION_RESOURCE_REBOOT_CONFIRMED')
    if (prior) return blocked('CONNECTION_RESOURCE_REBOOT_REQUIRED')
    if (resources.entries.length === 0) return result(true, false, 'CONNECTION_RESOURCE_NEW_HOME')
    try { if (await freshEpoch(home, now, resources)) return result(true, false, 'CONNECTION_RESOURCE_FRESH_EPOCH') }
    catch { return blocked('CONNECTION_RESOURCE_FRESH_EPOCH_INVALID') }
    if (resources.marker === undefined) return blocked('CONNECTION_RESOURCE_REBOOT_REQUIRED')
    if (legacyMarker && legacyBootProof && incomplete === 'CONNECTION_RESOURCE_REBOOT_REQUIRED') return result(true, false, 'CONNECTION_RESOURCE_REBOOT_CONFIRMED')
    if (!incomplete && markerProof) return result(true, false, 'CONNECTION_RESOURCE_SUPERVISED_STOPPED')
    return blocked(incomplete ?? 'CONNECTION_RESOURCE_REBOOT_REQUIRED')
  } catch (error) { return result(false, error instanceof ResourceFailure && ['CONNECTION_RESOURCE_EVIDENCE_CHANGED', 'CONNECTION_RESOURCE_CHECK_TIMEOUT'].includes(error.category), error instanceof ResourceFailure ? error.category : 'CONNECTION_RESOURCE_EVIDENCE_UNREADABLE') }
}
