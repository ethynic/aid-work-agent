import { structuralListSnapshot } from './structuralListFixture.js'
import { createBossResumeBatchOperation } from '../src/main/operations/bossResumeBatch.js'
import { emptyRecommendations } from './recommendationBoundaryFixture.js'
import type { BossSession } from '../src/main/operations/bossContext.js'
import { WinClickError } from '../src/main/input/WinMouseClicker.js'
import { detailSnap } from './detailGreetFixture.js'
/**
 * ResumeBatchReader 单测：推荐牛人页批量「点卡片 → 读简历 → Escape 关闭 → 下一张」链路。
 * fake 注入 snapshot/click/pressEscape/captureFullpage/wheel/sameView/stitch（参照 resume-reader.test.ts）。
 *
 * 快照构造按真机实证（2026-08-17，视口 1249x1277）：卡片行 = 「姓名(342,y-8) + 活跃状态(400,y-8)」
 * 同行 +「打招呼」按钮(1162,y)，行距 184px；点击点 = 配对姓名节点中心（姓名点）；详情 canvas 760x1264@(168,40)。
 * 状态机 fake：click(Win32) 把快照切到 canvas 态（openTimeout 卡保持列表态）、pressEscape 切回列表态
 * （closeFail 卡保持 canvas 态），按 scripts 脚本化每张卡的行为。
 * 2026-09-17 去 OCR 化：客户端只产出拼接长图，姓名与识别文本的交叉校验在云端做——
 * 姓名仍以卡片 DOM 配对为唯一来源，配对失败照旧记 failures。
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
import { withLocalRecovery } from '../src/main/boss/localOperationRecovery.js'
import { CodedOperationError } from '../src/main/operations/types.js'
import { ResumeReader, type DeviceRect, type ResumeReadResult } from '../src/main/boss/ResumeReader.js'
import type { DomSnapshot, ClickPoint } from '../src/main/boss/domSnapshot.js'
import { findButtonsByExactText, pairCardNameWithPoint } from '../src/main/boss/cardName.js'
import { viewportOf, FilterSetError } from '../src/main/boss/FilterSetter.js'

/** 卡片行定义：姓名（null = DOM 配对失败）+ 打招呼按钮中心 y */
interface Row {
  name: string | null
  buttonY: number
  nameDy?: number
}
const ROW1: Row = { name: '刘草威', buttonY: 146 }
const ROW2: Row = { name: '张三丰', buttonY: 330 }

/** 真机：打招呼按钮文本是字符串形态 "\n                  打招呼"（trim 后 === '打招呼'） */
const GREET_STRING = '\n                  打招呼'
const CANVAS_BOUNDS: [number, number, number, number] = [168, 40, 760, 1264] // 真机：详情 iframe canvas
// locateResumeCanvas 与视口求交（2026-09-11 小屏修复）：40+1264=1304 出屏（视口 1277）→ 截到 1237
const CANVAS_RECT: DeviceRect = { x: 168, y: 40, w: 760, h: 1237 }
const STITCHED_PNG = Buffer.from('fake-stitched-png-bytes')

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
  if (opts.canvas) return detailSnap({ name: '当前候选人', state: 'greet', decoyRows: rows, canvasNameOnly: true,
    extraItems: opts.extraCanvases?.map((bounds) => ({ canvas: true, bounds, parent: 0 })) })
  const snap = structuralListSnapshot(rows.map(row => ({ name: row.name,
    button: [1130, row.buttonY - 16, 64, 32],
    nameBounds: [317, row.buttonY - (row.nameDy ?? 8) - 10, 50, 20],
  })), 1249, 1277)
  for (const b of opts.extraCanvases ?? []) {
    const doc = snap.documents[0]!, node = (doc.nodes.nodeValue as number[]).length
    snap.strings.push('CANVAS')
    ;(doc.nodes.nodeValue as number[]).push(0)
    ;(doc.nodes.nodeName as number[]).push(snap.strings.length - 1)
    ;(doc.nodes.parentIndex as number[]).push(0)
    ;(doc.nodes.attributes as number[][]).push([])
    ;(doc.nodes.contentDocumentIndex as number[]).push(-1)
    doc.layout.nodeIndex.push(node); doc.layout.bounds.push(b); doc.layout.clientRects!.push([...b])
  }
  return snap
}

