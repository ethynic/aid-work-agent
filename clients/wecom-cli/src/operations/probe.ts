/**
 * wecom_probe operation（M1）：只读环境探测。
 *
 * 检查：win32 平台、交互桌面会话、PowerShell 可用性、WXWork.exe 进程存在性；
 * 进程在运行时再经 drivers/ps1/probe.ps1（EnumWindows/EnumChildWindows，只读枚举）
 * 解析主窗口 rect、登录态与当前内容页（内容子窗口类名编码页名）。
 *
 * 任何环境异常快速返回结构化结果；绝不激活窗口、发送输入或改剪贴板（effect 恒为 none）。
 *
 * 语义：非 win32 / 非交互会话是硬环境违规，返回 WINDOWS_REQUIRED /
 * INTERACTIVE_SESSION_REQUIRED 结构化失败；企微进程未运行不是失败，
 * 只体现在 data.wecom_running / data.login_state=offline（探测的职责是报告
 * 能力矩阵，不是替调用方决策）。
 */
import { fileURLToPath } from 'node:url'
import { probeEnvironment, type ProbeEnvironmentOptions } from '../platform/environment.js'
import { runPowerShellDriver, type RunPowerShellDriverFn } from '../platform/powershell.js'
import { hangUntilAbort, runWecomOperation } from './context.js'
import { CodedOperationError, type OperationResult, type OpContext, type WecomOperation } from './types.js'

/** dist/src/operations → 包根 drivers/ps1 */
const DRIVER_PATH = fileURLToPath(new URL('../../../drivers/ps1/probe.ps1', import.meta.url))

export interface WecomProbeArgs {
  /** 返回更多诊断字段（系统版本/Node 版本等），默认 false */
  verbose?: boolean
}

/** 测试 hook：AID_WECOM_TEST_HANG=1 时 probe 悬挂直到取消（仅用于并发/取消契约测试） */
const TEST_HANG_ENV = 'AID_WECOM_TEST_HANG'

/**
 * 测试 hook：AID_WECOM_TEST_DRIVER_STUB=1 时不启动真实 probe.ps1，
 * 返回固定的窗口/登录态桩数据（conformance 等测试不得触达真实企微窗口）。
 */
const TEST_DRIVER_STUB_ENV = 'AID_WECOM_TEST_DRIVER_STUB'

export function createWecomProbeOperation(
  deps: {
    probeEnvironmentFn?: (opts: ProbeEnvironmentOptions) => ReturnType<typeof probeEnvironment>
    runDriverFn?: RunPowerShellDriverFn
  } = {},
): WecomOperation<WecomProbeArgs> {
  const probeFn = deps.probeEnvironmentFn ?? probeEnvironment
  const runDriverFn = deps.runDriverFn ?? runPowerShellDriver
  return {
    name: 'wecom_probe',
    execute(args: WecomProbeArgs, ctx: OpContext): Promise<OperationResult> {
      return runWecomOperation(
        'readonly',
        ctx,
        () => {
          if (args === null || typeof args !== 'object' || Array.isArray(args)) {
            return '参数必须是对象（当前仅支持可选字段 verbose: boolean）'
          }
          if (args.verbose !== undefined && typeof args.verbose !== 'boolean') {
            return 'verbose 必须是布尔值'
          }
          return null
        },
        async () => {
          // 测试 hook 优先于真实探测：悬挂等待取消（conformance busyProbe 依赖）
          if (process.env[TEST_HANG_ENV] === '1') {
            ctx.progress({ stage: 'check', message: '测试 hook：悬挂等待取消' })
            await hangUntilAbort(ctx.signal)
          }
          const env = await probeFn({
            onStep: (step) => ctx.progress({ stage: 'check', message: step }),
          })
          if (!env.platform_ok) {
            throw new CodedOperationError(
              'WINDOWS_REQUIRED',
              `仅支持 Windows（win32-x64），当前平台：${env.platform}`,
            )
          }
          if (env.interactive_session === false) {
            throw new CodedOperationError(
              'INTERACTIVE_SESSION_REQUIRED',
              '需要已登录且未锁屏的交互桌面会话（当前为非交互会话，如服务/计划任务上下文）',
            )
          }
          const data: Record<string, unknown> = {
            platform: env.platform,
            interactive_session: env.interactive_session,
            session_name: env.session_name,
            powershell_available: env.powershell_available,
            powershell_path: env.powershell_path,
            wecom_running: env.wecom_running,
            wecom_processes: env.wecom_processes,
            login_state: 'offline',
            main_window: null,
            current_page: null,
          }
          // 进程在运行才做窗口级探测（probe.ps1 只读枚举，不激活窗口不发送输入）
          if (env.wecom_running && env.powershell_available) {
            ctx.progress({ stage: 'check', message: '解析企业微信主窗口与登录态' })
            const win =
              process.env[TEST_DRIVER_STUB_ENV] === '1'
                ? {
                    login_state: 'online',
                    main_window: { hwnd: 0, x: 0, y: 0, w: 1100, h: 750 },
                    current_page: '消息',
                  }
                : await runDriverFn({ script: DRIVER_PATH, signal: ctx.signal })
            data.login_state = win.login_state ?? 'offline'
            data.main_window = win.main_window ?? null
            data.current_page = win.current_page ?? null
          }
          if (args.verbose) {
            data.os_release = (await import('node:os')).release()
            data.node_version = process.version
          }
          const summary = !env.wecom_running
            ? `环境探测完成：未检测到 WXWork.exe 进程（login_state=offline），PowerShell ${env.powershell_available ? '可用' : '不可用'}`
            : `环境探测完成：WXWork.exe 运行中（${env.wecom_processes} 个进程），login_state=${data.login_state}，当前页=${data.current_page ?? '未知'}`
          return { message: summary, data }
        },
      )
    },
  }
}
