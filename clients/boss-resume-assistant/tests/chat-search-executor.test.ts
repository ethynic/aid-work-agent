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

/** 构造 snapshot：根视口 1249x1277。items(#text) / layout(搜索框 INPUT) / divs(非 INPUT 大容器)。 */
function searchSnap(opts: { items?: SnapItem[]; layout?: [number, number, number, number][]; divs?: [number, number, number, number][] } = {}): DomSnapshot {
  const strings: string[] = ['', 'INPUT', '#text', 'DIV']
  const idx: number[] = [0]
  const values: number[] = [0]
  const names: number[] = [0]
  const bounds: [number, number, number, number][] = [[0, 0, 1249, 1277]]
  for (const item of opts.items ?? []) {
    let si = strings.indexOf(item.text)
    if (si < 0) { strings.push(item.text); si = strings.length - 1 }
    idx.push(idx.length); values.push(si); names.push(2); bounds.push(item.bounds)
  }
  for (const b of opts.layout ?? []) { idx.push(idx.length); values.push(0); names.push(1); bounds.push(b) }
  for (const b of opts.divs ?? []) { idx.push(idx.length); values.push(0); names.push(3); bounds.push(b) }
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

// 真机参考坐标
const SEARCH_ICON = { x: 519, y: 135 }
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
      searchSnap({ items: [{ text: '联系人', bounds: CONTACTS_LABEL }, { text: '_测试公司', bounds: COMPANY }] }), // snap2：联系人标签 + 结果项公司名
      searchSnap({ items: [{ text: '发送', bounds: SEND_BTN }] }), // snap3：进入对话
    ]),
    click: r.click, typeChar: r.typeChar, sleep: r.sleep,
  })
  await executor.openContact({ name: NAME })
  assert.deepEqual(r.clicks, [SEARCH_ICON, { x: 368, y: 141 }, COMPANY_HIT])
  assert.equal(r.typed.join(''), NAME)
})

test('搜索框非唯一（doc0 有 2 个 INPUT）→ ChatSearchError，不点结果', async () => {
  const r = recorder()
  const executor = new ChatSearchExecutor({
    snapshot: snapshotQueue([searchSnap(), searchSnap({ layout: [SEARCH_BOX, [248, 150, 240, 30]] })]),
    click: r.click, typeChar: r.typeChar, sleep: r.sleep,
  })
  await assert.rejects(executor.openContact({ name: NAME }), /2 个候选搜索框/)
  assert.deepEqual(r.clicks, [SEARCH_ICON])
  assert.equal(r.typed.length, 0)
})

test('点击搜索图标后未找到搜索框 → ChatSearchError', async () => {
  const r = recorder()
  const executor = new ChatSearchExecutor({
    snapshot: snapshotQueue([searchSnap(), searchSnap()]),
    click: r.click, typeChar: r.typeChar, sleep: r.sleep,
  })
  await assert.rejects(executor.openContact({ name: NAME }), /未找到搜索框/)
  assert.deepEqual(r.clicks, [SEARCH_ICON])
})

test('搜索后无结果项公司名（搜索无结果）→ ChatSearchError，不点结果', async () => {
  const r = recorder()
  const executor = new ChatSearchExecutor({
    snapshot: snapshotQueue([
      searchSnap(),
      searchSnap({ layout: [SEARCH_BOX] }),
      searchSnap({ items: [{ text: '联系人', bounds: CONTACTS_LABEL }] }), // snap2：只有联系人标签，无公司名
    ]),
    click: r.click, typeChar: r.typeChar, sleep: r.sleep,
  })
  await assert.rejects(executor.openContact({ name: NAME }), /未在结果列表找到该姓名的可见项/)
  assert.equal(r.typed.join(''), NAME)
  assert.deepEqual(r.clicks, [SEARCH_ICON, { x: 368, y: 141 }])
})

test('搜索后无「联系人」标签（浮层未开/异常）→ ChatSearchError', async () => {
  const r = recorder()
  const executor = new ChatSearchExecutor({
    snapshot: snapshotQueue([searchSnap(), searchSnap({ layout: [SEARCH_BOX] }), searchSnap()]),
    click: r.click, typeChar: r.typeChar, sleep: r.sleep,
  })
  await assert.rejects(executor.openContact({ name: NAME }), /未在结果列表找到该姓名的可见项/)
  assert.deepEqual(r.clicks, [SEARCH_ICON, { x: 368, y: 141 }])
})

test('多个结果项公司名（多个匹配）→ ChatSearchError（无法唯一定位）', async () => {
  const r = recorder()
  const executor = new ChatSearchExecutor({
    snapshot: snapshotQueue([
      searchSnap(),
      searchSnap({ layout: [SEARCH_BOX] }),
      // snap2：2 个公司名（2 个结果项）
      searchSnap({ items: [{ text: '联系人', bounds: CONTACTS_LABEL }, { text: '_甲公司', bounds: COMPANY }, { text: '_乙公司', bounds: [304, 242, 200, 16] }] }),
    ]),
    click: r.click, typeChar: r.typeChar, sleep: r.sleep,
  })
  await assert.rejects(executor.openContact({ name: NAME }), /有 2 个可见命中/)
  assert.deepEqual(r.clicks, [SEARCH_ICON, { x: 368, y: 141 }])
})

test('点结果项后未进入对话（无发送按钮）→ ChatSearchError', async () => {
  const r = recorder()
  const executor = new ChatSearchExecutor({
    snapshot: snapshotQueue([
      searchSnap(),
      searchSnap({ layout: [SEARCH_BOX] }),
      searchSnap({ items: [{ text: '联系人', bounds: CONTACTS_LABEL }, { text: '_测试公司', bounds: COMPANY }] }),
      searchSnap(), // snap3：未进入对话，无发送按钮
    ]),
    click: r.click, typeChar: r.typeChar, sleep: r.sleep,
  })
  await assert.rejects(executor.openContact({ name: NAME }), /未进入对话/)
  assert.deepEqual(r.clicks, [SEARCH_ICON, { x: 368, y: 141 }, COMPANY_HIT])
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
  await assert.rejects(executor.openContact({ name: NAME }), /未找到搜索框/)
})

test('findSearchBox 按 nodeName=INPUT 过滤：多个大 DIV 满足几何但只 1 INPUT → 唯一（真机 bug 回归）', async () => {
  const r = recorder()
  const executor = new ChatSearchExecutor({
    snapshot: snapshotQueue([
      searchSnap(),
      searchSnap({ layout: [SEARCH_BOX], divs: [[192, 124, 311, 34], [192, 124, 339, 34], [192, 124, 263, 36]] }),
      searchSnap({ items: [{ text: '联系人', bounds: CONTACTS_LABEL }, { text: '_测试公司', bounds: COMPANY }] }),
      searchSnap({ items: [{ text: '发送', bounds: SEND_BTN }] }),
    ]),
    click: r.click, typeChar: r.typeChar, sleep: r.sleep,
  })
  await executor.openContact({ name: NAME })
  assert.deepEqual(r.clicks, [SEARCH_ICON, { x: 368, y: 141 }, COMPANY_HIT])
  assert.equal(r.typed.join(''), NAME)
})
