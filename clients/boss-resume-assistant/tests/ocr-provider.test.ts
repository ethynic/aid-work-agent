import assert from 'node:assert/strict'
import test from 'node:test'
import {
  runOcrOrFail,
  assertValidOcrResult,
  type OcrProvider,
  type OcrResult,
} from '../src/main/ocr/OcrProvider.js'

function makeResult(blocks: Array<Partial<{ text: string; confidence: number; box: number[]; pageIndex: number }>>): OcrResult {
  return {
    blocks: blocks.map((b, i) => ({
      text: b.text ?? `t${i}`,
      confidence: b.confidence ?? 0.9,
      box: (b.box ?? [0, 0, 10, 10]) as [number, number, number, number],
      pageIndex: b.pageIndex ?? 0,
    })),
  }
}

test('provider 未配置 → fail-loud 抛错', async () => {
  await assert.rejects(runOcrOrFail(null, { image: Buffer.alloc(0) }), /not configured/)
})

test('provider 不可用 → fail-loud 抛错', async () => {
  const p: OcrProvider = {
    name: 'fake',
    isAvailable: async () => false,
    recognize: async () => makeResult([]),
  }
  await assert.rejects(runOcrOrFail(p, { image: Buffer.alloc(0) }), /not available/)
})

test('正常输出补算 meanConfidence 与 hasLowConfidence', async () => {
  const p: OcrProvider = {
    name: 'fake',
    isAvailable: async () => true,
    recognize: async () => makeResult([{ confidence: 0.9 }, { confidence: 0.3 }]),
  }
  const r = await runOcrOrFail(p, { image: Buffer.alloc(0) }, 0.6)
  assert.equal(r.meanConfidence, 0.6)
  assert.equal(r.hasLowConfidence, true)
})

test('输出契约非法（confidence 越界）→ fail-loud', async () => {
  const p: OcrProvider = {
    name: 'fake',
    isAvailable: async () => true,
    recognize: async () => ({
      blocks: [{ text: 'x', confidence: 1.5, box: [0, 0, 1, 1], pageIndex: 0 }],
    }),
  }
  await assert.rejects(runOcrOrFail(p, { image: Buffer.alloc(0) }), /out of range/)
})

test('box 非四元 → fail-loud', () => {
  assert.throws(
    () =>
      assertValidOcrResult({
        blocks: [{ text: 'x', confidence: 0.5, box: [1, 2, 3] as unknown as [number, number, number, number], pageIndex: 0 }],
      }),
    /box must be/,
  )
})

test('pageIndex 缺失 → fail-loud', () => {
  assert.throws(
    () => assertValidOcrResult({ blocks: [{ text: 'x', confidence: 0.5, box: [1, 2, 3, 4] }] as never }),
    /pageIndex/,
  )
})
