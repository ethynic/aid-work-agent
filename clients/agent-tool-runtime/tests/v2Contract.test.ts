/**
 * 双消费方 v2 中立操作协议契约测试（宪章 P1-D 交付物 2/3，Runtime 侧）。
 *
 * 与 Python 侧 tests/unit/desktop_automation/contract_fakes.py 语义对齐：
 * - weixin_message_send_v2 / boss_send_to_v2 共享同一字段集（R15 + permit handle），
 *   两 tool inputSchema 字段名集合 diff 为空，无群/候选人专用字段
 * - target/hash/epoch/permit 全字段校验路径（缺失/篡改逐一拒绝；schema 最小必填集 SDK 层拒绝）
 * - 防重放：同 request_id 二次调用回既有回执且副作用计数不增；过期 epoch 拒绝；
 *   重放查找先于 deadline 检查（总工裁决：台账一致性重放不受 deadline 影响）
 * - evidence_ref 序号严格递增且唯一（重放不产生新序号）
 * - 防跨租户/跨 invocation：target_handle/target_ref 复用拒绝
 * - E2E：runtimeStack 注入 v2 测试 manifests（生产 TRUSTED_MANIFESTS 不变——P1-C 缝）
 *   + fakeV2Provider 跑通 claim → write-authorize → journal → 执行 → operation-result verified
 */
import assert from 'node:assert/strict'
import { createHash, randomUUID } from 'node:crypto'
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { test } from 'node:test'
import { Client } from '@modelcontextprotocol/sdk/client/index.js'
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js'
import type { CallLogEntry } from './helpers/fakeCloud.js'
import type { TestStack } from './helpers/runtimeStack.js'
import { fakeV2ProviderEntry, startTestStack } from './helpers/runtimeStack.js'
import type { ProviderManifest } from '../src/providers.js'

const WEIXIN_V2_TOOL = 'weixin_message_send_v2'
const BOSS_V2_TOOL = 'boss_send_to_v2'

/** R15 v2 统一操作描述顶层字段集（与 Python 契约 fake 的 V2_OPERATION_FIELDS 对齐） */
const R15_FIELDS = [
  'protocol_version',
  'operation',
  'provider_key',
  'target_ref',
  'target_handle',
  'target_version',
  'payload_ref',
  'payload_hash',
  'request_id',
  'delivery_id',
  'authorization_revision',
  'authorization_epoch',
  'resource_key',
  'deadline_at',
] as const

/** 群/候选人/正文/可执行路径等场景专用字段（中立协议不得出现） */
const BANNED_FIELDS = [
  'group_id', 'group_name', 'group_ref',
  'candidate_id', 'candidate_name', 'candidate_ref',
  'content', 'text', 'message', 'argv', 'url', 'executable', 'cwd', 'env',
]

// ---------- 直连 Provider 会话（shared/mcp-conformance 断言模式：真 SDK client） ----------

interface ProviderSession {
  client: Client
  close: () => Promise<void>
}

async function connectFakeV2(): Promise<ProviderSession> {
  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [fakeV2ProviderEntry(), 'mcp', '--stdio'],
    stderr: 'pipe',
  })
  const client = new Client({ name: 'v2-contract-test', version: '0.0.1' }, { capabilities: {} })
  await client.connect(transport)
  return {
    client,
    close: async () => {
      try {
        await client.close()
      } catch {
        // 进程已退出（crash 注入）时关闭幂等
      }
    },
  }
}

async function callV2Tool(client: Client, name: string, args: Record<string, unknown>): Promise<Record<string, unknown>> {
  const result = await client.callTool({ name, arguments: args })
  const structured = (result as { structuredContent?: Record<string, unknown> }).structuredContent
  if (structured && typeof structured === 'object') return structured
  const text = (result as { content?: Array<{ type: string; text?: string }> }).content?.find((c) => c.type === 'text')?.text
  if (text) return JSON.parse(text) as Record<string, unknown>
  throw new Error(`tool ${name} 返回无法解析`)
}

