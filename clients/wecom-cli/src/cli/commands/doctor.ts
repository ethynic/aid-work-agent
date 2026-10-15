/**
 * CLI 子命令 doctor：只读环境检查。
 *
 * 用法：
 *   aid-wecom doctor [--json]
 *   aid-wecom doctor --localdb [--json]   追加 local-db 深检段（设计 §5）
 *
 * 门禁检查（任一失败退出码 1）：① win32 平台 ② 交互桌面会话（已登录未锁屏）
 * ③ PowerShell 可用 ④ artifact 目录可创建/可写（写探针文件后立即删除）。
 * 信息项（不影响退出码）：WXWork.exe 进程存在性。
 *
 * --localdb 深检退出码语义（诊断命令，设计 §2 双就绪独立报告）：
 * - 深检结论 db / ui 都算"成功完成检查"（doctor 的诊断目的已达成）→ 退出 0；
 *   结论 ui 时输出醒目提示（本地库未就绪，业务回退 UI 通道）。
 * - 退出 1 仅用于引导阶段失败（未安装/未登录/登录态无法确认）——检查本身没能完成。
 * - 基础门禁项属于 UI 通道就绪范畴：失败仍展示 ❌，但不改变 --localdb 的退出码
 *   （本地库就绪不继承交互桌面会话门禁，设计 §2）。
 *
 * 严格只读：不激活窗口、不发送按键、不改剪贴板、不打开任何企微页面。
 * （深检 keyscan 为只读内存观察：OpenProcess(PROCESS_VM_READ)，无注入/无写/无提权，§8）
 */
import {
  artifactDir,
  checkArtifactDirWritable,
  probeEnvironment,
  type ProbeEnvironmentOptions,
} from '../../platform/environment.js'
import {
  readLocalState,
  type LocaldbDoctorDeps,
  type LocaldbDoctorReport,
} from '../../platform/localdb/index.js'
import { runLocaldbDoctor } from '../../platform/localdb/doctorLocaldb.js'

export interface DoctorCheck {
  name: string
  ok: boolean
  /** gate=门禁（失败退出码 1）；info=仅展示 */
  severity: 'gate' | 'info'
  detail: string
}

export interface DoctorReport {
  success: boolean
  checks: DoctorCheck[]
  artifact_dir: string | null
  /** 基础运行条件；进程存在不等于已核验登录态。 */
  runtime_readiness: { ready: boolean; reason?: string }
}

export interface DoctorCommandOptions {
  json?: boolean
  /** 测试注入（模拟非 win32 / 无 PowerShell 等） */
  env?: ProbeEnvironmentOptions
  localAppData?: string
  /** 追加 local-db 深检段（--localdb；设计 §5） */
  localdb?: boolean
  /** 深检组件依赖注入（测试全 mock；CLI 缺省全真实现） */
  localdbDeps?: LocaldbDoctorDeps
  /** localdb-state.json 目录覆盖（state 缺失提示的判定；测试注入） */
  localdbStateDir?: string | null
}

export async function runDoctorChecks(opts: DoctorCommandOptions = {}): Promise<DoctorReport> {
  const env = await probeEnvironment(opts.env)
  const checks: DoctorCheck[] = []

  checks.push({
    name: 'Windows 平台（win32-x64）',
    ok: env.platform_ok,
    severity: 'gate',
    detail: env.platform_ok ? env.platform : `当前平台 ${env.platform}，仅支持 Windows`,
  })

  checks.push({
    name: '交互桌面会话（已登录未锁屏）',
    ok: env.interactive_session === true,
    severity: 'gate',
    detail:
      env.interactive_session === true
        ? `SESSIONNAME=${env.session_name}`
        : env.interactive_session === null
          ? '非 Windows 平台，无法判定'
          : '非交互会话（服务/计划任务上下文），无法操作企业微信前台',
  })

  checks.push({
    name: 'PowerShell 可用',
    ok: env.powershell_available,
    severity: 'gate',
    detail: env.powershell_path ?? '未在 PATH 找到 powershell.exe',
  })

  const dir = artifactDir(opts.localAppData ?? opts.env?.localAppData ?? process.env.LOCALAPPDATA)
  if (dir === null) {
    checks.push({
      name: 'artifact 目录可创建/可写',
      ok: false,
      severity: 'gate',
      detail: 'LOCALAPPDATA 未设置，无法定位 artifact 目录',
    })
  } else {
    const artifact = checkArtifactDirWritable(dir)
    checks.push({ name: 'artifact 目录可创建/可写', ok: artifact.ok, severity: 'gate', detail: artifact.detail })
  }

  checks.push({
    name: 'WXWork.exe 进程（信息项）',
    ok: true,
    severity: 'info',
    detail: env.wecom_running ? `运行中（${env.wecom_processes} 个进程）` : '未检测到进程（执行业务操作前请启动并登录企业微信）',
  })

  return {
    success: checks.every((c) => c.severity !== 'gate' || c.ok),
    checks,
    artifact_dir: dir,
    runtime_readiness: {
      ready: checks.every((c) => c.severity !== 'gate' || c.ok) && env.wecom_running,
      reason: !env.wecom_running ? '请启动企业微信并登录'
        : checks.some((c) => c.severity === 'gate' && !c.ok) ? '桌面运行条件未满足' : '基础条件已满足，请确认企业微信已登录',
    },
  }
}

