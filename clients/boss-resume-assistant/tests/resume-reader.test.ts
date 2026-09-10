/**
 * ResumeReader 单测：在线简历读取链路（定位 canvas → 回顶 → 分段截图到底 → 拼接 → 逐段 OCR → 文本合并）。
 * fake 注入 snapshot/captureFullpage/wheel/sameView/stitch/ocr（参照 job-switcher.test.ts 风格）。
 *
 * P1 到底判定（2026-09-02 真机假到底加固）：字节差 ≥ BOTTOM_SIZE_EPSILON 快路径直接入列（不调
 * sameView）；字节差 < epsilon 调 sameView 像素确认；像素相同再滚一次 + 等更久再截一张，仍相同
 * （连续两次相同）才确认到底并丢弃两张重复截图；不同则滚动被吞恢复，确认截图入列继续。
 * OCR 逐段（crop-00.png..）+ mergeSegmentTexts 归一化重叠去重合并；接缝质量进 seamMis/suspectSeams/
 * textSeamUnmatched 元信息。
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
  mergeSegmentTexts,
  cleanOcrText,
  ocrNameMatches,
  SCROLL_TOP_NOTCHES,
  SEGMENT_WHEEL_NOTCHES,
  BOTTOM_CONFIRM_DELAY,
  MAX_SEGMENTS,
  MAX_CAPTURES,
  K_MIN,
  SEAM_MIS_SUSPECT,
  type DeviceRect,
  type OcrEngine,
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
const CROP_PNG = Buffer.from('fake-crop-png-bytes')
const OCR_TEXT = '张三 男 26岁 本科\nPHP 开发 5 年\n某科技公司 后端工程师\n项目：电商订单系统…'
/** 3 段互不重叠的段文本（归一化后无 ≥8 字符相似窗口 → 合并走 '\n' 直拼） */
const SEG_TEXTS = [
  '张三 男 26岁 本科\nPHP 开发 5 年',
  '某科技公司 后端工程师\n负责订单系统与库存系统',
  '专业技能：MySQL 索引优化 Redis 缓存 分布式事务',
]

/** fake 依赖容器：captureSizes 按次序给出每段整页 PNG 字节数（越界取末值）；stitch 落盘假 PNG
 *  供 copyFile，并向 cropDir 写 crop-NN.png 供逐段 OCR；ocrTexts 按段对位（越界取末值）。 */
interface Fake {
  wheels: Array<{ deltaY: number; notches: number }>
  captures: number[]
  sleeps: number[]
  sameViewCalls: Array<{ a: string; b: string }>
  /** sameView 返回值队列（shift 消费；空则恒 true） */
  sameViewResults: boolean[]
  /** 非空时 sameView 抛该错误（测试 fail-loud 路径） */
  sameViewError?: Error
  stitchCalls: Array<{ parts: string[]; rect: DeviceRect; outFile: string; cropDir?: string }>
  ocrBatchCalls: string[][]
  /** fake ocrBatch 返回的引擎标识（默认 winrt；rapid 用例改写后随结果透传） */
  ocrEngine: OcrEngine
  progress: string[]
  /** stitch 返回的每接缝错配率（越界取 0.08） */
  seamMis: number[]
  ocrTexts: string[]
  readerDeps: Omit<ResumeReadDeps, 'signal'>
}

