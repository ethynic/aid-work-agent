/**
 * CLI 子命令 doctor：只读环境检查。
 *
 * 用法：
 *   aid-wecom doctor [--json]
 *
 * 门禁检查（任一失败退出码 1）：① win32 平台 ② 交互桌面会话（已登录未锁屏）
 * ③ PowerShell 可用 ④ artifact 目录可创建/可写（写探针文件后立即删除）。
 * 信息项（不影响退出码）：WXWork.exe 进程存在性。
 *
 * 严格只读：不激活窗口、不发送按键、不改剪贴板、不打开任何企微页面。
 */
import {
  artifactDir,
  checkArtifactDirWritable,
  probeEnvironment,
  type ProbeEnvironmentOptions,
} from '../../platform/environment.js'

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
}

export interface DoctorCommandOptions {
  json?: boolean
  /** 测试注入（模拟非 win32 / 无 PowerShell 等） */
  env?: ProbeEnvironmentOptions
  localAppData?: string
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
  }
}

export async function doctorCommand(opts: DoctorCommandOptions): Promise<number> {
  const report = await runDoctorChecks(opts)
  if (opts.json) {
    console.log(JSON.stringify(report, null, 2))
    return report.success ? 0 : 1
  }
  for (const c of report.checks) {
    const mark = c.severity === 'info' ? 'ℹ️' : c.ok ? '✅' : '❌'
    console.log(`${mark} ${c.name}${c.detail ? `：${c.detail}` : ''}`)
  }
  if (!report.success) {
    console.log('\n存在失败项，请按提示修复后重试（doctor 为只读检查，未对系统做任何修改）')
    return 1
  }
  console.log('\n全部检查通过')
  return 0
}