// ---------- 测试夹具 ----------

interface ContractFixture {
  dir: string
  stateFile: string
  clicksFile: string
  cleanup: () => void
}

function makeFixture(prefix = 'aidwork-v2contract-'): ContractFixture {
  const dir = mkdtempSync(path.join(os.tmpdir(), prefix))
  return {
    dir,
    stateFile: path.join(dir, 'provider-state.json'),
    clicksFile: path.join(dir, 'clicks.jsonl'),
    cleanup: () => rmSync(dir, { recursive: true, force: true }),
  }
}

function clickCount(file: string): number {
  if (!existsSync(file)) return 0
  const text = readFileSync(file, 'utf8').trim()
  return text ? text.split('\n').length : 0
}

let uniqueSeq = 0

/** R15 形态 v2 操作描述（含直连测试用的假 permit handle；E2E 删除二者交由 Runtime 注入真许可） */
function v2OperationArgs(consumer: 'weixin' | 'boss', fx: ContractFixture, overrides: Record<string, unknown> = {}): Record<string, unknown> {
  uniqueSeq += 1
  const tool = consumer === 'weixin' ? WEIXIN_V2_TOOL : BOSS_V2_TOOL
  return {
    protocol_version: 2,
    operation: tool,
    provider_key: consumer === 'weixin' ? 'weixin' : 'boss-recruiting',
    target_ref: `contract-target-${uniqueSeq}`,
    target_handle: `handle:contract-target-${uniqueSeq}`,
    target_version: 'tv-1',
    payload_ref: `da:${consumer === 'weixin' ? 'weixin.fixed_content.v1' : 'boss.chat_reply.v1'}:frozen-${uniqueSeq}`,
    payload_hash: createHash('sha256').update(`frozen-${tool}-${uniqueSeq}`).digest('hex'),
    request_id: `req-${randomUUID()}`,
    delivery_id: `del-${randomUUID()}`,
    authorization_revision: 'rev-1',
    authorization_epoch: 1,
    resource_key: 'rk-contract-1',
    deadline_at: new Date(Date.now() + 600_000).toISOString(),
    permit_id: `permit-${randomUUID()}`,
    permit_token: `permit-token-${randomUUID()}`,
    state_file: fx.stateFile,
    clicks_file: fx.clicksFile,
    ...overrides,
  }
}

// ---------- 契约：schema 一致性 ----------

test('双消费方 schema 一致性：两 tool inputSchema 字段名集合完全一致（diff 为空），恰为 R15 + permit handle + 测试注入字段', async () => {
  const fx = makeFixture()
  const session = await connectFakeV2()
  try {
    const { tools } = await session.client.listTools()
    const byName = new Map(tools.map((t) => [t.name, t]))
    const weixin = byName.get(WEIXIN_V2_TOOL)
    const boss = byName.get(BOSS_V2_TOOL)
    assert.ok(weixin && boss, `必须注册双消费方 tool（实际: ${tools.map((t) => t.name).join(',')}）`)
    const propsOf = (t: NonNullable<typeof weixin>): string[] =>
      Object.keys((t.inputSchema as { properties?: Record<string, unknown> }).properties ?? {})
    const wxFields = propsOf(weixin)
    const bossFields = propsOf(boss)
    // 字段名集合双向 diff 为空
    assert.deepEqual([...wxFields].sort(), [...bossFields].sort())
    // 恰为 R15 统一操作描述 + permit handle + 共享测试注入字段
    const expected = [...R15_FIELDS, 'permit_id', 'permit_token', 'state_file', 'clicks_file', 'fault'].sort()
    assert.deepEqual([...wxFields].sort(), expected)
    // 无群/候选人/正文/可执行路径等场景专用字段
    for (const banned of BANNED_FIELDS) {
      assert.ok(!wxFields.includes(banned), `${WEIXIN_V2_TOOL} 不得含场景专用字段 ${banned}`)
      assert.ok(!bossFields.includes(banned), `${BOSS_V2_TOOL} 不得含场景专用字段 ${banned}`)
    }
    // 可选性共享：required 集合一致，且恰为最小必填集（幂等台账与载荷引用的最小身份；
    // 不含 permit 字段——由 Runtime 授权后注入。空集==空集的弱断言无检出能力）
    const requiredOf = (t: NonNullable<typeof weixin>): string[] =>
      [...((t.inputSchema as { required?: string[] }).required ?? [])].sort()
    assert.deepEqual(requiredOf(weixin), requiredOf(boss))
    assert.deepEqual(requiredOf(weixin), ['payload_hash', 'payload_ref', 'request_id', 'target_ref'])
  } finally {
    await session.close()
    fx.cleanup()
  }
})

