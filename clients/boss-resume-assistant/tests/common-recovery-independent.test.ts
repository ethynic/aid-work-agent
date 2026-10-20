import assert from 'node:assert/strict'
import test from 'node:test'
import { z } from 'zod'
import { operationRecoveryFacts, operationRecoveryFailure, type PageExecution } from '../src/main/operations/pageOperation.js'
import { CodedOperationError, CancelledError } from '../src/main/operations/types.js'
import { TOOL_DEFS } from '../src/mcp/toolDefs.js'
import { validateVisualArgs } from '../src/main/boss/visualRecovery.js'
import { runBossOperation, type BossSession } from '../src/main/operations/bossContext.js'
import { mkdtemp, rm, unlink, writeFile } from 'node:fs/promises'
import { existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { createHash, randomUUID } from 'node:crypto'
import { FilterSetError } from '../src/main/boss/FilterSetter.js'
import { execPowerShellScript } from '../src/main/input/powerShell.js'

const execution: PageExecution = { attemptsUsed: 2, ordinaryRetriesUsed: 1,
  inputStatus: 'attempted', stage: 'postcheck', verification: { status: 'inconclusive', evidence: ['缺少业务证明'] } }
const id = '13be2993-72fd-4db9-8436-bb11db1920ae'

test('independent: a fourth readonly adapter produces canonical versioned facts without selector changes', () => {
  const facts = operationRecoveryFacts({ name: 'boss_independent_preview', operationClass: 'readonly',
    goal: '读取授权目标', expected: '目标内容完整', target: '原始候选人' }, execution) as Record<string, any>
  assert.equal(facts.schema_version, 1)
  assert.equal(facts.operation_class, 'readonly')
  assert.deepEqual(facts.operation, { name: 'boss_independent_preview', goal: '读取授权目标',
    expected: '目标内容完整', target_label: '原始候选人', result_kind: 'page_state' })
  assert.deepEqual(facts.execution, { stage: 'postcheck', input_status: 'attempted', attempts_used: 2, ordinary_retries_used: 1 })
  assert.deepEqual(facts.verification, { status: 'inconclusive', evidence: ['缺少业务证明'] })
})

test('independent: external attempted input stays unknown with partial results and never gains a legacy page claim', () => {
  const facts = operationRecoveryFacts({ name: 'boss_independent_send', operationClass: 'external_effect',
    goal: '发送授权内容', expected: '可信发送回执' }, execution)
  assert.throws(() => operationRecoveryFailure(new CodedOperationError('UI_CHANGED', '后态无法确认'), facts,
    { completed_items: ['previously-done'] }), (error: unknown) => {
    assert.ok(error instanceof CodedOperationError)
    assert.equal(error.code, 'EXECUTION_UNKNOWN')
    assert.deepEqual(error.data?.completed_items, ['previously-done'])
    assert.equal(error.data?.page_operation, undefined)
    const recovery = error.data?.operation_recovery as Record<string, any>
    assert.equal(recovery.operation_class, 'external_effect')
    assert.equal(recovery.operation.result_kind, 'business_result')
    assert.equal(recovery.execution.ordinary_retries_used, 1)
    assert.equal(recovery.verification.status, 'inconclusive')
    return true
  })
})

test('independent: cancellation and business preconditions preserve their errors without paid recovery facts', () => {
  const facts = operationRecoveryFacts({ name: 'boss_independent_preview', operationClass: 'readonly', goal: '读', expected: '完整' }, execution)
  for (const error of [new CancelledError(), new CodedOperationError('PAYWALL', '需权益'),
    new CodedOperationError('INVALID_ARGUMENT', '参数不合法')]) {
    assert.throws(() => operationRecoveryFailure(error, facts), candidate => candidate === error)
  }
})

test('independent: typed execution preserves the original error without legacy instructions to abandon recovery', () => {
  const facts = operationRecoveryFacts({ name: 'boss_filter', operationClass: 'page_state',
    goal: '设置原筛选', expected: '真实筛选数及选项一致' }, execution)
  const original = '确认按钮点击响应无法确认'
  assert.throws(() => operationRecoveryFailure(new FilterSetError(original), facts), (error: unknown) => {
    assert.ok(error instanceof CodedOperationError)
    assert.equal(error.message, original)
    const recovery = error.data?.operation_recovery as Record<string, any>
    assert.equal(recovery.error.summary, original)
    assert.equal(recovery.verification.status, 'inconclusive')
    return true
  })
})

test('independent: Runner page/window and retrieve-only parameters pass the actual MCP producer schema', () => {
  const tool = TOOL_DEFS.find(item => item.name === 'boss_overlay_inspect')!
  const schema = z.object(tool.zodShape).strict()
  const context = { purpose: 'diagnose', operation: { name: 'boss_independent_preview', result_kind: 'page_state' },
    execution: { attempts_used: 2 }, available_actions: ['dismiss_click', 'press_escape'] }
  for (const mode of ['page', 'window'] as const) {
    const args = { request_id: id, observation_mode: mode, visual_context: context }
    assert.equal(schema.safeParse(args).success, true)
    assert.equal(validateVisualArgs(args), null)
  }
  const retrieve = { request_id: id, retrieve_only: true }
  assert.equal(schema.safeParse(retrieve).success, true)
  assert.equal(validateVisualArgs(retrieve), null)
  assert.equal(schema.safeParse({ request_id: id, observation_mode: 'screen' }).success, false)
  assert.equal(schema.safeParse({ request_id: id, x: 23, y: 42 }).success, false)
})

test('independent: postcheck cannot request another input while preserving the original operation context', () => {
  const context = { purpose: 'verify', operation: { name: 'boss_independent_send', result_kind: 'page_state' },
    execution: { attempts_used: 1 }, available_actions: [] }
  assert.equal(validateVisualArgs({ request_id: id, visual_context: context }), null)
  assert.notEqual(validateVisualArgs({ request_id: id, visual_context: { ...context, available_actions: ['press_escape'] } }), null)
})

test('independent: public operation result binds the original target and rejects reselection before the body', async () => {
  const originalTarget = `independent-${randomUUID()}`
  let bodies = 0, closes = 0, target = originalTarget
  const factory = async () => ({ getTargetId: async () => target, close: async () => { closes++ } }) as BossSession
  const ctx = { signal: new AbortController().signal, progress: () => {}, cdpPort: 9333 }
  const body = async () => { bodies++; return { message: 'ok', data: {
    operation_recovery: { schema_version: 1 }, page_operation: { schema_version: 1 }, preserved: 'original' } } }
  const first = await runBossOperation({ kind: 'readonly', name: 'boss_independent_preview' }, ctx, factory, () => null, body)
  assert.equal(first.success, true)
  const ref = first.data.browser_target_ref as string
  assert.match(ref, /^[0-9a-f-]{36}$/)
  try {
    assert.equal((first.data.operation_recovery as Record<string, unknown>).browser_target_ref, ref)
    assert.equal((first.data.page_operation as Record<string, unknown>).browser_target_ref, ref)
    assert.equal(JSON.stringify(first.data).includes(target), false)
    const same = await runBossOperation({ kind: 'readonly', name: 'boss_independent_preview' },
      { ...ctx, browserTargetRef: ref }, factory, () => null, body)
    assert.equal(same.success, true)
    assert.equal(same.data.browser_target_ref, ref)
    target = 'independent-other-target'
    const changed = await runBossOperation({ kind: 'readonly', name: 'boss_independent_preview' },
      { ...ctx, browserTargetRef: ref }, factory, () => null, body)
    assert.equal(changed.code, 'UI_CHANGED')
    assert.equal(changed.data.input_status, 'not_started')
    assert.equal(bodies, 2)
    assert.equal(closes, 3)
  } finally {
    await unlink(join(tmpdir(), 'aid-boss-targets', `${ref}.json`))
    const index = createHash('sha256').update(`9333:${originalTarget}`).digest('hex')
    await unlink(join(tmpdir(), 'aid-boss-targets', `${index}.target`))
  }
})

test('independent: every MCP adapter consumes the same opaque target transport field without raw target selectors', () => {
  for (const tool of TOOL_DEFS) {
    assert.equal(tool.zodShape._boss_target_ref?.safeParse(id).success, true, tool.name)
    assert.equal(tool.zodShape._boss_target_ref?.safeParse('raw-cdp-target').success, false, tool.name)
  }
})

test('independent: bound input privately stages its extra native dependency and never falls back when it is missing',
  { skip: process.platform !== 'win32', timeout: 30_000 }, async () => {
  const directory = await mkdtemp(join(tmpdir(), 'boss-common-independent-'))
  assert.ok(directory.startsWith(join(tmpdir(), 'boss-common-independent-')))
  try {
    const script = join(directory, 'win-click.ps1')
    await writeFile(script, `param([int]$CdpPort=0)
$ErrorActionPreference='Stop'
@{port=$CdpPort; physical=[IO.File]::ReadAllText((Join-Path $PSScriptRoot 'BossPhysicalInput.cs')); window=[IO.File]::ReadAllText((Join-Path $PSScriptRoot 'BossWindowExperiment.cs')); stage=$PSScriptRoot} | ConvertTo-Json -Compress
`)
    await writeFile(join(directory, 'BossPhysicalInput.cs'), 'independent-physical-bytes')
    await assert.rejects(execPowerShellScript(script, ['-CdpPort', '9333'], { timeout: 10_000 }), /BOSS_POWERSHELL_PREPARE/)
    await writeFile(join(directory, 'BossWindowExperiment.cs'), 'independent-window-bytes')
    const result = JSON.parse((await execPowerShellScript(script, ['-CdpPort', '9333'], { timeout: 10_000 })).stdout)
    assert.equal(result.port, 9333)
    assert.equal(result.physical, 'independent-physical-bytes')
    assert.equal(result.window, 'independent-window-bytes')
    assert.notEqual(result.stage, directory)
    assert.equal(existsSync(result.stage), false)
  } finally { await rm(directory, { recursive: true, force: true }) }
})
