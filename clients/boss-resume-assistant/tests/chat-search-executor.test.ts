/**
 * ChatSearchExecutor 单测：搜索找人链路（点搜索图标 → 输入姓名 → 点结果卡片 → 进入对话）。
 * fake 注入 snapshot/click/typeChar（与 greet/chat-reject 测试同范式）。
 *
 * 搜索框是 doc0 的 layout-only 节点（无文本，nodeValue 指向 ''），
 * findSearchBox 靠几何（cx<850/y100-200/w>200）定位，故快照需支持无文本的 layout 节点。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { ChatSearchExecutor, ChatSearchError } from '../src/main/boss/ChatSearchExecutor.js'
import type { DomSnapshot, ClickPoint } from '../src/main/boss/domSnapshot.js'

interface SnapItem {
  text: string
  bounds: [number, number, number, number]
}

/**
 * 构造 snapshot：根节点视口 1249x1277（真机窗口）。
 * items 为带文本的布局节点（nodeName '#text'）；layout 为搜索框 INPUT（无文本）；divs 为非 INPUT 的
 * 大容器节点（nodeName 'DIV'，模拟真机顶部行满足几何但非搜索框的容器，用于 findSearchBox 的 INPUT 过滤回归）。
 */
function searchSnap(opts: { items?: SnapItem[]; layout?: [number, number, number, number][]; divs?: [number, number, number, number][] } = {}): DomSnapshot {
  // strings: [0]=''(根/空文本), [1]='INPUT', [2]='#text', [3]='DIV'
  const strings: string[] = ['', 'INPUT', '#text', 'DIV']
  const idx: number[] = [0]            // 节点 0 = 根视口
  const values: number[] = [0]         // nodeValue: 根 → ''
  const names: number[] = [0]          // nodeName: 根 → ''(0) 非 INPUT
  const bounds: [number, number, number, number][] = [[0, 0, 1249, 1277]]
  for (const item of opts.items ?? []) {
    let si = strings.indexOf(item.text)
    if (si < 0) {
      strings.push(item.text)
      si = strings.length - 1
    }
    idx.push(idx.length)
    values.push(si)
    names.push(2) // '#text'
    bounds.push(item.bounds)
  }
  for (const b of opts.layout ?? []) {
    // 搜索框 INPUT：空文本、nodeName=INPUT，参与 findSearchBox 定位
    idx.push(idx.length)
    values.push(0)
    names.push(1) // 'INPUT'
    bounds.push(b)
  }
  for (const b of opts.divs ?? []) {
    // 非 INPUT 大容器（DIV）：空文本、nodeName=DIV，满足几何但不应被 findSearchBox 选中
    idx.push(idx.length)
    values.push(0)
    names.push(3) // 'DIV'
    bounds.push(b)
  }
  return {
    strings,
    documents: [
      {
        nodes: {
          nodeValue: { index: idx, value: values },
          nodeName: { index: idx, value: names },
          contentDocumentIndex: { index: [], value: [] },
        },
        layout: { nodeIndex: idx, bounds },
        scrollOffsetY: 0,
      },
    ],
  }
}

/** 依调用序返回 snapshot 队列，用完后重复最后一个 */
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
    click: async (p: ClickPoint) => {
      clicks.push(p)
    },
    typeChar: async (ch: string) => {
      typed.push(ch)
    },
    sleep: async () => {},
  }
}

// 真机参考坐标
const SEARCH_ICON = { x: 519, y: 135 } // 搜索图标（GetCursorPos 校准，固定常量）
const SEARCH_BOX: [number, number, number, number] = [248, 126, 240, 30] // 搜索框：center (368,141)
const CONTACTS_LABEL: [number, number, number, number] = [206, 173, 60, 26] // 「联系人」标签：center (236,186)
const SEND_BTN: [number, number, number, number] = [1086, 1215, 120, 36] // 发送按钮：center (1146,1233)，cx>850
const NAME = '张三'

test('正常找人：点搜索图标 → 点搜索框 → 逐字输入姓名 → 点结果卡片 → 进入对话（发送按钮出现）', async () => {
  const r = recorder()
  const executor = new ChatSearchExecutor({
    snapshot: snapshotQueue([
      searchSnap(), // snap0：点图标前（取视口）
      searchSnap({ layout: [SEARCH_BOX] }), // snap1：搜索框弹出（唯一）
      searchSnap({ items: [{ text: '联系人', bounds: CONTACTS_LABEL }] }), // snap2：结果「联系人」标签
      searchSnap({ items: [{ text: '发送', bounds: SEND_BTN }] }), // snap3：进入对话，发送按钮出现
    ]),
    click: r.click,
    typeChar: r.typeChar,
    sleep: r.sleep,
  })
  await executor.openContact({ name: NAME })
  // 3 次点击：搜索图标(519,135) → 搜索框(368,141) → 结果卡片(287, 186+24=210)
  assert.deepEqual(r.clicks, [SEARCH_ICON, { x: 368, y: 141 }, { x: 287, y: 210 }])
  assert.equal(r.typed.join(''), NAME)
})

test('搜索框非唯一（doc0 有 2 个 cx<850/y100-200/w>200 节点）→ ChatSearchError，不点结果卡片', async () => {
  const r = recorder()
  const executor = new ChatSearchExecutor({
    snapshot: snapshotQueue([
      searchSnap(), // snap0
      searchSnap({ layout: [SEARCH_BOX, [248, 150, 240, 30]] }), // snap1：两个候选搜索框
    ]),
    click: r.click,
    typeChar: r.typeChar,
    sleep: r.sleep,
  })
  await assert.rejects(executor.openContact({ name: NAME }), (e: unknown) => {
    assert.ok(e instanceof ChatSearchError)
    assert.match((e as Error).message, /2 个候选搜索框/)
    return true
  })
  // 只点了搜索图标，没点搜索框/结果卡片
  assert.deepEqual(r.clicks, [SEARCH_ICON])
  assert.equal(r.typed.length, 0)
})