/** 每张卡的行为脚本（按点击次序对位） */
interface CardScript {
  /** 点击后 canvas 永不出现（打开超时） */
  openTimeout?: boolean
  /** 读取阶段抛错（模拟读取管线异常，如拼接失败）→ ResumeReadError */
  readError?: boolean
  /** Escape 后 canvas 不消失（关闭失败） */
  closeFail?: boolean
  /** 首击后该卡片从列表消失（模拟推荐流刷新，重试前找不到原卡片） */
  vanishAfterClick?: boolean
  /** 首击后列表整体替换为给定行（模拟刷新后视口内容变化；配 deps.scroll 模拟滚动出新卡） */
  refreshList?: Row[]
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
    stitchCalls: [] as Array<{ parts: string[]; rect: DeviceRect; outFile: string }>,
    progress: [] as Array<[number, number]>,
    snapshotCount: 0,
    seamMis: [] as number[],
  }
  let state: 'list' | 'canvas' = opts.initialState ?? 'list'
  let cardIdx = 0 // 当前正在处理的卡片序号（readBatch 逐卡推进；重点同一张卡不推进）
  const vanishedRows = new Set<number>() // vanishAfterClick 卡：首击后从快照中移除（列表刷新）
  // 脚本在初始 rows 上按原始对位预绑定到行对象：refreshList 替换 rows 后 index 对位会错位
  // （滚动换回的既有行按对象取原脚本，refreshList 换入的新行默认空脚本=正常卡）
  const rowScript = new Map<Row, CardScript>()
  rows.forEach((row, i) => rowScript.set(row, scripts[i] ?? {}))
  let lastRow: Row | null = null
  const scriptOfRow = (row: Row | null | undefined): CardScript =>
    (row != null ? rowScript.get(row) : undefined) ?? {}
  /** 点击点 y → 卡片行序（buttonY 唯一，姓名点 buttonY-8 可反查） */
  const rowOfPoint = (y: number): number => {
    const hit = rows.findIndex((r) => Math.abs(r.buttonY + 70 - y) <= 2 || Math.abs(r.buttonY - (r.nameDy ?? 8) - y) <= 2)
    return hit >= 0 ? hit : cardIdx
  }
  const deps: ResumeBatchDeps = {
    snapshot: async () => {
      f.snapshotCount++
      return buildSnap(rows.filter((_, i) => !vanishedRows.has(i)), { canvas: state === 'canvas', extraCanvases: opts.extraCanvases })
    },
    click: async (point) => {
      f.clicks.push(point)
      // 脚本按卡片行对位（不是点击次序——重点机制下同一张卡会点多次）
      const idx = rowOfPoint(point.y)
      lastRow = rows[idx] ?? null
      cardIdx = idx
      const s = scriptOfRow(lastRow)
      state = s.openTimeout ? 'list' : 'canvas'
      if (s.vanishAfterClick) vanishedRows.add(idx)
      if (s.refreshList) rows.splice(0, rows.length, ...s.refreshList)
    },
    pressEscape: async () => {
      f.escapes++
      const s = scriptOfRow(lastRow ?? rows[cardIdx] ?? null)
      state = s.closeFail ? 'canvas' : 'list'
    },
    captureFullpage: async () => {
      f.captures++
      if (scriptOfRow(lastRow ?? rows[cardIdx] ?? null).readError) {
        // readError 卡：滚轮回顶后的截图阶段直接抛错（模拟截图/管线失败）
        throw new Error('截图管线异常（fake）')
      }
      return Buffer.alloc(1000) // 相邻两次字节相同 → sameView 确认 → 再滚再截仍相同 → 单段到底
    },
    wheel: async (rect, _viewport, deltaY, notches) => {
      f.wheels.push({ deltaY, notches, rect })
    },
    sameView: async (a, b, rect) => {
      f.sameViewCalls.push({ a, b, rect })
      return true // 恒同画面（配合恒定字节数 → 连续两次相同即到底）
    },
    stitch: async (parts, rect, outFile) => {
      f.stitchCalls.push({ parts, rect, outFile })
      await fs.writeFile(outFile, STITCHED_PNG)
      const seams = Math.max(0, parts.length - 1)
      return {
        width: 760,
        height: 2400,
        overlaps: Array.from({ length: seams }, () => 746),
        seamMis: Array.from({ length: seams }, (_, i) => f.seamMis[i] ?? 0.08),
      }
    },
    onProgress: (done, total) => {
      f.progress.push([done, total])
    },
    sleep: async () => {},
  }
  return { deps, f, reader: () => new ResumeBatchReader(deps) }
}

// ---------- locateCards：姓名锚定与视口过滤 ----------

test('locateCards：原始小数几何与 doctor 配对一致，iframe owner 和滚动偏移不提前取整', () => {
  const snap = buildSnap([{ name: '张三丰', buttonY: 211.5, nameDy: 15.5 }])
  const list = snap.documents[0]!
  // 真实边界：按钮 bounds y=196/h=31，ownerY=80 → 291.5；姓名中心=276。
  list.layout.bounds[list.layout.bounds.length - 2] = [1130, 196, 64, 31]
  list.layout.bounds[list.layout.bounds.length - 1] = [1130, 196, 64, 31]
  snap.documents = [
    { nodes: { nodeValue: [0, 0], contentDocumentIndex: { index: [1], value: [2] } },
      layout: { nodeIndex: [0, 1], bounds: [[0, 0, 1249, 1277], [0, 80, 1249, 1197]] } },
    { nodes: { nodeValue: [], contentDocumentIndex: [] }, layout: { nodeIndex: [], bounds: [] } },
    list,
  ]
  for (const scroll of [0, 100.25]) {
    list.scrollOffsetY = scroll
    const buttons = findButtonsByExactText(snap, '打招呼')
    const paired = pairCardNameWithPoint(snap, buttons[0]!, viewportOf(snap))
    assert.deepEqual(paired, { name: '张三丰', point: { x: 342, y: 276 - scroll } })
    assert.deepEqual(new ResumeBatchReader(makeBatch([]).deps).locateCards(snap), [
      { name: paired.name, namePoint: paired.point, greetButtonY: 291.5 - scroll },
    ])
  }
})