function makeFake(captureSizes: number[], ocrTexts: string[] = [OCR_TEXT]): Fake {
  const f: Fake = {
    wheels: [],
    captures: [],
    sleeps: [],
    sameViewCalls: [],
    sameViewResults: [],
    stitchCalls: [],
    ocrBatchCalls: [],
    ocrEngine: 'winrt',
    progress: [],
    seamMis: [],
    ocrTexts,
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
    stitch: async (parts, rect, outFile, cropDir) => {
      f.stitchCalls.push({ parts, rect, outFile, cropDir })
      await fs.writeFile(outFile, STITCHED_PNG)
      if (cropDir) {
        await fs.mkdir(cropDir, { recursive: true })
        for (let i = 0; i < parts.length; i++) {
          await fs.writeFile(path.join(cropDir, `crop-${String(i).padStart(2, '0')}.png`), CROP_PNG)
        }
      }
      const seams = Math.max(0, parts.length - 1)
      return {
        width: 727,
        height: 2827,
        overlaps: Array.from({ length: seams }, () => 746),
        seamMis: Array.from({ length: seams }, (_, i) => f.seamMis[i] ?? 0.08),
      }
    },
    ocrBatch: async (files) => {
      f.ocrBatchCalls.push(files)
      return {
        texts: files.map((_, i) => f.ocrTexts[Math.min(i, f.ocrTexts.length - 1)]!),
        engine: f.ocrEngine,
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

test('locateResumeCanvas：无 canvas / 只有小 canvas / 高度不过门槛 → null', () => {
  assert.equal(locateResumeCanvas(canvasSnap({})), null)
  assert.equal(locateResumeCanvas(canvasSnap({ canvases: [[0, 0, 100, 100]] })), null)
  assert.equal(locateResumeCanvas(canvasSnap({ canvases: [[0, 0, 727, 399]] })), null) // h<400 未过门槛
  // 真机 2026-08-18：572 高的合法弹层画布（旧门槛 600 曾误杀）必须识别
  assert.notEqual(locateResumeCanvas(canvasSnap({ canvases: [[0, 0, 760, 572]] })), null)
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
  const f = makeFake([1000, 2000, 3000, 3000, 3000], SEG_TEXTS) // 第 4/5 次截图与第 3 段相同
  const res = await makeReader(f).readResume()
  assert.equal(res.segments, 3)
  assert.equal(res.bottomReached, true)
  assert.equal(res.width, 727)
  assert.equal(res.height, 2827)
  assert.equal(f.captures.length, 5) // 3 段 + 2 张重复截图（已丢弃）
  // wheel = 回顶 50 格 + 3 次向下 8 格（段推进）+ 1 次向下 8 格（到底确认路径）
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
  // cropDir 透传：P2 起一次批量 OCR 调用处理全部段（crop-00..crop-02 顺序入参）
  assert.match(f.stitchCalls[0]!.cropDir ?? '', /crops$/)
  assert.equal(f.ocrBatchCalls.length, 1)
  assert.deepEqual(
    f.ocrBatchCalls[0]!.map((p) => path.basename(p)),
    ['crop-00.png', 'crop-01.png', 'crop-02.png'],
  )
  f.ocrBatchCalls[0]!.forEach((p) => assert.equal(path.dirname(p), f.stitchCalls[0]!.cropDir))
  // 3 段互不重叠 → '\n' 直拼合并；P2 每段先过 cleanOcrText（字符间空格清理）再合并
  const segClean = SEG_TEXTS.map(cleanOcrText)
  assert.equal(res.text, segClean.join('\n'))
  assert.equal(res.chars, segClean.join('\n').length)
  assert.equal(res.ocrEngine, 'winrt') // fake ocrBatch 透传的引擎标识（结果元信息）
  assert.deepEqual(f.progress, ['stitch', 'ocr'])
  // P1 元信息：接缝错配率（fake 默认 0.08）低于可疑阈值；SEG_TEXTS 互不重叠 → 文本接缝
  // 未对上（真实链路相邻段应重叠 ~15-25%，未对上即警告信号）
  assert.deepEqual(res.seamMis, [0.08, 0.08])
  assert.deepEqual(res.suspectSeams, [])
  assert.deepEqual(res.textSeamUnmatched, [1, 2])
  assert.equal(res.ocrEmptySegments, 0)
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
  assert.equal(res.chars, cleanOcrText(OCR_TEXT).length)
  assert.equal(res.text, cleanOcrText(OCR_TEXT))
  // wheel = 回顶 + 段推进 1 次 + 到底确认 1 次（共 3 次，真机旧实现是 2 次——多出的 1 次即确认滚动）
  assert.deepEqual(f.wheels, [UP, DOWN, DOWN])
  assert.equal(f.captures.length, 3)
  assert.equal(f.stitchCalls[0]!.parts.length, 1)
  assert.deepEqual(
    f.ocrBatchCalls[0]!.map((p) => path.basename(p)),
    ['crop-00.png'],
  )
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
  const f = makeFake([1000, 1000, 2000, 2000, 2000], ['甲段独立内容若干', '乙段独立内容另一些'])
  const res = await makeReader(f).readResume()
  assert.equal(res.segments, 2) // parts = [cap0, cap2]；被吞的 cap1 与确认用的 cap3/cap4 均不入列
  assert.equal(res.bottomReached, true)
  assert.equal(f.captures.length, 5)
  assert.match(f.stitchCalls[0]!.parts[0]!, /part-raw-0\.png$/)
  assert.match(f.stitchCalls[0]!.parts[1]!, /part-raw-2\.png$/) // 恢复的确认截图
  assert.equal(f.wheels.length, 5) // 回顶 + cap0 后推进 + 确认滚动 + cap2 后推进 + 确认滚动
  assert.equal(f.sameViewCalls.length, 3) // (cap1,cap0) (cap3,cap2) (cap4,cap2)
  assert.equal(res.text, '甲段独立内容若干\n乙段独立内容另一些')
})

test('②b 字节相同但像素不同（canvas 重绘噪声）→ 不判到底，该段正常入列继续', async () => {
  const f = makeFake([1000, 1000, 3000, 3000, 3000], SEG_TEXTS)
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
  const seg16 = [
    '姓名张三男二十六岁本科毕业',
    '求职意向为后端开发工程师',
    '曾就职于某电商科技公司',
    '主要负责订单域系统研发',
    '技术栈以 Java 生态为主',
    '数据库 MySQL 调优经验丰富',
    '缓存与消息队列均有实践',
    '带过三人小团队完成迭代',
    '上线过两次大促保障项目',
    '本科毕业于某理工大学',
    '英语水平六级可以流利阅读',
    '期望城市杭州或接受远程',
    '期望薪资面议随时到岗',
    '个人项目是一个记账工具',
    '爱好跑步与摄影记录日常',
    '自我评价踏实肯学抗压强',
  ]
  assert.equal(seg16.length, MAX_SEGMENTS)
  const sizes = Array.from({ length: 30 }, (_, i) => 1000 + i * 500) // 每段字节差 ≥ 200 → 永不到底
  const f = makeFake(sizes, seg16)
  const res = await makeReader(f).readResume()
  assert.equal(res.segments, MAX_SEGMENTS)
  assert.equal(res.bottomReached, false) // 未确认到底：调用方必须警告可能截断，绝不静默当完整简历
  assert.equal(f.captures.length, MAX_SEGMENTS)
  assert.equal(f.sameViewCalls.length, 0) // 快路径：字节差 ≥ epsilon 不调 sameView
  assert.equal(f.stitchCalls[0]!.parts.length, MAX_SEGMENTS)
  assert.equal(f.ocrBatchCalls[0]!.length, MAX_SEGMENTS)
  // 16 段互不重叠 → '\n' 直拼（每段先过 cleanOcrText），全部 15 个文本接缝未对上（合成数据；真实链路重叠段应命中）
  const seg16Clean = seg16.map(cleanOcrText)
  assert.equal(res.text, seg16Clean.join('\n'))
  assert.deepEqual(res.textSeamUnmatched, Array.from({ length: MAX_SEGMENTS - 1 }, (_, i) => i + 1))
  // 最后一段入库后不再多滚（第 MAX+1 次截图已被禁止）：回顶 1 次 + 每段后滚 15 次
  assert.equal(f.wheels.length, MAX_SEGMENTS)
  assert.deepEqual(
    f.wheels.filter((w) => w.deltaY < 0),
    Array.from({ length: MAX_SEGMENTS - 1 }, () => DOWN),
  )
  // 总截图次数安全上限（防确认路径死循环；正常路径数学上不可达，仅 belt-and-braces）
  assert.equal(MAX_CAPTURES, 2 * MAX_SEGMENTS + 8)
})

test('逐段 OCR 文本重叠去重（readResume 全链路）：相邻段重叠内容只保留一份', async () => {
  const s0 = '张三 男 26岁 本科\n某科技公司 后端工程师'
  const s1 = '某科技公司 后竭工程师\n负责订单系统交付' // 与 s0 重叠 10 个归一化字符，含 1 处 OCR 错字
  const s2 = '负责订单系统交付\n上线支撑双 eleven 大促' // 与 s1 重叠 7 字 + 空格形态一致
  const f = makeFake([1000, 2000, 3000, 3000, 3000], [s0, s1, s2])
  const res = await makeReader(f).readResume()
  assert.equal(res.segments, 3)
  // 接缝 1：s0 版本保留，s1 跳过重叠前缀、其余（含换行）原样接上；接缝 2 同理。
  // P2：合并前每段过 cleanOcrText（空格清理在归一化匹配之后不影响命中，存储文本变干净）
  assert.equal(res.text, '张三男26岁本科\n某科技公司后端工程师\n负责订单系统交付\n上线支撑双eleven大促')
  assert.deepEqual(res.textSeamUnmatched, [])
  assert.equal(res.ocrEmptySegments, 0)
})

test('P2：ocrBatch 引擎标识透传（rapid）+ 每段文本照样过 cleanOcrText（rapid 输出幂等无害）', async () => {
  const f = makeFake([1000, 1000, 1000], ['5 年 工 作 经 验\nPHP 开发'])
  f.ocrEngine = 'rapid'
  const res = await makeReader(f).readResume()
  assert.equal(res.ocrEngine, 'rapid')
  assert.equal(res.text, '5年工作经验\nPHP开发')
})

test('接缝错配率超阈值 → suspectSeams 元信息（调用方据此警告人工核对）', async () => {
  const f = makeFake([1000, 2000, 3000, 3000, 3000], SEG_TEXTS)
  f.seamMis = [0.5, 0.36] // 均超 SEAM_MIS_SUSPECT=0.35
  const res = await makeReader(f).readResume()
  assert.deepEqual(res.seamMis, [0.5, 0.36])
  assert.deepEqual(res.suspectSeams, [1, 2]) // 1-based 接缝序号
  assert.equal(SEAM_MIS_SUSPECT, 0.35)
})

test('单段 OCR 空文本不炸整体（图片/空白段真实存在）：计数 ocrEmptySegments，其余段照常合并', async () => {
  const f = makeFake([1000, 2000, 3000, 3000, 3000], [SEG_TEXTS[0]!, '   \n\t', SEG_TEXTS[2]!])
  const res = await makeReader(f).readResume()
  assert.equal(res.segments, 3)
  assert.equal(res.ocrEmptySegments, 1)
  assert.equal(res.text, `${cleanOcrText(SEG_TEXTS[0]!)}\n${cleanOcrText(SEG_TEXTS[2]!)}`) // 空段跳过（不追加 '\n' 噪声）
})

test('全部段 OCR 空文本 → fail-loud（绝不返回半成品），失败路径也清理临时目录', async () => {
  const f = makeFake([1000, 1000, 1000], ['   \n\t'])
  await assert.rejects(makeReader(f).readResume(), (e: unknown) => {
    assert.ok(e instanceof ResumeReadError)
    assert.match(e.message, /OCR 未识别到任何文字/)
    return true
  })
  assert.equal(f.ocrBatchCalls.length, 1) // 单段：批量 OCR 确实被调过（返回了空白）
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
    ocrBatch: async () => {
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

// ---------- cleanOcrText：OCR 文本空格清理（P2，纯函数；WinRT 兜底文本「5 年 工 作 经 验」全靠它） ----------

test('cleanOcrText：WinRT 字符间全空格（真机样例）→ 全部删除', () => {
  assert.equal(cleanOcrText('5 年 工 作 经 验'), '5年工作经验')
  assert.equal(cleanOcrText('康 嘉 润 飓 飓 活 跃'), '康嘉润飓飓活跃')
  // 数字/字母与中文之间的空格同样删除（任一侧 CJK 即删）
  assert.equal(cleanOcrText('PHP 开发 5 年'), 'PHP开发5年')
  assert.equal(cleanOcrText('30岁 | 大专 | 9年'), '30岁|大专|9年')
})

test('cleanOcrText：两侧都是 ASCII 字母/数字 → 保留单空格（不误伤）', () => {
  assert.equal(cleanOcrText('Linuw Windows'), 'Linuw Windows')
  assert.equal(cleanOcrText('10 15'), '10 15')
  assert.equal(cleanOcrText('MySQL 索引'), 'MySQL索引') // 中文侧删除
  // 连续多空格折叠成单空格（仅限保留分支）
  assert.equal(cleanOcrText('Linux   Windows'), 'Linux Windows')
})

test('cleanOcrText：换行结构原样保留（只动空格/tab/全角空格，不动换行）', () => {
  assert.equal(cleanOcrText('第一行 内容\n第二行 内容'), '第一行内容\n第二行内容')
  assert.equal(cleanOcrText('a\n\nb'), 'a\n\nb') // 空行保留
})

test('cleanOcrText：行首/行尾空白删除（含紧邻换行的空白）；全角空格清除', () => {
  assert.equal(cleanOcrText('  张三　男  \n  26岁  '), '张三男\n26岁')
  assert.equal(cleanOcrText('张三\u3000男'), '张三男') // \u3000 全角空格
  assert.equal(cleanOcrText('A \t B'), 'A B') // tab 与空格 run 折叠为单空格
})

test('cleanOcrText：CJK 标点/全角形式/ASCII 标点相邻的空格删除（唯一保留 = 两侧 ASCII 字母数字）', () => {
  assert.equal(cleanOcrText('你好 ，世界'), '你好，世界') // 全角逗号 \uff0c ∈ \uff00-\uffef
  assert.equal(cleanOcrText('大专 丨 9年'), '大专丨9年') // 丨 ∈ \u4e00-\u9fa5
  assert.equal(cleanOcrText('工作 · 生活'), '工作·生活') // 间隔号 · 为 CJK 相关
  assert.equal(cleanOcrText('Hello, World'), 'Hello,World') // ASCII 逗号侧：非 both-alnum → 删（真机干净文本无标点侧空格）
  assert.equal(cleanOcrText('30岁 | 大专 | 9年'), '30岁|大专|9年')
})

test('cleanOcrText：WinRT CRLF 行尾（cv-ocr.ps1 落盘的 OcrResult.Text）→ \\r 随行尾删除，输出统一 \\n 行尾', () => {
  assert.equal(cleanOcrText('5 年 工 作 经 验\r\n康 嘉 润\r\n'), '5年工作经验\n康嘉润\n')
  assert.equal(cleanOcrText('本科\r\n'), '本科\n') // 行尾裸 \r 同样删除
})

test('cleanOcrText：rapid 行重建输出（无字符间空格）幂等无害', () => {
  const rapidText = '黄钰鑫刚刚活跃30岁|大专丨9年丨离职-随时到岗\n熟悉Linux/Windows/Mac'
  assert.equal(cleanOcrText(rapidText), rapidText)
  assert.equal(cleanOcrText(''), '')
})

// ---------- mergeSegmentTexts：逐段 OCR 文本合并（归一化重叠去重，纯函数） ----------

test('mergeSegmentTexts：精确重叠去重（B 的重叠前缀被跳过，A 版本保留）', () => {
  const a = '前段独立内容' + '重叠区域至少八个字符'
  const b = '重叠区域至少八个字符' + '后段独立内容'
  const { text, seamMatches } = mergeSegmentTexts([a, b])
  assert.equal(text, '前段独立内容重叠区域至少八个字符后段独立内容')
  assert.deepEqual(seamMatches, [true])
})

test('mergeSegmentTexts：容错重叠（同内容两次 OCR 零星差异，≥80% 相似仍去重）', () => {
  const a = '背景' + '负责电商平台订单模块开发'
  const b = '负责电商平台订卑模块开发' + '上线' // 重叠区 12 字符含 1 处错字（单→卑）
  const { text, seamMatches } = mergeSegmentTexts([a, b])
  assert.equal(text, '背景负责电商平台订单模块开发上线') // A 的版本保留，B 只补「上线」
  assert.deepEqual(seamMatches, [true])
})

test('mergeSegmentTexts：格式保留（B 段切割点之后的空格/换行原样保留）', () => {
  const a = '自我评价：' + '精通Java并发与JVM调优'
  const b = '精通 Java 并发与 JVM 调优\n负责性能优化专项' // 重叠区在 B 里带空格打散
  const { text, seamMatches } = mergeSegmentTexts([a, b])
  assert.equal(text, '自我评价：精通Java并发与JVM调优\n负责性能优化专项') // B 的 '\n' 保留
  assert.deepEqual(seamMatches, [true])
})

test('mergeSegmentTexts：无重叠 → 换行直拼 A 与 B，seamMatches=false（可能有重复内容）', () => {
  const a = '工作经历阿里巴巴高级工程师'
  const b = '教育背景某大学计算机专业'
  const { text, seamMatches } = mergeSegmentTexts([a, b])
  assert.equal(text, `${a}\n${b}`)
  assert.deepEqual(seamMatches, [false])
})

test(`mergeSegmentTexts：重叠 < K_MIN=${K_MIN} 不算命中（短重叠极易误相似）→ 直拼不去重`, () => {
  const a = '开头内容' + 'ABCDEFG' // 尾部 7 字符重叠（< K_MIN）
  const b = 'ABCDEFG' + '结尾内容'
  const { text, seamMatches } = mergeSegmentTexts([a, b])
  assert.equal(text, `${a}\n${b}`) // 'ABCDEFG' 保留两份（宁重复不误删）
  assert.deepEqual(seamMatches, [false])
  assert.equal(text.includes('ABCDEFGABCDEFG'.slice(0, 7)), true) // 两份重叠内容都在
})

test('mergeSegmentTexts：单段数组原样返回；空数组返回空', () => {
  assert.deepEqual(mergeSegmentTexts(['唯一一段']), { text: '唯一一段', seamMatches: [] })
  assert.deepEqual(mergeSegmentTexts([]), { text: '', seamMatches: [] })
})

test('mergeSegmentTexts：空段（无可见字符）跳过不追加，该接缝记 true（无重复风险）', () => {
  const { text, seamMatches } = mergeSegmentTexts(['甲段落一', '  \n ', '乙段落二'])
  assert.equal(text, '甲段落一\n乙段落二') // 空段不产生 '\n' 噪声
  assert.deepEqual(seamMatches, [true, false]) // 甲/乙 无重叠 → 第二接缝 false
})

test('mergeSegmentTexts：累计文本全空白（首段纯图片）→ 直接换成 B，不留前导空白噪声', () => {
  const { text, seamMatches } = mergeSegmentTexts(['  \n\t ', '图片下方正文内容'])
  assert.equal(text, '图片下方正文内容')
  assert.deepEqual(seamMatches, [true])
})

// ---------- mergeSegmentTexts：真机规模重叠（P1 必修回归，MERGE_WINDOW=1024 校准） ----------
// 真机实证：相邻段像素 overlap ~746px ≈ 750-970 归一化字符（旧 MERGE_WINDOW=400 必然失配 →
// 全量直拼每接缝重复 ~800 字）。这里按真机几何构造：每段 ~1200 归一化字 + 700/800/900 字重叠 +
// ~2% 独立 OCR 错字（两侧错字位置独立，模拟同一内容两次识别的差异），断言命中去重 + 性能量级。

/** mulberry32 确定性 PRNG（无依赖、可复现）：构造非周期合成中文文本 */
function mulberry32(seed: number): () => number {
  let s = seed | 0
  return () => {
    s = (s + 0x6d2b79f5) | 0
    let t = Math.imul(s ^ (s >>> 15), 1 | s)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

/** 简历域常用字/词（归一化后形态，随机组合成非周期文本） */
const RESUME_CHARS =
  '负责订单系统开发维护优化数据库缓存消息队列设计接口文档测试上线监控排查故障性能压测容量规划团队协作沟通项目复盘迭代交付质量规范代码评审单元集成部署流水线容器编排服务治理注册发现配置中心网关路由鉴权限流熔断降级灰度发布回滚数据一致事务分布式幂等重试补偿对账清分结算报表导出批量定时任务调度电商库存物流支付退款售后营销活动优惠券秒杀拼团会员积分'

function randResumeText(rnd: () => number, len: number): string {
  let s = ''
  for (let i = 0; i < len; i++) s += RESUME_CHARS[Math.floor(rnd() * RESUME_CHARS.length)]
  return s
}

/** 模拟 OCR 错字：rate 比例的字符替换成随机字（seed 独立 → 两侧错字位置不同） */
function corruptOcr(s: string, rnd: () => number, rate: number): string {
  return s
    .split('')
    .map((ch) => (rnd() < rate ? RESUME_CHARS[Math.floor(rnd() * RESUME_CHARS.length)] : ch))
    .join('')
}

/** 归一化（与实现同规则：去全部空白）——断言用 */
const norm = (s: string): string => s.replace(/\s+/g, '')

/** 构造真机规模两段：full 前 1200 字为 A，B 从 (1200-L) 起再取 1200 字（与 A 重叠 L 字）。
 *  两侧加 OCR 式空格/换行 + 独立 2% 错字。返回 { a, b, fullNorm }（fullNorm = 无错字基准）。 */
function makeRealScaleSegments(L: number): { a: string; b: string; fullNorm: string } {
  const full = randResumeText(mulberry32(20260902), 3000)
  const aClean = full.slice(0, 1200)
  const bClean = full.slice(1200 - L, 1200 - L + 1200)
  const spaceOut = (s: string): string =>
    s
      .split('')
      .map((ch, i) => ch + (i % 23 === 22 ? '\n' : i % 11 === 10 ? ' ' : ''))
      .join('')
  const a = corruptOcr(spaceOut(aClean), mulberry32(L * 2 + 1), 0.02)
  const b = corruptOcr(spaceOut(bClean), mulberry32(L * 2 + 2), 0.02)
  return { a, b, fullNorm: full }
}

test('mergeSegmentTexts：真机规模重叠 700/800/900 归一化字（+2% 独立错字）全部命中去重，无大段重复', () => {
  for (const L of [700, 800, 900]) {
    const { a, b } = makeRealScaleSegments(L)
    const { text, seamMatches } = mergeSegmentTexts([a, b])
    assert.deepEqual(seamMatches, [true], `L=${L} 文本接缝必须命中（旧 MERGE_WINDOW=400 在此规模必失配）`)
    const mergedNorm = norm(text)
    // A 版本整体保留（命中时 merged = A 原文 + B 切割点后内容）
    assert.ok(mergedNorm.startsWith(norm(a)), `L=${L} A 段必须原样保留在前`)
    // 去重生效：总长 ≈ 1200 + (1200 - L)，重复一整段重叠会多出 ~L（700-900）字，容差 40 足以区分
    const expectLen = 1200 + (1200 - L)
    assert.ok(
      Math.abs(mergedNorm.length - expectLen) <= 40,
      `L=${L} 去重后长度 ${mergedNorm.length} 应 ≈ ${expectLen}（±40）`,
    )
    // B 的非重叠尾部（留 100 字余量容忍 matchedK 与真实重叠的微小偏差）接在合并结果末尾
    const bTail = norm(b).slice(L + 100)
    assert.ok(mergedNorm.endsWith(bTail), `L=${L} B 非重叠尾部必须完整接上`)
  }
})

test('mergeSegmentTexts：真机规模单接缝性能量级（提前放弃防退化，阈值给足 CI 抖动余量）', () => {
  // 小规模预热一次，排除首次调用的 JIT/懒加载开销干扰
  mergeSegmentTexts(['预热内容', '预热内容后续'])
  for (const L of [700, 900]) {
    const { a, b } = makeRealScaleSegments(L)
    const t0 = Date.now()
    const { seamMatches } = mergeSegmentTexts([a, b])
    const dt = Date.now() - t0
    assert.deepEqual(seamMatches, [true])
    // 真机规模（1024 窗 + 2% 错字）单接缝典型 ~10-100ms、最坏 ~0.7s；阈值 2000ms 防退化 +
    // 防 CI 抖动（naive 无提前放弃的等价实现是 ~3.6e8 字符操作/接缝，会超此阈值一个量级）
    assert.ok(dt < 2000, `L=${L} 单接缝匹配耗时 ${dt}ms 应 < 2000ms`)
  }
})

// ---------- ocrNameMatches：姓名交叉校验（张冠李戴防护，容差=1 字 OCR 误差，宁跳过不错存） ----------

test('ocrNameMatches：精确命中 + 空格打散命中（真机首行样本 "…0 0 康 嘉 润 飓 飓 活 跃…"）', () => {
  // 真机 OCR 原文（2026-08-14，带空格）：姓名常被空格打散，归一化后精确包含
  const line = '最 近 关 注 工 作 经 历 0 0 康 嘉 润 飓 飓 活 跃 严 24 《 大 亏 4 年 离 一 随 时 到 岗'
  assert.equal(ocrNameMatches('康嘉润', `${line}\n后续内容`), true)
  assert.equal(ocrNameMatches('张三', '张三 男 26岁 本科\nPHP 开发 5 年'), true)
})

test('ocrNameMatches：替换 1 字命中（OCR 错字：文本写 "庭嘉润"，姓名 "康嘉润"）', () => {
  assert.equal(ocrNameMatches('康嘉润', '最近关注 00 庭嘉润 飓飓 活跃 24 本科'), true)
})

test('ocrNameMatches：插入噪声尾巴命中（文本 "康嘉润飓"，姓名 "康嘉润"）', () => {
  assert.equal(ocrNameMatches('康嘉润', '康嘉润飓 活跃 24 本科'), true)
})

test('ocrNameMatches：漏 1 字命中（文本只有 "康嘉"，姓名 "康嘉润"）', () => {
  assert.equal(ocrNameMatches('康嘉润', '康嘉 活跃 24 本科'), true)
})

test('ocrNameMatches：完全不相关 → false（不命中绝不放行入库）', () => {
  assert.equal(ocrNameMatches('王五', '最 近 关 注 欧 阳 锦 绣 活 跃 24 本 科\nPHP 开发 5 年'), false)
  assert.equal(ocrNameMatches('刘草威', '张三 男 26岁 本科\n某科技公司 后端工程师'), false)
})

test('ocrNameMatches：姓名在 400 字窗口之外 → false（头部窗口防长文正文误命中）', () => {
  const far = '昨'.repeat(400) + '王五' + '后文不重要'
  assert.equal(ocrNameMatches('王五', far), false)
  // 窗口内（第 400 字符之前）仍命中
  assert.equal(ocrNameMatches('王五', '昨'.repeat(395) + '王五' + '后文'), true)
})

test('ocrNameMatches：空姓名 / 空文本 → false（无法校验即不放行，fail-safe）', () => {
  assert.equal(ocrNameMatches('', '王五 活跃'), false)
  assert.equal(ocrNameMatches('王五', ''), false)
  assert.equal(ocrNameMatches('王五', '   \n\t'), false)
})

test('ocrNameMatches：含·的姓名（如 阿依古丽·买买提）命中', () => {
  assert.equal(ocrNameMatches('阿依古丽·买买提', '最 近 关 注 阿 依 古 丽 · 买 买 提 活 跃 24 本 科'), true)
  // ·被 OCR 漏识成空格或错字也有 1 字容差
  assert.equal(ocrNameMatches('阿依古丽·买买提', '阿依古丽买买提 活跃'), true)
})
