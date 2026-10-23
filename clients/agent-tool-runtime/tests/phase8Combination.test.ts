/**
 * Phase 8 组合验收（组合 4，Phase 7）：心跳/进展分离展示与无进展告警。
 *
 * 一个连续场景覆盖（对照计划 Phase 8「心跳/进展分离展示与无进展告警」与
 * Phase 7 第 1 条）：
 * 1. 语义进展原文上报（含 current/total 数值推进也算语义进展）；
 * 2. 重复同一句语义内容不重报、不重置无进展时钟（68 条相同「设置筛选面板」教训）；
 * 3. 心跳续租带标注（当前步骤 + 最近语义进展 + 实际无新进展 Ns），不冒充业务推进、
 *    不原样重发业务消息；
 * 4. 无进展超阈值：本地 [无进展告警]（含「不代表业务推进/不自动重放」诊断语义），
 *    每个停滞周期只告警一次；新语义进展复位后进入第二停滞周期可再次告警；
 * 5. 复位后心跳无进展计数从 0 重新起算且不带告警标注；终态只回传一次（不默认重放）。
 * 直连 runner 轻量栈（skill-runner 进程内 handler），不依赖真实 Provider 子进程。
 */
import assert from 'node:assert/strict'
import { mkdtempSync } from 'node:fs'
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

interface Harness {
  deps: RunnerDeps
  progressPosts: Array<{ stage?: unknown; current?: unknown; total?: unknown; message?: unknown; at: number }>
  events: string[]
  results: Array<{ id: string; payload: InvocationResultPayload }>
}

function makeHarness(
  script: (onProgress: (p: ProviderProgress) => void) => Promise<void>,
  opts: { noProgressWarnMs?: number } = {},
): Harness {
  const progressPosts: Harness['progressPosts'] = []
  const events: string[] = []
  const results: Harness['results'] = []
  const api = {
    started: async () => ({}),
    progress: async (_id: string, payload: Record<string, unknown>) => {
      progressPosts.push({ ...payload, at: Date.now() })
      return { seq: progressPosts.length, cancel: false }
    },
    result: async (id: string, payload: InvocationResultPayload) => {
      results.push({ id, payload })
    },
    operationResult: async () => ({}),
  } as unknown as ApiClient
  const skillRunner = {
    callTool: async (_tool: string, _args: Record<string, unknown>, callOpts: { onProgress?: (p: ProviderProgress) => void }) => {
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
    runtimeDataDir: mkdtempSync(path.join(os.tmpdir(), 'aidwork-p8-combo-')),
    progressIntervalMs: 40,
    noProgressWarnMs: opts.noProgressWarnMs ?? 800,
    onEvent: (msg) => events.push(msg),
  }
  return { deps, progressPosts, events, results }
}

function skillInvocation(): ClaimedInvocation {
  return {
    invocation_id: `inv-p8-${randomUUID().slice(0, 8)}`,
    tool_name: 'skill_script_run',
    arguments: { skill: 'demo', entry: 'main.js', exec_hash: 'abc' },
    claim_token: 'tok-p8',
    provider: 'skill-runner',
  }
}

test('组合4：语义进展/心跳分离展示 + 双停滞周期各告警一次 + 复位重新起算 + 不默认重放', async () => {
  const { deps, progressPosts, events, results } = makeHarness(async (onProgress) => {
    onProgress({ message: 'stageA 设置筛选面板' })
    // 重复同一句：不得重报、不得重置无进展时钟
    for (let i = 0; i < 4; i++) {
      onProgress({ message: 'stageA 设置筛选面板' })
      await sleep(50)
    }
    // 第一停滞周期：超过阈值 800ms
    await sleep(1_000)
    // 新语义进展（message 变化 + current/total 数值推进）：原文上报并复位
    onProgress({ message: 'stageB 筛选完成', progress: 2, total: 5 })
    await sleep(120)
    // 数值与文案均无变化：重复推送不得当作推进
    onProgress({ message: 'stageB 筛选完成', progress: 2, total: 5 })
    // 第二停滞周期：复位后再次超过阈值，应再次告警（每个停滞周期一次）
    await sleep(1_000)
    onProgress({ message: 'stageC 入库完成', progress: 5, total: 5 })
    await sleep(120)
  })
  await runInvocation(skillInvocation(), deps)

  // 1) 三次语义进展均原文上报一次：不混入心跳标注，数值字段完整（展示用字段不丢失）
  const semanticA = progressPosts.filter((p) => p.message === 'stageA 设置筛选面板')
  const semanticB = progressPosts.filter((p) => p.message === 'stageB 筛选完成')
  const semanticC = progressPosts.filter((p) => p.message === 'stageC 入库完成')
  assert.equal(semanticA.length, 1, '语义进展 A 原文上报一次（重复同句不重报）')
  assert.equal(semanticB.length, 1, '语义进展 B 原文上报一次（数值无变化的重复不算推进）')
  assert.equal(semanticC.length, 1, '语义进展 C 原文上报一次')
  assert.equal(semanticB[0]!.current, 2, '数值推进作为语义进展保留 current')
  assert.equal(semanticB[0]!.total, 5)
  assert.equal(semanticB[0]!.stage, 'stageB')
  assert.ok(!semanticA.concat(semanticB, semanticC).some((p) => String(p.message).includes('心跳续租')),
    '语义进展原文不得混入心跳标注')

  // 2) 心跳续租与语义进展分离展示：显式标注（心跳续租 + 无新进展 Ns），
  //    不原样重发业务消息冒充推进
  const heartbeats = progressPosts.filter((p) => typeof p.message === 'string' && p.message.includes('（心跳续租，无新进展'))
  assert.ok(heartbeats.length >= 3, '两个停滞周期内应有多次心跳续租')
  assert.ok(!heartbeats.some((h) => h.message === 'stageA 设置筛选面板' || h.message === 'stageB 筛选完成'),
    '心跳不得原样重发业务消息')
  assert.ok(heartbeats.some((h) => String(h.message).includes('设置筛选面板')), '心跳报告最近语义进展（第一周期）')
  assert.ok(heartbeats.some((h) => String(h.message).includes('筛选完成')), '心跳报告最近语义进展（第二周期）')

  // 3) 无进展告警：两个停滞周期各一次；含诊断语义与最近进展上下文
  const alarms = events.filter((e) => e.includes('[无进展告警]'))
  assert.equal(alarms.length, 2, '每个停滞周期只告警一次，复位后可再次告警')
  assert.match(alarms[0]!, /stageA/)
  assert.match(alarms[0]!, /设置筛选面板/)
  assert.match(alarms[1]!, /stageB/)
  assert.match(alarms[1]!, /筛选完成/)
  for (const alarm of alarms) {
    assert.match(alarm, /不代表业务推进/)
    assert.match(alarm, /不自动重放/)
  }

  // 4) 复位重新起算：stageB 之后的心跳无新进展计数回到 0s 且不带告警标注
  const afterB = heartbeats.filter((h) => h.at >= semanticB[0]!.at)
  assert.ok(
    afterB.some((h) => String(h.message).includes('（心跳续租，无新进展 0s）') && !String(h.message).includes('告警阈值')),
    '新语义进展后心跳计数应从 0 重新起算',
  )
  // 超阈值后心跳消息附加告警标注（两个周期都出现）
  assert.ok(heartbeats.filter((h) => String(h.message).includes('已超过告警阈值')).length >= 2,
    '两个停滞周期的超阈值心跳都应带告警标注')

  // 5) 不默认重放：终态只回传一次
  assert.equal(results.length, 1)
  assert.equal(results[0]!.payload['success'], true)
})
