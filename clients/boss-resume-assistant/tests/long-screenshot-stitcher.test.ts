import assert from 'node:assert/strict'
import test from 'node:test'
import { PNG } from 'pngjs'
import { LongScreenshotStitcher, encodePng } from '../src/main/image/LongScreenshotStitcher.js'

const WIDTH = 40
const HEIGHT = 60

/** 生成一张宽 WIDTH、指定高度、内容按行号渐变（每行 R=y%255）的 PNG */
function gradientPng(height: number, base = 0): Buffer {
  const data = Buffer.alloc(WIDTH * height * 4)
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < WIDTH; x++) {
      const i = (y * WIDTH + x) * 4
      data[i] = (base + y) % 256 // R
      data[i + 1] = 0
      data[i + 2] = 0
      data[i + 3] = 255
    }
  }
  return encodePng(WIDTH, height, data)
}

/** 从一张大图取一个分片（高度 H，从 startY 开始） */
function slice(png: Buffer, startY: number, h: number): Buffer {
  const src = PNG.sync.read(png)
  const out = new PNG({ width: WIDTH, height: h })
  PNG.bitblt(src, out, 0, startY, WIDTH, h, 0, 0)
  return PNG.sync.write(out)
}

test('单分片直接返回', () => {
  const one = gradientPng(HEIGHT)
  const r = new LongScreenshotStitcher().stitch([one])
  assert.equal(r.ok, true)
  assert.equal(r.shardCount, 1)
  assert.equal(r.integrity, 'CONTINUOUS')
  assert.equal(r.height, HEIGHT)
})

test('两片有真实重叠 → 拼回连续长图', () => {
  // 造一张 100 行的原图，切成两片：片0 = 行0~59（60行），片1 = 行40~99（60行），重叠 20 行
  const full = gradientPng(100)
  const shard0 = slice(full, 0, 60)
  const shard1 = slice(full, 40, 60)

  const r = new LongScreenshotStitcher({ minOverlap: 10, maxOverlap: 40, mismatchThreshold: 50, step: 1 }).stitch([shard0, shard1])
  assert.equal(r.ok, true, `should stitch: ${r.reason}`)
  assert.equal(r.integrity, 'CONTINUOUS')
  assert.equal(r.height, 100, '拼接后高度应等于原图 100')
  assert.equal(r.shardCount, 2)
  // offsets：片0=0，片1 = 0 + 60 - overlap = 60-20 = 40
  assert.deepEqual(r.offsets, [0, 40])
})

test('两片无重叠（断层）→ GAP_DETECTED', () => {
  // 两片内容完全不同（不同 base），任何重叠区 MSE 都很高
  const shard0 = gradientPng(60, 0)
  const shard1 = gradientPng(60, 200) // 完全不同的渐变
  const r = new LongScreenshotStitcher({ minOverlap: 10, maxOverlap: 40, mismatchThreshold: 50 }).stitch([shard0, shard1])
  assert.equal(r.ok, false)
  assert.equal(r.integrity, 'GAP_DETECTED')
  assert.match(r.reason ?? '', /exceeds threshold|min mse/)
})

test('分片宽度不一致 → STITCH_FAILED', () => {
  const a = gradientPng(60)
  const b = encodePng(50, 60, Buffer.alloc(50 * 60 * 4)) // 不同宽度
  const r = new LongScreenshotStitcher().stitch([a, b])
  assert.equal(r.ok, false)
  assert.equal(r.integrity, 'STITCH_FAILED')
  assert.match(r.reason ?? '', /width mismatch/)
})

test('空分片列表 → 失败', () => {
  const r = new LongScreenshotStitcher().stitch([])
  assert.equal(r.ok, false)
  assert.equal(r.integrity, 'STITCH_FAILED')
})

test('三片连续拼接高度正确', () => {
  const full = gradientPng(140)
  const s0 = slice(full, 0, 60)
  const s1 = slice(full, 40, 60) // overlap 20
  const s2 = slice(full, 80, 60) // overlap 20
  const r = new LongScreenshotStitcher({ minOverlap: 10, maxOverlap: 40, mismatchThreshold: 50 }).stitch([s0, s1, s2])
  assert.equal(r.ok, true, `reason: ${r.reason}`)
  assert.equal(r.height, 140)
  assert.deepEqual(r.offsets, [0, 40, 80])
})
