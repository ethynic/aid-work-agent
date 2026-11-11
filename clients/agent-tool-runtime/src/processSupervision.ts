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
    this.ledger = { schemaVersion: 1, generation: instanceId, home: resolve(this.home), bootId: await this.bootId(), sessionId: await this.sessionId(), ownerPid: process.pid, jobs: [], business_started: false }
    await this.write(this.ledger)
    // rebootOnly 恒为 false：外部 Python 技能执行器能力已移除；字段保留是为兼容
    // 读取历史标记数据（recover 仍按旧语义处理 true 的存量标记）。
    return { schemaVersion: 2, supervision: await dpapiProtect(JSON.stringify({ generation: instanceId, home: resolve(this.home), bootId: this.ledger.bootId, sessionId: this.ledger.sessionId, rebootOnly: false })) }
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
  /** 历史台账只提供受管 Job 清单：读不出或绑定不符视为「jobs 未知」，继续走
   * 会话/回执证据链，不产生阻断恢复的记录类错误。 */
  private async tryReadLedger(proof: { generation: string; home: string; bootId: string; sessionId: number }): Promise<Ledger | undefined> {
    let ledger: Ledger
    try { ledger = JSON.parse(await dpapiUnprotect(readFileSync(join(this.directory(), `${proof.generation}.bin`), 'utf8'))) } catch { return undefined }
    if (!ledger || ledger.schemaVersion !== 1 || ledger.home !== proof.home || ledger.generation !== proof.generation || ledger.bootId !== proof.bootId || ledger.sessionId !== proof.sessionId
      || (ledger.business_started !== undefined && typeof ledger.business_started !== 'boolean')
      || !Number.isSafeInteger(ledger.ownerPid) || ledger.ownerPid <= 0 || !Array.isArray(ledger.jobs) || ledger.jobs.length > 10000
      || ledger.jobs.some(job => typeof job !== 'string' || !job.startsWith('AidWorkRuntime.') || !uuid.test(job.slice(15)))) return undefined
    return ledger
  }

  /** 恢复判据（产品原则）：不用 Windows 重启状态证明本软件的状态；不能核实的旧
   * 记录不得阻碍恢复接单；能确认原会话已无进程即可恢复。只用进程退出证据——
   * 受管 Job 终止、退出回执或原登录会话已无进程。返回值：true = 旧执行已退出
   * （或旧记录读不出且不会自愈，归档放行）；false = 可能仍在运行、系统查询暂时
   * 失败或解密助手超时，等待复查。记录类异常不再抛出。 */
  async recover(marker: string): Promise<boolean> {
    if (!this.enabled) return false
    const digest = createHash('sha256').update(marker).digest('hex')
    // 标记与监督凭据是本机原子写入：标记本体读不出、凭据被确定性拒绝或解密后
    // 明文损坏，即外部损坏且不会自愈，归档为历史并放行（identity-reconciliation.bin
    // 照常写入），不用它阻碍设备恢复接单；只有解密助手超时或不可用保持等待复查。
    let parsed: { instance_id?: unknown; schemaVersion?: unknown; supervision?: unknown }
    try { parsed = JSON.parse(marker) } catch { return this.retireExecution(digest) }
    if (!parsed || typeof parsed !== 'object' || typeof parsed.instance_id !== 'string' || !uuid.test(parsed.instance_id)
      || parsed.schemaVersion !== 2 || typeof parsed.supervision !== 'string') return this.retireExecution(digest)
    let proofText: string
    try { proofText = await dpapiUnprotect(parsed.supervision) }
    catch (error) {
      // 确定性拒绝（格式非法/DPAPI 拒绝/输出损坏）= 记录本身坏、永远不会核验
      // 通过 → 归档放行；只有助手超时或不可用保持临时（false），留给 10 秒复查。
      return error instanceof Error && ['DPAPI_INPUT_INVALID', 'DPAPI_DECRYPT_FAILED', 'DPAPI_OUTPUT_INVALID'].includes(error.message)
        ? this.retireExecution(digest) : false
    }
    let proof: Pick<Ledger, 'generation' | 'home' | 'bootId' | 'sessionId'> & { rebootOnly: boolean }
    try { proof = JSON.parse(proofText) } catch { return this.retireExecution(digest) }
    if (!proof || proof.generation !== parsed.instance_id || proof.home !== resolve(this.home) || typeof proof.bootId !== 'string'
      || !uuid.test(proof.bootId) || !Number.isSafeInteger(proof.sessionId) || proof.sessionId < 0
      || typeof proof.rebootOnly !== 'boolean') return this.retireExecution(digest)
    const ledger = await this.tryReadLedger(proof)
    if (proof.sessionId === await this.sessionId()) {
      // Caller holds the home lease. These Jobs cover managed Providers only; the
      // launcher never enables breakaway or Chrome retention. rebootOnly markers
      // predate the Job boundary for external executors and stay wait-only.
      if (proof.rebootOnly || !ledger) return false
      for (const job of ledger.jobs) {
        const result = await this.command(['terminate', '--job-name', job])
        if (result.activeProcesses !== 0) return false
      }
    } else {
      // Named Jobs are session-local; cross-session probes cannot see that
      // namespace, so the verdict relies on exit receipts or a confirmed session
      // exit. Temporary evidence outages stay unconfirmed (false) for recheck.
      const receipts = !proof.rebootOnly && !!ledger
        && (await Promise.all(ledger.jobs.map(job => this.stoppedReceipt(job, ledger.ownerPid)))).every(Boolean)
      if (!receipts) {
        let sessionAlive: boolean
        try { sessionAlive = await this.sessionHasProcesses(proof.sessionId) } catch { return false }
        if (sessionAlive) return false
      }
    }
    return this.retireExecution(digest, parsed.instance_id)
  }

  /** 旧执行已确认退出（或旧记录不可核实且不会自愈）：写入核对凭据，放行恢复。 */
  private async retireExecution(digest: string, instanceId?: string): Promise<true> {
    atomicWrite(join(this.home, 'identity-reconciliation.bin'), await dpapiProtect(JSON.stringify({ markerDigest: digest, ...(instanceId ? { instanceId } : {}) })))
    return true
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
