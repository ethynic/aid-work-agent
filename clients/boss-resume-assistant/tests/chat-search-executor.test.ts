/**
 * ChatSearchExecutor 单测：搜索找人链路（点搜索图标 → 输入姓名 → 点结果项公司名 → 进入对话）。
 * fake 注入 snapshot/click/typeChar。
 *
 * 真机关键（2026-08-13）：搜索结果浮层姓名是逐字节点（反爬），但**公司名是连续字符串且 "_" 开头**，
 * 故 locateTargetResult 找「联系人」标签下方的公司名（"_" 开头）点击，不靠姓名匹配。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { ChatSearchExecutor, ChatSearchError } from '../src/main/boss/ChatSearchExecutor.js'
import type { DomSnapshot, ClickPoint } from '../src/main/boss/domSnapshot.js'

interface SnapItem {
  text: string
  bounds: [number, number, number, number]
}

/** 构造 snapshot：根视口 1249x1277。items(#text) / layout(搜索框 INPUT) / divs(非 INPUT 大容器)。
 * 2026-08-24 布局锚点常驻：「批量」#text @(510,171,26,15) + 搜索图标容器 DIV 34x34 @(503,124)
 * （真机嗅探：鼠标悬停换算页面坐标 523,136 即落在此容器上）；noAnchor=true 构造无锚点页面。 */
const BATCH_ANCHOR: SnapItem = { text: '批量', bounds: [510, 171, 26, 15] }
const ICON_DIV: [number, number, number, number] = [503, 124, 34, 34]
const SEARCH_ENTRY_HIT: ClickPoint = { x: 520, y: 141 }

function searchSnap(opts: { items?: SnapItem[]; layout?: [number, number, number, number][]; divs?: [number, number, number, number][]; svgs?: [number, number, number, number][]; noAnchor?: boolean } = {}): DomSnapshot {
  const strings: string[] = ['', 'INPUT', '#text', 'DIV', 'svg']
  const idx: number[] = [0]
  const values: number[] = [0]
  const names: number[] = [0]
  const bounds: [number, number, number, number][] = [[0, 0, 1249, 1277]]
  for (const item of opts.noAnchor ? (opts.items ?? []) : [...(opts.items ?? []), BATCH_ANCHOR]) {
    let si = strings.indexOf(item.text)
    if (si < 0) { strings.push(item.text); si = strings.length - 1 }
    idx.push(idx.length); values.push(si); names.push(2); bounds.push(item.bounds)
  }
  for (const b of opts.layout ?? []) { idx.push(idx.length); values.push(0); names.push(1); bounds.push(b) }
  for (const b of opts.noAnchor ? (opts.divs ?? []) : [...(opts.divs ?? []), ICON_DIV]) { idx.push(idx.length); values.push(0); names.push(3); bounds.push(b) }
  for (const b of opts.svgs ?? []) { idx.push(idx.length); values.push(0); names.push(4); bounds.push(b) }
  return {
    strings,
    documents: [{
      nodes: { nodeValue: { index: idx, value: values }, nodeName: { index: idx, value: names }, contentDocumentIndex: { index: [], value: [] } },
      layout: { nodeIndex: idx, bounds },
      scrollOffsetY: 0,
    }],
  }
}

function snapshotQueue(snaps: DomSnapshot[]): () => Promise<DomSnapshot> {
  let i = 0
  return async () => snaps[Math.min(i++, snaps.length - 1)]!
}

interface FakeDeps {
  clicks: ClickPoint[]
  typed: string[]
  click: (p: ClickPoint) => Promise<void>
  typeChar: (ch: string) => Promise<void>
  sleep: (ms: number) => Promise<void>
}

function recorder(): FakeDeps {
  const clicks: ClickPoint[] = []
  const typed: string[] = []
  return {
    clicks,
    typed,
    click: async (p: ClickPoint) => { clicks.push(p) },
    typeChar: async (ch: string) => { typed.push(ch) },
    sleep: async () => {},
  }
}

// 真机参考坐标（旧固定坐标 519,135 已废弃：另一机器分辨率不同点错；现由批量锚点动态定位）
const SEARCH_BOX: [number, number, number, number] = [248, 126, 240, 30] // 搜索框 INPUT center (368,141)
const CONTACTS_LABEL: [number, number, number, number] = [206, 173, 60, 26] // 「联系人」标签 cy=186
const COMPANY: [number, number, number, number] = [304, 210, 200, 16] // 结果项公司名"_测试公司" center (404,218)
const COMPANY_HIT = { x: 404, y: 218 } // 公司名点击中心（联系人下方）
const SEND_BTN: [number, number, number, number] = [1086, 1215, 120, 36] // 发送按钮 center (1146,1233)
const NAME = '张三'