// ---------- 契约：全字段校验（缺失/篡改逐一拒绝） ----------

/** schema 层最小必填集（缺失由 SDK 直接拒绝，不进入 handler） */
const SCHEMA_REQUIRED = new Set(['request_id', 'target_ref', 'payload_ref', 'payload_hash'])

test('R15 + permit 全字段缺失逐一拒绝：schema 必填集 SDK 层拒绝 / 其余 INVALID_ARGUMENT / permit PERMIT_REQUIRED，均无副作用', async () => {
  const fx = makeFixture()
  const session = await connectFakeV2()
  try {
    for (const field of [...R15_FIELDS, 'permit_id', 'permit_token']) {
      const args = v2OperationArgs('weixin', fx)
      delete args[field]
      if (SCHEMA_REQUIRED.has(field)) {
        // 最小必填集：schema 层拒绝（SDK invalid params，不进入 handler）
        await assert.rejects(
          () => callV2Tool(session.client, WEIXIN_V2_TOOL, args),
          (err: unknown) => err instanceof Error,
          `${field} 缺失应在 schema 层被拒绝`,
        )
        continue
      }
      const r = await callV2Tool(session.client, WEIXIN_V2_TOOL, args)
      assert.equal(r['success'], false, `${field} 缺失必须拒绝`)
      assert.equal(r['effect'], 'none', `${field} 缺失 → effect none`)
      assert.equal(r['phase'], 'prepared')
      if (field === 'permit_id' || field === 'permit_token') {
        assert.equal(r['code'], 'PERMIT_REQUIRED', `${field} 缺失 → PERMIT_REQUIRED`)
      } else {
        assert.equal(r['code'], 'INVALID_ARGUMENT', `${field} 缺失 → INVALID_ARGUMENT`)
        assert.ok(String(r['message']).includes(field), `拒绝消息应指明缺失字段 ${field}（实际: ${String(r['message'])}）`)
      }
    }
    assert.equal(clickCount(fx.clicksFile), 0, '全部校验拒绝路径不得产生副作用')
  } finally {
    await session.close()
    fx.cleanup()
  }
})

test('payload_hash 篡改两路拒绝：非法形态（首见 PAYLOAD_HASH_INVALID）与重放不一致（PAYLOAD_HASH_MISMATCH），均不执行', async () => {
  const fx = makeFixture()
  const session = await connectFakeV2()
  try {
    const bad = await callV2Tool(session.client, WEIXIN_V2_TOOL, v2OperationArgs('weixin', fx, { payload_hash: 'z'.repeat(64) }))
    assert.equal(bad['code'], 'PAYLOAD_HASH_INVALID')
    assert.equal(bad['effect'], 'none')
    assert.equal(clickCount(fx.clicksFile), 0)

    const args = v2OperationArgs('boss', fx)
    const ok = await callV2Tool(session.client, BOSS_V2_TOOL, args)
    assert.equal(ok['phase'], 'verified')
    const tampered = await callV2Tool(session.client, BOSS_V2_TOOL, { ...args, payload_hash: 'f'.repeat(64) })
    assert.equal(tampered['code'], 'PAYLOAD_HASH_MISMATCH')
    assert.equal(tampered['effect'], 'none')
    assert.equal(clickCount(fx.clicksFile), 1, '篡改重放不得重新执行')
  } finally {
    await session.close()
    fx.cleanup()
  }
})

