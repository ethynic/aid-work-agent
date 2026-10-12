import { closeSync, existsSync, fsyncSync, mkdirSync, openSync, readFileSync, renameSync, rmSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import { randomUUID } from 'node:crypto'

export const RUNTIME_UPDATE_EXIT_ARGUMENT = '--aidwork-exit-for-update'
export function shouldResumeRuntime(input: { intent: boolean | undefined; recovered: boolean; login: boolean }): boolean {
  return input.intent !== false && (input.intent === true || input.recovered || input.login)
}
export function runtimeUpdateExit(input: { kind: string; packaged: boolean; platform: string; smoke: boolean; argv: readonly string[] }): boolean {
  return input.kind === 'runtime' && input.packaged && input.platform === 'win32' && !input.smoke && input.argv.some(arg => [RUNTIME_UPDATE_EXIT_ARGUMENT, '--aidwork-exit-for-uninstall'].includes(arg))
}
/** Product-scoped, CurrentUser-protected resume intent; never contains credentials. */
export class RuntimeUpdateIntent {
  private file: string
  private queue: Promise<void> = Promise.resolve()
  constructor(directory: string, private appId: string, private home: string, private protection: { protect(value: string): Promise<string>; unprotect(value: string): Promise<string> }) {
    this.file = path.join(directory, 'runtime-update-resume.bin')
  }
  private serialize<T>(work: () => Promise<T>): Promise<T> {
    const result = this.queue.then(work)
    this.queue = result.then(() => undefined, () => undefined)
    return result
  }
  save(resume: boolean): Promise<void> { return this.serialize(() => this.saveValue(resume)) }
  read(): Promise<boolean | undefined> { return this.serialize(() => this.readValue()) }
  clear(): Promise<void> { return this.serialize(async () => { rmSync(this.file, { force: true }) }) }
  private async saveValue(resume: boolean): Promise<void> {
    mkdirSync(path.dirname(this.file), { recursive: true })
    const temp = `${this.file}.${randomUUID()}.tmp`
    const protectedValue = await this.protection.protect(JSON.stringify({ schemaVersion: 1, appId: this.appId, home: this.home, resume, createdAt: Date.now() }))
    writeFileSync(temp, protectedValue, { flag: 'wx', mode: 0o600 })
    const fd = openSync(temp, 'r+'); try { fsyncSync(fd) } finally { closeSync(fd) }
    try { renameSync(temp, this.file) } finally { rmSync(temp, { force: true }) }
  }
  private async readValue(): Promise<boolean | undefined> {
    if (!existsSync(this.file)) return undefined
    const intent = JSON.parse(await this.protection.unprotect(readFileSync(this.file, 'utf8')))
    if (intent.schemaVersion !== 1 || intent.appId !== this.appId || intent.home !== this.home || typeof intent.resume !== 'boolean'
      || !Number.isSafeInteger(intent.createdAt) || intent.createdAt > Date.now() + 60000) throw new Error('RuntimeUpdateIntentInvalid')
    if (Date.now() - intent.createdAt > 7 * 86400000) {
      renameSync(this.file, `${this.file}.expired-${randomUUID()}`)
      return undefined
    }
    return intent.resume
  }
}