test('同卡片姓名使用原始小数几何，不依赖旧的16像素同行阈值', () => {
  for (const buttonY of [292, 291.5, 291.75]) {
    for (const dy of [15.75, 16, 16.25, -15.75, -16, -16.25]) {
      const snap = buildSnap([{ name: '张三丰', buttonY, nameDy: dy }])
      const button = findButtonsByExactText(snap, '打招呼')[0]!
      const paired = pairCardNameWithPoint(snap, button, viewportOf(snap))
      const expected = { name: '张三丰', point: { x: 342, y: buttonY - dy } }
      assert.deepEqual(paired, expected, `buttonY=${buttonY}, dy=${dy}`)
      const [card] = new ResumeBatchReader(makeBatch([]).deps).locateCards(snap)
      assert.deepEqual(card, { name: expected.name, namePoint: expected.point, greetButtonY: buttonY })
    }
  }
})

test('小数配对跨批累计排除，滚动重叠卡片不重复读取', async () => {
  const rows: Row[] = [{ name: '刘草威', buttonY: 291.5, nameDy: 15.5 },
    { name: '张三丰', buttonY: 475.5, nameDy: 15.5 }]
  const { reader, deps, f } = makeBatch(rows)
  const first = await reader().readBatch({ limit: 1 })
  assert.deepEqual(first.resumes.map((r) => r.name), ['刘草威'])
  let scrolls = 0
  deps.scroll = async () => {
    scrolls++
    rows.splice(0, rows.length, rows[1]!, { name: '王小明', buttonY: 659.5, nameDy: 15.5 })
  }
  const next = await reader().readBatch({ limit: 2, excludeNames: first.processed_names, scanMore: true })
  assert.deepEqual(next.resumes.map((r) => r.name), ['张三丰', '王小明'])
  assert.deepEqual(next.processed_names, ['张三丰', '王小明'])
  assert.deepEqual([...first.failures, ...next.failures], [])
  assert.equal(first.attempted + next.attempted, 3)
  assert.equal(next.stop_reason, 'limit_reached')
  assert.equal(scrolls, 1)
  assert.deepEqual(f.clicks.map((p) => p.y), [276, 460, 644])
})

test('续筛跳过上一批及重叠卡片，滚动后只读取新姓名', async () => {
  const rows = [ROW1, ROW2]
  const { reader, deps, f } = makeBatch(rows)
  let scrolls = 0
  deps.scroll = async () => {
    scrolls++
    rows.splice(0, rows.length, ROW2, { name: '王小明', buttonY: 514 })
  }
  const result = await reader().readBatch({ limit: 2, excludeNames: ['刘草威'], scanMore: true })
  assert.deepEqual(result.resumes.map((r) => r.name), ['张三丰', '王小明'])
  assert.deepEqual(result.processed_names, ['张三丰', '王小明'])
  assert.equal(result.stop_reason, 'limit_reached')
  assert.equal(scrolls, 1)
  assert.deepEqual(f.clicks.map((p) => p.y), [ROW2.buttonY - 8, 514 - 8])
})

test('续筛没有进展：两次延迟确认后停止，不点击已处理者', async () => {
  const { reader, deps, f } = makeBatch([ROW1])
  let scrolls = 0
  deps.scroll = async () => { scrolls++ }
  const result = await reader().readBatch({ limit: 3, excludeNames: ['刘草威'], scanMore: true })
  assert.equal(result.stop_reason, 'no_progress')
  assert.equal(scrolls, 2)
  assert.deepEqual(result.resumes, [])
  assert.deepEqual(result.processed_names, [])
  assert.equal(f.clicks.length, 0)
})

test('续筛 CSS 滚轮使用同节点实测比例，覆盖真实2倍及非整数比例', async () => {
  for (const scale of [1, 1.25, 1.5, 2, 2.5]) {
    const { reader, deps, f } = makeBatch([ROW1])
    const snapshot = deps.snapshot
    deps.snapshot = async () => {
      const snap = await snapshot()
      // 复现本机真实 root HTML 几何；候选人坐标仍用原 bounds，不经过输入单位桥接。
      snap.documents[0]!.layout.bounds[0] = [0, 0, 2504, 1474]
      snap.documents[0]!.layout.clientRects![0] = [0, 0, 2504 / scale, 1474 / scale]
      return snap
    }
    const scrolls: number[][] = []
    deps.scroll = async (x, y, deltaY) => { scrolls.push([x, y, deltaY]) }
    const result = await reader().readBatch({ limit: 1, excludeNames: [ROW1.name!], scanMore: true })
    assert.equal(result.stop_reason, 'no_progress')
    assert.deepEqual(scrolls, Array.from({ length: 2 }, () => [1252 / scale, 737 / scale, 737 / scale]))
    assert.deepEqual(f.clicks, [])
    assert.deepEqual(f.wheels, [])
  }
})

