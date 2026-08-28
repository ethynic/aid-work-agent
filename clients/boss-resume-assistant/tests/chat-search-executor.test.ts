/**
 * ChatSearchExecutor 单测：搜索找人链路（点搜索图标 → 定位搜索框 → 输入姓名 → 点结果项公司名 → 进入对话）。
 * fake 注入 snapshot/click/clickAndType/clearInput。
 *
 * 搜索框定位范式（2026-08-27 真机诊断后）：点击入口后左栏收缩条**原位变形**为 INPUT——
 * 无"新增"节点可 diff，改为直接识别（tag=INPUT + 形态；多 INPUT 取入口 y 邻近唯一者；
 * 0 INPUT 回退 contenteditable DIV 形态唯一命中）。
 * 输入落地：clickAndType 后校验姓名字符在 strings；未落地时 clearInput 清空重试一次。
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
  clickAndTypes: Array<{ point: ClickPoint; text: string }>
  clearInputCalls: number
  click: (p: ClickPoint) => Promise<void>
  clickAndType: (p: ClickPoint, viewport: { width: number; height: number }, text: string) => Promise<void>
  clearInput: () => Promise<void>
  sleep: (ms: number) => Promise<void>
}

function recorder(): FakeDeps {
  const clicks: ClickPoint[] = []
  const clickAndTypes: Array<{ point: ClickPoint; text: string }> = []
  const state = { clearInputCalls: 0 }
  return {
    clicks,
    clickAndTypes,
    get clearInputCalls() { return state.clearInputCalls },
    click: async (p: ClickPoint) => { clicks.push(p) },
    clickAndType: async (p: ClickPoint, _viewport: { width: number; height: number }, text: string) => { clickAndTypes.push({ point: p, text }) },
    clearInput: async () => { state.clearInputCalls++ },
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
    click: r.click, clickAndType: r.clickAndType, sleep: r.sleep,
  })
  await executor.openContact({ name: NAME })
  assert.deepEqual(r.clicks[0], SEARCH_ENTRY_HIT)
  assert.deepEqual(r.clicks[r.clicks.length - 1], COMPANY_HIT)
  // 搜索框聚焦+姓名输入走同一次 clickAndType（点 (368,141) + 输入姓名）
  assert.equal(r.clickAndTypes.length, 1)
  assert.deepEqual(r.clickAndTypes[0]!.point, { x: 368, y: 141 })
  assert.equal(r.clickAndTypes[0]!.text, NAME)
})

// 2026-08-27 真机诊断（INPUT 原位变形模型）：点搜索入口后，左栏顶部收缩条 DIV @(198,124,339,34)
// 原位变形为 INPUT（位置/尺寸几乎不变）——没有"新增"节点，diff 范式失效，改为直接识别 INPUT。
const MORPH_DIV: [number, number, number, number] = [198, 124, 339, 34] // 点击前的收缩条 DIV
const MORPH_INPUT: [number, number, number, number] = [198, 124, 339, 34] // 点击后原位变形出的 INPUT

test('INPUT 原位变形：点击前是 DIV 收缩条，点击后原位变 INPUT → 直接按 INPUT+形态定位（不做 diff）', async () => {
  const r = recorder()
  const executor = new ChatSearchExecutor({
    snapshot: snapshotQueue([
      searchSnap({ divs: [MORPH_DIV] }), // snap0：点前只有 DIV 收缩条（无 INPUT）
      searchSnap({ layout: [MORPH_INPUT] }), // snap1：原位变形为 INPUT（与 DIV 几乎同位置）
      searchSnap({ items: [{ text: '联系人', bounds: CONTACTS_LABEL }, { text: '_测试公司', bounds: COMPANY }, { text: '张', bounds: [300, 188, 16, 16] }, { text: '三', bounds: [318, 188, 16, 16] }] }),
      searchSnap({ items: [{ text: '发送', bounds: SEND_BTN }] }),
    ]),
    click: r.click, clickAndType: r.clickAndType, sleep: r.sleep,
  })
  await executor.openContact({ name: NAME })
  assert.deepEqual(r.clicks[0], SEARCH_ENTRY_HIT)
  assert.deepEqual(r.clicks[r.clicks.length - 1], COMPANY_HIT)
  assert.equal(r.clickAndTypes.length, 1)
  // INPUT @(198,124,339,34) 中心 (367.5,141)
  assert.deepEqual(r.clickAndTypes[0]!.point, { x: 367.5, y: 141 })
  assert.equal(r.clickAndTypes[0]!.text, NAME)
})

test('INPUT 原位变形失败模式回归：变形后页面同时存在旧收缩 DIV 与新 INPUT → 不误报"2 个候选"（diff 范式两真机失败之一）', async () => {
  const r = recorder()
  const executor = new ChatSearchExecutor({
    snapshot: snapshotQueue([
      searchSnap({ divs: [MORPH_DIV] }),
      // snap1：INPUT 原位出现，包裹容器 DIV（339 宽）仍在——diff 会误判"新增 2 个"，直接识别只认 INPUT
      searchSnap({ layout: [MORPH_INPUT], divs: [MORPH_DIV] }),
      searchSnap({ items: [{ text: '联系人', bounds: CONTACTS_LABEL }, { text: '_测试公司', bounds: COMPANY }, { text: '张', bounds: [300, 188, 16, 16] }, { text: '三', bounds: [318, 188, 16, 16] }] }),
      searchSnap({ items: [{ text: '发送', bounds: SEND_BTN }] }),
    ]),
    click: r.click, clickAndType: r.clickAndType, sleep: r.sleep,
  })
  await executor.openContact({ name: NAME })
  assert.deepEqual(r.clickAndTypes[0]!.point, { x: 367.5, y: 141 })
})

test('多 INPUT：仅一个与搜索入口 y 相邻（|Δy|≤80）→ 取该唯一邻近者（另一个在远处）', async () => {
  const r = recorder()
  const FAR_INPUT: [number, number, number, number] = [248, 385, 240, 30] // cy=400，距入口 y=141 有 259px
  const executor = new ChatSearchExecutor({
    snapshot: snapshotQueue([
      searchSnap(),
      searchSnap({ layout: [SEARCH_BOX, FAR_INPUT] }),
      searchSnap({ items: [{ text: '联系人', bounds: CONTACTS_LABEL }, { text: '_测试公司', bounds: COMPANY }, { text: '张', bounds: [300, 188, 16, 16] }, { text: '三', bounds: [318, 188, 16, 16] }] }),
      searchSnap({ items: [{ text: '发送', bounds: SEND_BTN }] }),
    ]),
    click: r.click, clickAndType: r.clickAndType, sleep: r.sleep,
  })
  await executor.openContact({ name: NAME })
  assert.deepEqual(r.clickAndTypes.length, 1)
  // 邻近带的 SEARCH_BOX（cy=141，Δy=0）胜出，不是远处的 (368,400)
  assert.deepEqual(r.clickAndTypes[0]!.point, { x: 368, y: 141 })
})

test('多 INPUT 且都在入口邻近带内 → 无法唯一 → ChatSearchError（fail-loud）', async () => {
  const r = recorder()
  const executor = new ChatSearchExecutor({
    snapshot: snapshotQueue([searchSnap(), searchSnap({ layout: [SEARCH_BOX, [248, 150, 240, 30]] })]),
    click: r.click, clickAndType: r.clickAndType, sleep: r.sleep,
  })
  await assert.rejects(executor.openContact({ name: NAME }), /2 个候选搜索输入框/)
  assert.ok(r.clicks.length >= 1)
  assert.equal(r.clickAndTypes.length, 0)
})

test('0 INPUT 回退：DIV 输入条形态唯一命中（旧版 contenteditable 页面兼容，按形态识别不校验属性）', async () => {
  const r = recorder()
  const DIV_FIELD: [number, number, number, number] = [248, 126, 240, 30] // contenteditable DIV 搜索框
  const executor = new ChatSearchExecutor({
    snapshot: snapshotQueue([
      searchSnap(),
      searchSnap({ divs: [DIV_FIELD] }), // 无 INPUT，只有 DIV 形态输入条
      searchSnap({ items: [{ text: '联系人', bounds: CONTACTS_LABEL }, { text: '_测试公司', bounds: COMPANY }, { text: '张', bounds: [300, 188, 16, 16] }, { text: '三', bounds: [318, 188, 16, 16] }] }),
      searchSnap({ items: [{ text: '发送', bounds: SEND_BTN }] }),
    ]),
    click: r.click, clickAndType: r.clickAndType, sleep: r.sleep,
  })
  await executor.openContact({ name: NAME })
  assert.deepEqual(r.clickAndTypes.length, 1)
  assert.deepEqual(r.clickAndTypes[0]!.point, { x: 368, y: 141 })
})

test('输入未落地（聚焦竞态）→ clearInput 清空重试一次成功', async () => {
  const r = recorder()
  const executor = new ChatSearchExecutor({
    snapshot: snapshotQueue([
      searchSnap(), // snap0：点图标前
      searchSnap({ layout: [SEARCH_BOX] }), // snap1：搜索框弹出
      // snap2（第一次输入后）：姓名字符未出现（聚焦落空）——只有联系人标签无姓名字符
      searchSnap({ items: [{ text: '联系人', bounds: CONTACTS_LABEL }, { text: '稍', bounds: [300, 188, 16, 16] }, { text: '后', bounds: [318, 188, 16, 16] }] }),
      searchSnap({ layout: [SEARCH_BOX] }), // snap3（清空后重定位）：搜索框仍在
      // snap4（第二次输入后）：姓名 3/3 字符出现 + 联系人标签 + 结果公司名
      searchSnap({ items: [{ text: '联系人', bounds: CONTACTS_LABEL }, { text: '_测试公司', bounds: COMPANY }, { text: '张', bounds: [300, 188, 16, 16] }, { text: '三', bounds: [318, 188, 16, 16] }] }),
      searchSnap({ items: [{ text: '发送', bounds: SEND_BTN }] }),
    ]),
    click: r.click, clickAndType: r.clickAndType, clearInput: r.clearInput, sleep: r.sleep,
  })
  await executor.openContact({ name: NAME })
  assert.equal(r.clearInputCalls, 1, '清空恰好一次')
  assert.equal(r.clickAndTypes.length, 2, '输入恰好两次（首次未落地+重试）')
  assert.deepEqual(r.clickAndTypes[0]!.point, { x: 368, y: 141 })
  assert.deepEqual(r.clickAndTypes[1]!.point, { x: 368, y: 141 })
  assert.equal(r.clickAndTypes[1]!.text, NAME)
  assert.deepEqual(r.clicks[r.clicks.length - 1], COMPANY_HIT)
})

test('输入未落地且重试仍失败 → ChatSearchError（文案注明已重试一次）', async () => {
  const r = recorder()
  const executor = new ChatSearchExecutor({
    snapshot: snapshotQueue([
      searchSnap(),
      searchSnap({ layout: [SEARCH_BOX] }),
      // 第一次输入后：无姓名字符
      searchSnap({ items: [{ text: '稍', bounds: [300, 188, 16, 16] }] }),
      searchSnap({ layout: [SEARCH_BOX] }),
      // 第二次输入后：仍无姓名字符（队列耗尽后重复本快照）
      searchSnap({ items: [{ text: '后', bounds: [318, 188, 16, 16] }] }),
    ]),
    click: r.click, clickAndType: r.clickAndType, clearInput: r.clearInput, sleep: r.sleep,
  })
  await assert.rejects(executor.openContact({ name: NAME }), /已清空重试一次仍失败/)
  assert.equal(r.clearInputCalls, 1)
  assert.equal(r.clickAndTypes.length, 2)
})

test('输入未落地且未注入 clearInput → 直接报错（不清空重打会拼接残留文本）', async () => {
  const r = recorder()
  const executor = new ChatSearchExecutor({
    snapshot: snapshotQueue([
      searchSnap(),
      searchSnap({ layout: [SEARCH_BOX] }),
      searchSnap({ items: [{ text: '稍', bounds: [300, 188, 16, 16] }] }),
    ]),
    click: r.click, clickAndType: r.clickAndType, sleep: r.sleep,
  })
  await assert.rejects(executor.openContact({ name: NAME }), /未落地/)
  assert.equal(r.clearInputCalls, 0)
  assert.equal(r.clickAndTypes.length, 1)
})

test('点击搜索图标后未找到搜索框（无 INPUT 亦无 DIV 形态输入条）→ ChatSearchError', async () => {
  const r = recorder()
  const executor = new ChatSearchExecutor({
    snapshot: snapshotQueue([searchSnap(), searchSnap()]),
    click: r.click, clickAndType: r.clickAndType, sleep: r.sleep,
  })
  await assert.rejects(executor.openContact({ name: NAME }), /未发现搜索输入框/)
  assert.ok(r.clicks.length >= 1, '入口已点击（无搜索框时含重试）')
})

test('点图标弹出的 INPUT 在右列（cx>=850，点错图标开了别的弹层）→ 形态过滤不当搜索框', async () => {
  const r = recorder()
  const RIGHT_POPUP_INPUT: [number, number, number, number] = [1000, 130, 240, 30] // 右列弹层表单输入框
  const executor = new ChatSearchExecutor({
    snapshot: snapshotQueue([searchSnap(), searchSnap({ layout: [RIGHT_POPUP_INPUT] })]),
    click: r.click, clickAndType: r.clickAndType, sleep: r.sleep,
  })
  await assert.rejects(executor.openContact({ name: NAME }), /未发现搜索输入框/)
  assert.ok(r.clicks.length === 1 || r.clicks.length === 2, `入口点击次数应 1 或 2（含重试），实际 ${r.clicks.length}`)
  assert.equal(r.clickAndTypes.length, 0)
})

test('搜索后无结果项公司名（搜索无结果）→ ChatSearchError，不点结果', async () => {
  const r = recorder()
  const executor = new ChatSearchExecutor({
    snapshot: snapshotQueue([
      searchSnap(),
      searchSnap({ layout: [SEARCH_BOX] }),
      searchSnap({ items: [{ text: '联系人', bounds: CONTACTS_LABEL }, { text: '张', bounds: [300, 188, 16, 16] }, { text: '三', bounds: [318, 188, 16, 16] }] }), // snap2：只有联系人标签，无公司名
    ]),
    click: r.click, clickAndType: r.clickAndType, sleep: r.sleep,
  })
  await assert.rejects(executor.openContact({ name: NAME }), /未在结果列表找到该姓名的可见项/)
  assert.equal(r.clickAndTypes.length, 1)
  assert.equal(r.clickAndTypes[0]!.text, NAME)
  assert.deepEqual(r.clicks, [SEARCH_ENTRY_HIT])
})

test('搜索后无「联系人」标签（浮层未开/异常）→ ChatSearchError', async () => {
  const r = recorder()
  const executor = new ChatSearchExecutor({
    snapshot: snapshotQueue([searchSnap(), searchSnap({ layout: [SEARCH_BOX] }), searchSnap({ items: [{ text: '张', bounds: [300, 188, 16, 16] }, { text: '三', bounds: [318, 188, 16, 16] }] })]),
    click: r.click, clickAndType: r.clickAndType, sleep: r.sleep,
  })
  await assert.rejects(executor.openContact({ name: NAME }), /未在结果列表找到该姓名的可见项/)
  assert.deepEqual(r.clicks, [SEARCH_ENTRY_HIT])
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
    click: r.click, clickAndType: r.clickAndType, sleep: r.sleep,
  })
  await assert.rejects(executor.openContact({ name: NAME }), /有 2 个可见命中/)
  assert.deepEqual(r.clicks, [SEARCH_ENTRY_HIT])
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
    click: r.click, clickAndType: r.clickAndType, sleep: r.sleep,
  })
  await assert.rejects(executor.openContact({ name: NAME }), /未进入对话/)
  assert.deepEqual(r.clicks[0], SEARCH_ENTRY_HIT)
  assert.deepEqual(r.clicks[r.clicks.length - 1], COMPANY_HIT)
  assert.equal(r.clickAndTypes.length, 1)
  assert.deepEqual(r.clickAndTypes[0]!.point, { x: 368, y: 141 })
})

test('取消（signal 已 abort）→ CancelledError，不点不输入', async () => {
  const r = recorder()
  const executor = new ChatSearchExecutor({
    snapshot: snapshotQueue([searchSnap()]),
    click: r.click, clickAndType: r.clickAndType, sleep: r.sleep, signal: AbortSignal.abort(),
  })
  await assert.rejects(executor.openContact({ name: NAME }), (e: unknown) => { assert.equal((e as Error).name, 'CancelledError'); return true })
  assert.equal(r.clicks.length, 0)
  assert.equal(r.clickAndTypes.length, 0)
})

test('搜索框在右列（cx>=850）→ 视为未找到搜索框', async () => {
  const r = recorder()
  const executor = new ChatSearchExecutor({
    snapshot: snapshotQueue([searchSnap(), searchSnap({ layout: [[880, 126, 240, 30]] })]),
    click: r.click, clickAndType: r.clickAndType, sleep: r.sleep,
  })
  await assert.rejects(executor.openContact({ name: NAME }), /未发现搜索输入框/)
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
    click: r.click, clickAndType: r.clickAndType, sleep: r.sleep,
  })
  await executor.openContact({ name: NAME })
  assert.deepEqual(r.clicks[0], SEARCH_ENTRY_HIT)
  assert.deepEqual(r.clicks[r.clicks.length - 1], COMPANY_HIT)
  // 搜索框聚焦+姓名输入走同一次 clickAndType（点 (368,141) + 输入姓名）
  assert.equal(r.clickAndTypes.length, 1)
  assert.deepEqual(r.clickAndTypes[0]!.point, { x: 368, y: 141 })
  assert.equal(r.clickAndTypes[0]!.text, NAME)
})

test('顶栏无「批量」锚点且无唯一 svg → 报未找到搜索入口，不点任何东西', async () => {
  const r = recorder()
  const executor = new ChatSearchExecutor({
    snapshot: snapshotQueue([searchSnap({ noAnchor: true }), searchSnap({ noAnchor: true })]),
    click: r.click, clickAndType: r.clickAndType, sleep: r.sleep,
  })
  await assert.rejects(executor.openContact({ name: NAME }), /未找到顶栏搜索入口/)
  assert.deepEqual(r.clicks, [])
  assert.equal(r.clickAndTypes.length, 0)
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
    click: r.click, clickAndType: r.clickAndType, sleep: r.sleep,
  })
  await executor.openContact({ name: NAME })
  // 聚簇取最大者（DIV 34x34）代表，点其中心 (520,141)——与点 svg(519.5,141.5) 等价
  assert.deepEqual(r.clicks[0], { x: 520, y: 141 })
  assert.equal(r.clickAndTypes.length, 1)
  assert.equal(r.clickAndTypes[0]!.text, NAME)
})
