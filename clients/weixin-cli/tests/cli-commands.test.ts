/**
 * CLI 命令端到端（spawn dist 产物）：doctor --json 结构化输出、probe 人类可读输出、
 * 未知/按对象命名子命令拒绝（命名铁律：对象只能是参数值，不能是子命令）。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { spawnSync } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const DIST_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const CLI = path.join(DIST_ROOT, 'src', 'cli', 'index.js')

function runCli(args: string[]): { status: number | null; stdout: string; stderr: string } {
  const r = spawnSync(process.execPath, [CLI, ...args], { encoding: 'utf8', timeout: 30000 })
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
  // Weixin.exe 进程为信息项，不影响退出码
  assert.ok(report.checks.some((c) => c.severity === 'info' && c.name.includes('Weixin.exe')))
  if (process.platform === 'win32') {
    assert.equal(report.success, true)
    assert.equal(status, 0)
    assert.ok(report.artifact_dir?.endsWith(path.join('AidWorkAgent', 'weixin-cli', 'artifacts')))
  } else {
    assert.equal(report.success, false)
    assert.equal(status, 1)
  }
})

test('doctor（人类可读）：本机 win32 全过，且为只读提示', () => {
  const { status, stdout } = runCli(['doctor'])
  assert.ok(stdout.includes('PowerShell'))
  if (process.platform === 'win32') {
    assert.equal(status, 0)
    assert.ok(stdout.includes('全部检查通过'))
  }
})

test('probe：人类可读输出，退出码 0（win32 交互会话）', () => {
  const { status, stdout } = runCli(['probe'])
  if (process.platform === 'win32') {
    assert.equal(status, 0)
    assert.ok(stdout.includes('环境探测完成'))
  }
})

test('未知子命令退出码 2；按对象命名的子命令（souyisou/article/chat）一律拒绝', () => {
  for (const bad of ['nope', 'souyisou', 'article', 'chat', 'contacts']) {
    const { status, stderr } = runCli([bad])
    assert.equal(status, 2, `子命令 ${bad} 应被拒绝`)
    assert.ok(stderr.includes('未知子命令'), `子命令 ${bad} 应提示未知子命令`)
  }
})

test('version --json：机器可读 manifest（含 schema_digest）', () => {
  const { status, stdout } = runCli(['version', '--json'])
  assert.equal(status, 0)
  const parsed = JSON.parse(stdout)
  assert.equal(parsed.provider_id, 'ai.aidwork.weixin')
  assert.match(parsed.schema_digest, /^sha256:[0-9a-f]{64}$/)
})

test('send --json：参数错误也输出 OperationResult JSON（AI 组合链路可解析），退出码 2', () => {
  // 缺 target_ref/text：operation 校验阶段即失败，不触达真实微信
  const { status, stdout } = runCli(['send', '--domain', 'chat', '--json'])
  assert.equal(status, 2)
  const parsed = JSON.parse(stdout.trim().split('\n').pop()!)
  assert.equal(parsed.success, false)
  assert.equal(parsed.code, 'INVALID_ARGUMENT')
  assert.equal(typeof parsed.run_id, 'string')
})
