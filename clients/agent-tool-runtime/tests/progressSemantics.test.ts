/**
 * Phase 7：ProgressForwarder 心跳续租与业务语义进展分离（2026-10-20 复盘：
 * 68 条相同「设置筛选面板」心跳掩盖卡死——重发同一句心跳只证明上报通路活跃，
 * 不能证明业务推进）。
 * - 语义进展（内容有变化）原文上报；心跳续租消息带「（心跳续租，无新进展 Ns）」标注
 * - 重复同一句语义内容不重置无进展时钟、不重复上报
 * - 无进展超过阈值：本地 [无进展告警]（只诊断不重放）+ 心跳消息附加告警标注
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
  opts: { noProgressWarnMs?: number; progressDelayMs?: number } = {},
): Harness {
  const progressPosts: Harness['progressPosts'] = []
  const events: string[] = []
  const results: Harness['results'] = []
  const api = {
    started: async () => ({}),
    progress: async (_id: string, payload: Record<string, unknown>) => {
      if (opts.progressDelayMs) await sleep(opts.progressDelayMs) // 模拟网络慢/队列消费慢
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
    runtimeDataDir: mkdtempSync(path.join(os.tmpdir(), 'aidwork-prog-sem-')),
    progressIntervalMs: 40,
    noProgressWarnMs: opts.noProgressWarnMs ?? 1_000,
    onEvent: (msg) => events.push(msg),
  }
  return { deps, progressPosts, events, results }
}

function skillInvocation(): ClaimedInvocation {
  return {
    invocation_id: `inv-prog-${randomUUID().slice(0, 8)}`,
    tool_name: 'skill_script_run',
    arguments: { skill: 'demo', entry: 'main.js', exec_hash: 'abc' },
    claim_token: 'tok-prog',
    provider: 'skill-runner',
  }
}

test('语义进展原文上报；心跳续租带标注与无进展时长；停滞超阈值告警一次并随语义进展复位', async () => {
  const { deps, progressPosts, events, results } = makeHarness(async (onProgress) => {
    onProgress({ message: 'stageA 设置筛选面板' })
    await sleep(1_600) // 超过 noProgressWarnMs=1000：停滞告警窗口
    onProgress({ message: 'stageB 筛选完成', progress: 1, total: 2 })
    await sleep(150)
  })
  await runInvocation(skillInvocation(), deps)

  // 语义进展：原文上报（不混入心跳标注），字段完整
  const semanticA = progressPosts.filter((p) => p.message === 'stageA 设置筛选面板')
  assert.equal(semanticA.length, 1, '语义进展 A 原文上报一次')
  const semanticB = progressPosts.filter((p) => p.message === 'stageB 筛选完成')
  assert.equal(semanticB.length, 1, '语义进展 B 原文上报一次')
  assert.equal(semanticB[0]!.current, 1)
  assert.equal(semanticB[0]!.total, 2)
  assert.equal(semanticB[0]!.stage, 'stageB')

  // 心跳续租：显式标注，不冒充业务推进
  const heartbeats = progressPosts.filter((p) => typeof p.message === 'string' && p.message.includes('（心跳续租，无新进展'))
  assert.ok(heartbeats.length >= 2, '停滞窗口内应有多次心跳续租')
  assert.ok(!heartbeats.some((h) => h.message === 'stageA 设置筛选面板'), '心跳不得原样重发业务消息')
  assert.ok(heartbeats.some((h) => String(h.message).includes('已超过告警阈值')), '超阈值后心跳应带告警标注')
  assert.ok(heartbeats.some((h) => String(h.message).includes('设置筛选面板')), '心跳报告最近语义进展')

  // 无进展告警：一次，含当前步骤、最近进展与「不自动重放」诊断语义
  const alarms = events.filter((e) => e.includes('[无进展告警]'))
  assert.equal(alarms.length, 1, '每个停滞周期只告警一次（避免刷屏）')
  assert.match(alarms[0]!, /stageA/)
  assert.match(alarms[0]!, /设置筛选面板/)
  assert.match(alarms[0]!, /不代表业务推进/)
  assert.match(alarms[0]!, /不自动重放/)

  // 语义进展复位无进展时钟：B 之后的心跳无新进展计数回到 0s 且不带告警标注
  const afterB = heartbeats.filter((h) => h.at >= semanticB[0]!.at)
  assert.ok(
    afterB.some((h) => String(h.message).includes('（心跳续租，无新进展 0s）') && !String(h.message).includes('告警阈值')),
    '新语义进展后心跳计数应从 0 重新起算',
  )

  // 不默认重放：终态只回传一次，执行器只跑一轮
  assert.equal(results.length, 1)
  assert.equal(results[0]!.payload['success'], true)
})

test('重复同一句语义内容不重报、不重置无进展时钟（68 条相同「设置筛选面板」教训）', async () => {
  const { deps, progressPosts, events, results } = makeHarness(async (onProgress) => {
    for (let i = 0; i < 5; i++) {
      onProgress({ message: 'stageA 设置筛选面板' })
      await sleep(60)
    }
    await sleep(850) // 首次语义进展后累计约 1.1s：若重复推送被误当作推进，告警不会在此窗口出现
  })
  await runInvocation(skillInvocation(), deps)

  const raw = progressPosts.filter((p) => p.message === 'stageA 设置筛选面板')
  assert.equal(raw.length, 1, '重复同一句只上报一次，不产生多条相同业务事件')

  const alarms = events.filter((e) => e.includes('[无进展告警]'))
  assert.equal(alarms.length, 1, '无进展时钟以首次语义进展为基准（重复推送不重置）')
  assert.match(alarms[0]!, /设置筛选面板/)
  assert.ok(
    progressPosts.some((p) => typeof p.message === 'string' && p.message.includes('已超过告警阈值')),
    '心跳消息应携带超阈值标注',
  )
  assert.equal(results.length, 1, '无进展不触发重放：终态只回传一次')
})

test('尚未收到业务进展时心跳明示等待状态（stage=running 不冒充业务步骤）', async () => {
  const { deps, progressPosts, events } = makeHarness(async () => {
    await sleep(200)
  })
  await runInvocation(skillInvocation(), deps)

  const heartbeats = progressPosts.filter((p) => typeof p.message === 'string' && p.message.includes('（心跳续租，无新进展'))
  assert.ok(heartbeats.length >= 1, '等待期应有心跳续租')
  assert.ok(
    heartbeats.every((h) => String(h.message).includes('尚未收到业务进展')),
    '无语义进展时心跳明示「尚未收到业务进展」',
  )
  assert.ok(heartbeats.every((h) => h.stage === 'running'))
  assert.ok(!events.some((e) => e.includes('[无进展告警]')), '未超阈值（200ms < 1000ms）不得告警')
})

test('同步连续 push 多个不同进展：按原文顺序全部上报，不丢中间步骤（快照须在 push 时固定）', async () => {
  // CodeReview 2026-10-21 复现：push A、B 后队列回调执行时才复制 semantic，
  // 实际发送 B、B（A 被合并覆盖）。修复后每次 push 固定当时的语义快照入队。
  const { deps, progressPosts, results } = makeHarness(
    async (onProgress) => {
      onProgress({ message: 'stepA 打开页面', progress: 1, total: 3 })
      onProgress({ message: 'stepB 输入关键词', progress: 2, total: 3 })
      onProgress({ message: 'stepC 点击搜索', progress: 3, total: 3 })
      await sleep(150)
    },
    { noProgressWarnMs: 10_000 },
  )
  await runInvocation(skillInvocation(), deps)

  const steps = ['stepA 打开页面', 'stepB 输入关键词', 'stepC 点击搜索']
  const positions = steps.map((text) => progressPosts
    .map((p, i) => ({ p, i }))
    .filter(({ p }) => p.message === text))
  assert.deepEqual(
    positions.map((hits) => hits.length),
    [1, 1, 1],
    '每个语义进展按原文上报恰好一次（不得丢失、不得重复为最后一步）',
  )
  assert.ok(
    positions[0]![0]!.i < positions[1]![0]!.i && positions[1]![0]!.i < positions[2]![0]!.i,
    '上报顺序与 push 顺序一致',
  )
  assert.deepEqual(
    positions.map((hits) => hits[0]!.p.current),
    [1, 2, 3],
    'current 随各次进展原文保留',
  )
  assert.ok(!progressPosts.some((p, i) => p.message === 'stepC 点击搜索' && i !== positions[2]![0]!.i),
    '不得把中间步骤覆盖为最后一步（B、B / C、C 回归形态）')
  assert.equal(results.length, 1)
})

test('队列积压（progress 消费慢）时中间步骤不丢：慢速消费下多进展仍按原文顺序上报', async () => {
  // 网络慢、发送串行化积压场景：push 快照若不在入队时固定，积压期间后续 push
  // 会改写未发送条目的内容。消费端每条 60ms，push 间隔 10ms，构造稳定积压。
  const { deps, progressPosts, results } = makeHarness(
    async (onProgress) => {
      onProgress({ message: 'slowA 定位输入框', progress: 1, total: 3 })
      await sleep(10)
      onProgress({ message: 'slowB 填入内容', progress: 2, total: 3 })
      await sleep(10)
      onProgress({ message: 'slowC 提交表单', progress: 3, total: 3 })
      await sleep(50)
    },
    { noProgressWarnMs: 10_000, progressDelayMs: 60 },
  )
  await runInvocation(skillInvocation(), deps)

  const steps = ['slowA 定位输入框', 'slowB 填入内容', 'slowC 提交表单']
  const positions = steps.map((text) => progressPosts
    .map((p, i) => ({ p, i }))
    .filter(({ p }) => p.message === text))
  assert.deepEqual(
    positions.map((hits) => hits.length),
    [1, 1, 1],
    '积压场景下每个语义进展仍原文上报恰好一次',
  )
  assert.ok(
    positions[0]![0]!.i < positions[1]![0]!.i && positions[1]![0]!.i < positions[2]![0]!.i,
    '积压消费仍按 push 顺序送达',
  )
  // 积压期间的心跳消息不得占用/覆盖语义进展原文位置（心跳在 push 之后入队）
  assert.ok(progressPosts.length >= 3)
  assert.equal(results.length, 1)
})
