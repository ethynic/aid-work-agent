import assert from 'node:assert/strict'
import test from 'node:test'
import { createWeixinSessionObserveOperation, type CaptureFn } from '../src/operations/sessionObserve.js'
import { validateSessionObservation } from '../src/platform/sessionObserver.js'

const request = { conversation_binding_id: 'binding', binding_version: 1, account_identity_version: 2, watermark: null }
const ctx = { signal: new AbortController().signal, progress: () => {} }
const capture: CaptureFn = async () => ({
  image: 'test-only.png', region: { minX: 0, maxX: 800, selfStartX: 500 },
  identity: { conversation_binding_id: 'binding', binding_version: 1, account_identity_version: 2 },
  evidence_ref: 'dpapi:controlled_test_evidence',
})

test('空基线聚合期A到A+B复用A的ID', async () => {
  let boxes: Array<{ text: string; x0: number; y0: number; x1: number; y1: number }> = []
  const op = createWeixinSessionObserveOperation({ captureFn: capture, recognizeFn: async () => boxes })
  const baseline = (await op.execute(request, ctx)).data
  const watermark = { last_local_message_id: null, window_fingerprint: baseline.window_fingerprint as string }
  boxes = [{ text: 'A', x0: 10, y0: 10, x1: 100, y1: 30 }]
  const first = (await op.execute({ ...request, watermark }, ctx)).data
  boxes.push({ text: 'B', x0: 10, y0: 100, x1: 100, y1: 120 })
  const next = (await op.execute({ ...request, watermark }, ctx)).data
  const a = first.ordered_messages as Array<{ local_message_id: string }>
  const ab = next.ordered_messages as Array<{ local_message_id: string }>
  assert.equal(ab.length, 2)
  assert.equal(ab[0]!.local_message_id, a[0]!.local_message_id)
})

test('实际身份版本与请求同步更新也不能接受旧指纹', async () => {
  let version = 1
  const op = createWeixinSessionObserveOperation({
    captureFn: async id => { const c = await capture(id); return { ...c, identity: { ...c.identity, binding_version: version } } },
    recognizeFn: async () => [],
  })
  const baseline = (await op.execute(request, ctx)).data
  version = 2
  const changed = await op.execute({ ...request, binding_version: 2, watermark: { last_local_message_id: null, window_fingerprint: baseline.window_fingerprint as string } }, ctx)
  assert.equal(changed.data.coverage, 'gap')
})

test('LRU淘汰第65个绑定后旧水位报gap', async () => {
  const op = createWeixinSessionObserveOperation({
    captureFn: async id => { const c = await capture(id); return { ...c, identity: { ...c.identity, conversation_binding_id: id } } },
    recognizeFn: async () => [],
  })
  const baseline = (await op.execute(request, ctx)).data
  for (let i = 0; i < 64; i++) await op.execute({ ...request, conversation_binding_id: `binding-${i}` }, ctx)
  const evicted = await op.execute({ ...request, watermark: { last_local_message_id: null, window_fingerprint: baseline.window_fingerprint as string } }, ctx)
  assert.equal(evicted.data.coverage, 'gap')
})

test('真实采集未接线时明确不可用，非法参数结构化拒绝', async () => {
  const op = createWeixinSessionObserveOperation()
  assert.equal((await op.execute(request, ctx)).data.coverage, 'unavailable')
  assert.equal((await op.execute(null as never, ctx)).code, 'INVALID_ARGUMENT')
})

test('身份漂移与缺失证据不能进入OCR或冒充complete_window', async () => {
  for (const bad of [
    { identity: { conversation_binding_id: 'other', binding_version: 1, account_identity_version: 2 } },
    { evidence_ref: '' },
  ]) {
    const op = createWeixinSessionObserveOperation({
      captureFn: async id => ({ ...await capture(id), ...bad }),
      recognizeFn: async () => { assert.fail('身份/证据未验证不应OCR') },
    })
    const result = await op.execute(request, ctx)
    assert.equal(result.data.coverage, 'unavailable')
    assert.deepEqual(result.data.ordered_messages, [])
  }
})

test('相同窗口指纹稳定，证据来自采集器；新增气泡只输出一次', async () => {
  let boxes = [{ text: '第一条', x0: 10, y0: 10, x1: 100, y1: 30 }]
  const op = createWeixinSessionObserveOperation({ captureFn: capture, recognizeFn: async () => boxes })
  const baseline = (await op.execute(request, ctx)).data
  assert.equal(validateSessionObservation(baseline).ok, true)
  const messages = baseline.ordered_messages as Array<{ local_message_id: string; source_evidence_ref: string }>
  assert.equal(messages[0]!.source_evidence_ref, 'dpapi:controlled_test_evidence')
  const watermark = { last_local_message_id: messages[0]!.local_message_id, window_fingerprint: baseline.window_fingerprint as string }
  const unchanged = (await op.execute({ ...request, watermark }, ctx)).data
  assert.equal(unchanged.window_fingerprint, baseline.window_fingerprint)
  assert.deepEqual(unchanged.ordered_messages, [])
  boxes = [...boxes, { text: '第二条', x0: 10, y0: 100, x1: 100, y1: 120 }]
  const next = (await op.execute({ ...request, watermark }, ctx)).data
  assert.equal((next.ordered_messages as unknown[]).length, 1)
})

test('Provider重启后空水位也报gap，不将已有消息重新当连续新消息', async () => {
  const deps = { captureFn: capture, recognizeFn: async () => [] }
  const first = (await createWeixinSessionObserveOperation(deps).execute(request, ctx)).data
  const restarted = createWeixinSessionObserveOperation(deps)
  const result = await restarted.execute({ ...request, watermark: { last_local_message_id: null, window_fingerprint: first.window_fingerprint as string } }, ctx)
  assert.equal(result.data.coverage, 'gap')
  assert.equal(result.data.gap_reason, 'alignment_broken')
  assert.equal(validateSessionObservation(result.data).ok, true)
})
