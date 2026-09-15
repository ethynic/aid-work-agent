import test from 'node:test'
import assert from 'node:assert/strict'
import { segmentTextBubbles, textFromBubbleRegions, type BubbleLayout } from '../src/platform/bubbleSegmentation.js'

const layout: BubbleLayout = {
  messageRegion: { x0: 0, y0: 0, x1: 300, y1: 200 }, peerLeft: 20, selfRight: 280,
  edgeTolerance: 2, peerFill: [238, 238, 240], selfFill: [157, 242, 159], minWidth: 20, minHeight: 15,
}
function fixture() {
  const rgba = new Uint8Array(300 * 200 * 4).fill(250)
  const rect = (x: number, y: number, w: number, h: number, color: readonly number[]) => {
    for (let py = y; py < y + h; py++) for (let px = x; px < x + w; px++) {
      const i = (py * 300 + px) * 4
      rgba.set([...color, 255], i)
    }
  }
  return { frame: { width: 300, height: 200, rgba }, rect }
}

test('长绿色本人文本跨越中央仍根据气泡右边缘识别self', () => {
  const { frame, rect } = fixture()
  rect(40, 30, 240, 30, layout.selfFill)
  const regions = segmentTextBubbles(frame, layout)
  assert.equal(regions.length, 1)
  assert.equal(regions[0]!.sender, 'self')
  assert.deepEqual(textFromBubbleRegions(regions, [{ text: '长句', x0: 45, y0: 35, x1: 100, y1: 50 }]), { kind: 'candidates', messages: [{ sender: 'self', text: '长句' }] })
})

test('历史截图内部绿气泡未停靠真实会话边缘，不纳入OCR正文', () => {
  const { frame, rect } = fixture()
  rect(150, 20, 100, 100, [237, 237, 237])
  rect(180, 45, 65, 20, layout.selfFill)
  rect(20, 140, 100, 25, layout.peerFill)
  const regions = segmentTextBubbles(frame, layout)
  assert.equal(regions.length, 1)
  assert.deepEqual(textFromBubbleRegions(regions, [
    { text: '图片中文字', x0: 185, y0: 50, x1: 235, y1: 60 },
    { text: '真实正文', x0: 25, y0: 145, x1: 110, y1: 155 },
  ]), { kind: 'candidates', messages: [{ sender: 'peer', text: '真实正文' }] })
})

test('灰色卡片有彩色图标拒绝，裁剪边缘气泡拒绝', () => {
  const { frame, rect } = fixture()
  rect(20, 30, 150, 60, layout.peerFill)
  rect(28, 40, 20, 20, [0, 70, 160])
  rect(20, 0, 70, 20, layout.peerFill)
  assert.deepEqual(segmentTextBubbles(frame, layout), [])
})

test('多个同行方向独立气泡不因OCR邻近合并', () => {
  const { frame, rect } = fixture()
  rect(20, 30, 80, 20, layout.peerFill)
  rect(20, 60, 80, 20, layout.peerFill)
  const regions = segmentTextBubbles(frame, layout)
  assert.equal(regions.length, 2)
  assert.equal(textFromBubbleRegions(regions, [
    { text: 'a', x0: 25, y0: 35, x1: 40, y1: 45 },
    { text: 'b', x0: 25, y0: 65, x1: 40, y1: 75 },
  ]).messages.length, 2)
})

test('OCR缺失和非法坐标不得返回部分正文', () => {
  const regions = [{ bounds: { x0: 20, y0: 20, x1: 100, y1: 100 }, sender: 'peer' as const }]
  const b = { text: 'hello', x0: 25, y0: 25, x1: 70, y1: 35 }
  for (const boxes of [[], [{ ...b, y0: NaN }]]) {
    const result = textFromBubbleRegions(regions, boxes)
    assert.equal(result.kind, 'gap')
    assert.deepEqual(result.messages, [])
  }
  assert.deepEqual(textFromBubbleRegions(regions, [b, { ...b, text: 'world', y0: 45, y1: 55 }]),
    { kind: 'candidates', messages: [{ sender: 'peer', text: 'hello\nworld' }] })
})

test('非法色彩和资源超限拒绝', () => {
  const { frame } = fixture()
  for (const peerFill of [[0, 0, 0], [NaN, 240, 240], [300, 240, 240], layout.selfFill]) {
    assert.throws(() => segmentTextBubbles(frame, { ...layout, peerFill: peerFill as [number, number, number] }))
  }
  assert.equal(textFromBubbleRegions([], Array.from({ length: 2049 }, () => ({ text: 'x', x0: 1, y0: 1, x1: 2, y1: 2 }))).kind, 'gap')
})

test('对抗图片可模拟停靠气泡：返回仍只是候选而非媒体认证', () => {
  const { frame, rect } = fixture()
  // A bitmap can draw exactly the same pixels as a real bubble.
  rect(100, 30, 180, 30, layout.selfFill)
  const regions = segmentTextBubbles(frame, layout)
  assert.equal(regions.length, 1)
  const result = textFromBubbleRegions(regions, [{ text: 'image text', x0: 110, y0: 35, x1: 200, y1: 50 }])
  assert.equal(result.kind, 'candidates')
  assert.equal('coverage' in result, false)
})

test('overlapping boxes and small boundary crossing assemble by row then x', () => {
  const regions = [{ bounds: { x0: 20, y0: 20, x1: 160, y1: 100 }, sender: 'self' as const }]
  const result = textFromBubbleRegions(regions, [
    { text: 'right', x0: 66, y0: 24, x1: 150, y1: 40 },
    { text: 'left', x0: 18, y0: 25, x1: 70, y1: 39 },
  ])
  assert.deepEqual(result, { kind: 'candidates', messages: [{ sender: 'self', text: 'left right' }] })
})
