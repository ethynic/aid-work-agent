import { existsSync, mkdirSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import { randomUUID } from 'node:crypto'
import type { RuntimeLoginDiagnosticEvent } from '@aid/local-tool-host-core/legacy/logDiagnostics'

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

export class RuntimeStartupFailure extends Error {
  constructor(readonly diagnosticEvent: RuntimeLoginDiagnosticEvent, message: string) { super(message) }
}

type StartupRequest = (value: unknown) => Promise<{ code: number; result: unknown }>

async function requestStartupStage(request: StartupRequest, method: 'getState' | 'start' | 'operations.get',
  params: Record<string, string>, event: RuntimeLoginDiagnosticEvent, message: string) {
  let reply: Awaited<ReturnType<StartupRequest>>
  try { reply = await request({ request_id: randomUUID(), method, params }) } catch (error) {
    if (error instanceof RuntimeStartupFailure) throw error
    throw new RuntimeStartupFailure(event, message)
  }
  if (reply.code !== 0) throw new RuntimeStartupFailure(event, message)
  return reply
}

export function readRuntimeStartupState(request: StartupRequest) {
  return requestStartupStage(request, 'getState', {}, 'runtime-login-state-read-failed', 'RuntimeLoginStateRejected')
}

/** A single login intent uses the normal Host port; never retries uncertain starts. */
export async function resumeRuntimeAtLogin(request: StartupRequest, wait: () => Promise<void> = () => new Promise(resolve => setTimeout(resolve, 500))): Promise<void> {
  const state = await readRuntimeStartupState(request)
  if (!state.result || typeof state.result !== 'object') throw new RuntimeStartupFailure('runtime-login-state-read-failed', 'RuntimeLoginStateInvalid')
  const snapshot = state.result as { state?: string; device?: { device_id?: string }; connection?: string }
  if (snapshot.state === 'reconciling') throw new RuntimeStartupFailure('runtime-login-stop-proof-pending', 'RuntimeLoginNeedsAttention')
  if (snapshot.state === 'blocked') throw new RuntimeStartupFailure('runtime-login-host-blocked', 'RuntimeLoginNeedsAttention')
  if (snapshot.connection === 'revoked') throw new RuntimeStartupFailure('runtime-login-device-revoked', 'RuntimeLoginNeedsAttention')
  if (snapshot.state !== 'stopped' || !snapshot.device?.device_id || snapshot.connection === 'unpaired') return
  const started = await requestStartupStage(request, 'start', { request_key: randomUUID() }, 'runtime-login-start-request-failed', 'RuntimeLoginStartRejected')
  const operation = started.result as { operation_id?: string; status?: string } | null
  if (!operation?.operation_id) throw new RuntimeStartupFailure('runtime-login-start-confirmation-failed', 'RuntimeLoginOperationInvalid')
  for (let attempt = 0; attempt < 60; attempt++) {
    const reply = await requestStartupStage(request, 'operations.get', { operation_id: operation.operation_id }, 'runtime-login-start-confirmation-failed', 'RuntimeLoginStartFailed')
    const result = reply.result as { status?: string; code?: number } | null
    if (!result || ['failed', 'reconciling'].includes(result.status ?? '')) throw new RuntimeStartupFailure('runtime-login-start-confirmation-failed', 'RuntimeLoginStartFailed')
    if (result.status === 'succeeded' && result.code === 0) return
    if (result.status !== 'running') throw new RuntimeStartupFailure('runtime-login-start-confirmation-failed', 'RuntimeLoginOperationInvalid')
    await wait()
  }
  throw new RuntimeStartupFailure('runtime-login-start-unconfirmed', 'RuntimeLoginStartUnconfirmed')
}