/** --localdb 深检段的人读输出：步骤列表 + 双就绪块 + 模式结论 */
function printLocaldbReport(report: LocaldbDoctorReport): void {
  console.log('\n== localdb 深检 ==')
  for (const s of report.steps) {
    const mark = s.ok ? '✅' : '❌'
    const kind = !s.ok && s.kind ? `[${s.kind}] ` : ''
    console.log(`${mark} ${s.step}. ${s.name}${s.detail ? `：${kind}${s.detail}` : ''}（${s.elapsed_ms}ms）`)
  }
  console.log(
    `\n本地库就绪：${report.localdb_ready ? '✅（mode=db）' : `❌（mode=${report.mode_decision.mode}${report.mode_decision.kind ? `, kind=${report.mode_decision.kind}` : ''}）`}`,
  )
  const ui = report.ui_ready
  console.log(
    `UI 就绪：${ui.ready ? '✅' : '❌'}（平台 ${ui.platform_ok ? '✅' : '❌'} / 交互会话 ${ui.interactive_session === true ? '✅' : ui.interactive_session === null ? '—' : '❌'} / PowerShell ${ui.powershell_available ? '✅' : '❌'}）`,
  )
  console.log(
    `模式结论：mode=${report.mode_decision.mode}${report.mode_decision.kind ? ` kind=${report.mode_decision.kind}` : ''}，版本 ${report.mode_decision.version ?? '未知'}，${report.mode_decision.checked_at}`,
  )
  for (const g of report.guidance ?? []) console.log(`- ${g}`)
  if (!report.localdb_ready) {
    // 醒目提示：深检结论 ui 也算诊断完成（退出 0），但本地库未就绪必须显式可见
    console.log('\n⚠️ 本地库通道未就绪——业务读取将回退 UI 通道（详见上方结论与建议）')
  }
}

export async function doctorCommand(opts: DoctorCommandOptions): Promise<number> {
  const report = await runDoctorChecks(opts)
  const localdb = opts.localdb === true ? await runLocaldbDoctor({}, opts.localdbDeps) : null
  if (opts.json) {
    console.log(JSON.stringify(localdb !== null ? { ...report, localdb } : report, null, 2))
  } else {
    for (const c of report.checks) {
      const mark = c.severity === 'info' ? 'ℹ️' : c.ok ? '✅' : '❌'
      console.log(`${mark} ${c.name}${c.detail ? `：${c.detail}` : ''}`)
    }
    if (localdb !== null) printLocaldbReport(localdb)
    else if (readLocalState(opts.localdbStateDir) === null) {
      // state 文件缺失提示（§5：state 缺失时 CLI 建议运行 --localdb）
      console.log('\n提示：尚未生成本地库通道结论，可运行 aid-wecom doctor --localdb 启用并检查')
    }
  }
  if (localdb !== null) {
    // --localdb 退出码见文件头注释：引导阶段失败=1；深检结论 db/ui=0（诊断完成）
    if (!localdb.success) {
      if (!opts.json) console.log('\n深检未能完成（引导阶段未通过），请按建议处理后重试')
      return 1
    }
    return 0
  }
  if (!report.success) {
    if (!opts.json) console.log('\n存在失败项，请按提示修复后重试（doctor 为只读检查，未对系统做任何修改）')
    return 1
  }
  if (!opts.json) console.log('\n全部检查通过')
  return 0
}
