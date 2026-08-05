import assert from 'node:assert/strict'
import test from 'node:test'
import { GreetExecutor, GreetError } from '../src/main/boss/GreetExecutor.js'
import type { DomSnapshot, ClickPoint } from '../src/main/boss/domSnapshot.js'

/** 构造 snapshot：node 0 根（视口 1917x1905），buttons 为「打招呼」按钮 bounds；offsetY 为列表文档滚动偏移；extraTexts 为附加文本（付费墙弹层等） */
function greetSnap(buttons: Array<[number, number, number, number]>, offsetY = 0, extraTexts: string[] = []): DomSnapshot {
  const idx = [0, ...buttons.map((_, i) => i + 1), ...extraTexts.map((_, i) => buttons.length + 1 + i)]
  return {
    // 相同文案共享一个 string table 下标（与真实 DOMSnapshot 一致）
    strings: ['', '打招呼', ...extraTexts],
    documents: [
      {
        nodes: {
          nodeValue: { index: idx, value: [0, ...buttons.map(() => 1), ...extraTexts.map((_, i) => 2 + i)] },
          contentDocumentIndex: { index: [], value: [] },
        },
        layout: {
          nodeIndex: idx,
          bounds: [[0, 0, 1917, 1905], ...buttons, ...extraTexts.map(() => [100, 200, 50, 20] as [number, number, number, number])],
        },
        scrollOffsetY: offsetY,
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

// 真机参考坐标：打招呼按钮在卡片右侧 x≈1720
const BTN1: [number, number, number, number] = [1690, 208, 64, 32] // 中心 (1722, 224)
const BTN2: [number, number, number, number] = [1690, 484, 64, 32]
const BTN3: [number, number, number, number] = [1690, 760, 64, 32]

test('逐个点击视口内可见按钮：每点一个重新定位，无可滚动时视口点完即结束', async () => {
  const r = recorder()
  const executor = new GreetExecutor({
    // 每轮 2 次 snapshot（定位 + 点完校验），按钮逐个消失；最后 1 次为滚动前探测（无 scroll 直接结束）
    snapshot: snapshotQueue([greetSnap([BTN1, BTN2, BTN3]), greetSnap([BTN2, BTN3]), greetSnap([BTN2, BTN3]), greetSnap([BTN3]), greetSnap([BTN3]), greetSnap([])]),
    click: r.click,
    sleep: r.sleep,
  })
  const result = await executor.greetVisible()
  assert.equal(result.greeted, 3)
  assert.equal(result.reachedEnd, true)
  // 每轮都点当前第一个（坐标不复用）
  assert.deepEqual(r.clicks, [
    { x: 1722, y: 224 },
    { x: 1722, y: 500 },
    { x: 1722, y: 776 },
  ])
})

test('视口外的按钮不点（Win32 点不到）', async () => {
  const r = recorder()
  const offScreen: [number, number, number, number] = [1690, 2500, 64, 32] // y 超出 1905 视口
  const executor = new GreetExecutor({
    snapshot: snapshotQueue([greetSnap([BTN1, offScreen]), greetSnap([offScreen])]),
    click: r.click,
    sleep: r.sleep,
  })
  const result = await executor.greetVisible()
  assert.equal(result.greeted, 1)
  assert.deepEqual(r.clicks, [{ x: 1722, y: 224 }])
})

test('真机回归：bounds 是文档绝对坐标，滚动后新露出的按钮按折算后的屏幕坐标点击', async () => {
  const r = recorder()
  // 真机 2026-08-05：首屏点完后，下一个按钮绝对 cy=2091（> 视口高 1905），滚动 1200 后才进入视口。
  // 若不减 scrollOffsetY，该按钮永远被当成视口外 → 表现为「不停往下滚但一个都不点」。
  const BELOW: [number, number, number, number] = [1690, 2075, 64, 32] // 绝对中心 cy=2091
  const executor = new GreetExecutor({
    snapshot: snapshotQueue([
      greetSnap([BELOW], 0), // 定位：2091 > 1905，不可见，不点
      greetSnap([BELOW], 0), // 滚动前
      greetSnap([BELOW], 1200), // 滚动后：offset 变化 → 未到底
      greetSnap([BELOW], 1200), // 定位：屏幕 cy = 2091-1200 = 891，可见
      greetSnap([], 1200), // 点完校验：按钮消失
      greetSnap([], 1200), // 定位：无按钮，进入滚动判定
    ]),
    click: r.click,
    sleep: r.sleep,
    scroll: async () => {},
  })
  const result = await executor.greetVisible()
  assert.equal(result.greeted, 1)
  assert.deepEqual(r.clicks, [{ x: 1722, y: 891 }])
})

test('--limit 限制点击数量（reachedEnd=false）', async () => {
  const r = recorder()
  const executor = new GreetExecutor({
    snapshot: snapshotQueue([greetSnap([BTN1, BTN2]), greetSnap([BTN2]), greetSnap([BTN2]), greetSnap([])]),
    click: r.click,
    sleep: r.sleep,
  })
  const result = await executor.greetVisible({ limit: 1 })
  assert.equal(result.greeted, 1)
  assert.equal(result.reachedEnd, false)
  assert.equal(r.clicks.length, 1)
})

test('默认上限 10：不给 limit 时点满 10 个自动停', async () => {
  const r = recorder()
  // 定位快照恒为 2 个按钮、校验快照恒为 1 个（模拟滚不完的列表）
  let call = 0
  const executor = new GreetExecutor({
    snapshot: async () => {
      call++
      return call % 2 === 1 ? greetSnap([BTN1, BTN2]) : greetSnap([BTN2])
    },
    click: r.click,
    sleep: r.sleep,
  })
  const result = await executor.greetVisible()
  assert.equal(result.greeted, 10)
  assert.equal(result.reachedEnd, false)
  assert.equal(r.clicks.length, 10)
})

test('视口点完自动滚动：滚动后出现新按钮继续点', async () => {
  const r = recorder()
  const scrolls: number[] = []
  // bounds 是文档绝对坐标：scrollY=800 时屏幕 y=500 的按钮，绝对 bounds.y = 500+800-16=1284
  const BTN2_ABS: [number, number, number, number] = [1690, 1284, 64, 32]
  const executor = new GreetExecutor({
    // 调用序：定位[B1] → 校验[] → 定位[]（内层退出）→ 滚动前(off=0) → 滚动后(off=800，滚动了)
    //   → 定位[B2] → 校验[] → 定位[] → 滚动前(off=800) → 滚动后(off=800 不变)
    //   → 重试：滚动前(800) → 滚动后(800 仍不变) → 到底
    snapshot: snapshotQueue([
      greetSnap([BTN1], 0),
      greetSnap([], 0),
      greetSnap([], 0),
      greetSnap([], 0),
      greetSnap([BTN2_ABS], 800),
      greetSnap([BTN2_ABS], 800),
      greetSnap([], 800),
      greetSnap([], 800),
      greetSnap([], 800),
      greetSnap([], 800),
      greetSnap([], 800),
      greetSnap([], 800),
    ]),
    click: r.click,
    sleep: r.sleep,
    scroll: async (deltaY) => {
      scrolls.push(deltaY)
    },
  })
  const result = await executor.greetVisible()
  assert.equal(result.greeted, 2)
  assert.equal(result.reachedEnd, true)
  assert.deepEqual(r.clicks, [
    { x: 1722, y: 224 },
    { x: 1722, y: 500 },
  ])
  assert.equal(scrolls.length, 3)
  assert.ok(scrolls.every((d) => d > 0 && d < 1905), '滚动距离必须小于视口高以保证重叠不漏人')
})

test('滚动后偏移不变（含一次重试）= 到底，立即结束', async () => {
  const r = recorder()
  let scrollCount = 0
  // 已滚到 3774：屏幕 y=224 的按钮绝对 bounds.y = 3774+224-16 = 3982
  const BTN1_ABS: [number, number, number, number] = [1690, 3982, 64, 32]
  const executor = new GreetExecutor({
    snapshot: snapshotQueue([
      greetSnap([BTN1_ABS], 3774),
      greetSnap([], 3774),
      greetSnap([], 3774),
      greetSnap([], 3774),
      greetSnap([], 3774),
      greetSnap([], 3774),
    ]),
    click: r.click,
    sleep: r.sleep,
    scroll: async () => {
      scrollCount++
    },
  })
  const result = await executor.greetVisible()
  assert.equal(result.greeted, 1)
  assert.equal(result.reachedEnd, true)
  assert.equal(scrollCount, 2)
})

test('点击后按钮数未减少（确认弹层/被拦截）→ fail-loud 停止，不再点下一个', async () => {
  const r = recorder()
  const executor = new GreetExecutor({
    // 点完第一个后按钮数不变（弹层挡住或点击未生效）
    snapshot: snapshotQueue([greetSnap([BTN1, BTN2]), greetSnap([BTN1, BTN2])]),
    click: r.click,
    sleep: r.sleep,
  })
  await assert.rejects(executor.greetVisible(), (e: unknown) => {
    assert.ok(e instanceof GreetError)
    assert.match(e.message, /按钮数未减少/)
    return true
  })
  assert.equal(r.clicks.length, 1)
})

test('点击后触发付费墙（该职位无开聊权益）→ 明确报错，不混淆为点击被拦截', async () => {
  const r = recorder()
  const paywallSnap = greetSnap([BTN1, BTN2], 0, ['该职位无开聊权益', '商品价格'])
  const executor = new GreetExecutor({
    snapshot: snapshotQueue([greetSnap([BTN1, BTN2]), paywallSnap]),
    click: r.click,
    sleep: r.sleep,
  })
  await assert.rejects(executor.greetVisible(), (e: unknown) => {
    assert.ok(e instanceof GreetError)
    assert.match(e.message, /付费墙/)
    return true
  })
  assert.equal(r.clicks.length, 1)
})

test('页面没有可点按钮且无滚动 → 0 人到底，不报错', async () => {
  const r = recorder()
  const executor = new GreetExecutor({ snapshot: snapshotQueue([greetSnap([])]), click: r.click, sleep: r.sleep })
  const result = await executor.greetVisible()
  assert.equal(result.greeted, 0)
  assert.equal(result.reachedEnd, true)
  assert.equal(r.clicks.length, 0)
})
