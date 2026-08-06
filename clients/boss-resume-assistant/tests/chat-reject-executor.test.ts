import assert from 'node:assert/strict'
import test from 'node:test'
import { ChatRejectExecutor, ChatRejectError } from '../src/main/boss/ChatRejectExecutor.js'
import type { DomSnapshot, ClickPoint } from '../src/main/boss/domSnapshot.js'

/** 构造 snapshot：根节点视口 1917x1905，items 为任意文本及其 bounds（相同文案共享 string 下标，与真实 DOMSnapshot 一致） */
function chatSnap(items: Array<{ text: string; bounds: [number, number, number, number] }>): DomSnapshot {
  const strings: string[] = ['']
  const idx: number[] = [0]
  const values: number[] = [0]
  const bounds: [number, number, number, number][] = [[0, 0, 1917, 1905]]
  for (const item of items) {
    let si = strings.indexOf(item.text)
    if (si < 0) {
      strings.push(item.text)
      si = strings.length - 1
    }
    idx.push(idx.length)
    values.push(si)
    bounds.push(item.bounds)
  }
  return {
    strings,
    documents: [
      {
        nodes: {
          nodeValue: { index: idx, value: values },
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

function recorder() {
  const clicks: ClickPoint[] = []
  return {
    clicks,
    click: async (p: ClickPoint) => {
      clicks.push(p)
    },
    sleep: async () => {},
  }
}

// 真机参考坐标：右侧面板底部「不合适」（cx=1740 > 850），确认弹层「确定」居中
const REJECT_BTN: [number, number, number, number] = [1700, 1800, 80, 36] // 中心 (1740, 1818)
const CONFIRM_BTN: [number, number, number, number] = [980, 1000, 88, 36] // 中心 (1024, 1018)

test('直接标记成功：点「不合适」后按钮消失，只点 1 次', async () => {
  const r = recorder()
  const executor = new ChatRejectExecutor({
    snapshot: snapshotQueue([
      chatSnap([{ text: '不合适', bounds: REJECT_BTN }]), // 定位
      chatSnap([]), // 点完校验：按钮消失
    ]),
    click: r.click,
    sleep: r.sleep,
  })
  await executor.rejectCurrent()
  assert.deepEqual(r.clicks, [{ x: 1740, y: 1818 }])
})

test('确认弹层：点「不合适」后弹出「确定」，再点确定后按钮消失', async () => {
  const r = recorder()
  const executor = new ChatRejectExecutor({
    snapshot: snapshotQueue([
      chatSnap([{ text: '不合适', bounds: REJECT_BTN }]), // 定位
      chatSnap([
        { text: '不合适', bounds: REJECT_BTN },
        { text: '确定', bounds: CONFIRM_BTN },
      ]), // 点完：按钮仍在 + 确认弹层
      chatSnap([]), // 点确定后：按钮消失
    ]),
    click: r.click,
    sleep: r.sleep,
  })
  await executor.rejectCurrent()
  assert.deepEqual(r.clicks, [
    { x: 1740, y: 1818 },
    { x: 1024, y: 1018 },
  ])
})

test('真机回归：同一文案多个 string 下标，可见节点挂在第 2 个上也要命中', async () => {
  // 真机 2026-08-06：「不合适」在 strings 表有 2 个条目，可见布局节点挂在第 2 个下标；
  // 用 findIndex 只取第 1 个会误判「没有按钮」
  const dupSnap: DomSnapshot = {
    strings: ['', '不合适', '不合适'],
    documents: [
      {
        nodes: {
          nodeValue: { index: [0, 1, 2], value: [0, 1, 2] },
          contentDocumentIndex: { index: [], value: [] },
        },
        layout: {
          nodeIndex: [0, 1, 2],
          bounds: [[0, 0, 1917, 1905], [0, 0, 0, 0], REJECT_BTN], // 节点1零尺寸不可见，节点2才是真按钮
        },
        scrollOffsetY: 0,
      },
    ],
  }
  const r = recorder()
  const executor = new ChatRejectExecutor({
    snapshot: snapshotQueue([dupSnap, chatSnap([])]),
    click: r.click,
    sleep: r.sleep,
  })
  await executor.rejectCurrent()
  assert.deepEqual(r.clicks, [{ x: 1740, y: 1818 }])
})

test('真机回归：标记成功后 BOSS 自动切到下一个会话（新会话也有「不合适」），按姓名变化判成功', async () => {
  // 真机 2026-08-06：点「不合适」成功后会话自动切换，按钮仍在（是下一个人的），只查按钮会误报失败
  const HEADER: [number, number, number, number] = [880, 190, 60, 26] // 中心 (910, 203)，头部识别带内
  const r = recorder()
  const executor = new ChatRejectExecutor({
    snapshot: snapshotQueue([
      chatSnap([
        { text: '不合适', bounds: REJECT_BTN },
        { text: '张三', bounds: HEADER },
      ]), // 定位：当前会话张三
      chatSnap([
        { text: '不合适', bounds: REJECT_BTN },
        { text: '李四', bounds: HEADER },
      ]), // 点完：切到李四（按钮是李四的）
    ]),
    click: r.click,
    sleep: r.sleep,
  })
  await executor.rejectCurrent()
  assert.deepEqual(r.clicks, [{ x: 1740, y: 1818 }])
})

test('点完按钮仍在且姓名未变、无确认弹层：报错（点击可能被拦截）', async () => {
  const HEADER: [number, number, number, number] = [880, 190, 60, 26]
  const r = recorder()
  const executor = new ChatRejectExecutor({
    snapshot: snapshotQueue([
      chatSnap([
        { text: '不合适', bounds: REJECT_BTN },
        { text: '张三', bounds: HEADER },
      ]),
      chatSnap([
        { text: '不合适', bounds: REJECT_BTN },
        { text: '张三', bounds: HEADER },
      ]), // 还是张三：点击没生效
    ]),
    click: r.click,
    sleep: r.sleep,
  })
  await assert.rejects(executor.rejectCurrent(), /会话未切换/)
  assert.equal(r.clicks.length, 1)
})

test('右侧面板没有「不合适」（未打开会话/已标记）：报错且不点', async () => {
  const r = recorder()
  const executor = new ChatRejectExecutor({
    snapshot: snapshotQueue([chatSnap([])]),
    click: r.click,
    sleep: r.sleep,
  })
  await assert.rejects(executor.rejectCurrent(), (e: Error) => {
    assert.ok(e instanceof ChatRejectError)
    assert.match(e.message, /没有「不合适」按钮/)
    return true
  })
  assert.equal(r.clicks.length, 0)
})

test('左列会话列表里的「不合适」不算数（cx<850 被排除）', async () => {
  const r = recorder()
  const executor = new ChatRejectExecutor({
    snapshot: snapshotQueue([chatSnap([{ text: '不合适', bounds: [300, 500, 80, 36] }])]), // cx=340 在左列
    click: r.click,
    sleep: r.sleep,
  })
  await assert.rejects(executor.rejectCurrent(), /没有「不合适」按钮/)
  assert.equal(r.clicks.length, 0)
})

test('右侧面板多个「不合适」：歧义报错且不点', async () => {
  const r = recorder()
  const executor = new ChatRejectExecutor({
    snapshot: snapshotQueue([
      chatSnap([
        { text: '不合适', bounds: REJECT_BTN },
        { text: '不合适', bounds: [1700, 900, 80, 36] },
      ]),
    ]),
    click: r.click,
    sleep: r.sleep,
  })
  await assert.rejects(executor.rejectCurrent(), /2 个「不合适」按钮/)
  assert.equal(r.clicks.length, 0)
})

test('点完按钮仍在且无确认弹层：报错（点击可能被拦截）', async () => {
  const r = recorder()
  const executor = new ChatRejectExecutor({
    snapshot: snapshotQueue([
      chatSnap([{ text: '不合适', bounds: REJECT_BTN }]),
      chatSnap([{ text: '不合适', bounds: REJECT_BTN }]), // 点完仍在，无「确定」
    ]),
    click: r.click,
    sleep: r.sleep,
  })
  await assert.rejects(executor.rejectCurrent(), /未找到确认弹层/)
  assert.equal(r.clicks.length, 1) // 只点了不合适，没有盲点其它
})

test('弹出原因选择层：报错请人工选择，绝不乱选原因', async () => {
  const r = recorder()
  const executor = new ChatRejectExecutor({
    snapshot: snapshotQueue([
      chatSnap([{ text: '不合适', bounds: REJECT_BTN }]),
      chatSnap([
        { text: '不合适', bounds: REJECT_BTN },
        { text: '经验不匹配', bounds: [900, 800, 120, 36] },
        { text: '学历不匹配', bounds: [900, 850, 120, 36] },
        { text: '确定', bounds: CONFIRM_BTN },
      ]),
    ]),
    click: r.click,
    sleep: r.sleep,
  })
  await assert.rejects(executor.rejectCurrent(), /原因选择层/)
  assert.equal(r.clicks.length, 1) // 不点确定、不选原因
})

test('确认弹层「确定」歧义（多个可见确定）：报错不盲点', async () => {
  const r = recorder()
  const executor = new ChatRejectExecutor({
    snapshot: snapshotQueue([
      chatSnap([{ text: '不合适', bounds: REJECT_BTN }]),
      chatSnap([
        { text: '不合适', bounds: REJECT_BTN },
        { text: '确定', bounds: CONFIRM_BTN },
        { text: '确定', bounds: [700, 1000, 88, 36] },
      ]),
    ]),
    click: r.click,
    sleep: r.sleep,
  })
  await assert.rejects(executor.rejectCurrent(), /未找到确认弹层/)
  assert.equal(r.clicks.length, 1)
})

test('点完确定后「不合适」仍存在：报错结果无法确认', async () => {
  const r = recorder()
  const executor = new ChatRejectExecutor({
    snapshot: snapshotQueue([
      chatSnap([{ text: '不合适', bounds: REJECT_BTN }]),
      chatSnap([
        { text: '不合适', bounds: REJECT_BTN },
        { text: '确定', bounds: CONFIRM_BTN },
      ]),
      chatSnap([{ text: '不合适', bounds: REJECT_BTN }]), // 确定点完仍在
    ]),
    click: r.click,
    sleep: r.sleep,
  })
  await assert.rejects(executor.rejectCurrent(), /结果无法确认/)
  assert.equal(r.clicks.length, 2)
})