test('正常找人：点搜索图标 → 搜索框 → 输入姓名 → 点结果项公司名 → 进入对话', async () => {
  const r = recorder()
  const executor = new ChatSearchExecutor({
    snapshot: snapshotQueue([
      searchSnap(), // snap0：点图标前
      searchSnap({ layout: [SEARCH_BOX] }), // snap1：搜索框弹出
      searchSnap({ items: [{ text: '联系人', bounds: CONTACTS_LABEL }, { text: '_测试公司', bounds: COMPANY }, { text: '张', bounds: [300, 188, 16, 16] }, { text: '三', bounds: [318, 188, 16, 16] }] }), // snap2：联系人标签 + 结果项公司名
      searchSnap({ items: [{ text: '发送', bounds: SEND_BTN }] }), // snap3：进入对话
    ]),
    click: r.click, typeChar: r.typeChar, sleep: r.sleep,
  })
  await executor.openContact({ name: NAME })
  assert.deepEqual(r.clicks[0], SEARCH_ENTRY_HIT)
  assert.deepEqual(r.clicks[r.clicks.length - 1], COMPANY_HIT)
  assert.ok(r.clicks.some((c) => c.x === 368 && c.y === 141), '应点击搜索框 (368,141)')
  assert.equal(r.typed.join(''), NAME)
})

test('diff 定位：点前已有左栏 INPUT（收缩态），点后新增一个 → 点新增的（分辨率无关）', async () => {
  const r = recorder()
  const SHRUNK: [number, number, number, number] = [400, 128, 60, 28] // 顶栏收缩态输入框（cx=430<850）
  const executor = new ChatSearchExecutor({
    snapshot: snapshotQueue([
      searchSnap({ layout: [SHRUNK] }), // snap0：点前已有收缩态 INPUT
      searchSnap({ layout: [SHRUNK, SEARCH_BOX] }), // snap1：点后新增展开搜索框
      searchSnap({ items: [{ text: '联系人', bounds: CONTACTS_LABEL }, { text: '_测试公司', bounds: COMPANY }, { text: '张', bounds: [300, 188, 16, 16] }, { text: '三', bounds: [318, 188, 16, 16] }] }),
      searchSnap({ items: [{ text: '发送', bounds: SEND_BTN }] }),
    ]),
    click: r.click, typeChar: r.typeChar, sleep: r.sleep,
  })
  await executor.openContact({ name: NAME })
  // 点击的是新增的搜索框 center (368,141)，不是收缩态那个 (430,142)
  assert.deepEqual(r.clicks[0], SEARCH_ENTRY_HIT)
  assert.deepEqual(r.clicks[r.clicks.length - 1], COMPANY_HIT)
  assert.ok(r.clicks.some((c) => c.x === 368 && c.y === 141), '应点击搜索框 (368,141)')
})

test('diff 定位：点图标弹出的 INPUT 在右列（cx>=850，点错图标开了别的弹层）→ 不当搜索框', async () => {
  const r = recorder()
  const RIGHT_POPUP_INPUT: [number, number, number, number] = [1000, 130, 240, 30] // 右列弹层表单输入框
  const executor = new ChatSearchExecutor({
    snapshot: snapshotQueue([searchSnap(), searchSnap({ layout: [RIGHT_POPUP_INPUT] })]),
    click: r.click, typeChar: r.typeChar, sleep: r.sleep,
  })
  await assert.rejects(executor.openContact({ name: NAME }), /未发现新增的搜索输入条|未找到搜索框/)
  assert.ok(r.clicks.length === 1 || r.clicks.length === 2, `入口点击次数应 1 或 2（含重试），实际 ${r.clicks.length}`)
  assert.equal(r.typed.length, 0)
})

test('搜索框非唯一（doc0 有 2 个 INPUT）→ ChatSearchError，不点结果', async () => {
  const r = recorder()
  const executor = new ChatSearchExecutor({
    snapshot: snapshotQueue([searchSnap(), searchSnap({ layout: [SEARCH_BOX, [248, 150, 240, 30]] })]),
    click: r.click, typeChar: r.typeChar, sleep: r.sleep,
  })
  await assert.rejects(executor.openContact({ name: NAME }), /2 个候选输入条|2 个候选搜索框/)
  assert.deepEqual(r.clicks.length >= 1, true)
  assert.equal(r.typed.length, 0)
})

