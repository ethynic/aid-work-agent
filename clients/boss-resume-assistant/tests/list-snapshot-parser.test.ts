import assert from 'node:assert/strict'
import test from 'node:test'
import { ListSnapshotParser, makeFingerprint, SAFE_INSET_TOP } from '../src/main/boss/ListSnapshotParser.js'
import type { DomSnapshot } from '../src/main/boss/domSnapshot.js'

/**
 * 构造测试 snapshot。推荐 frame（document 0）bounds y=200（避开顶部 150px header），
 * 候选文本 bounds [150, 200, 48, 17]（在 frame 内）。
 * 支持 sparse / dense 两种序列化与重复文本歧义。
 */
function buildSnapshot({ dense = false, duplicate = false } = {}): DomSnapshot {
  const nodeValue = dense
    ? [-1, 1, ...(duplicate ? [1] : [])]
    : { index: duplicate ? [1, 2] : [1], value: duplicate ? [1, 1] : [1] }
  return {
    strings: ['', '候选甲'],
    documents: [
      {
        // document 0：推荐 frame，bounds 起点在 (0, 200)，整张 frame 完全在安全区内
        nodes: { nodeValue: [-1], contentDocumentIndex: dense ? [1] : { index: [0], value: [1] } },
        layout: { nodeIndex: [0], bounds: [[0, 200, 1000, 700]] },
      },
      {
        nodes: { nodeValue, contentDocumentIndex: dense ? [-1, -1, -1] : { index: [], value: [] } },
        layout: {
          nodeIndex: duplicate ? [1, 2] : [1],
          bounds: duplicate ? [[150, 100, 48, 17], [300, 100, 48, 17]] : [[150, 100, 48, 17]],
        },
      },
    ],
  }
}

const opts = { viewport: { width: 1280, height: 960 }, fingerprintKey: 'test-key', fingerprintContext: 'job-1' }

test('sparse DOMSnapshot 定位唯一候选文本', () => {
  const r = new ListSnapshotParser().locateUniqueCandidate(buildSnapshot(), '候选甲', opts)
  assert.equal(r.status, 'LOCATED')
  if (r.status === 'LOCATED') {
    // owner offset (0,200) + bounds [150,100,48,17] center (174, 108.5) = (174, 308.5)
    assert.deepEqual(r.point, { x: 174, y: 308.5 })
    assert.equal(r.name, '候选甲')
  }
})

test('Chrome 150 dense-array 序列化同样定位', () => {
  const r = new ListSnapshotParser().locateUniqueCandidate(buildSnapshot({ dense: true }), '候选甲', opts)
  assert.equal(r.status, 'LOCATED')
})

test('文本有多个匹配 → UNLOCATABLE（不猜）', () => {
  const r = new ListSnapshotParser().locateUniqueCandidate(buildSnapshot({ duplicate: true }), '候选甲', opts)
  assert.equal(r.status, 'UNLOCATABLE')
  assert.match((r as { reason: string }).reason, /exactly one visible match/)
})

test('文本不在 snapshot 中 → UNLOCATABLE', () => {
  const r = new ListSnapshotParser().locateUniqueCandidate(buildSnapshot(), '候选人乙', opts)
  assert.equal(r.status, 'UNLOCATABLE')
})

test('招聘写动作文案 → UNLOCATABLE', () => {
  const r = new ListSnapshotParser().locateUniqueCandidate(buildSnapshot(), '打招呼', opts)
  assert.equal(r.status, 'UNLOCATABLE')
  assert.match((r as { reason: string }).reason, /write action/)
})

test('点击点在顶部 unsafe 区（被 header 遮挡）→ UNLOCATABLE', () => {
  // bounds y=10 → center y ≈ 18.5，加 owner offset y=40 = 58.5 < SAFE_INSET_TOP(150)
  const snap: DomSnapshot = {
    strings: ['', '候选乙'],
    documents: [
      { nodes: { nodeValue: [-1], contentDocumentIndex: { index: [0], value: [1] } }, layout: { nodeIndex: [0], bounds: [[0, 0, 1000, 900]] } },
      { nodes: { nodeValue: { index: [1], value: [1] }, contentDocumentIndex: { index: [], value: [] } }, layout: { nodeIndex: [1], bounds: [[10, 10, 48, 17]] } },
    ],
  }
  const r = new ListSnapshotParser().locateUniqueCandidate(snap, '候选乙', opts)
  assert.equal(r.status, 'UNLOCATABLE')
  assert.match((r as { reason: string }).reason, /outside safe viewport/)
  // 确认是因为顶部 inset
  assert.ok(SAFE_INSET_TOP === 150)
})

test('指纹稳定且含 context', () => {
  const fp1 = makeFingerprint('k', 'job-1', '张三')
  const fp2 = makeFingerprint('k', 'job-1', '张三')
  const fp3 = makeFingerprint('k', 'job-2', '张三')
  assert.equal(fp1, fp2, 'same inputs → same fingerprint')
  assert.notEqual(fp1, fp3, 'different context → different fingerprint')
  assert.equal(fp1.length, 16)
})

test('locateUniqueCandidate 返回的 fingerprint 与 makeFingerprint 一致', () => {
  const r = new ListSnapshotParser().locateUniqueCandidate(buildSnapshot(), '候选甲', opts)
  if (r.status !== 'LOCATED') throw new Error('expected LOCATED')
  assert.equal(r.fingerprint, makeFingerprint('test-key', 'job-1', '候选甲'))
})
