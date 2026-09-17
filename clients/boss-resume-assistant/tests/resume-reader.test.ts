/**
 * ResumeReader 单测：在线简历读取链路（定位 canvas → 回顶 → 分段截图到底 → 拼接 → 返回拼接长图）。
 * fake 注入 snapshot/captureFullpage/wheel/sameView/stitch（参照 job-switcher.test.ts 风格）。
 *
 * P1 到底判定（2026-09-02 真机假到底加固）：字节差 ≥ BOTTOM_SIZE_EPSILON 快路径直接入列（不调
 * sameView）；字节差 < epsilon 调 sameView 像素确认；像素相同再滚一次 + 等更久再截一张，仍相同
 * （连续两次相同）才确认到底并丢弃两张重复截图；不同则滚动被吞恢复，确认截图入列继续。
 * 2026-09-17 去 OCR 化：读取结果只含拼接长图与截图/拼接元信息，无任何文本字段
 * （文本识别在云端多模态模型，客户端不再产出 text/chars/ocr* 字段）。
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
  canvasCandidates,
  SCROLL_TOP_NOTCHES,
  SEGMENT_WHEEL_NOTCHES,
  BOTTOM_CONFIRM_DELAY,
  MAX_SEGMENTS,
  MAX_CAPTURES,
  SEAM_MIS_SUSPECT,
  type DeviceRect,
  type ResumeReadDeps,
} from '../src/main/boss/ResumeReader.js'
import type { DomSnapshot } from '../src/main/boss/domSnapshot.js'

/**
 * 构造 snapshot：根视口默认 1249x1277（doc0，owner 偏移 0），可用 viewport 覆盖（自适应门槛测试用）。
 * canvases：CANVAS 元素节点 bounds 列表（nodeName 稀疏表指向 strings 里的 'CANVAS' 字符串）。
 */