test('permit 校验：deadline 已过 → PERMIT_REQUIRED；重放 permit 不一致 → PERMIT_REQUIRED', async () => {
  const fx = makeFixture()
  const session = await connectFakeV2()
  try {
    const expired = await callV2Tool(session.client, WEIXIN_V2_TOOL, v2OperationArgs('weixin', fx, { deadline_at: new Date(Date.now() - 1_000).toISOString() }))
    assert.equal(expired['code'], 'PERMIT_REQUIRED')
    assert.equal(expired['effect'], 'none')

    const args = v2OperationArgs('weixin', fx)
    const first = await callV2Tool(session.client, WEIXIN_V2_TOOL, args)
    assert.equal(first['phase'], 'verified')
    const mismatched = await callV2Tool(session.client, WEIXIN_V2_TOOL, { ...args, permit_id: `other-${randomUUID()}` })
    assert.equal(mismatched['code'], 'PERMIT_REQUIRED')
    assert.equal(clickCount(fx.clicksFile), 1)
  } finally {
    await session.close()
    fx.cleanup()
  }
})

test('重放先于 deadline 检查（总工裁决）：may_have_started/verified 台账的重放即使 deadline 已过也回既有回执，不 PERMIT_REQUIRED', async () => {
  const fx = makeFixture()
  const s1 = await connectFakeV2()
  const crashArgs = v2OperationArgs('weixin', fx, { fault: 'crash' })
  try {
    await assert.rejects(
      () => callV2Tool(s1.client, WEIXIN_V2_TOOL, crashArgs),
      (err: unknown) => err instanceof Error,
      'crash 注入使连接随进程退出断开',
    )
  } finally {
    await s1.close()
  }
  assert.equal(clickCount(fx.clicksFile), 1, 'crash 副作用已发生（may_have_started）')
  const s2 = await connectFakeV2()
  try {
    // may_have_started 台账 + deadline 已过：回既有 unknown 回执（计划 §5.3），而非 PERMIT_REQUIRED
    const replayArgs: Record<string, unknown> = { ...crashArgs, deadline_at: new Date(Date.now() - 1_000).toISOString() }
    delete replayArgs['fault']
    const r = await callV2Tool(s2.client, WEIXIN_V2_TOOL, replayArgs)
    assert.equal(r['success'], false)
    assert.equal(r['code'], 'EXECUTION_UNKNOWN')
    assert.equal(r['effect'], 'unknown')
    assert.equal(r['phase'], 'may_have_started')
    assert.equal(clickCount(fx.clicksFile), 1, 'deadline 已过的重放不得重新执行')

    // verified 台账同理：applied 回执的重放不受 deadline 影响（逐字段一致）
    const okArgs = v2OperationArgs('boss', fx)
    const ok = await callV2Tool(s2.client, BOSS_V2_TOOL, okArgs)
    assert.equal(ok['phase'], 'verified')
    const okReplay = await callV2Tool(s2.client, BOSS_V2_TOOL, { ...okArgs, deadline_at: new Date(Date.now() - 1_000).toISOString() })
    assert.deepEqual(okReplay, ok)

    // 对照：未进台账的新请求 deadline 已过仍被拒（deadline 检查仅对新请求生效）
    const freshExpired = await callV2Tool(s2.client, WEIXIN_V2_TOOL, v2OperationArgs('weixin', fx, { deadline_at: new Date(Date.now() - 1_000).toISOString() }))
    assert.equal(freshExpired['code'], 'PERMIT_REQUIRED')
    assert.equal(clickCount(fx.clicksFile), 2, '仅新增一次正常执行（crash 1 + verified 1）')
  } finally {
    await s2.close()
    fx.cleanup()
  }
})

