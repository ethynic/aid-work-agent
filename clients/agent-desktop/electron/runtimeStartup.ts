import { existsSync, mkdirSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import { randomUUID } from 'node:crypto'

export const RUNTIME_LOGIN_ARGUMENT = '--aidwork-runtime-login'

export function runtimeLoginLaunch(input: { kind: string; packaged: boolean; platform: string; smoke: boolean; argv: readonly string[] }): boolean {
  return input.kind === 'runtime' && input.packaged && input.platform === 'win32' && !input.smoke && input.argv.includes(RUNTIME_LOGIN_ARGUMENT)
}

export class RuntimeStartup {
  private readonly options: { path: string; args: string[]; name: string }
  constructor(private readonly login: {
    getLoginItemSettings(options: { path: string; args: string[] }): { openAtLogin: boolean; executableWillLaunchAtLogin?: boolean; launchItems?: { name: string; enabled: boolean }[] }
    setLoginItemSettings(options: { path: string; args: string[]; name: string; openAtLogin: boolean; enabled: boolean }): void
  }, private readonly userData: string, executable: string, appId: string) {
    this.options = { path: executable, args: [RUNTIME_LOGIN_ARGUMENT], name: appId }
  }
  enabled(): boolean {
    const state = this.login.getLoginItemSettings(this.options)
    // Electron 43 can omit args when comparing Windows Run entries. The exact
    // product registry name still identifies its enabled startup-approved item.
    if (state.launchItems) return state.launchItems.some(item => item.name === this.options.name && item.enabled)
    return state.openAtLogin && state.executableWillLaunchAtLogin !== false
  }
  initialize(): void {
    const marker = path.join(this.userData, 'login-startup-initialized')
    if (existsSync(marker)) return
    this.setEnabled(true)
    mkdirSync(this.userData, { recursive: true })
    writeFileSync(marker, '1\n', { flag: 'wx' })
  }
  setEnabled(enabled: boolean): void {
    this.login.setLoginItemSettings({ ...this.options, openAtLogin: enabled, enabled })
  }
}

/** Poll cadence while the Host confirms the last execution's exit. */
const RECONCILE_POLL_MS = 2000

/** A single login intent uses the normal Host port; never retries uncertain starts.
 * A reconciling Host confirms the old execution on its own; Main only waits for that
 * confirmation and then starts normally. Closing the app ends the wait (no restart). */
export async function resumeRuntimeAtLogin(request: (value: unknown) => Promise<{ code: number; result: unknown }>,
  wait: (ms?: number) => Promise<void> = (ms = 500) => new Promise(resolve => setTimeout(resolve, ms))): Promise<void> {
  let snapshot = await readState(request)
  while (snapshot.state === 'reconciling') {
    await wait(RECONCILE_POLL_MS)
    snapshot = await readState(request)
  }
  if (snapshot.state === 'blocked' || snapshot.connection === 'revoked') throw new Error('RuntimeLoginNeedsAttention')
  if (snapshot.state !== 'stopped' || !snapshot.device?.device_id || ['unpaired', 'revoked'].includes(snapshot.connection ?? '')) return
  const started = await request({ request_id: randomUUID(), method: 'start', params: { request_key: randomUUID() } })
  if (started.code !== 0) throw new Error('RuntimeLoginStartRejected')
  const operation = started.result as { operation_id?: string; status?: string } | null
  if (!operation?.operation_id) throw new Error('RuntimeLoginOperationInvalid')
  for (let attempt = 0; attempt < 60; attempt++) {
    const reply = await request({ request_id: randomUUID(), method: 'operations.get', params: { operation_id: operation.operation_id } })
    const result = reply.result as { status?: string; code?: number } | null
    if (reply.code !== 0 || !result || ['failed', 'reconciling'].includes(result.status ?? '')) throw new Error('RuntimeLoginStartFailed')
    if (result.status === 'succeeded' && result.code === 0) return
    if (result.status !== 'running') throw new Error('RuntimeLoginOperationInvalid')
    await wait()
  }
  throw new Error('RuntimeLoginStartUnconfirmed')
}

async function readState(request: (value: unknown) => Promise<{ code: number; result: unknown }>): Promise<{ state?: string; device?: { device_id?: string }; connection?: string }> {
  const state = await request({ request_id: randomUUID(), method: 'getState', params: {} })
  if (state.code !== 0) throw new Error('RuntimeLoginStateRejected')
  if (!state.result || typeof state.result !== 'object') throw new Error('RuntimeLoginStateInvalid')
  return state.result as { state?: string; device?: { device_id?: string }; connection?: string }
}