test('续筛第二次确认使用 fresh 几何，不复用旧缩放与窗口尺寸', async () => {
  const { reader, deps } = makeBatch([ROW1])
  const snapshot = deps.snapshot
  const scrolls: number[][] = []
  deps.snapshot = async () => {
    const snap = await snapshot()
    snap.documents[0]!.layout.bounds[0] = scrolls.length ? [0, 0, 1800, 1000] : [0, 0, 2504, 1474]
    snap.documents[0]!.layout.clientRects![0] = scrolls.length ? [0, 0, 1200, 2000 / 3] : [0, 0, 1252, 737]
    return snap
  }
  deps.scroll = async (x, y, deltaY) => { scrolls.push([x, y, deltaY]) }
  const result = await reader().readBatch({ limit: 1, excludeNames: [ROW1.name!], scanMore: true })
  assert.equal(result.stop_reason, 'no_progress')
  assert.deepEqual(scrolls, [[626, 368.5, 368.5], [600, 1000 / 3, 1000 / 3]])
})

test('续筛按根 HTML 节点对齐 layout，不把首项 #document 的空 clientRect 当比例', async () => {
  for (const dense of [false, true]) {
    const { reader, deps } = makeBatch([ROW1])
    const snapshot = deps.snapshot
    deps.snapshot = async () => {
      const snap = await snapshot()
      const root = snap.documents[0]!
      root.layout.bounds[0] = [0, 0, 2504, 1474]
      root.layout.clientRects![0] = [0, 0, 0, 0]
      const htmlNode = 100
      root.nodes.nodeName = dense ? Array.from({ length: 101 }, (_, i) => i === htmlNode ? 1 : 0)
        : { index: [htmlNode], value: [1] }
      root.layout.nodeIndex.push(htmlNode)
      root.layout.bounds.push([0, 0, 2504, 1474])
      root.layout.clientRects!.push([0, 0, 1252, 737])
      return snap
    }
    const scrolls: number[][] = []
    deps.scroll = async (x, y, deltaY) => { scrolls.push([x, y, deltaY]) }
    const result = await reader().readBatch({ limit: 1, excludeNames: [ROW1.name!], scanMore: true })
    assert.equal(result.stop_reason, 'no_progress')
    assert.deepEqual(scrolls, [[626, 368.5, 368.5], [626, 368.5, 368.5]])
    const htmlSnapshot = deps.snapshot
    for (const dimension of [2, 3]) {
      for (const invalid of [NaN, Infinity, undefined, 0, -1]) {
        scrolls.length = 0
        deps.snapshot = async () => {
          const snap = await htmlSnapshot()
          snap.documents[0]!.layout.bounds[0]![dimension] = invalid as number
          return snap
        }
        const invalidResult = await reader().readBatch({ limit: 1, excludeNames: [ROW1.name!], scanMore: true })
        assert.equal(invalidResult.stop_reason, 'page_operation_failed')
        assert.ok(invalidResult.recovery?.summary)
        assert.deepEqual(scrolls, [])
      }
    }
  }
})

test('续筛缺失或冲突 CSS 几何拒绝发滚轮；普通批量读取不要求 DOMRects', async () => {
  const corruptions: Array<(snap: DomSnapshot) => void> = [
    (s) => { delete s.documents[0]!.layout.clientRects },
    (s) => { delete s.documents[0]!.nodes.nodeName },
    (s) => { s.documents[0]!.layout.clientRects![0] = [0, 0, 0, 737] },
    (s) => { s.documents[0]!.layout.clientRects![0] = [0, 0, NaN, 737] },
    (s) => { s.documents[0]!.layout.clientRects![0] = [0, 0, Infinity, 737] },
    (s) => { s.documents[0]!.layout.clientRects![0] = [0, 0, -10, 737] },
    (s) => { s.documents[0]!.layout.clientRects![0] = [10, 0, 1249, 1277] },
    (s) => { s.documents[0]!.layout.clientRects![0] = [0, 0, 1249, 638.5] },
    (s) => { s.documents[0]!.layout.bounds[0]![1] = 12 },
    (s) => { s.documents[0]!.scrollOffsetY = 20 },
    (s) => { s.documents[0]!.nodes.nodeName = [1, 1] },
  ]
  for (const corrupt of corruptions) {
    const { reader, deps, f } = makeBatch([ROW1])
    const snapshot = deps.snapshot
    deps.snapshot = async () => { const snap = await snapshot(); corrupt(snap); return snap }
    let scrolls = 0
    deps.scroll = async () => { scrolls++ }
    const invalidResult = await reader().readBatch({ limit: 1, excludeNames: [ROW1.name!], scanMore: true })
    assert.match(invalidResult.recovery!.summary, /CSS 几何/)
    assert.equal(scrolls, 0)
    assert.deepEqual(f.clicks, [])
  }
  const { reader, deps } = makeBatch([ROW1])
  const snapshot = deps.snapshot
  deps.snapshot = async () => { const snap = await snapshot(); delete snap.documents[0]!.layout.clientRects; return snap }
  const result = await reader().readBatch({ limit: 1 })
  assert.equal(result.resumes.length, 1)
  assert.equal(result.stop_reason, 'limit_reached')
})