// ---------- 契约：防重放 / 幂等 ----------

test('防重放：同 request_id 二次调用回既有回执（逐字段一致），副作用计数不增；跨 Provider 进程（状态文件持久）同样幂等', async () => {
  const fx = makeFixture()
  const args = v2OperationArgs('weixin', fx)
  const s1 = await connectFakeV2()
  let first: Record<string, unknown>
  try {
    first = await callV2Tool(s1.client, WEIXIN_V2_TOOL, args)
    assert.equal(first['success'], true)
    assert.equal(first['effect'], 'applied')
    assert.equal(first['phase'], 'verified')
    assert.ok(String(first['evidence_ref']).startsWith('v2-fake-evidence:'))
    // 同会话重放：回既有回执
    const sameSession = await callV2Tool(s1.client, WEIXIN_V2_TOOL, args)
    assert.deepEqual(sameSession, first)
    assert.equal(clickCount(fx.clicksFile), 1, '重放不得增加副作用')
  } finally {
    await s1.close()
  }
  // 跨进程重放（新 Provider 进程读同一状态文件）：同样回既有回执
  const s2 = await connectFakeV2()
  try {
    const crossProcess = await callV2Tool(s2.client, WEIXIN_V2_TOOL, args)
    assert.deepEqual(crossProcess, first!)
    assert.equal(clickCount(fx.clicksFile), 1, '跨进程重放不得增加副作用')
  } finally {
    await s2.close()
    fx.cleanup()
  }
})

test('双消费方共享幂等台账：weixin 已执行的 request_id 经 boss tool 重放回既有回执且不执行', async () => {
  const fx = makeFixture()
  const session = await connectFakeV2()
  try {
    const args = v2OperationArgs('weixin', fx)
    const first = await callV2Tool(session.client, WEIXIN_V2_TOOL, args)
    assert.equal(first['phase'], 'verified')
    // 同 request_id 经另一消费方 tool 重放：协议级幂等（不按 tool 分账）
    const replay = await callV2Tool(session.client, BOSS_V2_TOOL, args)
    assert.deepEqual(replay, first)
    assert.equal(clickCount(fx.clicksFile), 1)
  } finally {
    await session.close()
    fx.cleanup()
  }
})

test('evidence_ref 序号严格递增且唯一：不同请求先后执行产生不同序号，重放不产生新序号', async () => {
  const fx = makeFixture()
  const session = await connectFakeV2()
  try {
    const args1 = v2OperationArgs('weixin', fx)
    const r1 = await callV2Tool(session.client, WEIXIN_V2_TOOL, args1)
    const args2 = v2OperationArgs('boss', fx)
    const r2 = await callV2Tool(session.client, BOSS_V2_TOOL, args2)
    const e1 = String(r1['evidence_ref'])
    const e2 = String(r2['evidence_ref'])
    const seqOf = (e: string): number => Number(e.split(':').pop())
    assert.ok(e1 && e2, 'verified 回执必须携带 evidence_ref')
    assert.notEqual(e1, e2, 'evidence_ref 全局唯一（不同请求不得复用）')
    assert.ok(seqOf(e2) > seqOf(e1), `序号严格递增（${seqOf(e1)} -> ${seqOf(e2)}）`)
    // 重放回既有回执：不消耗新序号、evidence_ref 不变
    const replay1 = await callV2Tool(session.client, WEIXIN_V2_TOOL, args1)
    assert.equal(replay1['evidence_ref'], e1)
    const replay2 = await callV2Tool(session.client, BOSS_V2_TOOL, args2)
    assert.equal(replay2['evidence_ref'], e2)
    assert.equal(clickCount(fx.clicksFile), 2, '恰好两次执行')
  } finally {
    await session.close()
    fx.cleanup()
  }
})

