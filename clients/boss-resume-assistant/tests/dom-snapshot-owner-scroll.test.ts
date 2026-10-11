import assert from 'node:assert/strict'
import test from 'node:test'
import fs from 'node:fs/promises'
import { accumulateOwnerOffset, type DomSnapshot } from '../src/main/boss/domSnapshot.js'
import { locateResumeCanvas } from '../src/main/boss/ResumeReader.js'
import { ResumeBatchReader, type ResumeBatchDeps } from '../src/main/boss/ResumeBatchReader.js'

/** 匿名合成几何：根→可滚推荐iframe→固定可见详情iframe，复现本机737/2948父滚动。 */
function nestedSnapshot(scrollY: number, dense = false): DomSnapshot {
  const owner = (child: number) => dense ? [-1, child] : { index: [1], value: [child] }
  return {
    strings: ['', 'CANVAS', '张三丰', '打招呼', '刚刚活跃'],
    documents: [
      { nodes: { nodeValue: [], contentDocumentIndex: owner(1) },
        layout: { nodeIndex: [0, 1], bounds: [[0, 0, 2504, 1474], [336, 80, 2168, 1394]] } },
      { nodes: { nodeValue: [], contentDocumentIndex: owner(2) },
        layout: { nodeIndex: [0, 1], bounds: [[0, 0, 2168, 5676], [0, scrollY, 1468, 1394]] },
        scrollOffsetY: scrollY },
      { nodes: { nodeValue: [0, 2, 3, 4], nodeName: [1, 0, 0, 0], contentDocumentIndex: [] },
        layout: { nodeIndex: [0, 1, 2, 3], bounds: [
          [0, 0, 1468, 1394], [317, 266, 50, 20], [1130, 275.5, 64, 32], [370, 266, 60, 20],
        ] }, scrollOffsetX: 0, scrollOffsetY: 0 },
    ],
  }
}

const fullCanvas = { x: 336, y: 80, w: 1468, h: 1394 }

test('祖先滚动回归：固定可见详情owner在父偏移0/737/2948下保持同一视口原点', () => {
  for (const dense of [false, true]) {
    for (const scrollY of [0, 737, 2948, -43.75]) {
      assert.deepEqual(accumulateOwnerOffset(nestedSnapshot(scrollY, dense), 2), { x: 336, y: 80 })
    }
  }
})

test('祖先滚动回归：完整CANVAS在父偏移737/2948后不裁短、不误报不可见', () => {
  for (const dense of [false, true]) {
    for (const scrollY of [0, 737, 2948]) {
      assert.deepEqual(locateResumeCanvas(nestedSnapshot(scrollY, dense)), fullCanvas)
    }
  }
})

test('祖先滚动回归：嵌套卡片仍配对小数边界，leaf自身双轴偏移仅扣一次', () => {
  const snap = nestedSnapshot(737)
  snap.documents[2]!.scrollOffsetX = 2.5
  snap.documents[2]!.scrollOffsetY = 50.25
  const reader = new ResumeBatchReader({} as ResumeBatchDeps)
  assert.deepEqual(reader.locateCards(snap), [{ name: '张三丰',
    namePoint: { x: 675.5, y: 305.75 }, greetButtonY: 321.25 }])
  assert.deepEqual(accumulateOwnerOffset(snap, 2), { x: 336, y: 80 })
})