test('点击搜索图标后未找到搜索框（弹层未打开）→ ChatSearchError', async () => {
  const r = recorder()
  const executor = new ChatSearchExecutor({
    snapshot: snapshotQueue([
      searchSnap(), // snap0
      searchSnap(), // snap1：无搜索框节点
    ]),
    click: r.click,
    typeChar: r.typeChar,
    sleep: r.sleep,
  })
  await assert.rejects(executor.openContact({ name: NAME }), /未找到搜索框/)
  assert.deepEqual(r.clicks, [SEARCH_ICON])
})

test('输入姓名后未找到「联系人」分类标签（搜索无结果）→ ChatSearchError，不点结果卡片', async () => {
  const r = recorder()
  const executor = new ChatSearchExecutor({
    snapshot: snapshotQueue([
      searchSnap(), // snap0
      searchSnap({ layout: [SEARCH_BOX] }), // snap1
      searchSnap(), // snap2：无「联系人」标签
    ]),
    click: r.click,
    typeChar: r.typeChar,
    sleep: r.sleep,
  })
  await assert.rejects(executor.openContact({ name: NAME }), /未找到搜索结果「联系人」分类标签/)
  assert.equal(r.typed.join(''), NAME)
  // 点了搜索图标 + 搜索框，没点结果卡片
  assert.deepEqual(r.clicks, [SEARCH_ICON, { x: 368, y: 141 }])
})

test('点结果卡片后未进入对话（无发送按钮，搜索结果可能不存在）→ ChatSearchError', async () => {
  const r = recorder()
  const executor = new ChatSearchExecutor({
    snapshot: snapshotQueue([
      searchSnap(), // snap0
      searchSnap({ layout: [SEARCH_BOX] }), // snap1
      searchSnap({ items: [{ text: '联系人', bounds: CONTACTS_LABEL }] }), // snap2
      searchSnap(), // snap3：未进入对话，无发送按钮
    ]),
    click: r.click,
    typeChar: r.typeChar,
    sleep: r.sleep,
  })
  await assert.rejects(executor.openContact({ name: NAME }), (e: unknown) => {
    assert.ok(e instanceof ChatSearchError)
    assert.match((e as Error).message, /未进入对话/)
    return true
  })
  // 点了搜索图标 + 搜索框 + 结果卡片（3 次），但校验进入对话失败
  assert.deepEqual(r.clicks, [SEARCH_ICON, { x: 368, y: 141 }, { x: 287, y: 210 }])
})

test('取消（signal 已 abort）→ CancelledError，不点不输入', async () => {
  const r = recorder()
  const executor = new ChatSearchExecutor({
    snapshot: snapshotQueue([searchSnap()]),
    click: r.click,
    typeChar: r.typeChar,
    sleep: r.sleep,
    signal: AbortSignal.abort(),
  })
  await assert.rejects(executor.openContact({ name: NAME }), (e: unknown) => {
    assert.equal((e as Error).name, 'CancelledError')
    return true
  })
  assert.equal(r.clicks.length, 0)
  assert.equal(r.typed.length, 0)
})

test('搜索框在右列（cx>=850）不算数 → 视为未找到搜索框', async () => {
  const r = recorder()
  const executor = new ChatSearchExecutor({
    snapshot: snapshotQueue([
      searchSnap(),
      // cx=1000>=850，被排除 → 0 个有效搜索框
      searchSnap({ layout: [[880, 126, 240, 30]] }),
    ]),
    click: r.click,
    typeChar: r.typeChar,
    sleep: r.sleep,
  })
  await assert.rejects(executor.openContact({ name: NAME }), /未找到搜索框/)
})

test('findSearchBox 按 nodeName=INPUT 过滤：多个大 DIV 容器满足几何但只 1 个 INPUT → 唯一定位（真机 bug 回归 2026-08-13）', async () => {
  // 真机：点击搜索图标后 doc0 顶部行有多个大 DIV 容器（311x34/339x34/263x36 等）也满足
  // cx<850/y100-200/w>200，旧版 findSearchBox 只看几何会误判 8 个候选；必须按 nodeName=INPUT 过滤。
  const r = recorder()
  const executor = new ChatSearchExecutor({
    snapshot: snapshotQueue([
      searchSnap(),
      // 搜索框 INPUT(唯一) + 多个 DIV 容器(非 INPUT，都满足几何条件)
      searchSnap({
        layout: [SEARCH_BOX],
        divs: [
          [192, 124, 311, 34], // center≈(347,141) cx<850 y141 w311>200，但是 DIV
          [192, 124, 339, 34], // center≈(361,141)，DIV
          [192, 124, 263, 36], // center≈(323,142)，DIV
        ],
      }),
      searchSnap({ items: [{ text: '联系人', bounds: CONTACTS_LABEL }] }),
      searchSnap({ items: [{ text: '发送', bounds: SEND_BTN }] }),
    ]),
    click: r.click,
    typeChar: r.typeChar,
    sleep: r.sleep,
  })
  await executor.openContact({ name: NAME })
  // DIV 容器被 nodeName=INPUT 过滤排除，搜索框唯一 → 正常点击 (368,141)
  assert.deepEqual(r.clicks, [SEARCH_ICON, { x: 368, y: 141 }, { x: 287, y: 210 }])
  assert.equal(r.typed.join(''), NAME)
})
