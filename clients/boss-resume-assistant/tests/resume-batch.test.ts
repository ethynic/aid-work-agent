/**
 * ResumeBatchReader 单测：推荐牛人页批量「点卡片 → 读简历 → Escape 关闭 → 下一张」链路。
 * fake 注入 snapshot/clickBrowse/pressEscape/captureFullpage/wheel/stitch/ocr（参照 resume-reader.test.ts）。
 *
 * 快照构造按真机实证（2026-08-17，视口 1249x1277）：卡片行 = 「姓名(342,y-8) + 活跃状态(400,y-8)」
 * 同行 +「打招呼」按钮(1162,y)，行距 184px；点击点 = 卡片主体列 (600, y+70)；详情 canvas 760x1264@(168,40)。
 * 状态机 fake：clickBrowse 把快照切到 canvas 态（openTimeout 卡保持列表态）、pressEscape 切回列表态
 * （closeFail 卡保持 canvas 态），按 scripts 脚本化每张卡的行为。
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
  CARD_CLICK_X,
  CARD_CLICK_DY,
  type BatchCard,
  type ResumeBatchDeps,
  OPEN_CLICK_ATTEMPTS,
} from '../src/main/boss/ResumeBatchReader.js'
import { buildResumePayload } from '../src/main/operations/bossResumeDetail.js'
import type { DeviceRect } from '../src/main/boss/ResumeReader.js'
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
const CANVAS_RECT: DeviceRect = { x: 168, y: 40, w: 760, h: 1264 }
const STITCHED_PNG = Buffer.from('fake-stitched-png-bytes')
const DEFAULT_OCR = '张三 男 26岁 本科\nPHP 开发 5 年\n某科技公司 后端工程师'

/** 构造快照：根视口 1249x1277 + 「筛选」+ 卡片行（姓名/活跃状态/噪音/打招呼按钮）+ 可选大 canvas */
function buildSnap(rows: Row[], opts: { canvas?: boolean } = {}): DomSnapshot {
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
  /** 本卡 OCR 文本（缺省 DEFAULT_OCR） */
  ocrText?: string
}

/** 状态机 fake：click 切 canvas 态、escape 切列表态（脚本可覆盖）。
 * initialState='canvas' 模拟「残留详情弹层」（boss_resume_detail 读完不关详情的链路）。 */
function makeBatch(
  rows: Row[],
  scripts: CardScript[] = [],
  opts: { initialState?: 'list' | 'canvas' } = {},
) {
  const f = {
    clicks: [] as ClickPoint[],
    escapes: 0,
    wheels: [] as Array<{ deltaY: number; notches: number; rect: DeviceRect }>,
    captures: 0,
    stitchCalls: [] as Array<{ parts: string[]; rect: DeviceRect; outFile: string }>,
    ocrTexts: [] as string[],
    progress: [] as Array<[number, number]>,
    snapshotCount: 0,
  }
  let state: 'list' | 'canvas' = opts.initialState ?? 'list'
  let cardIdx = 0 // 当前正在处理的卡片序号（readBatch 逐卡推进；重点同一张卡不推进）
  const scriptAt = (i: number): CardScript => scripts[i] ?? {}
  /** 点击点 y → 卡片行序（buttonY 唯一，CARD_CLICK_DY=70 偏移后仍可反查） */
  const rowOfPoint = (y: number): number => {
    const hit = rows.findIndex((r) => Math.abs(r.buttonY + 70 - y) <= 2)
    return hit >= 0 ? hit : cardIdx
  }
  const deps: ResumeBatchDeps = {
    snapshot: async () => {
      f.snapshotCount++
      return buildSnap(rows, { canvas: state === 'canvas' })
    },
    clickBrowse: async (point) => {
      f.clicks.push(point)
      // 脚本按卡片行对位（不是点击次序——重点机制下同一张卡会点多次）
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
      return Buffer.alloc(1000) // 相邻两次字节相同 → 单段到底
    },
    wheel: async (rect, _viewport, deltaY, notches) => {
      f.wheels.push({ deltaY, notches, rect })
    },
    stitch: async (parts, rect, outFile) => {
      f.stitchCalls.push({ parts, rect, outFile })
      await fs.writeFile(outFile, STITCHED_PNG)
      return { width: 760, height: 2400, overlaps: [746] }
    },
    ocr: async (imgFile) => {
      f.ocrTexts.push(imgFile)
      const s = scriptAt(cardIdx)
      return s.readError ? '   \n\t' : (s.ocrText ?? DEFAULT_OCR)
    },
    onProgress: (done, total) => {
      f.progress.push([done, total])
    },
    sleep: async () => {},
  }
  return { deps, f, reader: () => new ResumeBatchReader(deps) }
}

// ---------- locateCards：姓名锚定与视口过滤 ----------

test('locateCards：姓名锚定（活跃状态左侧最近中文）+ 点击点 (600, 按钮y+70) + 按 y 排序', () => {
  const { deps, f } = makeBatch([ROW1, ROW2])
  const cards = new ResumeBatchReader(deps).locateCards(buildSnap([ROW1, ROW2]))
  assert.deepEqual(
    cards.map((c) => [c.name, c.clickPoint]),
    [
      ['刘草威', { x: CARD_CLICK_X, y: ROW1.buttonY + CARD_CLICK_DY }],
      ['张三丰', { x: CARD_CLICK_X, y: ROW2.buttonY + CARD_CLICK_DY }],
    ] as Array<[string | null, ClickPoint]>,
  )
  assert.equal(f.snapshotCount, 0) // locateCards 是纯函数，不触达 snapshot
})

