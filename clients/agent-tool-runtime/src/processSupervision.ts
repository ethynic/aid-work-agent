import { execFile } from 'node:child_process'
import { randomUUID, createHash } from 'node:crypto'
import { existsSync, lstatSync, mkdirSync, readFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { dpapiProtect, dpapiUnprotect, trustedSystemPowerShell } from './dpapi.js'
import { atomicWrite } from './pairingStore.js'

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
interface Ledger { schemaVersion: 1; generation: string; home: string; bootId: string; sessionId: number; ownerPid: number; jobs: string[] }
/** Fixed local launcher; no renderer, Device or model controls command arguments. */
export class ProcessSupervision {
  readonly executable = resolve(dirname(fileURLToPath(import.meta.url)), 'native/RuntimeProcessSupervisor.exe')
  private ledger?: Ledger
  private queue: Promise<void> = Promise.resolve()
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
    this.ledger = { schemaVersion: 1, generation: instanceId, home: resolve(this.home), bootId: await this.bootId(), sessionId: await this.sessionId(), ownerPid: process.pid, jobs: [] }
    await this.write(this.ledger)
    return { schemaVersion: 2, supervision: await dpapiProtect(JSON.stringify({ generation: instanceId, home: resolve(this.home), bootId: this.ledger.bootId, sessionId: this.ledger.sessionId, rebootOnly })) }
  }
  async prepareLaunch(entry: string): Promise<{ command: string; args: string[] }> {
    if (!this.ledger) throw new Error('execution supervision not initialized')
    const jobName = `AidWorkRuntime.${randomUUID()}`
    const work = this.queue.then(async () => { this.ledger!.jobs.push(jobName); await this.write(this.ledger!) })
    this.queue = work.catch(() => {}); await work
    const powershell = trustedSystemPowerShell()
    const ownerStart = await new Promise<string>((accept, reject) => execFile(powershell.executable, ['-NoProfile', '-NonInteractive', '-Command', `$ErrorActionPreference='Stop'; [Console]::Out.Write((Get-Process -Id ${process.pid}).StartTime.ToUniversalTime().Ticks)`], { cwd: powershell.cwd, env: powershell.env, windowsHide: true, timeout: 15000 }, (error, out) => error ? reject(new Error('owner identity unavailable')) : accept(out.trim())))
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
  get enabled() { return process.platform === 'win32' && existsSync(this.executable) }
}