test('续筛匿名失败仅当前屏去重；到搜索上限停止', async () => {
  const rows: Row[] = [{ name: null, buttonY: 146 }]
  const { reader, deps } = makeBatch(rows)
  let scrolls = 0
  const snapshot = deps.snapshot
  deps.snapshot = async () => {
    const snap = await snapshot()
    // 实际列表在 iframe 内滚动，主文档仍固定；可见卡片y不变但文档偏移推进。
    snap.documents.push({ nodes: { nodeValue: [], contentDocumentIndex: [] },
      layout: { nodeIndex: [], bounds: [] }, scrollOffsetY: scrolls })
    return snap
  }
  deps.scroll = async () => { scrolls++ }
  const result = await reader().readBatch({ limit: 1, excludeNames: ['已处理'], scanMore: true })
  assert.equal(result.stop_reason, 'search_limit')
  assert.equal(scrolls, 30)
  assert.equal(result.failures.length, 31)
  assert.deepEqual(result.processed_names, [])
})

test('失败尝试姓名也进入排除历史，详情关不掉立即停止', async () => {
  const { reader } = makeBatch([ROW1, ROW2], [{ readError: true, closeFail: true }])
  const result = await reader().readBatch({ limit: 2, excludeNames: [], scanMore: true })
  assert.deepEqual(result.processed_names, ['刘草威'])
  assert.equal(result.stop_reason, 'detail_not_closed')
  assert.equal(result.attempted, 1)
})

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
  // 去 OCR 化：readResult 只含拼接图与截图/拼接元信息（无 text/chars/ocr* 字段）
  const first = result.resumes[0]!.readResult
  assert.equal(first.segments, 1) // 相邻截图字节相同 → sameView 确认到底 → 单段
  assert.deepEqual(first.imageBuffer, STITCHED_PNG)
  assert.deepEqual(first.seamMis, [])
  assert.deepEqual(first.suspectSeams, [])
  assert.equal('text' in first, false)
  assert.equal('ocrEngine' in first, false)
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
  // 单份契约 payload：name_source='dom'（云端观测字段），images[0].base64 = fake stitch 落盘的假 PNG；
  // 无 ocr_* 字段（云端识别后回填）
  const payload = buildResumePayload('刘草威', 'PHP开发工程师', first, 'dom')
  assert.equal(payload.candidate_name, '刘草威')
  assert.equal(payload.name_source, 'dom')
  assert.equal(payload.job_name, 'PHP开发工程师')
  assert.equal('ocr_text' in payload, false)
  assert.equal('ocr_engine' in payload, false)
  const images = payload.images as Array<{ base64: string }>
  assert.equal(images[0]!.base64, STITCHED_PNG.toString('base64'))
  // P1 接缝质量元信息透传（云端契约不读取，已有先例）：单段无接缝 → 空数组
  assert.deepEqual(payload.seam_mis, [])
  assert.deepEqual(payload.suspect_seams, [])
})

test('② DOM 姓名配对失败（无名卡）→ 不点击直接记 failure（无姓名点=无可点点位，绝不盲点白读）', async () => {
  const noName: Row = { name: null, buttonY: 146 }
  const { reader, f } = makeBatch([noName])
  const result = await reader().readBatch({ limit: 1 })
  assert.deepEqual(result.resumes, []) // 不入 resumes
  assert.equal(result.failures.length, 1)
  assert.equal(result.failures[0]!.name, null)
  assert.match(result.failures[0]!.error, /未能确定候选人姓名（卡片 DOM 配对失败）/)
  assert.match(result.failures[0]!.error, /已跳过不入库/)
  assert.match(result.failures[0]!.error, /boss_resume_detail/)
  assert.equal(f.clicks.length, 0) // 2026-09-10 起姓名点=唯一点击点，配不出姓名不点击（反正不入库）
  assert.equal(f.escapes, 0) // 没开过详情，无需关闭
})

test('③ 打开失败但详情可关闭（列表仍可操作）→ 记失败后继续下一人，不进恢复', async () => {
  const { reader, f } = makeBatch([ROW1, ROW2], [{ openTimeout: true }, {}])
  const cap = captureStderr()
  let result
  try { result = await reader().readBatch({ limit: 2 }) } finally { cap.restore() }
  // 第一人（刘草威）打不开只记单份失败；第二人（张三丰）正常读取入库
  assert.equal(result.resumes.length, 1)
  assert.equal(result.resumes[0]!.name, '张三丰')
  assert.equal(result.failures.length, 1)
  assert.equal(result.failures[0]!.name, '刘草威')
  assert.match(result.failures[0]!.error, /点击卡片后简历详情未打开/)
  assert.equal(result.attempted, 2, '两人都尝试了')
  assert.equal(f.clicks.length, 3, '刘草威两次（重点重试）+ 张三丰一次')
  assert.equal(f.escapes, 1, '未开详情不发无关 Escape；张三丰正常关闭一次')
  assert.equal(result.stop_reason, 'viewport_complete', '正常走完视口而非提前停止')
  assert.equal(result.recovery, undefined, '列表仍可用时不交共用恢复')
  assert.deepEqual(f.progress, [[1, 2]])
  assert.match(cap.lines.join(''), /页面无 CANVAS 节点/)
  // 「关不掉才停止」分支与读取失败路径（readError+closeFail 用例）同构：detail_not_closed + 恢复
})

