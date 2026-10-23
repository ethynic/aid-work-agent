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
interface Ledger { schemaVersion: 1; generation: string; home: string; bootId: string; sessionId: number; ownerPid: number; jobs: string[]; business_started?: boolean }
/** Fixed local launcher; no renderer, Device or model controls command arguments. */
export class ProcessSupervision {
  readonly executable = resolve(dirname(fileURLToPath(import.meta.url)), 'native/RuntimeProcessSupervisor.exe')
  private ledger?: Ledger
  private queue: Promise<void> = Promise.resolve()
  private recoveringResources = false
  constructor(private home: string, private node: string) {}
  private directory() {
    const directory = join(this.home, 'process-supervision')
    if (existsSync(directory) && lstatSync(directory).isSymbolicLink()) throw new Error('supervision directory invalid')
    mkdirSync(directory, { recursive: true }); return directory
  }
  private async command(args: string[]): Promise<Record<string, unknown>> {
    if (!existsSync(this.executable)) throw new Error('process supervisor missing')
    return new Promise((accept, reject) => execFile(this.executable, args, { windowsHide: true, timeout: 20_000, maxBuffer: 65536 }, (error, stdout) => {
      if (error) { reject(new Error('process supervision unavailable')); return }
      try { accept(JSON.parse(stdout)) } catch { reject(new Error('process supervision invalid')) }
    }))
  }
  private async bootId(): Promise<string> {
    const result = await this.command(['boot-id'])
    if (typeof result.bootId !== 'string' || !uuid.test(result.bootId)) throw new Error('boot identity invalid')
    return result.bootId
  }
  private async write(ledger: Ledger) {
    atomicWrite(join(this.directory(), `${ledger.generation}.bin`), await dpapiProtect(JSON.stringify(ledger)))
  }
  private async sessionId(): Promise<number> {
    const result = await this.command(['session-id'])
    if (!Number.isSafeInteger(result.sessionId) || Number(result.sessionId) < 0) throw new Error('session identity invalid')
    return result.sessionId as number
  }
  async marker(instanceId: string): Promise<Record<string, unknown>> {
    if (process.platform !== 'win32') return {}
    if (!this.enabled) throw new Error('required process supervisor missing')
    const config = JSON.parse(readFileSync(join(this.home, 'config.json'), 'utf8').replace(/^\uFEFF/, ''))
    // External Python skill executors have not joined the Provider Job boundary.
    const rebootOnly = Boolean(config.skills?.python)
    this.ledger = { schemaVersion: 1, generation: instanceId, home: resolve(this.home), bootId: await this.bootId(), sessionId: await this.sessionId(), ownerPid: process.pid, jobs: [], business_started: false }
    await this.write(this.ledger)
    return { schemaVersion: 2, supervision: await dpapiProtect(JSON.stringify({ generation: instanceId, home: resolve(this.home), bootId: this.ledger.bootId, sessionId: this.ledger.sessionId, rebootOnly })) }
  }
  async prepareLaunch(entry: string): Promise<{ command: string; args: string[] }> {
    if (!this.ledger) throw new Error('execution supervision not initialized')
    if (this.recoveringResources) throw new Error('LOCAL_RECOVERY_BUSY')
    const jobName = `AidWorkRuntime.${randomUUID()}`
    const work = this.queue.then(async () => { this.ledger!.jobs.push(jobName); await this.write(this.ledger!) })
    this.queue = work.catch(() => {}); await work
    const powershell = trustedSystemPowerShell()
    const ownerStart = await new Promise<string>((accept, reject) => execFile(powershell.executable, ['-NoProfile', '-NonInteractive', '-Command', `$ErrorActionPreference='Stop'; [Console]::Out.Write((Get-Process -Id ${process.pid}).StartTime.ToUniversalTime().Ticks)`], { cwd: powershell.cwd, env: powershell.env, windowsHide: true, timeout: 15000 }, (error, out) => error ? reject(new Error('owner identity unavailable')) : accept(out.trim())))
    if (this.recoveringResources) throw new Error('LOCAL_RECOVERY_BUSY')
    if (!/^\d{17,19}$/.test(ownerStart)) throw new Error('owner identity invalid')
    return { command: this.executable, args: ['run', '--job-name', jobName, '--node', this.node, '--entry', entry,
      '--owner-pid', String(process.pid), '--owner-start-time', ownerStart, '--receipt', join(this.directory(), `${jobName}.receipt.json`)] }
  }
  async recover(marker: string): Promise<boolean> {
    if (!this.enabled) return false
    const parsed = JSON.parse(marker)
    if (!uuid.test(parsed.instance_id)) throw new Error('marker invalid')
    const digest = createHash('sha256').update(marker).digest('hex')
    const currentBoot = await this.bootId()
    let stopped = false
    if (parsed.schemaVersion === 2 && typeof parsed.supervision === 'string') {
      const proof = JSON.parse(await dpapiUnprotect(parsed.supervision))
      if (proof.generation !== parsed.instance_id || proof.home !== resolve(this.home) || !uuid.test(proof.bootId) || typeof proof.rebootOnly !== 'boolean') throw new Error('marker binding invalid')
      if (proof.bootId !== currentBoot) stopped = true
      // Named Jobs are session-scoped. Absence in another login session cannot
      // prove the old session's tree stopped; a real reboot remains sufficient.
      else if (proof.rebootOnly === false && proof.sessionId === await this.sessionId()) {
        const ledger: Ledger = JSON.parse(await dpapiUnprotect(readFileSync(join(this.directory(), `${proof.generation}.bin`), 'utf8')))
        if (ledger.schemaVersion !== 1 || ledger.home !== proof.home || ledger.generation !== proof.generation || ledger.bootId !== proof.bootId || ledger.sessionId !== proof.sessionId
          || (ledger.business_started !== undefined && typeof ledger.business_started !== 'boolean')
          || !Number.isSafeInteger(ledger.ownerPid) || !Array.isArray(ledger.jobs) || ledger.jobs.length > 10000
          || ledger.jobs.some(job => typeof job !== 'string' || !job.startsWith('AidWorkRuntime.') || !uuid.test(job.slice(15)))) throw new Error('ledger binding invalid')
        // Caller holds the home lease: old Host is gone. Kill only its protected,
        // registered jobs. The launcher never enables breakaway or Chrome retention.
        for (const job of ledger.jobs) {
          const result = await this.command(['terminate', '--job-name', job])
          if (result.activeProcesses !== 0) throw new Error('job stop unconfirmed')
        }
        stopped = true
      }
    } else if (Object.keys(parsed).join(',') === 'instance_id') {
      const witnessFile = join(this.directory(), `legacy-${digest}.bin`)
      if (existsSync(witnessFile)) {
        const witness = JSON.parse(await dpapiUnprotect(readFileSync(witnessFile, 'utf8')))
        if (witness.home !== resolve(this.home) || witness.digest !== digest || !uuid.test(witness.bootId)) throw new Error('legacy witness invalid')
        stopped = witness.bootId !== currentBoot
      } else atomicWrite(witnessFile, await dpapiProtect(JSON.stringify({ home: resolve(this.home), digest, bootId: currentBoot })))
    } else throw new Error('unsupported execution marker')
    if (stopped) atomicWrite(join(this.home, 'identity-reconciliation.bin'), await dpapiProtect(JSON.stringify({ markerDigest: digest, instanceId: parsed.instance_id })))
    return stopped
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

  private async sessionHasProcesses(sessionId: number): Promise<boolean> {
    const shell = trustedSystemPowerShell()
    return new Promise((accept, reject) => execFile(shell.executable, ['-NoProfile', '-NonInteractive', '-Command',
      `$ErrorActionPreference='Stop'; [Console]::Out.Write(@(Get-CimInstance Win32_Process -Filter "SessionId=${sessionId}").Count)`],
    { cwd: shell.cwd, env: shell.env, windowsHide: true, timeout: 15000, maxBuffer: 4096 }, (error, out) => {
      if (error || !/^\d+$/.test(out.trim())) reject(new Error('LOCAL_RECOVERY_PROCESS_CHECK_UNAVAILABLE'))
      else accept(Number(out.trim()) > 0)
    }))
  }

  private async stoppedReceipt(job: string, ownerPid: number): Promise<boolean> {
    const path = join(this.directory(), `${job}.receipt.json`)
    if (!existsSync(path)) return false
    try {
      const wrapper = JSON.parse(readFileSync(path, 'utf8'))
      if (wrapper.schemaVersion !== 1 || typeof wrapper.protectedData !== 'string' || lstatSync(path).isSymbolicLink()) return false
      const receipt = JSON.parse(await dpapiUnprotect(wrapper.protectedData))
      return receipt.schemaVersion === 1 && receipt.jobName === job && receipt.ownerPid === ownerPid
        && receipt.processStopped === true && receipt.remainingBusinessApps === 0
        && Number.isSafeInteger(receipt.nodePid) && receipt.nodePid > 0
    } catch { return false }
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
      const boot = await this.bootId(), session = await this.sessionId()
      const files = readdirSync(this.directory()).filter(file => /^[0-9a-f-]{36}\.bin$/i.test(file)).sort()
      const ledgers: Ledger[] = []
      for (const file of files) {
        const path = join(this.directory(), file)
        if (lstatSync(path).isSymbolicLink() || lstatSync(path).size > 2 * 1024 * 1024) throw new Error('LOCAL_RECOVERY_EVIDENCE_INVALID')
        let value: Ledger
        try { value = JSON.parse(await dpapiUnprotect(readFileSync(path, 'utf8'))) } catch { throw new Error('LOCAL_RECOVERY_EVIDENCE_INVALID') }
        if (value.schemaVersion !== 1 || value.home !== resolve(this.home) || !uuid.test(value.generation)
          || file.toLowerCase() !== `${value.generation}.bin`.toLowerCase() || !uuid.test(value.bootId)
          || !Number.isSafeInteger(value.sessionId) || value.sessionId < 0 || !Number.isSafeInteger(value.ownerPid) || value.ownerPid <= 0
          || (value.business_started !== undefined && typeof value.business_started !== 'boolean')
          || !Array.isArray(value.jobs) || value.jobs.length > 10000 || new Set(value.jobs).size !== value.jobs.length
          || value.jobs.some(job => typeof job !== 'string' || !job.startsWith('AidWorkRuntime.') || !uuid.test(job.slice(15)))) throw new Error('LOCAL_RECOVERY_EVIDENCE_INVALID')
        ledgers.push(value)
      }
      // New homes and probe-only generations cannot manufacture coverage for a
      // historical cloud hold. Legacy ledgers predate the unreleased probe.
      if (!ledgers.some(value => value.jobs.length > 0 && value.business_started !== false)) throw new Error('LOCAL_RECOVERY_NO_EXECUTION_EVIDENCE')
      for (const value of ledgers) {
        if (value.bootId.toLowerCase() !== boot.toLowerCase() || value.jobs.length === 0) continue
        if (value.sessionId !== session) {
          let receipts = true
          for (const job of value.jobs) {
            const local = await this.command(['probe', '--job-name', job])
            if (local.activeProcesses !== 0) throw new Error('LOCAL_RECOVERY_OTHER_SESSION')
            if (!await this.stoppedReceipt(job, value.ownerPid)) receipts = false
          }
          if (!receipts && await this.sessionHasProcesses(value.sessionId)) throw new Error('LOCAL_RECOVERY_OTHER_SESSION')
          continue
        }
        for (const job of value.jobs) {
          const terminated = await this.command(['terminate', '--job-name', job])
          const observed = await this.command(['probe', '--job-name', job])
          if (terminated.activeProcesses !== 0 || observed.activeProcesses !== 0) throw new Error('LOCAL_RECOVERY_STOP_UNPROVED')
        }
      }
      if (JSON.stringify(readdirSync(this.directory()).filter(file => /^[0-9a-f-]{36}\.bin$/i.test(file)).sort()) !== JSON.stringify(files)) throw new Error('LOCAL_RECOVERY_EVIDENCE_CHANGED')
      await this.checkLegacyProcesses()
      return { runtime_generation: this.ledger.generation, stop_evidence: 'supervised_device_boundary', resource_scope: 'managed_provider_pool' }
    } finally { this.recoveringResources = false }
  }
  get enabled() { return process.platform === 'win32' && existsSync(this.executable) }
}