test('locateCards：无名行 name=null；点击点超视口的卡丢弃（按钮可见但主体下半出屏）', () => {
  const noName: Row = { name: null, buttonY: 146 }
  const edgeRow: Row = { name: '边缘人', buttonY: 1214 } // 按钮 1214 < 1277 可见，点击点 1284 > 1277 出屏
  const cards: BatchCard[] = new ResumeBatchReader(makeBatch([]).deps).locateCards(
    buildSnap([noName, edgeRow]),
  )
  assert.equal(cards.length, 1)
  assert.equal(cards[0]!.name, null)
})

test('locateCards：无打招呼按钮 → 空数组（readBatch 首轮会转 ResumeBatchError）', () => {
  const cards = new ResumeBatchReader(makeBatch([]).deps).locateCards(buildSnap([]))
  assert.deepEqual(cards, [])
})

// ---------- readBatch 主链路 ----------

test('① 正常 2 份（limit 2）：逐卡点击/关闭顺序、resumes 姓名与契约 payload base64', async () => {
  const { reader, f } = makeBatch([ROW1, ROW2])
  const result = await reader().readBatch({ limit: 2 })
  assert.deepEqual(result.failures, [])
  assert.equal(result.attempted, 2)
  // 点击序列 = 卡片主体列 (600, 按钮 y+70)，按 y 从上到下
  assert.deepEqual(f.clicks, [
    { x: 600, y: ROW1.buttonY + CARD_CLICK_DY },
    { x: 600, y: ROW2.buttonY + CARD_CLICK_DY },
  ])
  assert.equal(f.escapes, 2) // 每份读完 Escape 关闭一次
  assert.deepEqual(
    result.resumes.map((r) => r.name),
    ['刘草威', '张三丰'],
  )
  assert.equal(result.resumes[0]!.readResult.text, DEFAULT_OCR)
  assert.equal(result.resumes[0]!.readResult.segments, 1) // 相邻截图字节相同 → 单段到底
  assert.deepEqual(result.resumes[0]!.readResult.imageBuffer, STITCHED_PNG)
  // 滚动点 = canvas 中心（wheel 回调收到的 rect 即详情画布 device 区域）
  assert.ok(f.wheels.length >= 2)
  assert.deepEqual(f.wheels[0]!.rect, CANVAS_RECT)
  // 进度逐份推进
  assert.deepEqual(f.progress, [
    [1, 2],
    [2, 2],
  ])
  // 单份契约 payload：images[0].base64 = fake stitch 落盘的假 PNG
  const payload = buildResumePayload('刘草威', 'PHP开发工程师', result.resumes[0]!.readResult)
  assert.equal(payload.candidate_name, '刘草威')
  assert.equal(payload.job_name, 'PHP开发工程师')
  assert.equal(payload.ocr_text, DEFAULT_OCR)
  const images = payload.images as Array<{ base64: string }>
  assert.equal(images[0]!.base64, STITCHED_PNG.toString('base64'))
})

test('② DOM 姓名配对失败 + OCR 首行启发式成功 → 用启发式姓名（康嘉润）', async () => {
  const noName: Row = { name: null, buttonY: 146 }
  const { reader, f } = makeBatch([noName], [{ ocrText: '康嘉润 活跃\n本科 5 年 后端' }])
  const result = await reader().readBatch({ limit: 1 })
  assert.deepEqual(result.failures, [])
  assert.deepEqual(
    result.resumes.map((r) => r.name),
    ['康嘉润'],
  )
  assert.equal(f.clicks.length, 1)
})

test('③ 打开超时（点后始终无 canvas）→ failures 记录后继续下一张（下一张成功）', async () => {
  const { reader, f } = makeBatch([ROW1, ROW2], [{ openTimeout: true }, {}])
  const result = await reader().readBatch({ limit: 2 })
  assert.equal(result.resumes.length, 1)
  assert.equal(result.resumes[0]!.name, '张三丰')
  assert.equal(result.failures.length, 1)
  assert.equal(result.failures[0]!.name, '刘草威')
  assert.match(result.failures[0]!.error, /未打开/)
  assert.match(result.failures[0]!.error, /未出现简历画布/)
  assert.equal(result.attempted, 2)
  // 失败卡按 OPEN_CLICK_ATTEMPTS 重点了 3 次 + 下一张 1 次
  assert.equal(f.clicks.length, OPEN_CLICK_ATTEMPTS + 1)
  assert.equal(f.escapes, 1) // 超时卡没打开详情，无需 Escape
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
    // DOM 配对失败的无名卡用「无名」占位文件名
    const noName: Row = { name: null, buttonY: 146 }
    const r2 = makeBatch([noName], [{ ocrText: '王五 活跃\n本科' }])
    await r2.reader().readBatch({ limit: 1, saveDir })
    assert.equal(existsSync(path.join(saveDir, '无名.png')), true)
  } finally {
    await fs.rm(saveDir, { recursive: true, force: true }).catch(() => {})
  }
})

// ---------- 起点防残留（boss_resume_detail 读完不关详情的链路） ----------

test('⑩ 残留详情弹层：入口先 Escape 关闭再正常批量（残留简历绝不误记到卡片姓名下）', async () => {
  const { reader, f } = makeBatch([ROW1], [], { initialState: 'canvas' })
  const result = await reader().readBatch({ limit: 1 })
  // 残留弹层被入口关闭：只有卡片主体 1 次点击（不是打开轮询误命中残留 canvas 直接读）
  assert.deepEqual(f.clicks, [{ x: CARD_CLICK_X, y: ROW1.buttonY + CARD_CLICK_DY }])
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
