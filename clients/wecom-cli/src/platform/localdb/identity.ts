/** 当前操作主窗口的账号归属；只看直接文件句柄，不依赖日志或解密密钥。 */
import { CancelledError, CodedOperationError, type OpContext } from '../../operations/types.js'
import { probeLoginState, type LoginProbeResult } from './login.js'
import { locateDataDir, type DataDirInfo } from './datadir.js'
import { runLocaldbPy, type RunLocaldbPyOpts, type LocaldbPyResult } from './driver.js'
import { detectRunning, type RunningInfo } from './running.js'

export interface CurrentAccount {
  account: string
  windowPid: number
  dataDir: DataDirInfo
}

export interface AccountProbeDeps {
  probeLoginStateFn?: (signal: AbortSignal) => Promise<LoginProbeResult>
  runPyFn?: (args: string[], opts?: RunLocaldbPyOpts) => Promise<LocaldbPyResult>
  locateDataDirFn?: typeof locateDataDir
  detectRunningFn?: () => Promise<RunningInfo>
}

export function checkCancelled(signal: AbortSignal): void {
  if (signal.aborted) throw new CancelledError()
}

export async function probeCurrentAccount(ctx: OpContext, deps: AccountProbeDeps = {}): Promise<CurrentAccount> {
  checkCancelled(ctx.signal)
  const login = await (deps.probeLoginStateFn ?? (signal => probeLoginState({ signal })))(ctx.signal)
  checkCancelled(ctx.signal)
  if (login.login_state === 'offline') {
    const running = await (deps.detectRunningFn ?? detectRunning)()
    checkCancelled(ctx.signal)
    if (running.probe === 'ok' && !running.running) throw new CodedOperationError('WECOM_NOT_FOUND', '企业微信未运行，请启动并登录后重试')
  }
  if (login.login_state === 'need_login' || login.login_state === 'offline') {
    throw new CodedOperationError('NOT_LOGGED_IN', '请打开并登录企业微信主窗口后重试')
  }
  if (login.login_state !== 'online' || !Number.isInteger(login.window_pid) || !login.window_pid) {
    throw new CodedOperationError('IDENTITY_UNCONFIRMED', '无法唯一确认当前操作主窗口与账号')
  }
  const result = await (deps.runPyFn ?? runLocaldbPy)(['active-account', String(login.window_pid)], { signal: ctx.signal })
  checkCancelled(ctx.signal)
  const value = result.data
  if (!result.ok || value?.ok !== true || value.pid !== login.window_pid || typeof value.message_db !== 'string') {
    throw new CodedOperationError('IDENTITY_UNCONFIRMED', '无法确认当前主窗口进程使用的唯一消息库')
  }
  const dataDir = await (deps.locateDataDirFn ?? locateDataDir)({ activeAccount: { pid: login.window_pid, message_db: value.message_db } })
  checkCancelled(ctx.signal)
  if (!dataDir.user_id || !dataDir.dir || !dataDir.db_paths?.message ||
      !dataDir.identity.identity_confirmed || dataDir.identity.process_pid !== login.window_pid) {
    throw new CodedOperationError('IDENTITY_UNCONFIRMED', '当前账号消息库目录未确认')
  }
  return { account: dataDir.user_id, windowPid: login.window_pid, dataDir }
}
