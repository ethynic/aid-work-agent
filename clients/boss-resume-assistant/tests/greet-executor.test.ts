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

// ---------- 定向模式（names）：先配对姓名再点击，配对失败一律跳过 ----------
// 2026-08-18 真机修复：列表顺序与 matched 名单顺序不保证一致，只认 limit 会打错人。
// 卡片行结构按真机锚定（同 resume-batch.test.ts）：姓名(342,y-8) + 活跃状态(400,y-8)
// 同行 + 噪音「本科」+ 打招呼按钮(1162,y)，根视口 1249x1277。

/**
 * 卡片行定义：姓名（null = DOM 配对失败的卡片）+ 按钮中心 y（屏幕坐标）；
 * status 省略 = 正常有「刚刚活跃」状态，status=null = 状态位为空（真机 2026-08-18
 * 「曹鹤洋」卡：无状态节点，但无论在线与否都可打招呼，配对不得依赖状态）
 */
interface NameRow {
  name: string | null
  buttonY: number
  status?: string | null
}
const ROW_A: NameRow = { name: '刘草威', buttonY: 146 }
const ROW_B: NameRow = { name: '张三丰', buttonY: 330 }
const ROW_NULL: NameRow = { name: null, buttonY: 514 }

/** 构造带卡片行的 snapshot：bounds 为文档绝对坐标（行 y + offsetY），scrollOffsetY=offsetY */
function namedSnap(rows: NameRow[], offsetY = 0): DomSnapshot {
  const strings: string[] = ['']
  const nvIndex: number[] = [0]
  const nvValue: number[] = [0]
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
  const addText = (s: string, bounds: [number, number, number, number]): void => {
    const ni = nextNi++
    nvIndex.push(ni)
    nvValue.push(intern(s))
    layoutNodeIndex.push(ni)
    layoutBounds.push(bounds)
  }
  for (const row of rows) {
    if (row.name !== null) addText(row.name, [317, row.buttonY - 18 + offsetY, 50, 20]) // 中心 (342, y-8)
    if (row.status !== null) addText(row.status ?? '刚刚活跃', [370, row.buttonY - 18 + offsetY, 60, 20]) // 中心 (400, y-8)
    addText('本科', [317, row.buttonY + 30 + offsetY, 40, 20]) // 噪音：同行带外
    addText('\n                  打招呼', [1130, row.buttonY - 16 + offsetY, 64, 32]) // 中心 (1162, y)
  }
  return {
    strings,
    documents: [
      {
        nodes: {
          nodeValue: { index: nvIndex, value: nvValue },
          contentDocumentIndex: { index: [], value: [] },
        },
        layout: { nodeIndex: layoutNodeIndex, bounds: layoutBounds },
        scrollOffsetY: offsetY,
      },
    ],
  }
}

test('定向：无「活跃」状态的卡片（状态位为空）仍能配上姓名并点击——配对不依赖在线状态', async () => {
  // 真机 2026-08-18「曹鹤洋」卡：状态位无任何文本，姓名列(342,y-8)仍在。
  // 此前以状态为锚整链断裂，定向打招呼滚遍全列表也找不到该人
  const r = recorder()
  // 点击曹鹤洋后他的行消失（按钮数 2→1 通过校验），刘草威保留
  const executor = new GreetExecutor({
    snapshot: snapshotQueue([
      namedSnap([{ name: '刘草威', buttonY: 146 }, { name: '曹鹤洋', buttonY: 330, status: null }]),
      namedSnap([{ name: '刘草威', buttonY: 146 }]),
    ]),
    click: r.click,
    sleep: r.sleep,
  })
  const result = await executor.greetVisible({ names: ['曹鹤洋'] })
  assert.equal(result.greeted, 1)
  assert.deepEqual(result.greetedNames, ['曹鹤洋'])
  // 只点了曹鹤洋的按钮（y=330），顶部刘草威（y=146）绝不点
  assert.deepEqual(r.clicks.map((c) => c.y), [330])
})

