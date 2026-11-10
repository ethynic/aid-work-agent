import { execFile } from 'node:child_process'
import { randomUUID, createHash } from 'node:crypto'
import { existsSync, lstatSync, mkdirSync, readFileSync, readdirSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { dpapiProtect, dpapiUnprotect, trustedSystemPowerShell } from './dpapi.js'
import { ManagementError } from '@aid/local-tool-host-core'
import { assertNoLegacyRuntimeProcess } from './legacyProcessCheck.js'
import { atomicWrite } from './pairingStore.js'

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const globalPrefix = 'Global\\'
const localPrefix = 'AidWorkRuntime.'
interface LedgerFields { generation: string; home: string; ownerPid: number; jobs: string[]; business_started?: boolean }
export type SupervisionLedger = LedgerFields & ({ schemaVersion: 1; bootId: string; sessionId: number } | { schemaVersion: 2; externalPython: boolean })
type GlobalLedger = Extract<SupervisionLedger, { schemaVersion: 2 }>
const isSession = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0
export function supervisionLedgerFile(name: string): { schemaVersion: 1 | 2; generation: string } | null {
  if (!name.toLowerCase().endsWith('.bin')) return null
  const base = name.slice(0, -4), global = base.toLowerCase().startsWith('global-'), generation = global ? base.slice('global-'.length) : base
  return uuid.test(generation) ? { schemaVersion: global ? 2 : 1, generation } : null
}
export function readSupervisionLedger(value: unknown, home: string, generation: string): SupervisionLedger {
  const ledger = value as SupervisionLedger | null
  const prefix = ledger?.schemaVersion === 2 ? globalPrefix + localPrefix : localPrefix
  if (!ledger || !uuid.test(generation) || ledger.home !== home
    || typeof ledger.generation !== 'string' || ledger.generation.toLowerCase() !== generation.toLowerCase()
    || !Number.isSafeInteger(ledger.ownerPid) || ledger.ownerPid <= 0
    || (ledger.business_started !== undefined && typeof ledger.business_started !== 'boolean')
    || !Array.isArray(ledger.jobs) || ledger.jobs.length > 10_000 || new Set(ledger.jobs).size !== ledger.jobs.length
    || ledger.jobs.some(job => typeof job !== 'string' || !job.startsWith(prefix) || !uuid.test(job.slice(prefix.length)))
    || (ledger.schemaVersion === 2 ? typeof ledger.externalPython !== 'boolean'
      : ledger.schemaVersion !== 1 || typeof ledger.bootId !== 'string' || !uuid.test(ledger.bootId) || !isSession(ledger.sessionId))) {
    throw new Error('LOCAL_RECOVERY_EVIDENCE_INVALID')
  }
  return ledger
}
function receiptName(job: string): string {
  return (job.startsWith(globalPrefix) ? job.slice(globalPrefix.length) : job) + '.receipt.json'
}
/** Fixed local launcher; no renderer, Device or model controls command arguments. */
export class ProcessSupervision {
  readonly executable = resolve(dirname(fileURLToPath(import.meta.url)), 'native/RuntimeProcessSupervisor.exe')
  private ledger?: GlobalLedger
  private queue: Promise<void> = Promise.resolve()
  private recoveringResources = false
  constructor(private home: string, private node: string) {}
  private directory() {
    const directory = join(this.home, 'process-supervision')
    if (existsSync(directory) && lstatSync(directory).isSymbolicLink()) throw new Error('supervision directory invalid')
    mkdirSync(directory, { recursive: true }); return directory
  }
  private async command(args: string[], timeout = 20_000): Promise<Record<string, unknown>> {
    if (!existsSync(this.executable)) throw new Error('process supervisor missing')
    return new Promise((accept, reject) => execFile(this.executable, args, { windowsHide: true, timeout, maxBuffer: 65536 }, (error, stdout) => {
      if (error) { reject(new Error('process supervision unavailable')); return }
      try { accept(JSON.parse(stdout)) } catch { reject(new Error('process supervision invalid')) }
    }))
  }
  private async write(ledger: GlobalLedger) {
    atomicWrite(join(this.directory(), `global-${ledger.generation}.bin`), await dpapiProtect(JSON.stringify(ledger)))
  }
  private async sessionId(): Promise<number> {
    const result = await this.command(['session-id'])
    if (!isSession(result.sessionId)) throw new Error('session identity invalid')
    return result.sessionId
  }
  async marker(instanceId: string): Promise<Record<string, unknown>> {
    if (process.platform !== 'win32') return {}
    if (!this.enabled) throw new Error('required process supervisor missing')
    const config = JSON.parse(readFileSync(join(this.home, 'config.json'), 'utf8').replace(/^\uFEFF/, ''))
    // External Python skill executors have not joined the Provider Job boundary.
    const externalPython = Boolean(config.skills?.python)
    this.ledger = { schemaVersion: 2, generation: instanceId, home: resolve(this.home), externalPython, ownerPid: process.pid, jobs: [], business_started: false }
    await this.write(this.ledger)
    return { schemaVersion: 3, supervision: await dpapiProtect(JSON.stringify({ generation: instanceId, home: resolve(this.home), externalPython })) }
  }
  async prepareLaunch(entry: string): Promise<{ command: string; args: string[] }> {
    if (!this.ledger) throw new Error('execution supervision not initialized')
    if (this.recoveringResources) throw new Error('LOCAL_RECOVERY_BUSY')
    const jobName = `${globalPrefix}${localPrefix}${randomUUID()}`
    const work = this.queue.then(async () => { this.ledger!.jobs.push(jobName); await this.write(this.ledger!) })
    this.queue = work.catch(() => {}); await work
    const powershell = trustedSystemPowerShell()
    const ownerStart = await new Promise<string>((accept, reject) => execFile(powershell.executable, ['-NoProfile', '-NonInteractive', '-Command', `$ErrorActionPreference='Stop'; [Console]::Out.Write((Get-Process -Id ${process.pid}).StartTime.ToUniversalTime().Ticks)`], { cwd: powershell.cwd, env: powershell.env, windowsHide: true, timeout: 15000 }, (error, out) => error ? reject(new Error('owner identity unavailable')) : accept(out.trim())))
    if (this.recoveringResources) throw new Error('LOCAL_RECOVERY_BUSY')
    if (!/^\d{17,19}$/.test(ownerStart)) throw new Error('owner identity invalid')
    return { command: this.executable, args: ['run', '--job-name', jobName, '--node', this.node, '--entry', entry,
      '--owner-pid', String(process.pid), '--owner-start-time', ownerStart, '--receipt', join(this.directory(), receiptName(jobName))] }
  }
  async recover(marker: string): Promise<boolean> {
    if (!this.enabled) return false
    try {
      const parsed = JSON.parse(marker.replace(/^\uFEFF/, ''))
      if (!uuid.test(parsed?.instance_id) || ![2, 3].includes(parsed.schemaVersion) || typeof parsed.supervision !== 'string') return false
      const proof = JSON.parse(await dpapiUnprotect(parsed.supervision))
      if (!proof || proof.generation !== parsed.instance_id || proof.home !== resolve(this.home)) return false
      const ledger = await this.readLedger(parsed.schemaVersion === 3 ? `global-${parsed.instance_id}.bin` : `${parsed.instance_id}.bin`)
      if (parsed.schemaVersion === 3) {
        if (ledger.schemaVersion !== 2 || ledger.externalPython !== proof.externalPython) return false
      } else if (ledger.schemaVersion !== 1 || ledger.bootId !== proof.bootId || ledger.sessionId !== proof.sessionId || typeof proof.rebootOnly !== 'boolean') return false
      // The caller holds the home lease. Only names bound by DPAPI can reach native termination.
      if (!await this.stopLedgerJobs(ledger) || (ledger.schemaVersion === 2 ? ledger.externalPython : proof.rebootOnly)) return false
      const digest = createHash('sha256').update(marker).digest('hex')
      atomicWrite(join(this.home, 'identity-reconciliation.bin'), await dpapiProtect(JSON.stringify({ markerDigest: digest, instanceId: parsed.instance_id })))
      return true
    } catch { return false }
  }
  /** Trusted ordinary Provider dispatch only; observation never establishes business coverage. */
  async noteBusinessExecution(): Promise<void> {
    if (!this.enabled || !this.ledger) return
    const work = this.queue.then(async () => {
      if (this.recoveringResources) throw new Error('LOCAL_RECOVERY_BUSY')
      if (this.ledger!.business_started === true) return
      const updated = { ...this.ledger!, business_started: true }
      await this.write(updated)
      this.ledger = updated
    })
    this.queue = work.catch(() => {}); await work
  }

  private async checkLegacyProcesses(): Promise<void> {
    try { await assertNoLegacyRuntimeProcess() } catch (error) {
      throw new Error(error instanceof ManagementError && error.code === 4
        ? 'LOCAL_RECOVERY_LEGACY_UNMANAGED' : 'LOCAL_RECOVERY_PROCESS_CHECK_UNAVAILABLE')
    }
  }

  private async readLedger(name: string): Promise<SupervisionLedger> {
    const expected = supervisionLedgerFile(name)
    if (!expected) throw new Error('LOCAL_RECOVERY_EVIDENCE_INVALID')
    const directory = join(this.home, 'process-supervision')
    const file = join(directory, name)
    const stat = lstatSync(file)
    if (lstatSync(directory).isSymbolicLink() || !stat.isFile() || stat.isSymbolicLink() || stat.size > 2 * 1024 * 1024) throw new Error('LOCAL_RECOVERY_EVIDENCE_INVALID')
    const ledger = readSupervisionLedger(JSON.parse(await dpapiUnprotect(readFileSync(file, 'utf8'))), resolve(this.home), expected.generation)
    if (ledger.schemaVersion !== expected.schemaVersion) throw new Error('LOCAL_RECOVERY_EVIDENCE_INVALID')
    return ledger
  }
  private async receiptRemainingBusinessApps(job: string, ownerPid: number): Promise<number | undefined> {
    const directory = join(this.home, 'process-supervision')
    const file = join(directory, receiptName(job))
    try {
      const stat = lstatSync(file)
      if (lstatSync(directory).isSymbolicLink() || !stat.isFile() || stat.isSymbolicLink() || stat.size > 65536) return
      const wrapper = JSON.parse(readFileSync(file, 'utf8'))
      if (wrapper.schemaVersion !== 1 || typeof wrapper.protectedData !== 'string') return
      const receipt = JSON.parse(await dpapiUnprotect(wrapper.protectedData))
      if (receipt.schemaVersion === 1 && receipt.jobName === job && receipt.ownerPid === ownerPid
        && receipt.processStopped === true && Number.isSafeInteger(receipt.remainingBusinessApps) && receipt.remainingBusinessApps >= 0
        && Number.isSafeInteger(receipt.nodePid) && receipt.nodePid > 0) return receipt.remainingBusinessApps
    } catch { return }
  }
  async hasPreservedLegacyProcesses(job: string, ownerPid: number): Promise<boolean> {
    return (await this.receiptRemainingBusinessApps(job, ownerPid) ?? 0) > 0
  }
  async stopLedgerJobs(ledger: SupervisionLedger, terminate = true, deadline = Date.now() + 20_000): Promise<boolean> {
    if (ledger.jobs.length === 0) return true
    // A Local name has no meaning outside its original session. There, only a
    // direct exit receipt may release it. Global names require no session probe.
    if (ledger.schemaVersion === 1 && ledger.sessionId !== await this.sessionId()) {
      for (const job of ledger.jobs) if (Date.now() >= deadline || await this.receiptRemainingBusinessApps(job, ledger.ownerPid) !== 0) return false
      return true
    }
    for (const job of ledger.jobs) {
      const remaining = deadline - Date.now()
      if (remaining <= 0) return false
      const result = await this.command([terminate ? 'terminate' : 'probe', '--job-name', job], Math.min(20_000, remaining))
      if (typeof result.exists !== 'boolean' || result.activeProcesses !== 0) return false
      // Older helpers could deliberately leave Chrome behind after the name disappeared.
      if (ledger.schemaVersion === 1 && !result.exists && await this.hasPreservedLegacyProcesses(job, ledger.ownerPid)) return false
    }
    return true
  }

  /** Caller owns this paired home's lease and the desktop lock; Provider admission
   * is paused and its idle managers have been shut down before this method runs.
   * This device boundary covers old calls even without an invocation -> Job map.
   */
  async stopManagedResources(): Promise<{ runtime_generation: string; stop_evidence: 'supervised_device_boundary'; resource_scope: 'managed_provider_pool' }> {
    if (!this.enabled || !this.ledger) throw new Error('LOCAL_RECOVERY_SUPERVISION_UNAVAILABLE')
    this.recoveringResources = true
    try {
      await this.queue
      await this.checkLegacyProcesses()
      const ledgerFiles = () => readdirSync(this.directory()).filter(file => supervisionLedgerFile(file) || file.toLowerCase().startsWith('global-')).sort()
      const files = ledgerFiles()
      const ledgers: SupervisionLedger[] = []
      for (const file of files) ledgers.push(await this.readLedger(file))
      // New homes and probe-only generations cannot manufacture coverage for a
      // historical cloud hold. Legacy ledgers predate the unreleased probe.
      if (!ledgers.some(value => value.jobs.length > 0 && value.business_started !== false)) throw new Error('LOCAL_RECOVERY_NO_EXECUTION_EVIDENCE')
      for (const ledger of ledgers) if (!await this.stopLedgerJobs(ledger)) throw new Error('LOCAL_RECOVERY_STOP_UNPROVED')
      if (JSON.stringify(ledgerFiles()) !== JSON.stringify(files)) throw new Error('LOCAL_RECOVERY_EVIDENCE_CHANGED')
      await this.checkLegacyProcesses()
      return { runtime_generation: this.ledger.generation, stop_evidence: 'supervised_device_boundary', resource_scope: 'managed_provider_pool' }
    } finally { this.recoveringResources = false }
  }
  get enabled() { return process.platform === 'win32' && existsSync(this.executable) }
}
