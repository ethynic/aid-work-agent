import { withDetailControls } from './detailGreetFixture.js'
import assert from 'node:assert/strict'
import test from 'node:test'
import fs from 'node:fs/promises'
import { accumulateOwnerOffset, type DomDocument, type DomSnapshot } from '../src/main/boss/domSnapshot.js'
import { locateResumeCanvas } from '../src/main/boss/ResumeReader.js'
import { ResumeBatchReader, type ResumeBatchDeps } from '../src/main/boss/ResumeBatchReader.js'

/**
 * 匿名合成几何：根→可滚推荐iframe→固定可见详情iframe，复现本机737/2948父滚动。
 *
 * 2026-11-13 重建：locateCards/readBatch 管线自 loadedCandidates 结构配对切换后，叶子文档
 * 必须携带真实卡片结构（card-list-wrap→list-body→card-list→card-item→name/btn-greet，
 * 与 loadedCandidateFixture/structuralListFixture 同构），否则配对返回空、测试长期红。
 * 几何断言数字与原版一致（owner 偏移 336/80、姓名点 675.5/305.75、按钮 y 321.25）。
 */
function nestedSnapshot(scrollY: number, dense = false, canvas = true): DomSnapshot {
  const strings = ['']
  const str = (s: string) => { let i = strings.indexOf(s); if (i < 0) { i = strings.length; strings.push(s) }; return i }
  function makeDoc(width: number, height: number, scrollX = 0, scrollY = 0) {
    const values: number[] = [], tags: number[] = [], parents: number[] = [], attrs: number[][] = [], frames: number[] = []
    const indices: number[] = [], bounds: number[][] = [], clients: number[][] = []
    const add = (tag: string, parent: number, cls: string, b: number[], text = ''): number => {
      const n = values.length
      values.push(str(text)); tags.push(str(tag)); parents.push(parent)
      attrs.push(cls ? [str('class'), str(cls)] : []); frames.push(-1)
      indices.push(n); bounds.push(b); clients.push([0, 0, b[2]! / 2, b[3]! / 2])
      return n
    }
    const d: DomDocument = {
      nodes: { nodeValue: values, nodeName: tags, parentIndex: parents, attributes: attrs, contentDocumentIndex: frames },
      layout: { nodeIndex: indices, bounds, clientRects: clients },
      scrollOffsetX: scrollX, scrollOffsetY: scrollY,
    }
    return { d, add }
  }

  // 文档0（根）：视口 2504x1474；HTML 是 rootCssScale 的缩放基准（bounds=2x clientRects）
  const root = makeDoc(2504, 1474)
  const html0 = root.add('HTML', -1, '', [0, 0, 2504, 1474])
  root.add('IFRAME', html0, '', [336, 80, 2168, 1394])
  ;(root.d.nodes.contentDocumentIndex as number[])[1] = 1

  // 文档1（可滚推荐 iframe）：自身 scrollY 与内部 iframe 的 y 抵消 → owner 偏移与滚动无关
  const mid = makeDoc(1468, 5676, 0, scrollY)
  const html1 = mid.add('HTML', -1, '', [0, 0, 1468, 5676])
  mid.add('IFRAME', html1, '', [0, scrollY, 1468, 1394])
  ;(mid.d.nodes.contentDocumentIndex as number[])[1] = 2

  // 文档2（固定可见详情 iframe）：完整卡片结构（loadedCandidates 结构配对所需）+ 可选简历画布
  const leaf = makeDoc(1468, 1394, 0, 0)
  const html2 = leaf.add('HTML', -1, '', [0, 0, 1468, 1394])
  if (canvas) leaf.add('CANVAS', html2, '', [0, 0, 1468, 1394])
  const wrap = leaf.add('DIV', html2, 'list-wrap card-list-wrap', [0, 0, 1468, 1394])
  const body = leaf.add('DIV', wrap, 'list-body', [0, 0, 1468, 1394])
  const list = leaf.add('DIV', body, 'card-list', [0, 0, 1468, 1394])
  const card = leaf.add('DIV', list, 'card-item', [30, 246, 1400, 160])
  const row = leaf.add('DIV', card, 'row name-wrap', [317, 246, 500, 60])
  const name = leaf.add('SPAN', row, 'name', [317, 266, 50, 20])
  leaf.add('#text', name, '', [317, 266, 50, 20], '张三丰')
  leaf.add('#text', row, '', [370, 266, 60, 20], '刚刚活跃')
  const actions = leaf.add('SPAN', card, 'btn-doc', [1100, 246, 120, 90])
  const buttons = leaf.add('DIV', actions, 'button-list', [1130, 275.5, 64, 32])
  const button = leaf.add('BUTTON', buttons, 'btn btn-greet', [1130, 275.5, 64, 32])
  leaf.add('#text', button, '', [1130, 275.5, 64, 32], '打招呼')

  if (!dense) {
    // 稀疏形（{index,value}）与稠密形（数组）双形支持：indexedValues 两种都得吃
    for (const d of [root.d, mid.d, leaf.d]) {
      const entries = (d.nodes.contentDocumentIndex as number[]).map((v, i) => [i, v] as const).filter(([, v]) => v >= 0)
      d.nodes.contentDocumentIndex = { index: entries.map(([i]) => i), value: entries.map(([, v]) => v) }
    }
  }
  return { strings, documents: [root.d, mid.d, leaf.d] }
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
  const snap = nestedSnapshot(737, false, false)
  snap.documents[0]!.scrollOffsetX = 17.5
  snap.documents[0]!.scrollOffsetY = 23.25
  snap.documents[1]!.scrollOffsetX = 41.25
  snap.documents[1]!.layout.bounds[1]![0] = 41.25
  const leaf = snap.documents[2]!
  leaf.scrollOffsetX = 2.75
  leaf.scrollOffsetY = 7.125
  // 叶子文档内新增一个 iframe 节点挂第4个文档（索引取当前节点数，避免与既有卡片节点冲突）
  const ownerNode = leaf.layout.nodeIndex.length
  leaf.nodes.contentDocumentIndex = { index: [ownerNode], value: [3] }
  leaf.layout.nodeIndex.push(ownerNode)
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
        // 列表态无画布（locateResumeCanvas 为 null）；详情态带画布并补操作列控件
        const snap = nestedSnapshot(scrollY, false, detail)
        return detail ? withDetailControls(snap) : snap
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