test('may_have_started（crash 注入）重复请求不重新执行：回 unknown/may_have_started 回执', async () => {
  const fx = makeFixture()
  const s1 = await connectFakeV2()
  const args = v2OperationArgs('weixin', fx, { fault: 'crash' })
  try {
    await assert.rejects(
      () => callV2Tool(s1.client, WEIXIN_V2_TOOL, args),
      (err: unknown) => err instanceof Error,
      'crash 注入使连接随进程退出断开',
    )
  } finally {
    await s1.close()
  }
  assert.equal(clickCount(fx.clicksFile), 1, 'crash 前副作用已发生（输入已开始）')
  const s2 = await connectFakeV2()
  try {
    const replayArgs = { ...args }
    delete replayArgs['fault']
    const r = await callV2Tool(s2.client, WEIXIN_V2_TOOL, replayArgs)
    assert.equal(r['success'], false)
    assert.equal(r['code'], 'EXECUTION_UNKNOWN')
    assert.equal(r['effect'], 'unknown')
    assert.equal(r['phase'], 'may_have_started')
    assert.equal(r['safe_to_retry'], false)
    assert.equal(clickCount(fx.clicksFile), 1, 'may_have_started 重复请求不得重新执行')
  } finally {
    await s2.close()
    fx.cleanup()
  }
})

test('unknown 结果的重复请求不重新执行：回既有 unknown 回执（逐字段一致）', async () => {
  const fx = makeFixture()
  const session = await connectFakeV2()
  try {
    const args = v2OperationArgs('boss', fx, { fault: 'unknown' })
    const first = await callV2Tool(session.client, BOSS_V2_TOOL, args)
    assert.equal(first['success'], false)
    assert.equal(first['code'], 'EXECUTION_UNKNOWN')
    assert.equal(first['effect'], 'unknown')
    assert.equal(first['phase'], 'unknown')
    assert.equal(first['safe_to_retry'], false)
    const replayArgs = { ...args }
    delete replayArgs['fault']
    const replay = await callV2Tool(session.client, BOSS_V2_TOOL, replayArgs)
    assert.deepEqual(replay, first, 'unknown 重放回既有回执，不得转为执行成功')
    assert.equal(clickCount(fx.clicksFile), 1)
  } finally {
    await session.close()
    fx.cleanup()
  }
})

test('过期 authorization_epoch 拒绝：新请求与重放低于已见水位均 AUTHORIZATION_EPOCH_EXPIRED，不执行', async () => {
  const fx = makeFixture()
  const session = await connectFakeV2()
  try {
    const epoch5 = v2OperationArgs('weixin', fx, { authorization_epoch: 5 })
    const ok = await callV2Tool(session.client, WEIXIN_V2_TOOL, epoch5)
    assert.equal(ok['phase'], 'verified')

    // 新请求（新 target/新 request_id）携带过期 epoch
    const stale = await callV2Tool(session.client, BOSS_V2_TOOL, v2OperationArgs('boss', fx, { authorization_epoch: 3 }))
    assert.equal(stale['code'], 'AUTHORIZATION_EPOCH_EXPIRED')
    assert.equal(stale['effect'], 'none')
    assert.equal(stale['phase'], 'prepared')
    assert.equal(stale['safe_to_retry'], false)

    // 同 request_id 重放降级 epoch 同样拒绝
    const staleReplay = await callV2Tool(session.client, WEIXIN_V2_TOOL, { ...epoch5, authorization_epoch: 2 })
    assert.equal(staleReplay['code'], 'AUTHORIZATION_EPOCH_EXPIRED')
    assert.equal(clickCount(fx.clicksFile), 1, '过期授权不得执行')
  } finally {
    await session.close()
    fx.cleanup()
  }
})

// ---------- 契约：防跨租户/跨 invocation（target 一次性绑定） ----------

