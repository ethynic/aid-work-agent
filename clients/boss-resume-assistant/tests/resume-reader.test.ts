/**
 * ResumeReader 单测：在线简历读取链路（定位 canvas → 回顶 → 分段截图到底 → 拼接 → OCR）。
 * fake 注入 snapshot/captureFullpage/wheel/stitch/ocr（参照 job-switcher.test.ts 风格）。
 *
 * 真机关键（2026-08-14，窗口 1249x1277）：canvas 727x1237、简历总高 3062、3 段覆盖全文；
 * 到底判定 = 相邻段整页 PNG 字节数差 < 200（canvas 虚拟滚动 scrollOffset 恒 0 不可用）；
 * 每段向下滚 8 格（-120×8）、回顶向上 50 格（+120×50，防护值）。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { existsSync } from 'node:fs'
import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { randomUUID } from 'node:crypto'
import {
  ResumeReader,
  ResumeReadError,
  locateResumeCanvas,
  SCROLL_TOP_NOTCHES,
  SEGMENT_WHEEL_NOTCHES,
  MAX_SEGMENTS,
  type DeviceRect,
  type ResumeReadDeps,
} from '../src/main/boss/ResumeReader.js'
import type { DomSnapshot } from '../src/main/boss/domSnapshot.js'

/**
 * 构造 snapshot：根视口 1249x1277（doc0，owner 偏移 0）。
 * canvases：CANVAS 元素节点 bounds 列表（nodeName 稀疏表指向 strings 里的 'CANVAS' 字符串）。
 */
function canvasSnap(opts: { canvases?: Array<[number, number, number, number]> } = {}): DomSnapshot {
  const strings: string[] = ['']
  const nvIndex: number[] = [0]
  const nvValue: number[] = [0]
  const nameIndex: number[] = [0]
  const nameValue: number[] = [0]
  const layoutNodeIndex: number[] = [0]
  const layoutBounds: Array<[number, number, number, number]> = [[0, 0, 1249, 1277]]

  const intern = (s: string): number => {
    let i = strings.indexOf(s)
    if (i < 0) {
      strings.push(s)
      i = strings.length - 1
    }
    return i
  }
  let nextNi = 1
  for (const b of opts.canvases ?? []) {
    const nameSi = intern('CANVAS')
    const ni = nextNi++
    nvIndex.push(ni)
    nvValue.push(0) // 元素节点无文本
    nameIndex.push(ni)
    nameValue.push(nameSi)
    layoutNodeIndex.push(ni)
    layoutBounds.push(b)
  }
  return {
    strings,
    documents: [
      {
        nodes: {
          nodeValue: { index: nvIndex, value: nvValue },
          nodeName: { index: nameIndex, value: nameValue },
          contentDocumentIndex: { index: [], value: [] },
        },
        layout: { nodeIndex: layoutNodeIndex, bounds: layoutBounds },
        scrollOffsetY: 0,
      },
    ],
  }
}

const CANVAS_BOUNDS: [number, number, number, number] = [0, 0, 727, 1237] // 真机：详情 iframe 顶部 canvas
const CANVAS_RECT: DeviceRect = { x: 0, y: 0, w: 727, h: 1237 }
const STITCHED_PNG = Buffer.from('fake-stitched-png-bytes')
const OCR_TEXT = '张三 男 26岁 本科\nPHP 开发 5 年\n某科技公司 后端工程师\n项目：电商订单系统…'

/** fake 依赖容器：captureSizes 按次序给出每段整页 PNG 字节数；stitch 落盘假 PNG 供 copyFile */
interface Fake {
  wheels: Array<{ deltaY: number; notches: number }>
  captures: number[]
  stitchCalls: Array<{ parts: string[]; rect: DeviceRect; outFile: string }>
  ocrCalls: string[]
  progress: string[]
  ocrText: string
  readerDeps: Omit<ResumeReadDeps, 'signal'>
}