test('三层祖先逐层扣双轴小数滚动，目标文档自身滚动不计入owner', () => {
  const snap = nestedSnapshot(737)
  snap.documents[0]!.scrollOffsetX = 17.5
  snap.documents[0]!.scrollOffsetY = 23.25
  snap.documents[1]!.scrollOffsetX = 41.25
  snap.documents[1]!.layout.bounds[1]![0] = 41.25
  const leaf = snap.documents[2]!
  leaf.scrollOffsetX = 2.75
  leaf.scrollOffsetY = 7.125
  leaf.nodes.contentDocumentIndex = { index: [4], value: [3] }
  leaf.layout.nodeIndex.push(4)
  leaf.layout.bounds.push([42.5, 61.75, 800, 1000])
  snap.documents.push({ nodes: { nodeValue: [], contentDocumentIndex: [] },
    layout: { nodeIndex: [0], bounds: [[0, 0, 800, 1000]] }, scrollOffsetX: 99, scrollOffsetY: 100 })
  assert.deepEqual(accumulateOwnerOffset(snap, 0), { x: 0, y: 0 })
  assert.deepEqual(accumulateOwnerOffset(snap, 1), { x: 318.5, y: 56.75 })
  assert.deepEqual(accumulateOwnerOffset(snap, 2), { x: 318.5, y: 56.75 })
  assert.deepEqual(accumulateOwnerOffset(snap, 3), { x: 358.25, y: 111.375 })
})

test('缺省滚动字段保持原零滚动行为', () => {
  const snap = nestedSnapshot(0)
  for (const doc of snap.documents) { delete doc.scrollOffsetX; delete doc.scrollOffsetY }
  assert.deepEqual(accumulateOwnerOffset(snap, 2), { x: 336, y: 80 })
  assert.deepEqual(locateResumeCanvas(snap), fullCanvas)
})

test('祖先修复保留隐藏、缺失、多owner与循环拒绝，画布不误定位到隐藏frame', () => {
  const hidden = nestedSnapshot(737)
  hidden.documents[0]!.layout.nodeIndex = [0]
  hidden.documents[0]!.layout.bounds.length = 1
  assert.throws(() => accumulateOwnerOffset(hidden, 2), /no visible layout bounds/)
  assert.equal(locateResumeCanvas(hidden), null)
  const missing = nestedSnapshot(737)
  missing.documents[0]!.nodes.contentDocumentIndex = []
  assert.throws(() => accumulateOwnerOffset(missing, 2), /exactly one visible owner/)
  const duplicate = nestedSnapshot(737)
  duplicate.documents[0]!.nodes.contentDocumentIndex = { index: [1, 2], value: [1, 1] }
  assert.throws(() => accumulateOwnerOffset(duplicate, 2), /found 2/)
  const cycle = nestedSnapshot(737)
  cycle.documents[0]!.nodes.contentDocumentIndex = []
  cycle.documents[2]!.nodes.contentDocumentIndex = { index: [0], value: [1] }
  assert.throws(() => accumulateOwnerOffset(cycle, 2), /ancestry cycle/)
})

test('祖先滚动后读取管线把完整画布区域传给wheel、像素确认及stitch', async () => {
  for (const scrollY of [737, 2948]) {
    let detail = false
    const rects: Array<unknown> = []
    const reader = new ResumeBatchReader({
      snapshot: async () => {
        const snap = nestedSnapshot(scrollY)
        if (!detail) snap.documents[2]!.nodes.nodeName = [0, 0, 0, 0]
        return snap
      },
      click: async () => { detail = true }, pressEscape: async () => { detail = false },
      captureFullpage: async () => Buffer.alloc(1000),
      wheel: async (rect) => { rects.push(rect) },
      sameView: async (_a, _b, rect) => { rects.push(rect); return true },
      stitch: async (_parts, rect, outFile) => {
        rects.push(rect)
        await fs.writeFile(outFile, Buffer.from('synthetic-test-image'))
        return { width: rect.w, height: rect.h, overlaps: [], seamMis: [] }
      },
      sleep: async () => {},
    })
    const result = await reader.readBatch({ limit: 1 })
    assert.equal(result.resumes.length, 1)
    assert.deepEqual(result.failures, [])
    assert.ok(rects.length >= 5)
    for (const rect of rects) assert.deepEqual(rect, fullCanvas)
    assert.equal(result.resumes[0]!.readResult.width, 1468)
    assert.equal(result.resumes[0]!.readResult.height, 1394)
  }
})
