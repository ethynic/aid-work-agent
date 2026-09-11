/**
 * ResumeBatchReader 单测：推荐牛人页批量「点卡片 → 读简历 → Escape 关闭 → 下一张」链路。
 * fake 注入 snapshot/click/pressEscape/captureFullpage/wheel/sameView/stitch/ocr（参照 resume-reader.test.ts）。
 *
 * 快照构造按真机实证（2026-08-17，视口 1249x1277）：卡片行 = 「姓名(342,y-8) + 活跃状态(400,y-8)」
 * 同行 +「打招呼」按钮(1162,y)，行距 184px；点击点 = 卡片主体列 (600, y+70)；详情 canvas 760x1264@(168,40)。
 * 状态机 fake：click(Win32) 把快照切到 canvas 态（openTimeout 卡保持列表态）、pressEscape 切回列表态
 * （closeFail 卡保持 canvas 态），按 scripts 脚本化每张卡的行为。
 * 默认 OCR 文本头部按卡片姓名生成（defaultOcr，真机形如空格打散）——姓名交叉校验（ocrNameMatches）
 * 默认通过；要测「校验不过」用 script.ocrText 给出不含卡片名的头部。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { randomUUID } from 'node:crypto'
import { existsSync } from 'node:fs'
import {
  ResumeBatchReader,
  ResumeBatchError,
  type BatchCard,
  type ResumeBatchDeps,
  OPEN_CLICK_ATTEMPTS,
} from '../src/main/boss/ResumeBatchReader.js'
import { buildResumePayload } from '../src/main/operations/bossResumeDetail.js'
import { cleanOcrText } from '../src/main/boss/ResumeReader.js'
import type { DeviceRect, ResumeReadResult } from '../src/main/boss/ResumeReader.js'
import type { DomSnapshot, ClickPoint } from '../src/main/boss/domSnapshot.js'

/** 卡片行定义：姓名（null = DOM 配对失败）+ 打招呼按钮中心 y */
interface Row {
  name: string | null
  buttonY: number
}
const ROW1: Row = { name: '刘草威', buttonY: 146 }
const ROW2: Row = { name: '张三丰', buttonY: 330 }

/** 真机：打招呼按钮文本是字符串形态 "\n                  打招呼"（trim 后 === '打招呼'） */
const GREET_STRING = '\n                  打招呼'
const CANVAS_BOUNDS: [number, number, number, number] = [168, 40, 760, 1264] // 真机：详情 iframe canvas
// locateResumeCanvas 与视口求交（2026-09-11 小屏修复）：40+1264=1304 出屏（视口 1277）→ 截到 1237
const CANVAS_RECT: DeviceRect = { x: 168, y: 40, w: 760, h: 1237 }
const STITCHED_PNG = Buffer.from('fake-stitched-png-bytes')

/** 默认 OCR 文本：头部按卡片姓名生成（真机形如姓名被空格打散），保证姓名交叉校验通过 */
function defaultOcr(name: string | null): string {
  const scattered = (name ?? '某').split('').join(' ')
  return `最 近 关 注 ${scattered} 活 跃 24 本 科\nPHP 开发 5 年\n某科技公司 后端工程师`
}

/** 捕获 stderr（[boss-batch] 诊断日志走 process.stderr.write） */
function captureStderr(): { lines: string[]; restore: () => void } {
  const lines: string[] = []
  const orig = process.stderr.write.bind(process.stderr)
  process.stderr.write = ((chunk: string | Uint8Array): boolean => {
    lines.push(String(chunk))
    return true
  }) as typeof process.stderr.write
  return { lines, restore: () => { process.stderr.write = orig } }
}