test('③d 重试前原卡片从列表消失（列表仍可用）→ 记失败继续下一人，不终止批次', async () => {
  const { reader, f } = makeBatch([ROW1, ROW2], [{ openTimeout: true, vanishAfterClick: true }, {}])
  const result = await reader().readBatch({ limit: 2 })
  // 甲首击未打开且重试前卡片消失（列表刷新）：只记单份失败；乙正常读取
  assert.equal(result.resumes.length, 1)
  assert.equal(result.resumes[0]!.name, '张三丰')
  assert.equal(result.failures.length, 1)
  assert.equal(result.failures[0]!.name, '刘草威')
  assert.match(result.failures[0]!.error, /原候选人卡片已从列表消失/)
  assert.equal(result.attempted, 2)
  assert.equal(f.clicks.length, 2, '甲仅首击一次（卡片消失后不重击旧坐标）+ 乙一次')
  assert.equal(f.escapes, 1, '乙正常关闭一次')
  assert.equal(result.stop_reason, 'viewport_complete', '正常走完视口而非 UI_CHANGED 终止')
  assert.equal(result.recovery, undefined)
  assert.deepEqual(f.progress, [[1, 2]])
})

test('③e 重试前原卡片消失且当前视口为空 → 回主循环滚动继续处理，不终止批次', async () => {
  const rows = [ROW1, ROW2]
  const { reader, deps, f } = makeBatch(rows, [{ openTimeout: true, refreshList: [] }, {}])
  let scrolls = 0
  deps.scroll = async () => {
    scrolls++
    if (scrolls === 1) rows.splice(0, rows.length, ROW2) // 滚动后乙回到视口
  }
  const result = await reader().readBatch({ limit: 1, scanMore: true })
  // 甲首击未打开且列表刷新后视口为空：记单份失败回主循环，经滚动找到乙继续
  assert.equal(result.failures.length, 1)
  assert.equal(result.failures[0]!.name, '刘草威')
  assert.match(result.failures[0]!.error, /原候选人卡片已从列表消失/)
  assert.equal(result.resumes.length, 1)
  assert.equal(result.resumes[0]!.name, '张三丰')
  assert.equal(result.attempted, 2)
  assert.equal(scrolls, 1, '视口为空时通过滚动寻找后续候选人，而非抛错终止')
  assert.equal(result.stop_reason, 'limit_reached', '乙读取成功达到上限正常结束')
  assert.equal(result.recovery, undefined)
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
  assert.deepEqual(f.clicks, []) // 无姓名点绝不点击（点了也不入库，白占真实鼠标滚动）
  assert.equal(result.attempted, 0)
  assert.equal(f.escapes, 0) // 没开过详情，无需关闭
  assert.equal(result.failures.length, 1)
  assert.equal(result.failures[0]!.name, null)
  assert.match(result.failures[0]!.error, /未能确定候选人姓名/)
  assert.match(result.failures[0]!.error, /已跳过不入库/)
  assert.match(cap.lines.join(''), /卡片\[无名卡\] 失败：未能确定候选人姓名/)
})

