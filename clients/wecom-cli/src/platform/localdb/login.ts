/**
 * 1.10 轻量登录探测原语（本地库路径专用；设计 §3.4 登录状态判定入口）。
 *
 * 复用 probe.ps1 的登录判定核心（窗口枚举：online=可见主窗口候选 / need_login=登录二维码窗 /
 * offline=进程或窗口皆无），由 drivers/ps1/localdb-login.ps1 自包含实现——**不做交互桌面
 * 会话门禁**（设计 §2：本地库就绪不继承 UI 门禁，避免把 doctor 的 INTERACTIVE_SESSION_
 * REQUIRED 硬门槛带回本地库路径；进程/会话预检归调用方）。与账号身份证据（datadir 的
 * Config.cfg/目录交叉核验）分开：身份、登录态、密钥有效性三个独立判断（设计 §3.4）。
 *
 * 只读保证：驱动只 EnumWindows/GetWindowRect 等纯读 API，不激活窗口、不发送输入、不截图。
 * best-effort：驱动失败（非零退出/无 DRIVER_JSON/业务 ok=false/超时/取消）一律归并
 * login_state='unknown'，绝不抛出——轻量探测的职责是报告，不是替调用方决策。
 *
 * 测试注入 runDriverFn 替身（三种状态 + 失败），零 PowerShell 依赖；真机冒烟见开发计划
 * 登记区（登录态下实跑一次）。
 */
import { fileURLToPath } from 'node:url'
import { runPowerShellDriver, type RunPowerShellDriverFn } from '../powershell.js'

/**
 * 驱动脚本路径：dist/src/platform/localdb → 包根 drivers/ps1（ps1 驱动不进 dist，
 * 按源码树定位，与 probe.ts DRIVER_PATH 同一约定；打包登记属组合任务，见开发计划）。
 */
const LOGIN_DRIVER_PATH = fileURLToPath(new URL('../../../../drivers/ps1/localdb-login.ps1', import.meta.url))

/** 登录态四值：online / need_login / offline 由驱动判定，unknown 为 TS 侧失败归并 */
export const LOCALDB_LOGIN_STATES = ['online', 'need_login', 'offline', 'unknown'] as const
export type LocaldbLoginState = (typeof LOCALDB_LOGIN_STATES)[number]

export interface LoginProbeResult {
  login_state: LocaldbLoginState
  /** 结果来源固定标记（区分未来可能的非驱动判定来源） */
  source: 'localdb-login-driver'
  /** 复用操作主窗口选择规则得到的 PID；歧义、未登录或驱动失败为空。 */
  window_pid: number | null
  window_error?: string
}

export interface ProbeLoginStateDeps {
  signal?: AbortSignal
  /** 驱动执行器注入（缺省 runPowerShellDriver；测试 mock，零 PowerShell 依赖） */
  runDriverFn?: RunPowerShellDriverFn
  /** 驱动脚本路径覆盖（测试用；缺省包根 drivers/ps1/localdb-login.ps1） */
  driverPath?: string
}

/**
 * 轻量登录探测：跑 localdb-login.ps1 → 三态归一。
 * 驱动按标准嵌套 data 协议输出 login_state（runPowerShellDriver 返回的就是 data）；
 * 顶层 login_state / 嵌套 data.login_state 两种形态都容忍（防御性，runner 替换时不断链）。
 * 驱动异常值/失败 → 'unknown'，不抛出。
 */
export async function probeLoginState(deps: ProbeLoginStateDeps = {}): Promise<LoginProbeResult> {
  const runDriverFn = deps.runDriverFn ?? runPowerShellDriver
  const driverPath = deps.driverPath ?? LOGIN_DRIVER_PATH
  let state: LocaldbLoginState = 'unknown'
  let windowPid: number | null = null
  let windowError: string | undefined
  try {
    const raw = await runDriverFn({ script: driverPath, signal: deps.signal })
    const nested = raw.data
    const value =
      typeof raw.login_state === 'string'
        ? raw.login_state
        : nested !== null && typeof nested === 'object' && typeof (nested as Record<string, unknown>).login_state === 'string'
          ? ((nested as Record<string, unknown>).login_state as string)
          : ''
    const payload = nested !== null && typeof nested === 'object' ? nested as Record<string, unknown> : raw
    if (value === 'online' && Number.isInteger(payload.window_pid) && Number(payload.window_pid) > 0) {
      windowPid = Number(payload.window_pid)
    }
    if (payload.window_error === 'WINDOW_AMBIGUOUS') {
      windowError = 'WINDOW_AMBIGUOUS'
      windowPid = null
    }
    if ((LOCALDB_LOGIN_STATES as readonly string[]).includes(value)) {
      state = value as LocaldbLoginState
    }
  } catch {
    // 驱动失败（CodedOperationError / CancelledError / spawn 失败等）→ unknown，不抛
  }
  return { login_state: state, source: 'localdb-login-driver', window_pid: windowPid, ...(windowError ? { window_error: windowError } : {}) }
}
