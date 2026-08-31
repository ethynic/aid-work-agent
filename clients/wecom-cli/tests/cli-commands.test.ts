/**
 * CLI 命令端到端（spawn dist 产物）：doctor/version 结构化输出、未知子命令拒绝、
 * add-customer 缺 --yes 参数错误、probe 驱动桩（AID_WECOM_TEST_DRIVER_STUB=1）。
 *
 * 铁律：本文件绝不触达真实 drivers/ps1 脚本——probe 一律走驱动桩
 * （进程在运行时也不启动 probe.ps1）；add-customer 在参数校验阶段即失败，不 spawn 驱动。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { spawnSync } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const DIST_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const CLI = path.join(DIST_ROOT, 'src', 'cli', 'index.js')

function runCli(args: string[], extraEnv: Record<string, string> = {}): { status: number | null; stdout: string; stderr: string } {
  const r = spawnSync(process.execPath, [CLI, ...args], {
    encoding: 'utf8',
    timeout: 30000,
    env: { ...process.env, ...extraEnv },
  })
  return { status: r.status, stdout: r.stdout ?? '', stderr: r.stderr ?? '' }
}

test('doctor --json：结构化输出（success/checks/artifact_dir），退出码与 success 一致', () => {
  const { status, stdout } = runCli(['doctor', '--json'])
  const report = JSON.parse(stdout) as {
    success: boolean
    checks: Array<{ name: string; ok: boolean; severity: 'gate' | 'info'; detail: string }>
    artifact_dir: string | null
  }
  assert.equal(typeof report.success, 'boolean')
  assert.ok(Array.isArray(report.checks) && report.checks.length >= 4)
  // 门禁项齐备：平台 / 交互会话 / PowerShell / artifact 目录
  const gateNames = report.checks.filter((c) => c.severity === 'gate').map((c) => c.name)
  assert.ok(gateNames.some((n) => n.includes('Windows')))
  assert.ok(gateNames.some((n) => n.includes('交互')))
  assert.ok(gateNames.some((n) => n.includes('PowerShell')))
  assert.ok(gateNames.some((n) => n.includes('artifact')))
  // WXWork.exe 进程为信息项，不影响退出码
  assert.ok(report.checks.some((c) => c.severity === 'info' && c.name.includes('WXWork.exe')))
  if (process.platform === 'win32') {
    assert.equal(report.success, true)
    assert.equal(status, 0)
    assert.ok(report.artifact_dir?.endsWith(path.join('AidWorkAgent', 'wecom-cli', 'artifacts')))
  } else {
    assert.equal(report.success, false)
    assert.equal(status, 1)
  }
})

test('probe --json（驱动桩）：结构化 OperationResult，退出码 0（win32 交互会话）', () => {
  const { status, stdout } = runCli(['probe', '--json'], { AID_WECOM_TEST_DRIVER_STUB: '1' })
  const parsed = JSON.parse(stdout.trim().split('\n').pop()!)
  assert.equal(typeof parsed.success, 'boolean')
  assert.equal(typeof parsed.run_id, 'string')
  if (process.platform === 'win32') {
    assert.equal(status, 0)
    assert.equal(parsed.success, true)
    assert.equal(parsed.code, 'OK')
    assert.equal(parsed.effect, 'none')
  } else {
    assert.equal(parsed.code, 'WINDOWS_REQUIRED')
    assert.equal(status, 1)
  }
})

test('未知子命令退出码 2；按对象命名的子命令（contacts/customer）一律拒绝', () => {
  for (const bad of ['nope', 'contacts', 'customer', 'message']) {
    const { status, stderr } = runCli([bad])
    assert.equal(status, 2, `子命令 ${bad} 应被拒绝`)
    assert.ok(stderr.includes('未知子命令'), `子命令 ${bad} 应提示未知子命令`)
  }
})

test('version --json：机器可读 manifest（含 schema_digest）', () => {
  const { status, stdout } = runCli(['version', '--json'])
  assert.equal(status, 0)
  const parsed = JSON.parse(stdout)
  assert.equal(parsed.provider_id, 'ai.aidwork.wecom')
  assert.match(parsed.schema_digest, /^sha256:[0-9a-f]{64}$/)
})

test('add-customer --json：缺 --yes → 参数错误 OperationResult JSON，退出码 2，不触达企微', () => {
  const { status, stdout } = runCli(['add-customer', '--phone', '13800138000', '--json'])
  assert.equal(status, 2)
  const parsed = JSON.parse(stdout.trim().split('\n').pop()!)
  assert.equal(parsed.success, false)
  assert.equal(parsed.code, 'INVALID_ARGUMENT')
  assert.equal(parsed.effect, 'none')
  assert.equal(typeof parsed.run_id, 'string')
})

test('add-customer --json：非法手机号 → INVALID_ARGUMENT，退出码 2', () => {
  const { status, stdout } = runCli(['add-customer', '--phone', '123', '--yes', '--json'])
  assert.equal(status, 2)
  const parsed = JSON.parse(stdout.trim().split('\n').pop()!)
  assert.equal(parsed.code, 'INVALID_ARGUMENT')
})

test('search --json：缺 --query → INVALID_ARGUMENT，退出码 2，不触达企微', () => {
  const { status, stdout } = runCli(['search', '--json'])
  assert.equal(status, 2)
  const parsed = JSON.parse(stdout.trim().split('\n').pop()!)
  assert.equal(parsed.success, false)
  assert.equal(parsed.code, 'INVALID_ARGUMENT')
  assert.equal(parsed.effect, 'none')
})

test('send --json：缺 --target-ref/--text → INVALID_ARGUMENT，退出码 2，不触达企微', () => {
  const { status, stdout } = runCli(['send', '--json'])
  assert.equal(status, 2)
  const parsed = JSON.parse(stdout.trim().split('\n').pop()!)
  assert.equal(parsed.success, false)
  assert.equal(parsed.code, 'INVALID_ARGUMENT')
})

test('send --json：篡改 target_ref → INVALID_ARGUMENT（签名校验失败），退出码 2，不调驱动', () => {
  const { status, stdout } = runCli(['send', '--target-ref', 'aGVsbG8.dGFtcGVyZWQ', '--text', 'hi', '--json'])
  assert.equal(status, 2)
  const parsed = JSON.parse(stdout.trim().split('\n').pop()!)
  assert.equal(parsed.code, 'INVALID_ARGUMENT')
})

test('read --json：缺 --target-ref → INVALID_ARGUMENT，退出码 2，不触达企微', () => {
  const { status, stdout } = runCli(['read', '--json'])
  assert.equal(status, 2)
  const parsed = JSON.parse(stdout.trim().split('\n').pop()!)
  assert.equal(parsed.success, false)
  assert.equal(parsed.code, 'INVALID_ARGUMENT')
  assert.equal(parsed.effect, 'none')
})

test('read --json：非法 --max-pages（NaN / 超上限）→ INVALID_ARGUMENT，退出码 2，不触达企微', () => {
  for (const bad of ['abc', '0', '11']) {
    const { status, stdout } = runCli(['read', '--target-ref', 'x', '--max-pages', bad, '--json'])
    assert.equal(status, 2, `--max-pages ${bad} 应被拒绝`)
    const parsed = JSON.parse(stdout.trim().split('\n').pop()!)
    assert.equal(parsed.code, 'INVALID_ARGUMENT')
  }
})

test('watch：非法 --interval → 退出码 2，不触达企微', () => {
  for (const bad of ['abc', '0', '-1']) {
    const { status, stderr } = runCli(['watch', '--interval', bad])
    assert.equal(status, 2, `--interval ${bad} 应被拒绝`)
    assert.ok(stderr.includes('--interval'))
  }
})
