import assert from 'node:assert/strict'
import { test } from 'node:test'
import { randomUUID } from 'node:crypto'
import { createBossRecoveryProbeOperation } from '../src/main/operations/bossRecoveryProbe.js'
import type { CdpGateway } from '../src/main/cdp/CdpGateway.js'

function fixture(count = 1, path = 'index', label: string | null = '筛选·2', panel = false) {
  const calls: string[] = []
  const targets = Array.from({ length: count }, (_, n) => ({ targetId: `isolated-${randomUUID()}-${n}`, type: 'page', url: `https://www.zhipin.com/web/chat/${path}`, title: 'test' }))
  const gateway = {
    async connect(endpoint: string) { assert.equal(endpoint, 'http://127.0.0.1:9222'); calls.push('connect-existing') },
    async getTargets() { calls.push('getTargets'); return targets },
    async attachToBoundBossTarget(target: string) { assert.equal(target, targets[0]!.targetId); calls.push('attach-exact') },
    async captureDomSnapshot() { calls.push('snapshot');
      const texts = [...(label ? [label] : []), ...(panel ? ['经验要求'] : [])]
      return { strings: ['', ...texts], documents: [{ nodes: { nodeValue: [0, ...texts.map((_, n) => n + 1)], contentDocumentIndex: [], parentIndex: [0, ...texts.map(() => 0)] },
        layout: { nodeIndex: [0, ...texts.map((_, n) => n + 1)], bounds: [[0, 0, 1280, 900], ...texts.map((_, n) => [900, 100 + n * 40, 80, 25])] } }] }
    },
    async close() { calls.push('close') },
  }
  return { calls, operation: createBossRecoveryProbeOperation(() => gateway as unknown as CdpGateway) }
}

test('unbound recovery reads a single existing BOSS tab without launching, focus, input or visual recovery', async () => {
  const { calls, operation } = fixture()
  const result = await operation.execute({}, { signal: new AbortController().signal, progress() {} })
  assert.equal(result.success, true)
  assert.equal(result.data.target_relation, 'current_unbound')
  assert.deepEqual(result.data.page_summary, { observed: true, page_kind: 'chat', result: 'unknown' })
  assert.deepEqual(calls, ['connect-existing', 'getTargets', 'attach-exact', 'snapshot', 'getTargets', 'close'])
})

test('a stale supplied original binding cannot select another current tab', async () => {
  const { calls, operation } = fixture()
  const result = await operation.execute({}, { browserTargetRef: randomUUID(), signal: new AbortController().signal, progress() {} })
  assert.equal(result.success, false)
  assert.equal(result.code, 'WRONG_PAGE')
  assert.deepEqual(calls, ['close'])
})

test('unbound multiple tabs are reported, never picked as the old page', async () => {
  const { calls, operation } = fixture(2)
  const result = await operation.execute({}, { signal: new AbortController().signal, progress() {} })
  assert.equal(result.success, false)
  assert.equal(result.code, 'WRONG_PAGE')
  assert.deepEqual(calls, ['connect-existing', 'getTargets', 'close'])
})

for (const [label, panel, expected] of [['筛选', false, 'match'], ['筛选·2', false, 'mismatch'], [null, false, 'unknown'], ['筛选', true, 'mismatch']] as const) {
  test(`clear verification reads visible badge/panel: ${label}/${panel} => ${expected}`, async () => {
    const { calls, operation } = fixture(1, 'recommend', label, panel)
    const result = await operation.execute({ expectation: { kind: 'clear_filter' } }, { signal: new AbortController().signal, progress() {} })
    assert.equal(result.success, true, 'observation succeeded, independently of whether the expected page state matches')
    assert.equal((result.data.page_summary as Record<string, unknown>).result, expected)
    assert.ok(calls.includes('snapshot'))
  })
}

test('same filter count is evidence only for count, never proof of the full original selection', async () => {
  const { operation } = fixture(1, 'recommend', '筛选·2')
  const result = await operation.execute({ expectation: { kind: 'filter', count: 2 } }, { signal: new AbortController().signal, progress() {} })
  assert.deepEqual(result.data.page_summary, { observed: true, page_kind: 'recommend', result: 'unknown', filter_count: 2, panel_open: false, count_result: 'match' })
})

test('navigation uses existing exact URL rule instead of hidden recommendation iframe text', async () => {
  const { operation } = fixture(1, 'index', '筛选')
  const result = await operation.execute({ expectation: { kind: 'navigation', target: 'recommend' } }, { signal: new AbortController().signal, progress() {} })
  assert.deepEqual(result.data.page_summary, { observed: true, page_kind: 'chat', result: 'mismatch' })
})