function makeFake(captureSizes: number[], ocrText = OCR_TEXT): Fake {
  const f: Fake = {
    wheels: [],
    captures: [],
    stitchCalls: [],
    ocrCalls: [],
    progress: [],
    ocrText,
    readerDeps: undefined!,
  }
  let captureIdx = 0
  f.readerDeps = {
    snapshot: async () => canvasSnap({ canvases: [CANVAS_BOUNDS] }),
    captureFullpage: async () => {
      const size = captureSizes[Math.min(captureIdx++, captureSizes.length - 1)]!
      f.captures.push(size)
      return Buffer.alloc(size)
    },
    wheel: async (deltaY, notches) => {
      f.wheels.push({ deltaY, notches })
    },
    stitch: async (parts, rect, outFile) => {
      f.stitchCalls.push({ parts, rect, outFile })
      await fs.writeFile(outFile, STITCHED_PNG)
      return { width: 727, height: 2827, overlaps: [746, 744] }
    },
    ocr: async (imgFile) => {
      f.ocrCalls.push(imgFile)
      return f.ocrText
    },
    sleep: async () => {},
    onProgress: (stage) => {
      f.progress.push(stage)
    },
  }
  return f
}

function makeReader(f: Fake, opts: { signal?: AbortSignal } = {}): ResumeReader {
  return new ResumeReader({ ...f.readerDeps, signal: opts.signal })
}

// ---------- locateResumeCanvas ----------

test('locateResumeCanvas：唯一大 canvas → 屏幕 device 区域（owner 偏移 0，真机 727x1237）', () => {
  const rect = locateResumeCanvas(canvasSnap({ canvases: [CANVAS_BOUNDS] }))
  assert.deepEqual(rect, CANVAS_RECT)
})

test('locateResumeCanvas：多个 canvas 取面积最大者（排除小图标 canvas）', () => {
  const rect = locateResumeCanvas(
    canvasSnap({
      canvases: [
        [900, 1200, 80, 80], // 小图标 canvas
        [500, 900, 500, 700], // 中等 canvas
        CANVAS_BOUNDS, // 简历 canvas（最大）
      ],
    }),
  )
  assert.deepEqual(rect, CANVAS_RECT)
})

test('locateResumeCanvas：无 canvas / 只有小 canvas / 高度不过门槛 → null', () => {
  assert.equal(locateResumeCanvas(canvasSnap({})), null)
  assert.equal(locateResumeCanvas(canvasSnap({ canvases: [[0, 0, 100, 100]] })), null)
  assert.equal(locateResumeCanvas(canvasSnap({ canvases: [[0, 0, 727, 600]] })), null) // h=600 未过门槛
})

// ---------- readResume 主链路 ----------

test('正常 3 段：第 4 段字节数相同触发到底（本段丢弃），wheel = 回顶 50 格 + 3 次向下 8 格', async () => {
  const f = makeFake([1000, 2000, 3000, 3000]) // 第 4 次截图与第 3 次字节相同 → 到底
  const res = await makeReader(f).readResume()
  assert.equal(res.text, OCR_TEXT)
  assert.equal(res.chars, OCR_TEXT.length)
  assert.equal(res.segments, 3)
  assert.equal(res.bottomReached, true)
  assert.equal(res.width, 727)
  assert.equal(res.height, 2827)
  assert.equal(f.captures.length, 4)
  assert.deepEqual(f.wheels, [
    { deltaY: 120, notches: SCROLL_TOP_NOTCHES }, // 回顶（防护值）
    { deltaY: -120, notches: SEGMENT_WHEEL_NOTCHES },
    { deltaY: -120, notches: SEGMENT_WHEEL_NOTCHES },
    { deltaY: -120, notches: SEGMENT_WHEEL_NOTCHES },
  ])
  assert.equal(f.stitchCalls.length, 1)
  assert.equal(f.stitchCalls[0]!.parts.length, 3) // 到底的那段未进拼接（已删除）
  assert.match(f.stitchCalls[0]!.parts[0]!, /part-raw-0\.png$/)
  assert.match(f.stitchCalls[0]!.parts[2]!, /part-raw-2\.png$/)
  assert.deepEqual(f.stitchCalls[0]!.rect, CANVAS_RECT)
  assert.equal(f.ocrCalls.length, 1)
  assert.equal(f.ocrCalls[0], f.stitchCalls[0]!.outFile)
  assert.deepEqual(f.progress, ['stitch', 'ocr'])
  // 临时目录已清理
  const tmpDir = path.dirname(f.stitchCalls[0]!.parts[0]!)
  assert.equal(existsSync(tmpDir), false)
})