test('定向：状态显示「在线」等变体（不含「活跃」）同样不影响配对', async () => {
  const r = recorder()
  // 点击后刘帅的行（唯一按钮）消失，按钮数 1→0 通过校验
  const executor = new GreetExecutor({
    snapshot: snapshotQueue([
      namedSnap([{ name: '刘帅', buttonY: 146, status: '刚刚在线' }]),
      namedSnap([]),
    ]),
    click: r.click,
    sleep: r.sleep,
  })
  const result = await executor.greetVisible({ names: ['刘帅'] })
  assert.equal(result.greeted, 1)
  assert.deepEqual(result.greetedNames, ['刘帅'])
})

test('定向：names=[B] 时只点 B 的按钮，列表顶部的 A 与配对失败的卡片绝不点', async () => {
  const r = recorder()
  const executor = new GreetExecutor({
    // 首屏 3 行（A/B/无名），点掉 B 后剩 2 行 → 名单完成立即返回
    snapshot: snapshotQueue([namedSnap([ROW_A, ROW_B, ROW_NULL]), namedSnap([ROW_A, ROW_NULL])]),
    click: r.click,
    sleep: r.sleep,
  })
  const result = await executor.greetVisible({ names: ['张三丰'] })
  assert.equal(result.greeted, 1)
  assert.equal(result.reachedEnd, false) // 名单全部完成（未滚到底）
  assert.deepEqual(result.greetedNames, ['张三丰'])
  assert.deepEqual(result.missingNames, [])
  // 只点了 B 行按钮 (1162, 330)：顶部的 A 与无名卡片被跳过（修复前会点 A——打错人）
  assert.deepEqual(r.clicks, [{ x: 1162, y: 330 }])
})

test('定向：视口内多目标从上往下逐个点，点完 A 点 B（每点重新配对定位）', async () => {
  const r = recorder()
  const executor = new GreetExecutor({
    // 定位[A,B] → 点 A → 校验[B] → 定位[B] → 点 B → 校验[] → 名单完成返回
    snapshot: snapshotQueue([
      namedSnap([ROW_A, ROW_B]),
      namedSnap([ROW_B]),
      namedSnap([ROW_B]),
      namedSnap([]),
    ]),
    click: r.click,
    sleep: r.sleep,
  })
  const result = await executor.greetVisible({ names: ['刘草威', '张三丰'] })
  assert.equal(result.greeted, 2)
  assert.deepEqual(result.greetedNames, ['刘草威', '张三丰'])
  assert.deepEqual(result.missingNames, [])
  assert.deepEqual(r.clicks, [
    { x: 1162, y: 146 },
    { x: 1162, y: 330 },
  ])
})

test('定向：视口内无目标 → 滚动加载继续找（B 在下一屏），全找到后结束', async () => {
  const r = recorder()
  const scrolls: number[] = []
  // B 在首屏视口外（bounds 绝对中心 y=1130），滚动 800 后屏幕 y=330 进入视口
  const executor = new GreetExecutor({
    // 内层定位[A] → 点 A → 校验[] → 定位[]（无按钮）→ 滚动前(0) → 滚动后(800) →
    // 定位[B@800]（屏幕 y=330）→ 点 B → 校验[] → 名单完成返回
    snapshot: snapshotQueue([
      namedSnap([ROW_A], 0),
      namedSnap([], 0),
      namedSnap([], 0),
      namedSnap([], 0),
      namedSnap([ROW_B], 800),
      namedSnap([ROW_B], 800),
      namedSnap([], 800),
    ]),
    click: r.click,
    sleep: r.sleep,
    scroll: async (deltaY) => {
      scrolls.push(deltaY)
    },
  })
  const result = await executor.greetVisible({ names: ['刘草威', '张三丰'] })
  assert.equal(result.greeted, 2)
  assert.deepEqual(result.greetedNames, ['刘草威', '张三丰'])
  assert.deepEqual(result.missingNames, [])
  assert.deepEqual(r.clicks, [
    { x: 1162, y: 146 },
    { x: 1162, y: 330 },
  ])
  assert.equal(scrolls.length, 1) // 只滚了一次就找到 B
})