function canvasSnap(
  opts: { canvases?: Array<[number, number, number, number]>; viewport?: [number, number] } = {},
): DomSnapshot {
  const strings: string[] = ['']
  const nvIndex: number[] = [0]
  const nvValue: number[] = [0]
  const nameIndex: number[] = [0]
  const nameValue: number[] = [0]
  const layoutNodeIndex: number[] = [0]
  const layoutBounds: Array<[number, number, number, number]> = [
    [0, 0, opts.viewport?.[0] ?? 1249, opts.viewport?.[1] ?? 1277],
  ]

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

/** fake 依赖容器：captureSizes 按次序给出每段整页 PNG 字节数（越界取末值）；stitch 落盘假 PNG 供 copyFile。 */
interface Fake {
  wheels: Array<{ deltaY: number; notches: number }>
  captures: number[]
  sleeps: number[]
  sameViewCalls: Array<{ a: string; b: string }>
  /** sameView 返回值队列（shift 消费；空则恒 true） */
  sameViewResults: boolean[]
  /** 非空时 sameView 抛该错误（测试 fail-loud 路径） */
  sameViewError?: Error
  stitchCalls: Array<{ parts: string[]; rect: DeviceRect; outFile: string }>
  progress: string[]
  /** stitch 返回的每接缝错配率（越界取 0.08） */
  seamMis: number[]
  readerDeps: Omit<ResumeReadDeps, 'signal'>
}

function makeFake(captureSizes: number[]): Fake {
  const f: Fake = {
    wheels: [],
    captures: [],
    sleeps: [],
    sameViewCalls: [],
    sameViewResults: [],
    stitchCalls: [],
    progress: [],
    seamMis: [],
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
    sameView: async (a, b) => {
      f.sameViewCalls.push({ a, b })
      if (f.sameViewError) throw f.sameViewError
      return f.sameViewResults.length > 0 ? f.sameViewResults.shift()! : true
    },
    stitch: async (parts, rect, outFile) => {
      f.stitchCalls.push({ parts, rect, outFile })
      await fs.writeFile(outFile, STITCHED_PNG)
      const seams = Math.max(0, parts.length - 1)
      return {
        width: 727,
        height: 2827,
        overlaps: Array.from({ length: seams }, () => 746),
        seamMis: Array.from({ length: seams }, (_, i) => f.seamMis[i] ?? 0.08),
      }
    },
    sleep: async (ms) => {
      f.sleeps.push(ms)
    },
    onProgress: (stage) => {
      f.progress.push(stage)
    },
  }
  return f
}

function makeReader(f: Fake, opts: { signal?: AbortSignal } = {}): ResumeReader {
  return new ResumeReader({ ...f.readerDeps, signal: opts.signal })
}

const DOWN = { deltaY: -120, notches: SEGMENT_WHEEL_NOTCHES }
const UP = { deltaY: 120, notches: SCROLL_TOP_NOTCHES }

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

test('locateResumeCanvas：无 canvas / 只有小 canvas / 低于自适应门槛 → null', () => {
  assert.equal(locateResumeCanvas(canvasSnap({})), null)
  assert.equal(locateResumeCanvas(canvasSnap({ canvases: [[0, 0, 100, 100]] })), null)
  // 视口 1249x1277 → 门槛 312x319：h=250 未过
  assert.equal(locateResumeCanvas(canvasSnap({ canvases: [[0, 0, 727, 250]] })), null)
  // 真机 2026-08-18：572 高的合法弹层画布（旧固定门槛 600 曾误杀）必须识别
  assert.notEqual(locateResumeCanvas(canvasSnap({ canvases: [[0, 0, 760, 572]] })), null)
})

test('locateResumeCanvas：门槛随视口自适应，不写死像素（2026-09-11 客户小屏教训）', () => {
  // 参考视口：门槛 312x319，572 高画布过、320 以下不过
  assert.notEqual(locateResumeCanvas(canvasSnap({ canvases: [[0, 0, 727, 572]] })), null)
  assert.equal(locateResumeCanvas(canvasSnap({ canvases: [[0, 0, 727, 318]] })), null)
  // 小屏视口 800x600 → 门槛 200x150：450x300 的合法弹层画布必须识别
  // （写死 400 的时代它在客户小屏上会被误判「详情未打开」）
  assert.notEqual(
    locateResumeCanvas(canvasSnap({ viewport: [800, 600], canvases: [[100, 80, 450, 300]] })),
    null,
  )
  // 同一小屏视口下几十像素图标 canvas 仍被排除
  assert.equal(
    locateResumeCanvas(canvasSnap({ viewport: [800, 600], canvases: [[0, 0, 120, 90]] })),
    null,
  )
})

test('locateResumeCanvas：画布底部出屏 → 与视口求交（小屏修复：裁剪/滚轮恒在可见区内）', () => {
  // 弹层画布 727x1000 @ y=600，底部 1600 出屏（视口 1277）→ 截断为可见部分
  assert.deepEqual(
    locateResumeCanvas(canvasSnap({ canvases: [[100, 600, 727, 1000]] })),
    { x: 100, y: 600, w: 727, h: 677 },
  )
  // 画布整体在视口下方（完全不可见）→ null
  assert.equal(locateResumeCanvas(canvasSnap({ canvases: [[100, 1300, 727, 500]] })), null)
})

test('canvasCandidates：全部 CANVAS 尺寸面积降序（含未过阈值的，供打开失败诊断；不含坐标）', () => {
  assert.deepEqual(
    canvasCandidates(
      canvasSnap({
        canvases: [
          [900, 1200, 80, 80], // 小图标 canvas
          [0, 0, 380, 560], // 差一点过阈值的弹层画布（诊断关键现场）
          CANVAS_BOUNDS,
        ],
      }),
    ),
    [{ w: 727, h: 1237 }, { w: 380, h: 560 }, { w: 80, h: 80 }],
  )
  assert.deepEqual(canvasCandidates(canvasSnap({})), [])
})

// ---------- readResume 主链路（P1 到底判定 = 字节快路径 + sameView 像素确认 + 连续两次相同） ----------

test('正常 3 段：第 4 段字节相同 → sameView 确认 → 再滚再截仍相同 → 确认到底（丢弃 2 张重复截图）', async () => {
  const f = makeFake([1000, 2000, 3000, 3000, 3000]) // 第 4/5 次截图与第 3 段相同
  const res = await makeReader(f).readResume()
  assert.equal(res.segments, 3)
  assert.equal(res.bottomReached, true)
  assert.equal(res.width, 727)
  assert.equal(res.height, 2827)
  assert.equal(f.captures.length, 5) // 3 段 + 2 张重复截图（已丢弃）
  // wheel = 回顶 50 格 + 3 次向下 5 格（段推进）+ 1 次向下 5 格（到底确认路径）
  assert.deepEqual(f.wheels, [UP, DOWN, DOWN, DOWN, DOWN])
  // sameView 调用两次（候选 + 确认），都比对「当前截图 vs 上一入列段 part-raw-2」
  assert.deepEqual(
    f.sameViewCalls.map((c) => [path.basename(c.a), path.basename(c.b)]),
    [
      ['part-raw-3.png', 'part-raw-2.png'],
      ['part-raw-4.png', 'part-raw-2.png'],
    ],
  )
  // 确认路径等待 BOTTOM_CONFIRM_DELAY（懒加载留时间），普通段间隔 SEGMENT_REPAINT_DELAY
  assert.deepEqual(f.sleeps, [1500, 900, 900, 900, BOTTOM_CONFIRM_DELAY])
  assert.equal(f.stitchCalls.length, 1)
  assert.equal(f.stitchCalls[0]!.parts.length, 3)
  assert.match(f.stitchCalls[0]!.parts[0]!, /part-raw-0\.png$/)
  assert.match(f.stitchCalls[0]!.parts[2]!, /part-raw-2\.png$/)
  assert.deepEqual(f.stitchCalls[0]!.rect, CANVAS_RECT)
  assert.deepEqual(f.progress, ['stitch'])
  // P1 元信息：接缝错配率（fake 默认 0.08）低于可疑阈值 → 无可疑接缝
  assert.deepEqual(res.seamMis, [0.08, 0.08])
  assert.deepEqual(res.suspectSeams, [])
  // 拼接图字节随结果返回（供 base64 入库；临时文件已清理）
  assert.deepEqual(res.imageBuffer, STITCHED_PNG)
  const tmpDir = path.dirname(f.stitchCalls[0]!.parts[0]!)
  assert.equal(existsSync(tmpDir), false)
})

test('单段：第 2 段即到底（简历一屏放得下），只拼 1 段（连续两次相同才确认）', async () => {
  const f = makeFake([1000, 1000, 1000]) // 第 2/3 次截图均与第 1 段相同
  const res = await makeReader(f).readResume()
  assert.equal(res.segments, 1)
  assert.equal(res.bottomReached, true)
  // wheel = 回顶 + 段推进 1 次 + 到底确认 1 次（共 3 次，真机旧实现是 2 次——多出的 1 次即确认滚动）
  assert.deepEqual(f.wheels, [UP, DOWN, DOWN])
  assert.equal(f.captures.length, 3)
  assert.equal(f.stitchCalls[0]!.parts.length, 1)
  assert.deepEqual(res.seamMis, []) // 单段无接缝
  const tmpDir = path.dirname(f.stitchCalls[0]!.parts[0]!)
  assert.equal(existsSync(tmpDir), false)
})

test('① 字节相同 → sameView true → 确认再滚仍相同 → 到底：wheel 次数 = 回顶 + 推进 + 确认', async () => {
  const f = makeFake([5000, 5000, 5000])
  const res = await makeReader(f).readResume()
  assert.equal(res.segments, 1)
  assert.equal(res.bottomReached, true)
  assert.equal(f.wheels.length, 3)
  assert.deepEqual(f.wheels.filter((w) => w.deltaY < 0), [DOWN, DOWN])
  assert.equal(f.sameViewCalls.length, 2)
})

test('② 滚动被吞恢复：字节相同 → 确认再滚后不同 → 确认截图入列继续（假到底不截断简历）', async () => {
  // cap1 字节同 cap0（滚轮被吞）→ sameView true → 确认滚动后 cap2=2000 不同 → cap2 入列继续；
  // 之后 cap3/cap4 连续与 cap2 相同 → 真到底
  const f = makeFake([1000, 1000, 2000, 2000, 2000])
  const res = await makeReader(f).readResume()
  assert.equal(res.segments, 2) // parts = [cap0, cap2]；被吞的 cap1 与确认用的 cap3/cap4 均不入列
  assert.equal(res.bottomReached, true)
  assert.equal(f.captures.length, 5)
  assert.match(f.stitchCalls[0]!.parts[0]!, /part-raw-0\.png$/)
  assert.match(f.stitchCalls[0]!.parts[1]!, /part-raw-2\.png$/) // 恢复的确认截图
  assert.equal(f.wheels.length, 5) // 回顶 + cap0 后推进 + 确认滚动 + cap2 后推进 + 确认滚动
  assert.equal(f.sameViewCalls.length, 3) // (cap1,cap0) (cap3,cap2) (cap4,cap2)
})

test('②b 字节相同但像素不同（canvas 重绘噪声）→ 不判到底，该段正常入列继续', async () => {
  const f = makeFake([1000, 1000, 3000, 3000, 3000])
  f.sameViewResults = [false, true, true] // cap1 像素不同 → 入列；cap3/cap4 连续相同 → 到底
  const res = await makeReader(f).readResume()
  assert.equal(res.segments, 3) // parts = [cap0, cap1(字节同但像素异), cap2]
  assert.equal(res.bottomReached, true)
  assert.equal(f.wheels.length, 5)
  assert.equal(f.sameViewCalls.length, 3)
})

test('③ 快路径：字节差 ≥ epsilon 的相邻段不调 sameView（省一次 PS 调用）', async () => {
  // 每段字节差 500 ≥ BOTTOM_SIZE_EPSILON=200 → 永不触发像素确认；并入 MAX_SEGMENTS 边界用例
  const sizes = Array.from({ length: 30 }, (_, i) => 1000 + i * 500)
  const f = makeFake(sizes)
  const res = await makeReader(f).readResume()
  assert.equal(f.sameViewCalls.length, 0) // 快路径：字节差足够大，直接判「不同」
  assert.equal(res.segments, MAX_SEGMENTS)
  assert.equal(res.bottomReached, false)
})

test('④ sameView 抛错（cv-segdiff.ps1 失败/裁剪越界）→ ResumeReadError fail-loud', async () => {
  const f = makeFake([1000, 1000])
  f.sameViewError = new Error('cv-segdiff.ps1 执行失败(exit=1): crop rect exceeds image')
  await assert.rejects(makeReader(f).readResume(), (e: unknown) => {
    assert.ok(e instanceof ResumeReadError)
    assert.match(e.message, /像素级同画面比对失败/)
    assert.match(e.message, /crop rect exceeds image/)
    return true
  })
  assert.equal(f.captures.length, 2)
  assert.equal(f.stitchCalls.length, 0)
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

test('取消（signal 在到底确认路径中触发）：确认滚动前检查，抛 CancelledError', async () => {
  const f = makeFake([1000, 1000])
  const controller = new AbortController()
  const orig = f.readerDeps.sameView
  f.readerDeps.sameView = async (a, b) => {
    controller.abort() // 首次像素确认即请求取消
    return orig(a, b)
  }
  await assert.rejects(makeReader(f, { signal: controller.signal }).readResume(), (e: unknown) => {
    assert.equal((e as Error).name, 'CancelledError')
    return true
  })
  assert.deepEqual(f.wheels, [UP, DOWN]) // 确认路径的额外滚动未发生
  assert.equal(f.captures.length, 2)
})

test('saveImageTo：拼接图被复制到目标路径（内容一致）', async () => {
  const f = makeFake([1000, 1000, 1000])
  const saveTo = path.join(os.tmpdir(), `boss-cv-save-${randomUUID()}.png`)
  try {
    await makeReader(f).readResume({ saveImageTo: saveTo })
    assert.equal(existsSync(saveTo), true)
    assert.deepEqual(await fs.readFile(saveTo), STITCHED_PNG)
  } finally {
    await fs.rm(saveTo, { force: true }).catch(() => {})
  }
})

test(`⑥ 段数上限 MAX_SEGMENTS=${MAX_SEGMENTS}：一直不到底时最多 ${MAX_SEGMENTS} 段（防护），bottomReached=false`, async () => {
  const sizes = Array.from({ length: 30 }, (_, i) => 1000 + i * 500) // 每段字节差 ≥ 200 → 永不到底
  const f = makeFake(sizes)
  const res = await makeReader(f).readResume()
  assert.equal(res.segments, MAX_SEGMENTS)
  assert.equal(res.bottomReached, false) // 未确认到底：调用方必须警告可能截断，绝不静默当完整简历
  assert.equal(f.captures.length, MAX_SEGMENTS)
  assert.equal(f.sameViewCalls.length, 0) // 快路径：字节差 ≥ epsilon 不调 sameView
  assert.equal(f.stitchCalls[0]!.parts.length, MAX_SEGMENTS)
  assert.deepEqual(res.suspectSeams, []) // fake 默认接缝错配率 0.08 < 阈值
  // 最后一段入库后不再多滚（第 MAX+1 次截图已被禁止）：回顶 1 次 + 每段后滚 15 次
  assert.equal(f.wheels.length, MAX_SEGMENTS)
  assert.deepEqual(
    f.wheels.filter((w) => w.deltaY < 0),
    Array.from({ length: MAX_SEGMENTS - 1 }, () => DOWN),
  )
  // 总截图次数安全上限（防确认路径死循环；正常路径数学上不可达，仅 belt-and-braces）
  assert.equal(MAX_CAPTURES, 2 * MAX_SEGMENTS + 8)
})

test('接缝错配率超阈值 → suspectSeams 元信息（调用方据此警告人工核对）', async () => {
  const f = makeFake([1000, 2000, 3000, 3000, 3000])
  f.seamMis = [0.5, 0.36] // 均超 SEAM_MIS_SUSPECT=0.35
  const res = await makeReader(f).readResume()
  assert.deepEqual(res.seamMis, [0.5, 0.36])
  assert.deepEqual(res.suspectSeams, [1, 2]) // 1-based 接缝序号
  assert.equal(SEAM_MIS_SUSPECT, 0.35)
})

test('去 OCR 化契约：readResume 结果不含任何文本字段（文本识别在云端）', async () => {
  const f = makeFake([1000, 2000, 3000, 3000, 3000])
  const res = await makeReader(f).readResume() as unknown as Record<string, unknown>
  for (const gone of ['text', 'chars', 'ocrEngine', 'ocrAccel', 'ocrEmptySegments', 'textSeamUnmatched']) {
    assert.equal(gone in res, false, `结果不应再含 ${gone}（本地 OCR 链路已下线）`)
  }
  // 只保留截图/拼接契约字段
  assert.deepEqual(
    Object.keys(res).sort(),
    ['bottomReached', 'height', 'imageBuffer', 'seamMis', 'segments', 'suspectSeams', 'width'],
  )
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