test('点击搜索图标后未找到搜索框 → ChatSearchError', async () => {
  const r = recorder()
  const executor = new ChatSearchExecutor({
    snapshot: snapshotQueue([searchSnap(), searchSnap()]),
    click: r.click, typeChar: r.typeChar, sleep: r.sleep,
  })
  await assert.rejects(executor.openContact({ name: NAME }), /未发现新增的搜索输入条|未找到搜索框/)
  assert.ok(r.clicks.length >= 1, '入口已点击（无搜索框时含重试）')
})

test('搜索后无结果项公司名（搜索无结果）→ ChatSearchError，不点结果', async () => {
  const r = recorder()
  const executor = new ChatSearchExecutor({
    snapshot: snapshotQueue([
      searchSnap(),
      searchSnap({ layout: [SEARCH_BOX] }),
      searchSnap({ items: [{ text: '联系人', bounds: CONTACTS_LABEL }, { text: '张', bounds: [300, 188, 16, 16] }, { text: '三', bounds: [318, 188, 16, 16] }] }), // snap2：只有联系人标签，无公司名
    ]),
    click: r.click, typeChar: r.typeChar, sleep: r.sleep,
  })
  await assert.rejects(executor.openContact({ name: NAME }), /未在结果列表找到该姓名的可见项/)
  assert.equal(r.typed.join(''), NAME)
  assert.deepEqual(r.clicks, [SEARCH_ENTRY_HIT, { x: 368, y: 141 }])
})

test('搜索后无「联系人」标签（浮层未开/异常）→ ChatSearchError', async () => {
  const r = recorder()
  const executor = new ChatSearchExecutor({
    snapshot: snapshotQueue([searchSnap(), searchSnap({ layout: [SEARCH_BOX] }), searchSnap({ items: [{ text: '张', bounds: [300, 188, 16, 16] }, { text: '三', bounds: [318, 188, 16, 16] }] })]),
    click: r.click, typeChar: r.typeChar, sleep: r.sleep,
  })
  await assert.rejects(executor.openContact({ name: NAME }), /未在结果列表找到该姓名的可见项/)
  assert.deepEqual(r.clicks, [SEARCH_ENTRY_HIT, { x: 368, y: 141 }])
})

test('多个结果项公司名（多个匹配）→ ChatSearchError（无法唯一定位）', async () => {
  const r = recorder()
  const executor = new ChatSearchExecutor({
    snapshot: snapshotQueue([
      searchSnap(),
      searchSnap({ layout: [SEARCH_BOX] }),
      // snap2：2 个公司名（2 个结果项）
      searchSnap({ items: [{ text: '联系人', bounds: CONTACTS_LABEL }, { text: '_甲公司', bounds: COMPANY }, { text: '_乙公司', bounds: [304, 242, 200, 16] }, { text: '张', bounds: [300, 188, 16, 16] }, { text: '三', bounds: [318, 188, 16, 16] }] }),
    ]),
    click: r.click, typeChar: r.typeChar, sleep: r.sleep,
  })
  await assert.rejects(executor.openContact({ name: NAME }), /有 2 个可见命中/)
  assert.deepEqual(r.clicks, [SEARCH_ENTRY_HIT, { x: 368, y: 141 }])
})

test('点结果项后未进入对话（无发送按钮）→ ChatSearchError', async () => {
  const r = recorder()
  const executor = new ChatSearchExecutor({
    snapshot: snapshotQueue([
      searchSnap(),
      searchSnap({ layout: [SEARCH_BOX] }),
      searchSnap({ items: [{ text: '联系人', bounds: CONTACTS_LABEL }, { text: '_测试公司', bounds: COMPANY }, { text: '张', bounds: [300, 188, 16, 16] }, { text: '三', bounds: [318, 188, 16, 16] }] }),
      searchSnap(), // snap3：未进入对话，无发送按钮
    ]),
    click: r.click, typeChar: r.typeChar, sleep: r.sleep,
  })
  await assert.rejects(executor.openContact({ name: NAME }), /未进入对话/)
  assert.deepEqual(r.clicks[0], SEARCH_ENTRY_HIT)
  assert.deepEqual(r.clicks[r.clicks.length - 1], COMPANY_HIT)
  assert.ok(r.clicks.some((c) => c.x === 368 && c.y === 141), '应点击搜索框 (368,141)')
})

