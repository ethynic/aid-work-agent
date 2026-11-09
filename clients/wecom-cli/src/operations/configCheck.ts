/**
 * 受信配置检查工具（代理任务 P2；P1 契约 §2）。
 *
 * - wecom_config_doctor：与 `aid-wecom doctor --localdb` 同逻辑（复用 runDoctorChecks +
 *   runLocaldbDoctor），只读诊断，业务报告进 data；self_user_id 探测可确认时附带当前登录
 *   账号，不可确认时字段缺省、工具不报错（doctor 是诊断，就绪判定由服务端按契约规则消费）。
 * - wecom_config_calibrate：与 `aid-wecom calibrate` 同逻辑（身份预核 + PowerShell 双点
 *   标定，写类效果与独占语义沿 runWecomOperation('write')），驱动原始结果中的本机
 *   cache_path 仅存本机诊断，不进入 data（P1 契约：云端不读）。
 *
 * 两者均不加入 Agent 模型工具集（沿 wecom_session_directory 页面专用先例：在 MCP
 * listTools 中暴露，是否受信/上报由 host core 决定）；独占运行由 MCP server 的
 * runExclusive（进程单飞 + 命名管道互斥）保证。
 */
import { mkdirSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { runWecomOperation, type CompletedTracker } from './context.js'
import { CodedOperationError, type OpContext, type WecomOperation } from './types.js'
import { runDoctorChecks, type DoctorCommandOptions } from '../cli/commands/doctor.js'
import { runLocaldbDoctor } from '../platform/localdb/doctorLocaldb.js'
import { probeCurrentAccount, type CurrentAccount } from '../platform/localdb/identity.js'
import { withSendSnapshot } from '../platform/localdb/sendVerification.js'
import { runPowerShellDriver, type RunPowerShellDriverFn } from '../platform/powershell.js'
import { artifactDir, type ProbeEnvironmentOptions } from '../platform/environment.js'

/** dist/src/operations → 包根 drivers/ps1（与 probe.ts 同级定位） */
export const CALIBRATE_DRIVER_PATH = fileURLToPath(new URL('../../../drivers/ps1/point-calibrate.ps1', import.meta.url))

export interface WecomConfigDoctorDeps {
  /** 基础门禁检查注入（沿 DoctorCommandOptions.env；测试模拟非 win32/无 PowerShell） */
  env?: ProbeEnvironmentOptions
  /** artifact 目录定位覆盖（沿 DoctorCommandOptions.localAppData；测试注入临时目录） */
  localAppData?: string
  /** 深检组件依赖注入（沿 DoctorCommandOptions.localdbDeps；测试全 mock） */
  localdbDeps?: DoctorCommandOptions['localdbDeps']
  /** 当前账号探测（缺省 probeCurrentAccount；测试注入） */
  probeAccountFn?: (ctx: OpContext) => Promise<CurrentAccount>
}

export function createWecomConfigDoctorOperation(deps: WecomConfigDoctorDeps = {}): WecomOperation<Record<string, never>> {
  return {
    name: 'wecom_config_doctor',
    execute: (_args, ctx) =>
      runWecomOperation('readonly', ctx, () => null, async (opCtx) => {
        opCtx.progress({ stage: 'check', message: '执行基础环境门禁检查' })
        const report = await runDoctorChecks({
          env: deps.env,
          localAppData: deps.localAppData,
          localdbDeps: deps.localdbDeps,
        })
        opCtx.progress({ stage: 'check', message: '执行本地库深检' })
        const localdb = await runLocaldbDoctor({}, deps.localdbDeps)
        const data: Record<string, unknown> = {
          schema_version: 1,
          success: report.success,
          checks: report.checks,
          runtime_readiness: report.runtime_readiness,
          localdb,
        }
        // 身份探测失败不吞诊断：self_user_id 缺省即可，工具不因身份未确认而报错
        try {
          const current = await (deps.probeAccountFn ?? probeCurrentAccount)(opCtx)
          if (current.account) data.self_user_id = current.account
        } catch {
          // 就绪结论已由 checks/localdb 承载；身份未确认只体现为 self_user_id 缺省
        }
        const summary = !localdb.success
          ? '本地库深检未能完成（引导阶段未通过），请按 localdb.guidance 处理后重试'
          : localdb.localdb_ready
            ? '配置检查完成：本地库通道就绪（mode=db）'
            : '配置检查完成：本地库通道未就绪（mode=ui），业务将回退 UI 通道'
        return { message: summary, data }
      }),
  }
}

export interface WecomConfigCalibrateDeps {
  /** 当前账号探测（缺省 probeCurrentAccount；测试注入） */
  probeAccountFn?: (ctx: OpContext) => Promise<CurrentAccount>
  /** 身份预核（缺省 withSendSnapshot 校验本人显示名），返回本人显示名；测试注入 */
  verifyIdentityFn?: (current: CurrentAccount, ctx: OpContext) => Promise<string>
  /** 标定驱动执行（缺省 runPowerShellDriver；测试注入桩数据） */
  runDriverFn?: RunPowerShellDriverFn
  /** 标定产物目录准备（缺省 artifactDir() 下按时间戳建目录；测试注入临时目录，返回 null=不可定位） */
  prepareArtifactDirFn?: () => string | null
}

/** 默认身份预核：本地库 context 查询确认本人显示名（与 aid-wecom calibrate 同实现） */
async function defaultVerifyIdentity(current: CurrentAccount, ctx: OpContext): Promise<string> {
  let selfName = ''
  await withSendSnapshot(current, ctx, {}, 'send-baseline', async (query) => {
    const meta = await query(['--task', 'context', '--account', current.account])
    if (meta.self_user_id !== current.account || typeof meta.self_name !== 'string' || !meta.self_name.trim()) {
      throw new CodedOperationError('IDENTITY_UNCONFIRMED', '本地库本人显示名未确认，未执行坐标标定')
    }
    selfName = meta.self_name as string
  })
  return selfName
}

/** 默认产物目录：%LOCALAPPDATA%\AidWorkAgent\wecom-cli\artifacts\calibrate-<ts>-<pid> */
function defaultPrepareArtifactDir(): string | null {
  const root = artifactDir()
  if (!root) return null
  const dir = join(root, `calibrate-${Date.now()}-${process.pid}`)
  mkdirSync(dir, { recursive: true })
  return dir
}

export function createWecomConfigCalibrateOperation(
  deps: WecomConfigCalibrateDeps = {},
): WecomOperation<Record<string, never>> {
  return {
    name: 'wecom_config_calibrate',
    execute: (_args, ctx) =>
      runWecomOperation('write', ctx, () => null, async (opCtx, tracker: CompletedTracker) => {
        const current = await (deps.probeAccountFn ?? probeCurrentAccount)(opCtx)
        const selfName = await (deps.verifyIdentityFn ?? defaultVerifyIdentity)(current, opCtx)
        const dir = (deps.prepareArtifactDirFn ?? defaultPrepareArtifactDir)()
        if (!dir) throw new CodedOperationError('INTERNAL_ERROR', '无法定位标定记录目录', 'none')
        const raw = await (deps.runDriverFn ?? runPowerShellDriver)({
          script: CALIBRATE_DRIVER_PATH,
          args: ['-SelfName', selfName, '-ArtifactDir', dir],
          signal: opCtx.signal,
        })
        tracker.completed = 1
        // 契约 §2.2：白名单组装 data——驱动原始结果中的 cache_path 等本机诊断不进入返回
        const data: Record<string, unknown> = {
          schema_version: 1,
          self_user_id: current.account,
          title_point: raw.title_point,
          input_point: raw.input_point,
          width: raw.width,
          height: raw.height,
          dpi: raw.dpi,
        }
        return { message: '企微标题与输入区双点标定完成', data }
      }),
  }
}
