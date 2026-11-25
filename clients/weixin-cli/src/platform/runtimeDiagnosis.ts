import { fileURLToPath } from 'node:url'
import { z } from 'zod'
import { probeEnvironment, type EnvironmentProbe, type ProbeEnvironmentOptions } from './environment.js'
import { runPowerShellDriver, type RunPowerShellDriverFn } from './powershell.js'
import { CancelledError, CodedOperationError } from '../operations/types.js'

const DRIVER = fileURLToPath(new URL('../../../drivers/ps1/window-doctor.ps1', import.meta.url))
const windowSchema = z.object({
  window_state: z.enum(['foreground', 'background', 'minimized', 'hidden', 'no_main_window', 'ambiguous']),
  login_state: z.enum(['online', 'unknown']), restored: z.boolean(),
  error_code: z.enum(['WEIXIN_NOT_FOUND', 'WINDOW_AMBIGUOUS', 'UI_CHANGED', 'CONTENT_UNAVAILABLE', 'FOREGROUND_LOST', 'INTERNAL_ERROR']).optional(),
})
export type WindowState = z.infer<typeof windowSchema>['window_state'] | 'not_running' | 'unavailable' | 'unsupported'
const GUIDANCE: Record<WindowState, string> = {
  foreground: '微信窗口已在前台',
  background: '微信窗口在后台，需要时可运行 doctor --restore-window',
  minimized: '微信已最小化，可运行 doctor --restore-window',
  hidden: '微信主窗口已隐藏，可运行 doctor --restore-window 或点击微信托盘图标',
  no_main_window: '进程运行中，未识别到可核对主窗口，请手动打开并登录微信',
  ambiguous: '存在多个微信主窗口，请手动确认目标窗口后重试',
  not_running: '请启动微信并登录',
  unavailable: '窗口诊断未完成，请在微信聊天窗口打开后重试',
  unsupported: '桌面运行条件未满足，请在 Windows 交互会话中运行',
}

export interface RuntimeDiagnosis {
  environment: EnvironmentProbe
  window_state: WindowState
  login_state: 'online' | 'unknown'
  runtime_readiness: { ready: boolean; reason: string }
  guidance: string
  restored: boolean
  error_code?: string
}

export async function diagnoseWeixinRuntime(opts: {
  env?: ProbeEnvironmentOptions
  probeEnvironmentFn?: (opts: ProbeEnvironmentOptions) => Promise<EnvironmentProbe>
  runDriverFn?: RunPowerShellDriverFn
  restoreWindow?: boolean
  signal?: AbortSignal
} = {}): Promise<RuntimeDiagnosis> {
  const environment = await (opts.probeEnvironmentFn ?? probeEnvironment)(opts.env ?? {})
  if (opts.signal?.aborted) throw new CancelledError()
  const prerequisites = environment.platform_ok && environment.interactive_session === true && environment.powershell_available
  let state: WindowState = prerequisites ? 'not_running' : 'unsupported'
  let login: 'online' | 'unknown' = 'unknown'
  let restored = false
  let errorCode: string | undefined
  if (prerequisites && environment.weixin_running) {
    try {
      const result = windowSchema.parse(await (opts.runDriverFn ?? runPowerShellDriver)({
        script: DRIVER, ...(opts.restoreWindow ? { args: ['-RestoreWindow'] } : {}),
        useVisionProxy: false, timeoutMs: opts.restoreWindow ? 15_000 : 2500, signal: opts.signal,
      }))
      state = result.window_state
      login = result.login_state
      restored = result.restored
      errorCode = result.error_code
    } catch (error) {
      if (opts.signal?.aborted) throw new CancelledError()
      state = 'unavailable'
      errorCode = error instanceof CodedOperationError ? error.code : 'INTERNAL_ERROR'
    }
  }
  const visible = state === 'foreground' || state === 'background'
  const ready = prerequisites && login === 'online' && visible
  const guidance = errorCode === 'RESULT_TIMEOUT' ? '窗口诊断超时，请稍后重试'
    : errorCode === 'WEIXIN_NOT_FOUND' ? GUIDANCE.no_main_window
      : errorCode === 'WINDOW_AMBIGUOUS' ? GUIDANCE.ambiguous
        : visible && login === 'unknown' ? '已找到微信窗口，但无法读取原生会话列表（UIA）'
          : opts.restoreWindow && errorCode ? '微信窗口恢复失败，请手动打开微信聊天窗口后重试' : GUIDANCE[state]
  return {
    environment, window_state: state, login_state: login, restored, ...(errorCode ? { error_code: errorCode } : {}),
    runtime_readiness: { ready, reason: ready ? '已核实微信原生会话列表' : guidance },
    guidance,
  }
}