test('取消（signal 已 abort）→ CancelledError，不点不输入', async () => {
  const r = recorder()
  const executor = new ChatSearchExecutor({
    snapshot: snapshotQueue([searchSnap()]),
    click: r.click, typeChar: r.typeChar, sleep: r.sleep, signal: AbortSignal.abort(),
  })
  await assert.rejects(executor.openContact({ name: NAME }), (e: unknown) => { assert.equal((e as Error).name, 'CancelledError'); return true })
  assert.equal(r.clicks.length, 0)
  assert.equal(r.typed.length, 0)
})

test('搜索框在右列（cx>=850）→ 视为未找到搜索框', async () => {
  const r = recorder()
  const executor = new ChatSearchExecutor({
    snapshot: snapshotQueue([searchSnap(), searchSnap({ layout: [[880, 126, 240, 30]] })]),
    click: r.click, typeChar: r.typeChar, sleep: r.sleep,
  })
  await assert.rejects(executor.openContact({ name: NAME }), /未发现新增的搜索输入条|未找到搜索框/)
})

test('findSearchBox 按 nodeName=INPUT 过滤：多个大 DIV 满足几何但只 1 INPUT → 唯一（真机 bug 回归）', async () => {
  const r = recorder()
  const executor = new ChatSearchExecutor({
    snapshot: snapshotQueue([
      searchSnap(),
      searchSnap({ layout: [SEARCH_BOX], divs: [[192, 124, 311, 34], [192, 124, 339, 34], [192, 124, 263, 36]] }),
      searchSnap({ items: [{ text: '联系人', bounds: CONTACTS_LABEL }, { text: '_测试公司', bounds: COMPANY }, { text: '张', bounds: [300, 188, 16, 16] }, { text: '三', bounds: [318, 188, 16, 16] }] }),
      searchSnap({ items: [{ text: '发送', bounds: SEND_BTN }] }),
    ]),
    click: r.click, typeChar: r.typeChar, sleep: r.sleep,
  })
  await executor.openContact({ name: NAME })
  assert.deepEqual(r.clicks[0], SEARCH_ENTRY_HIT)
  assert.deepEqual(r.clicks[r.clicks.length - 1], COMPANY_HIT)
  assert.ok(r.clicks.some((c) => c.x === 368 && c.y === 141), '应点击搜索框 (368,141)')
  assert.equal(r.typed.join(''), NAME)
})

test('顶栏无「批量」锚点且无唯一 svg → 报未找到搜索入口，不点任何东西', async () => {
  const r = recorder()
  const executor = new ChatSearchExecutor({
    snapshot: snapshotQueue([searchSnap({ noAnchor: true }), searchSnap({ noAnchor: true })]),
    click: r.click, typeChar: r.typeChar, sleep: r.sleep,
  })
  await assert.rejects(executor.openContact({ name: NAME }), /未找到顶栏搜索入口/)
  assert.deepEqual(r.clicks, [])
  assert.equal(r.typed.length, 0)
})

test('真机嵌套形态：容器 DIV 34x34 + 内部 svg 13x13 双命中 → 聚簇唯一定位点容器中心', async () => {
  const r = recorder()
  const NESTED_SVG: [number, number, number, number] = [513, 135, 13, 13] // 真机嗅探的放大镜 svg
  const executor = new ChatSearchExecutor({
    snapshot: snapshotQueue([
      searchSnap({ svgs: [NESTED_SVG] }), // 默认锚（批量+DIV 容器）+ 嵌套 svg 同时在位
      searchSnap({ layout: [SEARCH_BOX], svgs: [NESTED_SVG] }),
      searchSnap({ items: [{ text: '联系人', bounds: CONTACTS_LABEL }, { text: '_测试公司', bounds: COMPANY }, { text: '张', bounds: [300, 188, 16, 16] }, { text: '三', bounds: [318, 188, 16, 16] }] }),
      searchSnap({ items: [{ text: '发送', bounds: SEND_BTN }] }),
    ]),
    click: r.click, typeChar: r.typeChar, sleep: r.sleep,
  })
  await executor.openContact({ name: NAME })
  // 聚簇取最大者（DIV 34x34）代表，点其中心 (520,141)——与点 svg(519.5,141.5) 等价
  assert.deepEqual(r.clicks[0], { x: 520, y: 141 })
  assert.equal(r.typed.join(''), NAME)
})
