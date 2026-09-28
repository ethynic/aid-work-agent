/**
 * wecom_chat_search M4 契约测试（Jev 决策 + 坐标句柄 + overlay 保持打开；全 mock，
 * 绝不触达真实企微窗口/驱动脚本）。
 *
 * 覆盖：新 payload 透传（best/坐标/概率/jev/timing）/ 空 items → TARGET_NOT_FOUND+reason /
 * type 过滤后 best 重选（probability 最高，全 null 则第一条）/ jev degraded（规则 best、
 * 概率 null）/ target_ref 真实签发含 overlay 相对坐标（老式无坐标 ref 仍可验证）。
 */
import assert from 'node:assert/strict'
import { existsSync, mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test, { after } from 'node:test'
import { createWecomChatSearchOperation } from '../src/operations/chatSearch.js'
import { createTargetRef, verifyTargetRef } from '../src/platform/targetRef.js'
import type { OpContext } from '../src/operations/types.js'
import type { RunPowerShellDriverFn } from '../src/platform/powershell.js'

function silentCtx(): OpContext {
  return { signal: new AbortController().signal, progress: () => {} }
}

const tempDirs: string[] = []
after(() => {
  for (const d of tempDirs) rmSync(d, { recursive: true, force: true })
})

/** mock createRefFn：坐标拼进 ref 字符串，便于断言 createRefFn 收到了 overlay 相对坐标 */
const refWithCoords = (name: string, type: string, subtitle?: string, coords?: { x: number; y: number }) =>
  `ref:${type}:${name}:${subtitle ?? ''}:${coords ? `${coords.x},${coords.y}` : 'nocoords'}`

function makeOp(
  runDriverFn: RunPowerShellDriverFn,
  createRefFn: (name: string, type: string, subtitle?: string, coords?: { x: number; y: number }) => string = refWithCoords,
) {
  const dir = mkdtempSync(join(tmpdir(), 'wecom-chat-search-v2-test-'))
  tempDirs.push(dir)
  const op = createWecomChatSearchOperation({
    runDriverFn,
    createRefFn,
    artifactDirFn: () => dir,
  })
  return { op, dir }
}

test('v2：新 payload 透传 → best=items[best_index]，screen 坐标=overlay+相对坐标，jev/timing 原样', async () => {
  const { op, dir } = makeOp(async (opts) => {
    // ArtifactDir 已创建且为 search-<ts> 目录（稳定帧截图/driver-log 落点）
    assert.equal(opts.args?.[2], '-ArtifactDir')
    const artifactDir = opts.args?.[3] as string
    assert.ok(artifactDir.startsWith(dir) && /search-\d{4}-\d{2}-\d{2}T/.test(artifactDir))
    assert.ok(existsSync(artifactDir))
    return {
      query: '陆伟',
      items: [
        { name: '陆伟', subtitle: '微信联系人', section: '联系人', x: 200, y: 60, probability: 0.2 },
        { name: '陆伟@微信', subtitle: '', section: '联系人', x: 200, y: 120, probability: 0.71 },
        { name: '产品讨论群', subtitle: '3人', section: '群聊', x: 210, y: 200, probability: 0.09 },
      ],
      best_index: 1,
      overlay: { x: 100, y: 50, w: 400, h: 542 },
      jev: { used: true, latency_ms: 812, is_ambiguous: false, ambiguous_confidence: 0.93, best_confidence: 0.71 },
      timing_ms: { focus: 250, box_check: 900, typing: 1200, overlay: 2100, jev: 812, total: 5300 },
    }
  })
  const r = await op.execute({ query: '陆伟' }, silentCtx())
  assert.equal(r.success, true)
  assert.equal(r.code, 'OK')
  assert.equal(r.effect, 'none')
  assert.equal(r.data.search_successful, true)
  const best = r.data.best as Record<string, unknown>
  assert.equal(best.name, '陆伟@微信')
  assert.equal(best.screen_x, 300, 'screen_x = overlay.x + item.x')
  assert.equal(best.screen_y, 170, 'screen_y = overlay.y + item.y')
  assert.equal(best.confidence, 0.71)
  assert.deepEqual(best.probabilities, { R0: 0.2, R1: 0.71, R2: 0.09 })
  assert.equal(best.target_ref, 'ref:contact:陆伟@微信::200,120')
  const items = r.data.items as Array<{ name: string; x: number; y: number; probability: number | null; target_ref: string }>
  assert.equal(items.length, 3)
  assert.equal(items[0]!.probability, 0.2)
  assert.equal(items[0]!.x, 200)
  assert.equal(items[2]!.target_ref, 'ref:group:产品讨论群:3人:210,200')
  const jev = r.data.jev as Record<string, unknown>
  assert.equal(jev.used, true)
  assert.equal(jev.best_confidence, 0.71)
  assert.equal(jev.is_ambiguous, false)
  assert.equal(jev.ambiguous_confidence, 0.93)
  assert.deepEqual(r.data.timing_ms, { focus: 250, box_check: 900, typing: 1200, overlay: 2100, jev: 812, total: 5300 })
  assert.match(r.message, /Jev 最优「陆伟@微信」/)
})

test('v2：空 items → TARGET_NOT_FOUND + reason=no_results，data 附 jev/timing 透传', async () => {
  const { op } = makeOp(async () => ({
    query: '不存在的人',
    items: [],
    best_index: null,
    overlay: { x: 100, y: 50, w: 400, h: 84 },
    jev: { used: false, latency_ms: 0, reason: 'no_items' },
    timing_ms: { focus: 250, box_check: 900, typing: 1100, overlay: 1900, jev: 0, total: 4200 },
  }))
  const r = await op.execute({ query: '不存在的人' }, silentCtx())
  assert.equal(r.success, false)
  assert.equal(r.code, 'TARGET_NOT_FOUND')
  assert.equal(r.effect, 'none')
  assert.equal(r.retryable, false)
  assert.equal(r.data.search_successful, false)
  assert.equal(r.data.reason, 'no_results')
  assert.deepEqual(r.data.items, [])
  assert.equal(r.data.best, null)
  assert.equal((r.data.jev as { reason?: string }).reason, 'no_items')
  assert.equal((r.data.timing_ms as { total?: number }).total, 4200)
})

test('v2：type 过滤把驱动 best 滤掉 → 从过滤后 items 重选（probability 最高）', async () => {
  const { op } = makeOp(async () => ({
    items: [
      { name: '陆伟', subtitle: '', section: '联系人', x: 200, y: 60, probability: 0.55 },
      { name: '产品讨论群', subtitle: '3人', section: '群聊', x: 210, y: 120, probability: 0.8 },
      { name: '项目协调群', subtitle: '', section: '群聊', x: 210, y: 180, probability: 0.3 },
    ],
    best_index: 0,
    overlay: { x: 10, y: 20, w: 400, h: 500 },
    jev: { used: true, latency_ms: 700, best_confidence: 0.55 },
    timing_ms: { total: 3000 },
  }))
  const r = await op.execute({ query: '群', type: 'group' }, silentCtx())
  assert.equal(r.success, true)
  const items = r.data.items as Array<{ name: string }>
  assert.equal(items.length, 2)
  const best = r.data.best as Record<string, unknown>
  assert.equal(best.name, '产品讨论群', 'best 重选为过滤后 probability 最高项')
  assert.equal(best.confidence, 0.8, 'jev.best_confidence 不适用（指向被滤项）时回落到条目概率')
  assert.equal(best.screen_x, 220)
  // 概率分布仍覆盖驱动全部原始条目（含被 type 过滤的 R0）
  assert.deepEqual(best.probabilities, { R0: 0.55, R1: 0.8, R2: 0.3 })
})

test('v2：过滤后 best 全 null probability → 重选第一条；全被滤掉 → filtered_out', async () => {
  const { op } = makeOp(async () => ({
    items: [
      { name: '产品讨论群', subtitle: '', section: '群聊', x: 210, y: 120, probability: null },
      { name: '项目协调群', subtitle: '', section: '群聊', x: 210, y: 180, probability: null },
    ],
    best_index: 5, // 越界/无效 → 不在集合内，触发重选
    overlay: { x: 10, y: 20, w: 400, h: 500 },
    jev: { used: false, latency_ms: 0, reason: 'no_api_key' },
    timing_ms: { total: 2000 },
  }))
  const r = await op.execute({ query: '群', type: 'group' }, silentCtx())
  assert.equal(r.success, true)
  const best = r.data.best as Record<string, unknown>
  assert.equal(best.name, '产品讨论群', '全 null 重选第一条')
  assert.equal(best.confidence, null)

  const { op: op2 } = makeOp(async () => ({
    items: [{ name: '陆伟', subtitle: '', section: '联系人', x: 200, y: 60, probability: 0.9 }],
    best_index: 0,
    overlay: { x: 10, y: 20, w: 400, h: 500 },
    jev: { used: true, latency_ms: 600, best_confidence: 0.9 },
    timing_ms: { total: 2000 },
  }))
  const r2 = await op2.execute({ query: '陆伟', type: 'group' }, silentCtx())
  assert.equal(r2.success, false)
  assert.equal(r2.code, 'TARGET_NOT_FOUND')
  assert.equal(r2.data.reason, 'filtered_out')
  assert.deepEqual(r2.data.items, [])
  assert.equal(r2.data.best, null)
  assert.equal((r2.data.jev as { used?: boolean }).used, true, 'filtered_out 分支仍透传 jev')
})

test('v2：jev degraded → probability 全 null，best 按驱动规则结果（name==query 优先）', async () => {
  const { op } = makeOp(async () => ({
    items: [
      { name: '产品讨论群', subtitle: '3人', section: '群聊', x: 210, y: 120, probability: null },
      { name: '陆伟', subtitle: '微信联系人', section: '联系人', x: 200, y: 60, probability: null },
    ],
    best_index: 1, // 驱动规则：归一化 name == query 的第一条
    overlay: { x: 100, y: 50, w: 400, h: 300 },
    jev: { used: false, latency_ms: 0, reason: 'no_api_key' },
    timing_ms: { total: 1800 },
  }))
  const r = await op.execute({ query: '陆伟' }, silentCtx())
  assert.equal(r.success, true)
  const best = r.data.best as Record<string, unknown>
  assert.equal(best.name, '陆伟')
  assert.equal(best.confidence, null)
  assert.deepEqual(best.probabilities, { R0: null, R1: null })
  const jev = r.data.jev as Record<string, unknown>
  assert.equal(jev.used, false)
  assert.equal(jev.reason, 'no_api_key')
  assert.doesNotMatch(r.message, /Jev/, '降级时 message 不提 Jev')
})

test('v2：target_ref 真实签发含 overlay 相对坐标；老式无坐标 ref 仍可验证', async () => {
  const keyDir = mkdtempSync(join(tmpdir(), 'wecom-targetref-v2-'))
  tempDirs.push(keyDir)
  const { op } = makeOp(
    async () => ({
      items: [{ name: '陆伟', subtitle: '微信联系人', section: '联系人', x: 180, y: 88, probability: null }],
      best_index: 0,
      overlay: { x: 100, y: 50, w: 400, h: 300 },
      jev: { used: false, latency_ms: 0, reason: 'no_items' },
      timing_ms: { total: 900 },
    }),
    (name, type, subtitle, coords) => createTargetRef(name, type, subtitle, { keyDir, coords }),
  )
  const r = await op.execute({ query: '陆伟' }, silentCtx())
  assert.equal(r.success, true)
  const items = r.data.items as Array<{ target_ref: string }>
  const id = verifyTargetRef(items[0]!.target_ref, { keyDir })
  assert.equal(id.name, '陆伟')
  assert.equal(id.type, 'contact')
  assert.equal(id.subtitle, '微信联系人')
  assert.equal(id.x, 180, 'verify 解出 payload 内的 overlay 相对坐标')
  assert.equal(id.y, 88)

  // 老式（无坐标）签发与验证不受影响
  const oldRef = createTargetRef('张三', 'contact', '', { keyDir })
  const oldId = verifyTargetRef(oldRef, { keyDir })
  assert.equal(oldId.x, undefined)
  assert.equal(oldId.y, undefined)
})
