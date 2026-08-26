/**
 * ChatSendExecutor 单测：发消息链路（定位发送按钮 → 激活输入框+逐字输入 → dry-run/真发送）。
 * fake 注入 snapshot/click/clickAndType（与 greet/chat-reject 测试同范式）。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { ChatSendExecutor, ChatSendError, locateSendButton } from '../src/main/boss/ChatSendExecutor.js'
import type { DomSnapshot, ClickPoint } from '../src/main/boss/domSnapshot.js'

/** 构造 snapshot：根节点视口 1249x1277（真机窗口），items 为文本及其 bounds（同文案共享 string 下标） */
function chatSnap(items: Array<{ text: string; bounds: [number, number, number, number] }>, strings?: string[]): DomSnapshot {
  const strTab: string[] = strings ?? ['']
  const idx: number[] = [0]
  const values: number[] = [0]
  const bounds: [number, number, number, number][] = [[0, 0, 1249, 1277]]
  for (const item of items) {
    let si = strTab.indexOf(item.text)
    if (si < 0) {
      strTab.push(item.text)
      si = strTab.length - 1
    }
    idx.push(idx.length)
    values.push(si)
    bounds.push(item.bounds)
  }
  return {
    strings: strTab,
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

interface FakeDeps {
  clicks: ClickPoint[]
  clickAndTypes: Array<{ point: ClickPoint; text: string }>
  click: (p: ClickPoint) => Promise<void>
  clickAndType: (p: ClickPoint, viewport: { width: number; height: number }, text: string) => Promise<void>
  sleep: (ms: number) => Promise<void>
}

function recorder(): FakeDeps {
  const clicks: ClickPoint[] = []
  const clickAndTypes: Array<{ point: ClickPoint; text: string }> = []
  return {
    clicks,
    clickAndTypes,
    click: async (p: ClickPoint) => {
      clicks.push(p)
    },
    clickAndType: async (p: ClickPoint, _viewport: { width: number; height: number }, text: string) => {
      clickAndTypes.push({ point: p, text })
    },
    sleep: async () => {},
  }
}

// 真机参考坐标：发送按钮 center (1146,1233) → bounds=[1086,1215,120,36]；激活点 = center+(-130,-38)=(1016,1195)
const SEND_BTN: [number, number, number, number] = [1086, 1215, 120, 36]
const MSG = '你好，我是 HR'

test('dry-run：定位发送按钮 → 点击激活点 → 逐字输入 → 校验输入落地，不点发送', async () => {
  const r = recorder()
  const executor = new ChatSendExecutor({
    snapshot: snapshotQueue([
      chatSnap([{ text: '发送', bounds: SEND_BTN }]), // 定位发送按钮
      chatSnap([{ text: '发送', bounds: SEND_BTN }, { text: MSG, bounds: [200, 1180, 800, 30] }]), // dry-run 校验：strings 含 message
    ]),
    click: r.click,
    clickAndType: r.clickAndType,
    sleep: r.sleep,
  })
  const result = await executor.sendMessage({ message: MSG, dryRun: true })
  assert.equal(result.sent, false)
  // 激活点点击+输入走同一次 clickAndType（Win32 原子），没点发送按钮
  assert.equal(r.clicks.length, 0)
  assert.equal(r.clickAndTypes.length, 1)
  assert.deepEqual(r.clickAndTypes[0]!.point, { x: 1016, y: 1195 })
  assert.equal(r.clickAndTypes[0]!.text, MSG)
})

test('真发送：输入后点发送按钮，发送后输入框清空（strings 不含 message）→ sent=true', async () => {
  const r = recorder()
  const executor = new ChatSendExecutor({
    snapshot: snapshotQueue([
      chatSnap([{ text: '发送', bounds: SEND_BTN }]), // 定位发送按钮
      chatSnap([{ text: '发送', bounds: SEND_BTN }]), // 真发送前重新定位
      chatSnap([{ text: '发送', bounds: SEND_BTN }]), // 发送后校验：strings 不含 message（已清空）
    ]),
    click: r.click,
    clickAndType: r.clickAndType,
    sleep: r.sleep,
  })
  const result = await executor.sendMessage({ message: MSG })
  assert.equal(result.sent, true)
  // clickAndType 1 次（激活点+输入）+ click 1 次（发送按钮）
  assert.equal(r.clickAndTypes.length, 1)
  assert.deepEqual(r.clickAndTypes[0]!.point, { x: 1016, y: 1195 })
  assert.equal(r.clickAndTypes[0]!.text, MSG)
  assert.deepEqual(r.clicks, [{ x: 1146, y: 1233 }])
})

test('发送校验失败（发送后输入框未清空，strings 仍含 message）→ ChatSendError（含无法确认 → EXECUTION_UNKNOWN）', async () => {
  const r = recorder()
  const executor = new ChatSendExecutor({
    snapshot: snapshotQueue([
      chatSnap([{ text: '发送', bounds: SEND_BTN }]), // 定位发送按钮
      chatSnap([{ text: '发送', bounds: SEND_BTN }]), // 真发送前重新定位
      chatSnap([{ text: '发送', bounds: SEND_BTN }, { text: MSG, bounds: [200, 1180, 800, 30] }]), // 发送后仍含 message
    ]),
    click: r.click,
    clickAndType: r.clickAndType,
    sleep: r.sleep,
  })
  await assert.rejects(executor.sendMessage({ message: MSG }), (e: unknown) => {
    assert.ok(e instanceof ChatSendError)
    assert.match((e as Error).message, /无法确认/)
    return true
  })
  // 已完成激活输入（clickAndType）+ 点了发送按钮（发送动作已发出，但结果未知）
  assert.equal(r.clickAndTypes.length, 1)
  assert.equal(r.clicks.length, 1)
})

test('无发送按钮（cx>850 视口内 0 个）→ ChatSendError，不点不输入', async () => {
  const r = recorder()
  const executor = new ChatSendExecutor({
    snapshot: snapshotQueue([chatSnap([])]),
    click: r.click,
    clickAndType: r.clickAndType,
    sleep: r.sleep,
  })
  await assert.rejects(executor.sendMessage({ message: MSG }), (e: unknown) => {
    assert.ok(e instanceof ChatSendError)
    assert.match((e as Error).message, /0 个/)
    return true
  })
  assert.equal(r.clicks.length, 0)
  assert.equal(r.clickAndTypes.length, 0)
})

test('左列的「发送」不算数（cx<=850 被排除）→ 视为无发送按钮', async () => {
  const r = recorder()
  const executor = new ChatSendExecutor({
    // cx=400 在左列区域（<=850），被排除 → 0 个有效发送按钮
    snapshot: snapshotQueue([chatSnap([{ text: '发送', bounds: [340, 1100, 120, 36] }])]),
    click: r.click,
    clickAndType: r.clickAndType,
    sleep: r.sleep,
  })
  await assert.rejects(executor.sendMessage({ message: MSG }), /0 个/)
  assert.equal(r.clicks.length, 0)
})

test('多个发送按钮（cx>850）→ 歧义 ChatSendError，不点不输入', async () => {
  const r = recorder()
  const executor = new ChatSendExecutor({
    snapshot: snapshotQueue([
      chatSnap([
        { text: '发送', bounds: SEND_BTN },
        { text: '发送', bounds: [1086, 600, 120, 36] }, // 第二个也在右侧
      ]),
    ]),
    click: r.click,
    clickAndType: r.clickAndType,
    sleep: r.sleep,
  })
  await assert.rejects(executor.sendMessage({ message: MSG }), (e: unknown) => {
    assert.ok(e instanceof ChatSendError)
    assert.match((e as Error).message, /2 个/)
    return true
  })
  assert.equal(r.clicks.length, 0)
})

test('真发送前发送按钮消失（页面切换/弹层）→ ChatSendError，消息未发送', async () => {
  const r = recorder()
  const executor = new ChatSendExecutor({
    snapshot: snapshotQueue([
      chatSnap([{ text: '发送', bounds: SEND_BTN }]), // 定位发送按钮（输入激活点）
      chatSnap([]), // 真发送前重新定位：发送按钮消失
    ]),
    click: r.click,
    clickAndType: r.clickAndType,
    sleep: r.sleep,
  })
  await assert.rejects(executor.sendMessage({ message: MSG }), /发送按钮消失/)
  // 激活输入已完成（clickAndType），未点发送
  assert.equal(r.clickAndTypes.length, 1)
  assert.equal(r.clicks.length, 0)
})

test('dry-run 输入校验失败（strings 不含 message）→ ChatSendError', async () => {
  const r = recorder()
  const executor = new ChatSendExecutor({
    snapshot: snapshotQueue([
      chatSnap([{ text: '发送', bounds: SEND_BTN }]), // 定位发送按钮
      chatSnap([{ text: '发送', bounds: SEND_BTN }]), // dry-run 校验：strings 不含 message（输入未落地）
    ]),
    click: r.click,
    clickAndType: r.clickAndType,
    sleep: r.sleep,
  })
  await assert.rejects(executor.sendMessage({ message: MSG, dryRun: true }), /输入未落地/)
  assert.equal(r.clickAndTypes.length, 1)
  assert.equal(r.clicks.length, 0)
})

test('取消（signal 已 abort）→ CancelledError，不点不输入', async () => {
  const r = recorder()
  const executor = new ChatSendExecutor({
    snapshot: snapshotQueue([chatSnap([{ text: '发送', bounds: SEND_BTN }])]),
    click: r.click,
    clickAndType: r.clickAndType,
    sleep: r.sleep,
    signal: AbortSignal.abort(),
  })
  await assert.rejects(executor.sendMessage({ message: MSG }), (e: unknown) => {
    assert.equal((e as Error).name, 'CancelledError')
    return true
  })
  assert.equal(r.clicks.length, 0)
})

test('locateSendButton 导出函数：同文案多 string 下标也全部命中', () => {
  // 真机 §17 坑：同一文案在 strings 表可能有多个条目
  const snap = chatSnap([{ text: '发送', bounds: SEND_BTN }])
  // 额外塞一个重复下标的「发送」string（无布局节点）确保不误计
  snap.strings.push('发送')
  const { point, count } = locateSendButton(snap)
  assert.equal(count, 1)
  assert.deepEqual(point, { x: 1146, y: 1233 })
})
