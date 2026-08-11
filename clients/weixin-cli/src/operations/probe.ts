/**
 * weixin_probe operation（设计 §5.1，M1 唯一 tool）：只读环境探测。
 *
 * 检查：win32 平台、交互桌面会话、PowerShell 可用性、Weixin.exe 进程存在性。
 * 任何环境快速返回结构化结果；绝不激活窗口、发送输入或改剪贴板（effect 恒为 none）。
 *
 * 语义：非 win32 / 非交互会话是硬环境违规，返回 WINDOWS_REQUIRED /
 * INTERACTIVE_SESSION_REQUIRED 结构化失败；微信进程未运行不是失败，
 * 只体现在 data.weixin_running（探测的职责是报告能力矩阵，不是替调用方决策）。
 */
import { probeEnvironment, type ProbeEnvironmentOptions } from '../platform/environment.js'
import { hangUntilAbort, runWeixinOperation } from './context.js'
import { CodedOperationError, type OperationResult, type OpContext, type WeixinOperation } from './types.js'

export interface WeixinProbeArgs {
  /** 返回更多诊断字段（系统版本/Node 版本等），默认 false */
  verbose?: boolean
}

/** 测试 hook：AID_WEIXIN_TEST_HANG=1 时 probe 悬挂直到取消（仅用于并发/取消契约测试） */
const TEST_HANG_ENV = 'AID_WEIXIN_TEST_HANG'

export function createWeixinProbeOperation(
  deps: { probeEnvironmentFn?: (opts: ProbeEnvironmentOptions) => ReturnType<typeof probeEnvironment> } = {},
): WeixinOperation<WeixinProbeArgs> {
  const probeFn = deps.probeEnvironmentFn ?? probeEnvironment
  return {
    name: 'weixin_probe',
    execute(args: WeixinProbeArgs, ctx: OpContext): Promise<OperationResult> {
      return runWeixinOperation(
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
            weixin_running: env.weixin_running,
            weixin_processes: env.weixin_processes,
          }
          if (args.verbose) {
            data.os_release = (await import('node:os')).release()
            data.node_version = process.version
          }
          const summary = env.weixin_running
            ? `环境探测完成：Weixin.exe 运行中（${env.weixin_processes} 个进程），PowerShell ${env.powershell_available ? '可用' : '不可用'}`
            : `环境探测完成：未检测到 Weixin.exe 进程，PowerShell ${env.powershell_available ? '可用' : '不可用'}`
          return { message: summary, data }
        },
      )
    },
  }
}