test('单段：第 2 段即到底（简历一屏放得下），只拼 1 段', async () => {
  const f = makeFake([1000, 1000]) // 第 2 次截图与第 1 次字节相同 → 到底
  const res = await makeReader(f).readResume()
  assert.equal(res.segments, 1)
  assert.equal(res.bottomReached, true)
  assert.equal(res.chars, OCR_TEXT.length)
  assert.deepEqual(f.wheels, [
    { deltaY: 120, notches: SCROLL_TOP_NOTCHES },
    { deltaY: -120, notches: SEGMENT_WHEEL_NOTCHES }, // 段 0 后照常滚动一次，下一段相同 → 停
  ])
  assert.equal(f.stitchCalls[0]!.parts.length, 1)
  const tmpDir = path.dirname(f.stitchCalls[0]!.parts[0]!)
  assert.equal(existsSync(tmpDir), false)
})

test('canvas 未找到 → ResumeReadError（fail-loud），不滚不截不拼', async () => {
  const f = makeFake([1000])
  const reader = new ResumeReader({
    ...f.readerDeps,
    snapshot: async () => canvasSnap({}), // 页面没有大 canvas（未打开简历详情）
    captureFullpage: async () => {
      throw new Error('不应被调用')
    },
    stitch: async () => {
      throw new Error('不应被调用')
    },
    ocr: async () => {
      throw new Error('不应被调用')
    },
  })
  await assert.rejects(reader.readResume(), (e: unknown) => {
    assert.ok(e instanceof ResumeReadError)
    assert.match(e.message, /未找到简历详情画布/)
    return true
  })
  assert.equal(f.wheels.length, 0)
  assert.equal(f.captures.length, 0)
  assert.equal(f.stitchCalls.length, 0)
})

test('OCR 空文本 → fail-loud（绝不返回半成品），失败路径也清理临时目录', async () => {
  const f = makeFake([1000, 1000], '   \n\t')
  await assert.rejects(makeReader(f).readResume(), (e: unknown) => {
    assert.ok(e instanceof ResumeReadError)
    assert.match(e.message, /OCR 未识别到任何文字/)
    return true
  })
  assert.equal(f.ocrCalls.length, 1) // OCR 确实被调过（返回了空白）
  const tmpDir = path.dirname(f.stitchCalls[0]!.parts[0]!)
  assert.equal(existsSync(tmpDir), false)
})

test('取消（signal 已 abort）：入口抛 CancelledError，不触达页面', async () => {
  const f = makeFake([1000])
  await assert.rejects(makeReader(f, { signal: AbortSignal.abort() }).readResume(), (e: unknown) => {
    assert.equal((e as Error).name, 'CancelledError')
    return true
  })
  assert.equal(f.wheels.length, 0)
  assert.equal(f.captures.length, 0)
})

test('saveImageTo：拼接图被复制到目标路径（内容一致）', async () => {
  const f = makeFake([1000, 1000])
  const saveTo = path.join(os.tmpdir(), `boss-cv-save-${randomUUID()}.png`)
  try {
    await makeReader(f).readResume({ saveImageTo: saveTo })
    assert.equal(existsSync(saveTo), true)
    assert.deepEqual(await fs.readFile(saveTo), STITCHED_PNG)
  } finally {
    await fs.rm(saveTo, { force: true }).catch(() => {})
  }
})

test(`段数上限 MAX_SEGMENTS=${MAX_SEGMENTS}：一直不到底时最多截 ${MAX_SEGMENTS} 段（防护），bottomReached=false`, async () => {
  const sizes = Array.from({ length: 30 }, (_, i) => 1000 + i * 500) // 每段字节差 ≥ 200 → 永不到底
  const f = makeFake(sizes)
  const res = await makeReader(f).readResume()
  assert.equal(res.segments, MAX_SEGMENTS)
  assert.equal(res.bottomReached, false) // 未确认到底：调用方必须警告可能截断，绝不静默当完整简历
  assert.equal(f.stitchCalls[0]!.parts.length, MAX_SEGMENTS)
  // 最后一段入库后不再多滚（第 MAX+1 次截图已被禁止）：回顶 1 次 + 每段后滚 11 次
  assert.equal(f.wheels.length, MAX_SEGMENTS)
  assert.deepEqual(
    f.wheels.filter((w) => w.deltaY < 0),
    Array.from({ length: MAX_SEGMENTS - 1 }, () => ({ deltaY: -120, notches: SEGMENT_WHEEL_NOTCHES })),
  )
})