test('跨 invocation 复用 target_handle / target_ref → TARGET_BINDING_INVALID，不执行', async () => {
  const fx = makeFixture()
  const session = await connectFakeV2()
  try {
    const args1 = v2OperationArgs('weixin', fx)
    const first = await callV2Tool(session.client, WEIXIN_V2_TOOL, args1)
    assert.equal(first['phase'], 'verified')

    const args2 = v2OperationArgs('boss', fx)
    const reuseHandle = await callV2Tool(session.client, BOSS_V2_TOOL, { ...args2, target_handle: args1['target_handle'] })
    assert.equal(reuseHandle['code'], 'TARGET_BINDING_INVALID')
    assert.equal(reuseHandle['effect'], 'none')

    const args3 = v2OperationArgs('weixin', fx)
    const reuseRef = await callV2Tool(session.client, WEIXIN_V2_TOOL, { ...args3, target_ref: args1['target_ref'] })
    assert.equal(reuseRef['code'], 'TARGET_BINDING_INVALID')
    assert.equal(reuseRef['effect'], 'none')

    assert.equal(clickCount(fx.clicksFile), 1, '跨 invocation 复用 target 不得执行')
  } finally {
    await session.close()
    fx.cleanup()
  }
})

// ---------- E2E：v2 测试 manifests 注入 + fakeV2Provider 全链路 ----------

/** v2 契约样例 manifest（仅测试注入；生产 TRUSTED_MANIFESTS 仍 v1，v2Gate.test.ts 守门） */
const V2_WEIXIN_MANIFEST: ProviderManifest = {
  provider_key: 'weixin',
  provider_id: 'ai.aidwork.weixin',
  tools: [WEIXIN_V2_TOOL],
  execution_target: 'local_required',
  protocol_version: 2,
  shared_lock_capable: true,
  write_tools: new Set([WEIXIN_V2_TOOL]),
}

const V2_BOSS_MANIFEST: ProviderManifest = {
  provider_key: 'boss-recruiting',
  provider_id: 'ai.aidwork.boss-recruiting',
  tools: [BOSS_V2_TOOL],
  execution_target: 'local_required',
  protocol_version: 2,
  shared_lock_capable: true,
  write_tools: new Set([BOSS_V2_TOOL]),
}

async function startV2ContractStack(dataDir: string): Promise<TestStack> {
  return startTestStack({
    // boss 入口同样指向 fakeV2Provider：双消费方共用同一 v2 契约实现（仅 tool 名不同）
    providerEntries: { weixin: fakeV2ProviderEntry(), 'boss-recruiting': fakeV2ProviderEntry() },
    manifests: { weixin: V2_WEIXIN_MANIFEST, 'boss-recruiting': V2_BOSS_MANIFEST },
    runtimeDataDir: dataDir,
  })
}

async function waitForOperationResult(stack: TestStack, id: string, label: string): Promise<CallLogEntry> {
  await stack.cloud.waitFor(
    () => stack.cloud.callsFor(id).some((c) => c.type === 'operation_result'),
    15_000,
    label,
  )
  return stack.cloud.callsFor(id).find((c) => c.type === 'operation_result')!
}

async function waitForOutboxDrained(stack: TestStack, label = 'outbox drained'): Promise<void> {
  await stack.cloud.waitFor(() => stack.outbox.loadAll().length === 0, 10_000, label)
}