/** 构造快照：根视口 1249x1277 + 「筛选」+ 卡片行（姓名/活跃状态/噪音/打招呼按钮）+ 可选大 canvas */
function buildSnap(
  rows: Row[],
  opts: { canvas?: boolean; extraCanvases?: Array<[number, number, number, number]> } = {},
): DomSnapshot {
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
  /** 文本节点（nodeValue 指向 strings；nodeName 空） */
  const addText = (s: string, bounds: [number, number, number, number]): void => {
    const ni = nextNi++
    nvIndex.push(ni)
    nvValue.push(intern(s))
    nameIndex.push(ni)
    nameValue.push(0)
    layoutNodeIndex.push(ni)
    layoutBounds.push(bounds)
  }
  /** CANVAS 元素节点（nodeName='CANVAS'，无文本） */
  const addCanvas = (bounds: [number, number, number, number]): void => {
    const ni = nextNi++
    nvIndex.push(ni)
    nvValue.push(0)
    nameIndex.push(ni)
    nameValue.push(intern('CANVAS'))
    layoutNodeIndex.push(ni)
    layoutBounds.push(bounds)
  }

  addText('筛选', [1100, 50, 80, 24])
  for (const row of rows) {
    // 真机锚定：姓名(342,y-8) 与活跃状态(400,y-8) 同行，按钮中心 (1162,y)
    if (row.name !== null) addText(row.name, [317, row.buttonY - 18, 50, 20]) // 中心 (342, y-8)
    addText('刚刚活跃', [370, row.buttonY - 18, 60, 20]) // 中心 (400, y-8)
    addText('本科', [317, row.buttonY + 30, 40, 20]) // 噪音：同行带外（y 差 38 > 8）
    addText(GREET_STRING, [1130, row.buttonY - 16, 64, 32]) // 中心 (1162, y)
  }
  if (opts.canvas) addCanvas(CANVAS_BOUNDS)
  for (const b of opts.extraCanvases ?? []) addCanvas(b)

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

/** 每张卡的行为脚本（按点击次序对位） */
interface CardScript {
  /** 点击后 canvas 永不出现（打开超时） */
  openTimeout?: boolean
  /** 读取阶段 OCR 返回空白 → ResumeReadError */
  readError?: boolean
  /** Escape 后 canvas 不消失（关闭失败） */
  closeFail?: boolean
  /** 本卡 OCR 文本（缺省 defaultOcr(卡片姓名)：头部含姓名，交叉校验通过） */
  ocrText?: string
}

/** 状态机 fake：click 切 canvas 态、escape 切列表态（脚本可覆盖）。
 * initialState='canvas' 模拟「残留详情弹层」（boss_resume_detail 读完不关详情的链路）。 */
function makeBatch(
  rows: Row[],
  scripts: CardScript[] = [],
  opts: { initialState?: 'list' | 'canvas'; extraCanvases?: Array<[number, number, number, number]> } = {},
) {
  const f = {
    clicks: [] as ClickPoint[],
    escapes: 0,
    wheels: [] as Array<{ deltaY: number; notches: number; rect: DeviceRect }>,
    captures: 0,
    sameViewCalls: [] as Array<{ a: string; b: string; rect: DeviceRect }>,
    stitchCalls: [] as Array<{ parts: string[]; rect: DeviceRect; outFile: string; cropDir?: string }>,
    ocrBatchCalls: [] as string[][],
    progress: [] as Array<[number, number]>,
    snapshotCount: 0,
    seamMis: [] as number[],
  }
  let state: 'list' | 'canvas' = opts.initialState ?? 'list'
  let cardIdx = 0 // 当前正在处理的卡片序号（readBatch 逐卡推进；重点同一张卡不推进）
  const scriptAt = (i: number): CardScript => scripts[i] ?? {}
  /** 点击点 y → 卡片行序（buttonY 唯一，主体列点 buttonY+70 / 姓名点 buttonY-8 均可反查） */
  const rowOfPoint = (y: number): number => {
    const hit = rows.findIndex((r) => Math.abs(r.buttonY + 70 - y) <= 2 || Math.abs(r.buttonY - 8 - y) <= 2)
    return hit >= 0 ? hit : cardIdx
  }
  const deps: ResumeBatchDeps = {
    snapshot: async () => {
      f.snapshotCount++
      return buildSnap(rows, { canvas: state === 'canvas', extraCanvases: opts.extraCanvases })
    },
    click: async (point) => {
      f.clicks.push(point)
      // 脚本按卡片行对位（不是点击次序——重点机制下同一张卡会点多次）；
      // 2026-09-10 起点击姓名点（姓名节点中心 = buttonY-8）也归位到对应行
      const s = scriptAt(rowOfPoint(point.y))
      state = s.openTimeout ? 'list' : 'canvas'
      cardIdx = rowOfPoint(point.y)
    },
    pressEscape: async () => {
      f.escapes++
      const s = scriptAt(cardIdx)
      state = s.closeFail ? 'canvas' : 'list'
    },
    captureFullpage: async () => {
      f.captures++
      return Buffer.alloc(1000) // 相邻两次字节相同 → sameView 确认 → 再滚再截仍相同 → 单段到底
    },
    wheel: async (rect, _viewport, deltaY, notches) => {
      f.wheels.push({ deltaY, notches, rect })
    },
    sameView: async (a, b, rect) => {
      f.sameViewCalls.push({ a, b, rect })
      return true // 恒同画面（配合恒定字节数 → 连续两次相同即到底）
    },
    stitch: async (parts, rect, outFile, cropDir) => {
      f.stitchCalls.push({ parts, rect, outFile, cropDir })
      await fs.writeFile(outFile, STITCHED_PNG)
      if (cropDir) {
        await fs.mkdir(cropDir, { recursive: true })
        for (let i = 0; i < parts.length; i++) {
          await fs.writeFile(path.join(cropDir, `crop-${String(i).padStart(2, '0')}.png`), Buffer.alloc(64))
        }
      }
      const seams = Math.max(0, parts.length - 1)
      return {
        width: 760,
        height: 2400,
        overlaps: Array.from({ length: seams }, () => 746),
        seamMis: Array.from({ length: seams }, (_, i) => f.seamMis[i] ?? 0.08),
      }
    },
    ocrBatch: async (files) => {
      f.ocrBatchCalls.push(files)
      const s = scriptAt(cardIdx)
      // 单段到底（恒定字节数 fake）→ files 只有 1 个 crop；文本按卡片脚本对位
      const text = s.readError ? '   \n\t' : (s.ocrText ?? defaultOcr(rows[cardIdx]?.name ?? null))
      return { texts: files.map(() => text), engine: 'winrt' }
    },
    onProgress: (done, total) => {
      f.progress.push([done, total])
    },
    sleep: async () => {},
  }
  return { deps, f, reader: () => new ResumeBatchReader(deps) }
}

// ---------- locateCards：姓名锚定与视口过滤 ----------

test('locateCards：姓名锚定（活跃状态左侧最近中文）+ 姓名节点中心为点击点 + 按 y 排序', () => {
  const { deps, f } = makeBatch([ROW1, ROW2])
  const cards = new ResumeBatchReader(deps).locateCards(buildSnap([ROW1, ROW2]))
  assert.deepEqual(
    cards.map((c) => [c.name, c.namePoint]),
    [
      ['刘草威', { x: 342, y: ROW1.buttonY - 8 }],
      ['张三丰', { x: 342, y: ROW2.buttonY - 8 }],
    ] as Array<[string | null, ClickPoint | null]>,
  )
  assert.equal(f.snapshotCount, 0) // locateCards 是纯函数，不触达 snapshot
})

test('locateCards：无名行 name/namePoint=null（readBatch 不点击直接记 failure）', () => {
  const noName: Row = { name: null, buttonY: 146 }
  const cards: BatchCard[] = new ResumeBatchReader(makeBatch([]).deps).locateCards(
    buildSnap([noName]),
  )
  assert.equal(cards.length, 1)
  assert.equal(cards[0]!.name, null)
  assert.equal(cards[0]!.namePoint, null)
})

test('locateCards：无打招呼按钮 → 空数组（readBatch 首轮会转 ResumeBatchError）', () => {
  const cards = new ResumeBatchReader(makeBatch([]).deps).locateCards(buildSnap([]))
  assert.deepEqual(cards, [])
})

// ---------- readBatch 主链路 ----------

test('① 正常 2 份（limit 2）：逐卡点击/关闭顺序、resumes 姓名与契约 payload base64 + name_source=dom', async () => {
  const { reader, f } = makeBatch([ROW1, ROW2])
  const result = await reader().readBatch({ limit: 2 })
  assert.deepEqual(result.failures, [])
  assert.equal(result.attempted, 2)
  // 2026-09-10 起首选点击姓名节点中心（342, 按钮 y-8），按 y 从上到下
  assert.deepEqual(f.clicks, [
    { x: 342, y: ROW1.buttonY - 8 },
    { x: 342, y: ROW2.buttonY - 8 },
  ])
  assert.equal(f.escapes, 2) // 每份读完 Escape 关闭一次
  assert.deepEqual(
    result.resumes.map((r) => r.name),
    ['刘草威', '张三丰'],
  )
  // P2：OCR 文本经 cleanOcrText 清理字符间空格后入 readResult（fake 文本带 WinRT 式空格打散）
  assert.equal(result.resumes[0]!.readResult.text, cleanOcrText(defaultOcr('刘草威')))
  assert.equal(result.resumes[0]!.readResult.ocrEngine, 'winrt') // 引擎标识随 ocrBatch 透传
  assert.equal(result.resumes[0]!.readResult.segments, 1) // 相邻截图字节相同 → sameView 确认到底 → 单段
  assert.deepEqual(result.resumes[0]!.readResult.imageBuffer, STITCHED_PNG)
  // P1 元信息随 readResult 返回（单段无接缝）：供 buildResumePayload 透传
  assert.deepEqual(result.resumes[0]!.readResult.seamMis, [])
  assert.deepEqual(result.resumes[0]!.readResult.suspectSeams, [])
  assert.deepEqual(result.resumes[0]!.readResult.textSeamUnmatched, [])
  assert.equal(result.resumes[0]!.readResult.ocrEmptySegments, 0)
  // sameView 透传给内部 ResumeReader（rect = 详情画布 device 区域，同 wheel 模式）
  assert.equal(f.sameViewCalls.length, 4) // 每份 2 次（候选 + 确认），2 份
  f.sameViewCalls.forEach((c) => assert.deepEqual(c.rect, CANVAS_RECT))
  // 滚动点 = canvas 中心（wheel 回调收到的 rect 即详情画布 device 区域）
  assert.ok(f.wheels.length >= 2)
  assert.deepEqual(f.wheels[0]!.rect, CANVAS_RECT)
  // 进度逐份推进
  assert.deepEqual(f.progress, [
    [1, 2],
    [2, 2],
  ])
  // 单份契约 payload：name_source='dom'（云端最后防线：非 OCR 来源才许入库），images[0].base64 = fake stitch 落盘的假 PNG
  const payload = buildResumePayload('刘草威', 'PHP开发工程师', result.resumes[0]!.readResult, 'dom')
  assert.equal(payload.candidate_name, '刘草威')
  assert.equal(payload.name_source, 'dom')
  assert.equal(payload.job_name, 'PHP开发工程师')
  assert.equal(payload.ocr_text, cleanOcrText(defaultOcr('刘草威')))
  assert.equal(payload.ocr_engine, 'winrt') // P2：实际引擎随 payload 元信息透传
  const images = payload.images as Array<{ base64: string }>
  assert.equal(images[0]!.base64, STITCHED_PNG.toString('base64'))
  // P1 接缝质量元信息透传（云端契约不读取，已有先例）：单段无接缝 → 空数组/0
  assert.deepEqual(payload.seam_mis, [])
  assert.deepEqual(payload.suspect_seams, [])
  assert.deepEqual(payload.text_seam_unmatched, [])
  assert.equal(payload.ocr_empty_segments, 0)
})

test('② DOM 姓名配对失败（无名卡）→ 不点击直接记 failure（无姓名点=无可点点位，绝不盲点白读）', async () => {
  const noName: Row = { name: null, buttonY: 146 }
  const { reader, f } = makeBatch([noName], [{ ocrText: '康嘉润 活跃\n本科 5 年 后端' }])
  const result = await reader().readBatch({ limit: 1 })
  assert.deepEqual(result.resumes, []) // 不入 resumes
  assert.equal(result.failures.length, 1)
  assert.equal(result.failures[0]!.name, null)
  assert.match(result.failures[0]!.error, /未能确定候选人姓名（卡片 DOM 配对失败）/)
  assert.match(result.failures[0]!.error, /已跳过不入库/)
  assert.match(result.failures[0]!.error, /boss_resume_detail/)
  assert.equal(f.clicks.length, 0) // 2026-09-10 起姓名点=唯一点击点，配不出姓名不点击（P0 反正不入库）
  assert.equal(f.escapes, 0) // 没开过详情，无需关闭
})

test('②b 姓名 OCR 交叉校验不通过（OCR 头部是别人的名字，疑似点开详情与卡片不符）→ failure 不入库', async () => {
  const { reader, f } = makeBatch([ROW1], [{ ocrText: '最 近 关 注 欧 阳 锦 绣 活 跃 24 本 科\n后端 5 年' }])
  const cap = captureStderr()
  let result
  try {
    result = await reader().readBatch({ limit: 1 })
  } finally {
    cap.restore()
  }
  assert.deepEqual(result.resumes, [])
  assert.equal(result.failures.length, 1)
  assert.equal(result.failures[0]!.name, '刘草威')
  assert.match(result.failures[0]!.error, /姓名交叉校验未通过/)
  assert.match(result.failures[0]!.error, /刘草威/)
  assert.match(result.failures[0]!.error, /疑似点开详情与卡片不符/)
  assert.match(result.failures[0]!.error, /已跳过不入库/)
  assert.equal(f.clicks.length, 1)
  assert.equal(f.escapes, 1)
  // 诊断日志带 OCR 元信息（字数/引擎），winrt 低质量文本拖垮校验时可直接从日志定位
  const all = cap.lines.join('')
  assert.match(all, /卡片\[刘草威\] 失败：姓名交叉校验未通过/)
  assert.match(all, /ocr_chars=\d+，引擎=winrt；winrt 识别质量低于 RapidOCR/)
})

test('②c 姓名 OCR 交叉校验容忍 1 字误差（OCR 错字 "刘苇威" vs 卡片名 "刘草威"）→ 正常入库', async () => {
  const row: Row = { name: '刘草威', buttonY: 146 }
  const { reader } = makeBatch([row], [{ ocrText: '最 近 关 注 刘 苇 威 活 跃 24 本 科\nPHP 5 年' }])
  const result = await reader().readBatch({ limit: 1 })
  assert.deepEqual(result.failures, [])
  assert.deepEqual(
    result.resumes.map((r) => r.name),
    ['刘草威'],
  )
})

test('③ 打开超时（点后始终无 canvas）→ failures 记录后继续下一张（下一张成功）', async () => {
  const { reader, f } = makeBatch([ROW1, ROW2], [{ openTimeout: true }, {}])
  const cap = captureStderr()
  let result
  try {
    result = await reader().readBatch({ limit: 2 })
  } finally {
    cap.restore()
  }
  assert.equal(result.resumes.length, 1)
  assert.equal(result.resumes[0]!.name, '张三丰')
  assert.equal(result.failures.length, 1)
  assert.equal(result.failures[0]!.name, '刘草威')
  assert.match(result.failures[0]!.error, /未打开/)
  assert.match(result.failures[0]!.error, /未出现简历画布/)
  assert.equal(result.attempted, 2)
  // 失败卡按 OPEN_CLICK_ATTEMPTS 重点了 3 次 + 下一张 1 次
  assert.equal(f.clicks.length, OPEN_CLICK_ATTEMPTS + 1)
  // 2026-08-18 起失败路径也尝试 Escape 清场（画布判定未命中但详情可能实际开着，
  // 不关会挡住列表导致后续卡片连环点空）：超时卡清场 1 次 + 成功卡关详情 1 次
  assert.equal(f.escapes, 2)
  // 2026-09-10 排障整改：逐卡诊断日志（无 canvas 现场 + 点击序列 + 批量摘要）必须落 stderr
  const all = cap.lines.join('')
  assert.match(all, /\[boss-batch\] 批量开始 limit=2/)
  assert.match(all, /视口 1249x1277，卡片 2 行，按钮 y=\[146,330\]，行距≈184px/)
  assert.match(all, /卡片\[刘草威\] 失败：点击卡片后简历详情未打开/)
  // 点击序列：三次全姓名点（废除绝对像素主体列点）
  assert.match(all, /点击序列 \(342,138\)姓名点→\(342,138\)姓名点→\(342,138\)姓名点/)
  assert.match(all, /页面无 CANVAS 节点/)
  assert.match(all, /卡片\[张三丰\] 已读取（ocr_chars=\d+，引擎=winrt/)
  assert.match(all, /批量结束：尝试 2 张，成功 1 份，失败 1 个/)
})

test('③b 打开失败诊断日志带画布尺寸：接近阈值的大画布提示疑似详情已开（附件简历型/阈值问题定位依据）', async () => {
  const row: Row = { name: '刘草威', buttonY: 146 }
  const { reader } = makeBatch([row], [{ openTimeout: true }], {
    // 列表态下页面有一个 380x290 的画布：宽过阈值但高差 10px 被卡（阈值 300，小屏短画布场景）
    extraCanvases: [[0, 0, 380, 290]],
  })
  const cap = captureStderr()
  let result
  try {
    result = await reader().readBatch({ limit: 1 })
  } finally {
    cap.restore()
  }
  assert.deepEqual(result.resumes, [])
  const all = cap.lines.join('')
  assert.match(all, /页面 CANVAS 尺寸：380x290/)
  assert.match(all, /存在接近阈值的大画布，疑似详情实际已打开/)
})

test('③c 无名行不点击直接记 failure（无姓名点=无可点点位，绝不盲点白读）', async () => {
  const noName: Row = { name: null, buttonY: 146 }
  const { reader, f } = makeBatch([noName], [{ openTimeout: true }])
  const cap = captureStderr()
  let result
  try {
    result = await reader().readBatch({ limit: 1 })
  } finally {
    cap.restore()
  }
  assert.deepEqual(result.resumes, [])
  assert.deepEqual(f.clicks, []) // 无姓名点绝不点击（点了 P0 也不入库，白占真实鼠标滚动）
  assert.equal(result.attempted, 0)
  assert.equal(f.escapes, 0) // 没开过详情，无需关闭
  assert.equal(result.failures.length, 1)
  assert.equal(result.failures[0]!.name, null)
  assert.match(result.failures[0]!.error, /未能确定候选人姓名/)
  assert.match(result.failures[0]!.error, /已跳过不入库/)
  assert.match(cap.lines.join(''), /卡片\[无名卡\] 失败：未能确定候选人姓名/)
})

test('④ 读取抛错（OCR 空白）→ failures 记录 + 详情已关闭（Escape）+ 继续下一张', async () => {
  const { reader, f } = makeBatch([ROW1, ROW2], [{ readError: true }, {}])
  const result = await reader().readBatch({ limit: 2 })
  assert.equal(result.resumes.length, 1)
  assert.equal(result.resumes[0]!.name, '张三丰')
  assert.equal(result.failures.length, 1)
  assert.equal(result.failures[0]!.name, '刘草威')
  assert.match(result.failures[0]!.error, /读取简历失败/)
  assert.match(result.failures[0]!.error, /OCR 未识别到任何文字/)
  assert.equal(f.escapes, 2) // 失败卡关详情 1 次 + 成功卡关详情 1 次
  assert.equal(f.clicks.length, 2)
})

test('⑤ 关闭失败 → 当前份仍入 resumes + failures 带「未关闭」+ 不再处理后续（只点了 1 次）', async () => {
  const { reader, f } = makeBatch([ROW1, ROW2], [{ closeFail: true }])
  const result = await reader().readBatch({ limit: 2 })
  assert.deepEqual(
    result.resumes.map((r) => r.name),
    ['刘草威'],
  ) // 内容有效，仍收进 resumes
  assert.equal(result.failures.length, 1)
  assert.equal(result.failures[0]!.name, '刘草威')
  assert.match(result.failures[0]!.error, /未关闭/)
  assert.equal(result.attempted, 1)
  assert.equal(f.clicks.length, 1) // 不再处理后续卡片
  assert.equal(f.escapes, 1)
  assert.deepEqual(f.progress, []) // 中止路径不推进进度
})

test('⑥ limit 先于卡片数截断（2 张卡 limit 1 → 只处理 1 张）', async () => {
  const { reader, f } = makeBatch([ROW1, ROW2])
  const result = await reader().readBatch({ limit: 1 })
  assert.deepEqual(
    result.resumes.map((r) => r.name),
    ['刘草威'],
  )
  assert.equal(f.clicks.length, 1)
  assert.equal(result.attempted, 1)
  assert.deepEqual(f.progress, [[1, 1]])
})

test('⑦ 无卡片（无打招呼按钮）→ ResumeBatchError（fail-loud）', async () => {
  const { reader, f } = makeBatch([])
  await assert.rejects(reader().readBatch({ limit: 1 }), (e: unknown) => {
    assert.ok(e instanceof ResumeBatchError)
    assert.match(e.message, /未找到任何牛人卡片/)
    return true
  })
  assert.equal(f.clicks.length, 0)
})

test('⑧ signal abort → CancelledError，不触达页面', async () => {
  const { deps, f } = makeBatch([ROW1])
  deps.signal = AbortSignal.abort()
  await assert.rejects(new ResumeBatchReader(deps).readBatch({ limit: 1 }), (e: unknown) => {
    assert.equal((e as Error).name, 'CancelledError')
    return true
  })
  assert.equal(f.clicks.length, 0)
  assert.equal(f.captures, 0)
})

test('⑨ save_dir：每份拼接图按姓名落盘（readResume 收到 saveImageTo 路径）', async () => {
  const saveDir = path.join(os.tmpdir(), `boss-batch-save-${randomUUID()}`)
  await fs.mkdir(saveDir, { recursive: true })
  try {
    const { reader } = makeBatch([ROW1, ROW2])
    const result = await reader().readBatch({ limit: 2, saveDir })
    assert.equal(result.resumes.length, 2)
    assert.equal(existsSync(path.join(saveDir, '刘草威.png')), true)
    assert.equal(existsSync(path.join(saveDir, '张三丰.png')), true)
    // 2026-09-10 起 DOM 配对失败的无名卡不再点击读取（姓名点=唯一点击点），无图无记录
    const noName: Row = { name: null, buttonY: 146 }
    const r2 = makeBatch([noName], [{ ocrText: '王五 活跃\n本科' }])
    const result2 = await r2.reader().readBatch({ limit: 1, saveDir })
    assert.equal(existsSync(path.join(saveDir, '无名.png')), false)
    assert.deepEqual(result2.resumes, [])
    assert.equal(result2.failures.length, 1)
  } finally {
    await fs.rm(saveDir, { recursive: true, force: true }).catch(() => {})
  }
})

// ---------- 起点防残留（boss_resume_detail 读完不关详情的链路） ----------

test('⑩ 残留详情弹层：入口先 Escape 关闭再正常批量（残留简历绝不误记到卡片姓名下）', async () => {
  const { reader, f } = makeBatch([ROW1], [], { initialState: 'canvas' })
  const result = await reader().readBatch({ limit: 1 })
  // 残留弹层被入口关闭：只有姓名点 1 次点击（不是打开轮询误命中残留 canvas 直接读）
  assert.deepEqual(f.clicks, [{ x: 342, y: ROW1.buttonY - 8 }])
  // 2 次 Escape：入口关残留 1 次 + 读完关详情 1 次
  assert.equal(f.escapes, 2)
  assert.deepEqual(result.failures, [])
  assert.deepEqual(
    result.resumes.map((r) => r.name),
    ['刘草威'],
  )
  assert.equal(result.attempted, 1)
})

test('⑪ 残留详情弹层 Escape 关不掉 → ResumeBatchError（fail-loud，不点任何卡片）', async () => {
  const { reader, f } = makeBatch([ROW1], [{ closeFail: true }], { initialState: 'canvas' })
  await assert.rejects(reader().readBatch({ limit: 1 }), (e: unknown) => {
    assert.ok(e instanceof ResumeBatchError)
    assert.match(e.message, /未能关闭/)
    return true
  })
  assert.equal(f.clicks.length, 0) // 绝不带弹层盲点卡片
  assert.equal(f.captures, 0)
  assert.equal(f.escapes, 1) // 只尝试过关残留弹层
})

// ---------- P1 接缝质量元信息 → 云端契约 payload 透传 ----------

test('⑫ buildResumePayload 冒烟：suspect_seams/text_seam_unmatched/ocr_empty_segments/seam_mis/ocr_engine 全量透传', () => {
  const fakeResult: ResumeReadResult = {
    text: '张三的简历全文',
    chars: 7,
    segments: 3,
    bottomReached: false,
    width: 727,
    height: 3200,
    imageBuffer: Buffer.from('png'),
    seamMis: [0.5, 0.08],
    suspectSeams: [1],
    textSeamUnmatched: [2],
    ocrEmptySegments: 1,
    ocrEngine: 'rapid',
    ocrAccel: 'cpu',
  }
  const payload = buildResumePayload('张三', null, fakeResult, 'param')
  assert.deepEqual(payload.seam_mis, [0.5, 0.08])
  assert.deepEqual(payload.suspect_seams, [1])
  assert.deepEqual(payload.text_seam_unmatched, [2])
  assert.equal(payload.ocr_empty_segments, 1)
  assert.equal(payload.bottom_reached, false)
  assert.equal(payload.ocr_chars, 7)
  assert.equal(payload.ocr_engine, 'rapid')
  assert.equal(payload.ocr_accel, 'cpu')
})
