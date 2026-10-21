/**
 * Phase 7 独立回归（测试工程师补充，不与开发自测文件混放）：
 * 1) ProgressForwarder 语义推进的字段级判定：message 不变但 current/total 数值推进
 *    仍是业务语义进展（原文上报 + 复位无进展时钟）——开发用例只覆盖 message 整句替换。
 * 2) log.ts 写失败告警的限频语义：5 分钟窗口结束后新失败会再次告警（窗口限频，
 *    不是永久静默）——开发用例只验证窗口内不重复。用 Date.now patch 推进时钟。
 * node:test 每文件独立子进程，模块级状态不跨文件污染。
 */
import assert from 'node:assert/strict'
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { randomUUID } from 'node:crypto'
import { test } from 'node:test'
import { runInvocation } from '../src/invocationRunner.js'
import type { RunnerDeps } from '../src/invocationRunner.js'
import type { ApiClient, ClaimedInvocation, InvocationResultPayload } from '../src/apiClient.js'
import type { SkillRunnerHandler } from '../src/skillRunner.js'
import { ProviderSet } from '../src/providerManager.js'
import { deriveResourceKey } from '../src/desktopLock.js'

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms))

type ProviderProgress = { message?: string; progress?: number; total?: number }

function makeHarness(
  script: (onProgress: (p: ProviderProgress) => void) => Promise<void>,
  opts: { noProgressWarnMs?: number } = {},
): { deps: RunnerDeps; progressPosts: Array<Record<string, unknown>>; events: string[]; results: unknown[] } {
  const progressPosts: Array<Record<string, unknown>> = []
  const events: string[] = []
  const results: unknown[] = []
  const api = {
    started: async () => ({}),
    progress: async (_id: string, payload: Record<string, unknown>) => {
      progressPosts.push({ ...payload })
      return { seq: progressPosts.length, cancel: false }
    },
    result: async (id: string, payload: InvocationResultPayload) => {
      results.push({ id, payload })
    },
    operationResult: async () => ({}),
  } as unknown as ApiClient
  const skillRunner = {
    callTool: async (_t: string, _a: Record<string, unknown>, callOpts: { onProgress?: (p: ProviderProgress) => void }) => {
      await script((p) => callOpts.onProgress?.(p))
      return { success: true, effect: 'none', data: { ok: 1 } }
    },
  } as unknown as SkillRunnerHandler
  const deps: RunnerDeps = {
    api,
    providers: new ProviderSet({}),
    skillRunner,
    desktopCheck: async () => true,
    desktopResourceKey: deriveResourceKey(randomUUID()),
    retryBaseMs: 10,
    retryMaxMs: 50,
    runtimeDataDir: mkdtempSync(path.join(os.tmpdir(), 'aidwork-p7-ind-')),
    progressIntervalMs: 40,
    noProgressWarnMs: opts.noProgressWarnMs ?? 1_000,
    onEvent: (msg) => events.push(msg),
  }
  return { deps, progressPosts, events, results }
}

function skillInvocation(): ClaimedInvocation {
  return {
    invocation_id: `inv-p7-ind-${randomUUID().slice(0, 8)}`,
    tool_name: 'skill_script_run',
    arguments: { skill: 'demo', entry: 'main.js', exec_hash: 'abc' },
    claim_token: 'tok-p7-ind',
    provider: 'skill-runner',
  }
}

test('current/total 数值推进（文案相同）算语义进展：原文上报且复位无进展时钟，全程无误告警', async () => {
  // 时间线：0ms(m=1) → 600ms(m=2) → 1400ms(m=3) → 1600ms 结束。
  // 数值推进若算语义进展：最后语义时刻 1400ms，结束前无进展仅 ~200ms < 1000ms → 不告警。
  // 若回归（数值推进不算）：最后语义时刻 0ms，1000ms 后心跳即告警——本用例即失败。
  const { deps, progressPosts, events, results } = makeHarness(async (onProgress) => {
    onProgress({ message: 'stageA 固定文案', progress: 1, total: 4 })
    await sleep(600)
    onProgress({ message: 'stageA 固定文案', progress: 2, total: 4 })
    await sleep(800)
    onProgress({ message: 'stageA 固定文案', progress: 3, total: 4 })
    await sleep(200)
  })
  await runInvocation(skillInvocation(), deps)

  const raw = progressPosts.filter((p) => p['message'] === 'stageA 固定文案')
  assert.equal(raw.length, 3, '每次数值推进都应原文上报一次（与全字段重复的去重不同）')
  assert.deepEqual(
    raw.map((p) => p['current']),
    [1, 2, 3],
    'current 逐次推进且保留在 payload 中',
  )
  assert.equal(raw[0]!['stage'], 'stageA')
  assert.ok(!events.some((e) => e.includes('[无进展告警]')), '数值推进持续复位无进展时钟，不应出现停滞告警')
  assert.ok(
    !progressPosts.some((p) => typeof p['message'] === 'string' && p['message'].includes('已超过告警阈值')),
    '未停滞则心跳不得携带告警标注',
  )
  assert.equal(results.length, 1)
})

test('log 写失败限频是窗口限频：5 分钟窗口结束后新失败再次告警，健康计数持续累计', async () => {
  const failHome = mkdtempSync(path.join(os.tmpdir(), 'p7-ind-log-'))
  const savedHome = process.env.AIDWORK_RUNTIME_HOME
  const originalWrite = process.stderr.write
  const captured: string[] = []
  const capturingWrite = ((chunk: Uint8Array | string) => {
    captured.push(typeof chunk === 'string' ? chunk : Buffer.from(chunk).toString('utf8'))
    return true
  }) as unknown as typeof process.stderr.write
  const realNow = Date.now
  let fakeNow = realNow()
  Date.now = () => fakeNow
  const { logInfo, logFileHealthSnapshot } = await import('../src/log.js')
  try {
    writeFileSync(path.join(failHome, 'logs'), 'block-mkdir', 'utf8')
    process.env.AIDWORK_RUNTIME_HOME = failHome
    process.stderr.write = capturingWrite

    const before = logFileHealthSnapshot().fileWriteFailures
    logInfo('fail-1')
    logInfo('fail-2') // 窗口内：累计但不重复告警
    fakeNow += 5 * 60_000 + 1 // 越过限频窗口
    logInfo('fail-3')

    const after = logFileHealthSnapshot()
    assert.equal(after.fileWriteFailures, before + 3, '三次失败全部累计')
    const alarms = captured.filter((line) => line.includes('日志文件写入失败'))
    assert.equal(alarms.length, 2, '首次告警 + 窗口结束后再次告警（限频≠永久静默）')
    assert.match(alarms[1]!, /已累计 3 次/, '第二次告警应携带累计次数')
  } finally {
    Date.now = realNow
    process.stderr.write = originalWrite
    process.env.AIDWORK_RUNTIME_HOME = savedHome
    rmSync(failHome, { recursive: true, force: true })
  }
})