async function runV2E2E(stack: TestStack, dataDir: string, consumer: 'weixin' | 'boss'): Promise<void> {
  const fx = makeFixture('aidwork-v2c-e2e-')
  try {
    // E2E 不预填 permit handle：由 Runtime write-authorize 后注入（注入缺失会被 Provider PERMIT_REQUIRED 拒绝）
    const args = v2OperationArgs(consumer, fx)
    delete args['permit_id']
    delete args['permit_token']
    const tool = consumer === 'weixin' ? WEIXIN_V2_TOOL : BOSS_V2_TOOL
    const providerKey = consumer === 'weixin' ? 'weixin' : 'boss-recruiting'
    const id = stack.cloud.enqueueInvocation(tool, args, { provider: providerKey })

    const result = await waitForOperationResult(stack, id, `v2 e2e ${consumer}`)
    const payload = result.payload
    const inv = stack.cloud.getInvocation(id)!

    // 回执字段（claim/request_id/permit handle/effect/phase/success/evidence）
    assert.equal(payload['claim_token'], inv.claim_token)
    assert.equal(payload['request_id'], args['request_id'])
    assert.equal(payload['permit_id'], inv.permit?.permit_id)
    assert.equal(payload['permit_token'], inv.permit?.permit_token)
    assert.equal(payload['success'], true)
    assert.equal(payload['code'], 'OK')
    assert.equal(payload['effect'], 'applied')
    assert.equal(payload['phase'], 'verified')
    assert.equal(payload['safe_to_retry'], false)
    assert.ok(String(payload['evidence_ref']).startsWith('v2-fake-evidence:'))

    // fakeCloud 服务端语义镜像：applied+verified → invocation succeeded 落账
    assert.equal(inv.state, 'succeeded')
    assert.equal(inv.result?.['effect'], 'applied')

    // 许可恰好一次，字段绑定（request_id/target_version/payload_hash）
    const auth = stack.cloud.callsFor(id).filter((c) => c.type === 'write_authorize')
    assert.equal(auth.length, 1)
    assert.equal(auth[0]!.payload['request_id'], args['request_id'])
    assert.equal(auth[0]!.payload['target_version'], args['target_version'])
    assert.equal(auth[0]!.payload['payload_hash'], args['payload_hash'])

    // journal：输入开始前 may_have_started 一行（permit_id/payload_hash/operation）
    const journalFile = path.join(dataDir, 'journal', `${id}.jsonl`)
    assert.ok(existsSync(journalFile), 'journal 必须在输入开始前落盘')
    const journal = JSON.parse(readFileSync(journalFile, 'utf8').trim().split('\n')[0]!) as Record<string, unknown>
    assert.equal(journal['invocation_id'], id)
    assert.equal(journal['permit_id'], inv.permit?.permit_id)
    assert.equal(journal['payload_hash'], args['payload_hash'])
    assert.equal(journal['operation'], tool)
    assert.equal(journal['phase'], 'may_have_started')

    // 副作用恰好一次；outbox ACK 后清空；旧 /result 端点未使用
    assert.equal(clickCount(fx.clicksFile), 1)
    await waitForOutboxDrained(stack, `v2 e2e ${consumer} outbox drained`)
    assert.ok(!stack.cloud.callsFor(id).some((c) => c.type === 'result'), 'v2 不得走旧 /result 端点')
  } finally {
    fx.cleanup()
  }
}

test('E2E 微信消费方：claim → write-authorize（fakeCloud v2 端点）→ journal → Provider 执行 → operation-result verified 全链路', async () => {
  const dataDir = mkdtempSync(path.join(os.tmpdir(), 'aidwork-v2c-stack-'))
  const stack = await startV2ContractStack(dataDir)
  try {
    await runV2E2E(stack, dataDir, 'weixin')
  } finally {
    await stack.stop()
    rmSync(dataDir, { recursive: true, force: true })
  }
})

test('E2E BOSS 消费方（第二采纳者）：同栈同契约跑通 boss_send_to_v2 全链路（双消费方共享协议）', async () => {
  const dataDir = mkdtempSync(path.join(os.tmpdir(), 'aidwork-v2c-stack-'))
  const stack = await startV2ContractStack(dataDir)
  try {
    await runV2E2E(stack, dataDir, 'boss')
  } finally {
    await stack.stop()
    rmSync(dataDir, { recursive: true, force: true })
  }
})
