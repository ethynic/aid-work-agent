import assert from 'node:assert/strict'
import test from 'node:test'
import { TesseractJsProvider } from '../src/main/ocr/TesseractJsProvider.js'

/** mock tesseract worker，返回构造的 words */
function mockWorkerFactory(words: Array<{ text: string; confidence: number; bbox?: { x0: number; y0: number; x1: number; y1: number } }>) {
  return async () => ({
    recognize: async () => ({
      data: {
        text: words.map((w) => w.text).join(' '),
        confidence: 90,
        words: Object.fromEntries(
          words.map((w, i) => [
            String(i),
            { text: w.text, confidence: w.confidence, bbox: w.bbox ?? { x0: 0, y0: 0, x1: 10, y1: 10 } },
          ]),
        ),
      },
    }),
    terminate: async () => {},
  })
}

test('recognize 把 words 映射为 OcrBlock（带 box 和 confidence）', async () => {
  const p = new TesseractJsProvider({
    workerFactory: mockWorkerFactory([
      { text: '张三', confidence: 95, bbox: { x0: 10, y0: 20, x1: 50, y1: 40 } },
      { text: '前端工程师', confidence: 88, bbox: { x0: 10, y0: 50, x1: 100, y1: 70 } },
    ]) as never,
  })
  const r = await p.recognize({ image: Buffer.alloc(0), pageIndex: 2 })
  assert.equal(r.blocks.length, 2)
  assert.equal(r.blocks[0]!.text, '张三')
  assert.equal(r.blocks[0]!.confidence, 0.95) // 95 -> 0.95
  assert.deepEqual(r.blocks[0]!.box, [10, 20, 40, 20]) // [x0,y0,w,h]
  assert.equal(r.blocks[0]!.pageIndex, 2)
})

test('空文本的 word 被跳过', async () => {
  const p = new TesseractJsProvider({
    workerFactory: mockWorkerFactory([
      { text: '   ', confidence: 90 },
      { text: '有效', confidence: 80 },
    ]) as never,
  })
  const r = await p.recognize({ image: Buffer.alloc(0) })
  assert.equal(r.blocks.length, 1)
  assert.equal(r.blocks[0]!.text, '有效')
})

test('无 words 时退回 data.text 单块', async () => {
  const p = new TesseractJsProvider({
    workerFactory: (async () => ({
      recognize: async () => ({ data: { text: '整段文本', confidence: 70, words: {} } }),
      terminate: async () => {},
    })) as never,
  })
  const r = await p.recognize({ image: Buffer.alloc(0), pageIndex: 1 })
  assert.equal(r.blocks.length, 1)
  assert.equal(r.blocks[0]!.text, '整段文本')
  assert.equal(r.blocks[0]!.pageIndex, 1)
})

test('isAvailable 始终 true（纯 WASM）', async () => {
  const p = new TesseractJsProvider({ workerFactory: mockWorkerFactory([]) as never })
  assert.equal(await p.isAvailable(), true)
})

test('dispose 销毁 worker', async () => {
  let terminated = false
  const p = new TesseractJsProvider({
    workerFactory: (async () => ({
      recognize: async () => ({ data: { text: '', confidence: 0, words: {} } }),
      terminate: async () => {
        terminated = true
      },
    })) as never,
  })
  await p.recognize({ image: Buffer.alloc(0) }) // 触发 worker 创建
  await p.dispose()
  assert.equal(terminated, true)
})