test('定向：names 含页面滚到底也不存在的人 → missingNames 如实返回，绝不点任何按钮', async () => {
  const r = recorder()
  let scrollCount = 0
  const executor = new GreetExecutor({
    // 视口只有 A（不在名单）→ 无目标滚动；两次滚动 offset 不变 → 到底
    snapshot: snapshotQueue([
      namedSnap([ROW_A], 0), // 定位：无目标
      namedSnap([ROW_A], 0), // 滚动前
      namedSnap([ROW_A], 0), // 滚动后（offset 不变）
      namedSnap([ROW_A], 0), // 重试：滚动前
      namedSnap([ROW_A], 0), // 滚动后（仍不变 → 到底）
    ]),
    click: r.click,
    sleep: r.sleep,
    scroll: async () => {
      scrollCount++
    },
  })
  const result = await executor.greetVisible({ names: ['王五'] })
  assert.equal(result.greeted, 0)
  assert.equal(result.reachedEnd, true)
  assert.deepEqual(result.greetedNames, [])
  assert.deepEqual(result.missingNames, ['王五'])
  assert.equal(r.clicks.length, 0) // 不在名单的 A 绝不点（宁可不打，不能打错）
  assert.equal(scrollCount, 2) // 滚到底判定含一次重试
})

test('定向：配对失败的卡片（无名行）绝不点，滚到底后进 missingNames', async () => {
  const r = recorder()
  const executor = new GreetExecutor({
    // 视口只有无名卡：配对失败 → 无目标 → 无滚动依赖 → 视口点完即结束（到底）
    snapshot: snapshotQueue([namedSnap([ROW_NULL]), namedSnap([ROW_NULL])]),
    click: r.click,
    sleep: r.sleep,
  })
  const result = await executor.greetVisible({ names: ['刘草威'] })
  assert.equal(result.greeted, 0)
  assert.equal(result.reachedEnd, true)
  assert.deepEqual(result.missingNames, ['刘草威'])
  assert.equal(r.clicks.length, 0)
})

test('定向：limit 小于 names 数量时作为总上限保险截断，剩余进 missingNames', async () => {
  const r = recorder()
  const executor = new GreetExecutor({
    snapshot: snapshotQueue([namedSnap([ROW_A, ROW_B]), namedSnap([ROW_B])]),
    click: r.click,
    sleep: r.sleep,
  })
  const result = await executor.greetVisible({ limit: 1, names: ['刘草威', '张三丰'] })
  assert.equal(result.greeted, 1)
  assert.equal(result.reachedEnd, false)
  assert.deepEqual(result.greetedNames, ['刘草威'])
  assert.deepEqual(result.missingNames, ['张三丰']) // 被 limit 截断：没打也没找到
  assert.equal(r.clicks.length, 1)
})

test('定向：names 空数组 → GreetError（非法入参 fail-loud）', async () => {
  const r = recorder()
  const executor = new GreetExecutor({ snapshot: snapshotQueue([namedSnap([])]), click: r.click, sleep: r.sleep })
  await assert.rejects(executor.greetVisible({ names: [] }), (e: unknown) => {
    assert.ok(e instanceof GreetError)
    assert.match(e.message, /names 不能是空数组/)
    return true
  })
  assert.equal(r.clicks.length, 0)
})

test('非定向模式结果不含 greetedNames/missingNames 键（契约零变化）', async () => {
  const r = recorder()
  const executor = new GreetExecutor({
    snapshot: snapshotQueue([greetSnap([BTN1]), greetSnap([]), greetSnap([])]),
    click: r.click,
    sleep: r.sleep,
  })
  const result = await executor.greetVisible()
  assert.deepEqual(Object.keys(result).sort(), ['greeted', 'reachedEnd'])
})