test('④ 读取抛错 → failures 记录 + 详情已关闭（Escape）+ 继续下一张', async () => {
  const { reader, f } = makeBatch([ROW1, ROW2], [{ readError: true }, {}])
  const result = await reader().readBatch({ limit: 2 })
  assert.equal(result.resumes.length, 1)
  assert.equal(result.resumes[0]!.name, '张三丰')
  assert.equal(result.failures.length, 1)
  assert.equal(result.failures[0]!.name, '刘草威')
  assert.match(result.failures[0]!.error, /读取简历失败/)
  assert.match(result.failures[0]!.error, /截图管线异常/)
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
  assert.equal(f.escapes, 2)
  assert.equal(result.recovery!.execution.ordinaryRetriesUsed, 1)
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

test('⑦ 无卡片 → 结构化页面错误供共用恢复，不声明已经读完', async () => {
  const { reader, f } = makeBatch([])
  const result = await reader().readBatch({ limit: 1 })
  assert.equal(result.stop_reason, 'page_operation_failed')
  assert.match(result.recovery!.summary, /未找到任何牛人卡片/)
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
    const r2 = makeBatch([noName])
    const result2 = await r2.reader().readBatch({ limit: 1, saveDir })
    assert.equal(existsSync(path.join(saveDir, '无名.png')), false)
    assert.deepEqual(result2.resumes, [])
    assert.equal(result2.failures.length, 1)
  } finally {
    await fs.rm(saveDir, { recursive: true, force: true }).catch(() => {})
  }
})

// ---------- 起点防残留（boss_resume_detail 读完不关详情的链路） ----------

test('累计历史到1000即停止，余下卡片不读取', async () => {
  const { reader, f } = makeBatch([ROW1, ROW2])
  const result = await reader().readBatch({ limit: 3, scanMore: true,
    excludeNames: Array.from({ length: 999 }, (_, index) => `已处理${index}`) })
  assert.equal(result.stop_reason, 'history_limit')
  assert.deepEqual(result.resumes.map((r) => r.name), [ROW1.name])
  assert.equal(result.processed_names.length, 1)
  assert.equal(f.clicks.length, 1)
})

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

test('⑪ 残留详情正常Escape两次仍未关闭 → 结构化错误，不点背景卡片', async () => {
  const { reader, f } = makeBatch([ROW1, ROW2], [{ closeFail: true }], { initialState: 'canvas' })
  const result = await reader().readBatch({ limit: 1 })
  assert.match(result.recovery!.summary, /未能关闭/)
  assert.equal(result.recovery!.execution.ordinaryRetriesUsed, 1)
  assert.equal(f.clicks.length, 0)
  assert.equal(f.escapes, 2)
})

// ---------- P1 接缝质量元信息 → 云端契约 payload 透传 ----------

test('⑫ buildResumePayload 冒烟：suspect_seams/seam_mis/bottom_reached 透传，无 ocr_* 字段', () => {
  const fakeResult: ResumeReadResult = {
    segments: 3,
    bottomReached: false,
    width: 727,
    height: 3200,
    imageBuffer: Buffer.from('png'),
    seamMis: [0.5, 0.08],
    suspectSeams: [1],
  }
  const payload = buildResumePayload('张三', null, fakeResult, 'param')
  assert.deepEqual(payload.seam_mis, [0.5, 0.08])
  assert.deepEqual(payload.suspect_seams, [1])
  assert.equal(payload.bottom_reached, false)
  assert.equal(payload.segments, 3)
  assert.equal(payload.image_width, 727)
  assert.equal(payload.image_height, 3200)
  // 去 OCR 化：文本字段全部由云端识别回填，客户端 payload 不产出
  for (const gone of ['ocr_text', 'ocr_chars', 'ocr_engine', 'ocr_accel', 'ocr_empty_segments', 'text_seam_unmatched']) {
    assert.equal(gone in payload, false, `payload 不应再含 ${gone}`)
  }
})


test('收尾输入直接异常仍保留已读取图像，不能整批重做', async () => {
  const { reader, deps, f } = makeBatch([ROW1, ROW2])
  deps.pressEscape = async () => { throw new WinClickError('[BOSS_INPUT_UNKNOWN] 输入结果未知') }
  const result = await reader().readBatch({ limit: 2 })
  assert.equal(result.resumes.length, 1)
  assert.equal(result.resumes[0]!.name, ROW1.name)
  assert.equal(result.resumes[0]!.readResult.imageBuffer.equals(STITCHED_PNG), true)
  assert.equal(result.stop_reason, 'page_operation_failed')
  assert.equal(result.recovery!.execution.ordinaryRetriesUsed, 0)
  assert.equal(result.recovery!.execution.inputStatus, 'unknown')
  assert.equal(f.clicks.length, 1)
})

test('打开输入直接异常不重击同卡，保留失败现场供共享视觉恢复', async () => {
  const { reader, deps } = makeBatch([ROW1, ROW2])
  let clicks = 0
  deps.click = async () => { clicks++; throw new WinClickError('落点被浏览器弹窗阻挡', 2) }
  const result = await reader().readBatch({ limit: 2 })
  assert.equal(clicks, 1)
  assert.deepEqual(result.resumes, [])
  assert.equal(result.recovery!.execution.ordinaryRetriesUsed, 0)
  assert.equal(result.recovery!.execution.verification.status, 'inconclusive')
})


test('批次成功facts透传实际click/Escape输入，不写死not_started', async () => {
  const { deps, f } = makeBatch([ROW1])
  const original = ResumeReader.prototype.readResume
  ResumeReader.prototype.readResume = async () => ({ segments: 1, bottomReached: true, width: 760, height: 2400,
    imageBuffer: STITCHED_PNG, seamMis: [], suspectSeams: [] })
  const session: BossSession = {
    snapshot: deps.snapshot, click: deps.click, pressEscape: deps.pressEscape,
    clickBrowse: async () => { assert.fail('不应使用其他点击通道') }, clickAndType: async () => { assert.fail('不应键入') },
    mouseWheel: async () => {}, captureFullpage: deps.captureFullpage,
    getUrl: async () => 'https://www.zhipin.com/web/chat/recommend', close: async () => {},
  }
  try {
    const result = await createBossResumeBatchOperation(async () => session).execute({ limit: 1 }, { signal: new AbortController().signal, progress() {} })
    assert.equal(result.success, true)
    const facts = result.data.operation_recovery as any
    assert.equal(facts.execution.input_status, 'attempted')
    assert.equal(facts.execution.attempts_used, 1)
    assert.equal(facts.execution.ordinary_retries_used, 0)
    assert.equal(facts.execution.stage, 'postcheck')
    assert.equal(facts.verification.status, 'satisfied')
    assert.equal(f.clicks.length, 1)
    assert.equal(f.escapes, 1)
  } finally { ResumeReader.prototype.readResume = original }
})

test('明确空匹配成功facts保持not_started，不因成功验证伪造输入', async () => {
  let snapshots = 0
  const noInput = async () => { assert.fail('明确空结果不应发送任何输入') }
  const session: BossSession = {
    snapshot: async () => ++snapshots === 1 ? buildSnap([]) : emptyRecommendations(),
    click: noInput, clickBrowse: noInput, pressEscape: noInput, clickAndType: noInput, mouseWheel: noInput,
    captureFullpage: async () => { assert.fail('空结果不采图') },
    getUrl: async () => 'https://www.zhipin.com/web/chat/recommend', close: async () => {},
  }
  const result = await createBossResumeBatchOperation(async () => session).execute({ limit: 1 }, { signal: new AbortController().signal, progress() {} })
  assert.equal(result.success, true)
  const facts = result.data.operation_recovery as any
  assert.equal(facts.execution.input_status, 'not_started')
  assert.equal(facts.execution.attempts_used, 1)
  assert.equal(facts.execution.ordinary_retries_used, 0)
  assert.equal(facts.verification.status, 'satisfied')
  assert.equal(result.data.stop_reason, 'no_matching_candidates')
})


test('成功终态保留当前项收尾的一次重试，不用默认0覆盖', async () => {
  const { deps, reader } = makeBatch([ROW1])
  const close = deps.pressEscape
  let presses = 0
  deps.pressEscape = async () => { if (++presses === 2) await close() }
  const executor = reader()
  const result = await executor.readBatch({ limit: 1 })
  assert.equal(result.resumes.length, 1)
  assert.equal(result.recovery, undefined)
  assert.equal(executor.batchExecution.inputStatus, 'attempted')
  assert.equal(executor.batchExecution.ordinaryRetriesUsed, 1)
  assert.equal(executor.batchExecution.attemptsUsed, 2)
  const copy = executor.batchExecution
  copy.inputStatus = 'not_started'
  assert.equal(executor.batchExecution.inputStatus, 'attempted', '回执是副本，调用方不能改执行事实')
})


test('CDP close timeout retains the first image and checkpoint recovery never reads or charges that candidate again', async () => {
  const batch = makeBatch([ROW1, ROW2])
  batch.deps.checkpoint = {}
  const close = batch.deps.pressEscape
  let injected = false
  batch.deps.pressEscape = async () => {
    if (!injected) { injected = true; throw new Error('CDP request timed out: Input.dispatchMouseEvent') }
    await close()
  }
  const first = await batch.reader().readBatch({ limit: 2 })
  assert.equal(first.resumes.length, 1)
  const saved = first.resumes[0]
  const second = await batch.reader().readBatch({ limit: 2 })
  assert.equal(second.resumes.length, 2)
  assert.equal(second.resumes[0], saved)
  assert.equal(batch.f.stitchCalls.length, 2, 'completed candidate must not be captured a second time')
  assert.equal(second.stop_reason, 'limit_reached')
})

test('隐藏widget零输入拒绝不将未读取卡片永久排除，并保留同批领域续读', async () => {
  const batch = makeBatch([ROW1])
  batch.deps.checkpoint = {}
  const click = batch.deps.click
  let refused = true
  batch.deps.click = async (point, viewport) => {
    if (refused) { refused = false; throw new WinClickError('STALE_OBSERVATION: render widget hidden; input refused', 2) }
    await click(point, viewport)
  }
  const first = await batch.reader().readBatch({ limit: 1 })
  assert.equal(first.resumes.length, 0)
  assert.deepEqual(first.processed_names, [], '尚未读取不能进入后端续筛排除名单')
  assert.equal(first.recovery?.execution.inputStatus, 'not_started')
  const second = await batch.reader().readBatch({ limit: 1 })
  assert.equal(second.resumes.length, 1)
  assert.equal(second.attempted, 1, '同一未完成卡片不是新的候选人尝试')
  assert.equal(second.failures.length, 0)
})


for (const code of ['NOT_LOGGED_IN', 'UI_CHANGED', 'CANCELLED'] as const) {
  test('retained batch survives a pre-body recovery dependency: ' + code, async () => {
    const { deps } = makeBatch([ROW1, ROW2])
    const originalRead = ResumeReader.prototype.readResume
    ResumeReader.prototype.readResume = async () => ({ segments: 1, bottomReached: true, width: 760, height: 2400,
      imageBuffer: STITCHED_PNG, seamMis: [], suspectSeams: [] })
    const controller = new AbortController()
    let connections = 0
    const session: BossSession = {
      snapshot: deps.snapshot, click: deps.click,
      pressEscape: async () => { throw new Error('CDP request timed out: Input.dispatchMouseEvent') },
      clickBrowse: async () => {}, clickAndType: async () => {}, mouseWheel: async () => {},
      captureFullpage: deps.captureFullpage, getUrl: async () => 'https://www.zhipin.com/web/chat/recommend', close: async () => {},
    }
    try {
      const operation = createBossResumeBatchOperation(async () => {
        if (++connections === 1) return session
        if (code === 'CANCELLED') controller.abort()
        throw new CodedOperationError(code, 'fixture dependency before Reader')
      })
      const result = await withLocalRecovery(operation, { env: {}, sleep: async () => {} }).execute({ limit: 2 },
        { signal: controller.signal, progress() {} })
      assert.equal(connections, 2)
      assert.equal((result.data.resumes as unknown[]).length, 1, 'final receipt must carry the already captured image')
      assert.equal((result.data.cleanup_error as any).code, code)
      assert.equal(result.data.stop_reason, code === 'CANCELLED' ? 'cancelled' : 'page_operation_failed')
    } finally { ResumeReader.prototype.readResume = originalRead }
  })
}
