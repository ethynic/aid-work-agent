/**
 * greet/accept 的 onProgress 逐人回调与 abort → CANCELLED（规格 m02 §9）。
 *
 * 用 fake session 脚本化 snapshot 队列：验证 progress 事件的 current/total 逐人推进，
 * 以及 signal abort 后 operation 返回 CANCELLED + partial effect + 已完成量。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { createBossGreetOperation } from '../src/main/operations/bossGreet.js'
import { createBossAcceptResumeOperation } from '../src/main/operations/bossAcceptResume.js'
import type { BossSession } from '../src/main/operations/bossContext.js'
import type { OpContext, ProgressEvent } from '../src/main/operations/types.js'
import type { DomSnapshot, ClickPoint } from '../src/main/boss/domSnapshot.js'

/** 依调用序返回 snapshot 队列，用完后重复最后一个。
 * 默认 URL 是沟通页（accept 的 ensureChatPage 用）；greet 用例必须显式传推荐页 URL——
 * greet 前置校验按 URL 判定（坑 17），默认沟通页会被 WRONG_PAGE 拒绝 */
function fakeSession(snaps: DomSnapshot[], opts: { url?: string } = {}) {
  const clicks: ClickPoint[] = []
  let i = 0
  const session: BossSession = {
    snapshot: async () => snaps[Math.min(i++, snaps.length - 1)]!,
    click: async (p) => {
      clicks.push(p)
    },
    clickBrowse: async () => {},
    mouseWheel: async () => {},
    pressEscape: async () => {},
    clickAndType: async () => {},
    captureFullpage: async () => Buffer.alloc(0),
    getUrl: async () => opts.url ?? 'https://www.zhipin.com/web/chat/index',
    close: async () => {},
  }
  return { session, clicks }
}

/** greet snapshot：根 + 「筛选」+ N 个「打招呼」按钮 */
function greetSnap(buttons: Array<[number, number, number, number]>): DomSnapshot {
  const nodeCount = 2 + buttons.length
  const idx = Array.from({ length: nodeCount }, (_, i) => i)
  return {
    strings: ['', '筛选', '打招呼'],
    documents: [
      {
        nodes: {
          nodeValue: { index: idx, value: [0, 1, ...buttons.map(() => 2)] },
          contentDocumentIndex: { index: [], value: [] },
        },
        layout: { nodeIndex: idx, bounds: [[0, 0, 1917, 1905], [100, 100, 50, 20], ...buttons] },
      },
    ],
  }
}

const BTN1: [number, number, number, number] = [1690, 208, 64, 32]
const BTN2: [number, number, number, number] = [1690, 484, 64, 32]
const BTN3: [number, number, number, number] = [1690, 760, 64, 32]

/** accept snapshot：文案项构造（与 resume-consent.test.ts 同范式） */
type Item = { text: string; bounds: [number, number, number, number] }
function consentSnap(items: Item[]): DomSnapshot {
  const idx = [0, ...items.map((_, i) => i + 1)]
  const uniqTexts = items.map((it) => it.text).filter((t, i, arr) => arr.indexOf(t) === i)
  const stringIds = items.map((it) => 1 + uniqTexts.indexOf(it.text))
  return {
    strings: ['', ...uniqTexts],
    documents: [
      {
        nodes: {
          nodeValue: { index: idx, value: [0, ...stringIds] },
          contentDocumentIndex: { index: [], value: [] },
        },
        layout: { nodeIndex: idx, bounds: [[0, 0, 1917, 1905], ...items.map((it) => it.bounds)] },
      },
    ],
  }
}
const MARKER = '对方想发送附件简历给您，您是否同意'
const listItem = (y: number): Item => ({ text: MARKER, bounds: [400, y - 16, 298, 32] })
const banner = (y: number): Item[] => [
  { text: MARKER, bounds: [900, y - 16, 226, 32] },
  { text: '同意', bounds: [1295, y - 20, 80, 40] },
]

test('greet：onProgress 逐人回调，current/total 逐人推进', async () => {
  const events: ProgressEvent[] = []
  const ac = new AbortController()
  const ctx: OpContext = { signal: ac.signal, progress: (p) => events.push(p) }
  // probe → 3 轮（locate + after）→ 滚动探测
  const { session } = fakeSession(
    [
      greetSnap([BTN1, BTN2, BTN3]),
      greetSnap([BTN1, BTN2, BTN3]),
      greetSnap([BTN2, BTN3]),
      greetSnap([BTN2, BTN3]),
      greetSnap([BTN3]),
      greetSnap([BTN3]),
      greetSnap([]),
      greetSnap([]),
    ],
    { url: 'https://www.zhipin.com/web/chat/recommend' },
  )
  const op = createBossGreetOperation(async () => session)
  const r = await op.execute({ limit: 3 }, ctx)
  assert.equal(r.success, true)
  assert.equal(r.data.greeted, 3)
  const progressEvents = events.filter((e) => e.stage === 'execute' && e.current !== undefined)
  assert.deepEqual(
    progressEvents.map((e) => [e.current, e.total]),
    [
      [1, 3],
      [2, 3],
      [3, 3],
    ],
  )
  assert.ok(events.some((e) => e.stage === 'done'))
})

test('greet：打完 2 人后 abort → CANCELLED + partial + completed=2', async () => {
  const ac = new AbortController()
  const ctx: OpContext = {
    signal: ac.signal,
    progress: (p) => {
      if (p.stage === 'execute' && p.current === 2) ac.abort()
    },
  }
  const { session } = fakeSession(
    [
      greetSnap([BTN1, BTN2, BTN3]),
      greetSnap([BTN1, BTN2, BTN3]),
      greetSnap([BTN2, BTN3]),
      greetSnap([BTN2, BTN3]),
      greetSnap([BTN3]),
      greetSnap([BTN3]),
      greetSnap([]),
    ],
    { url: 'https://www.zhipin.com/web/chat/recommend' },
  )
  const op = createBossGreetOperation(async () => session)
  const r = await op.execute({ limit: 5 }, ctx)
  assert.equal(r.success, false)
  assert.equal(r.code, 'CANCELLED')
  assert.equal(r.effect, 'partial')
  assert.equal(r.retryable, false)
  assert.equal(r.data.completed, 2)
})

test('accept：onProgress 逐人回调，1 人后 abort → CANCELLED + partial', async () => {
  const events: ProgressEvent[] = []
  const ac = new AbortController()
  const ctx: OpContext = {
    signal: ac.signal,
    progress: (p) => {
      events.push(p)
      if (p.stage === 'execute' && p.current === 1) ac.abort()
    },
  }
  // 定位[左列1个] → 打开后[处理条] → 点完校验[无同意] → （下一轮循环顶部 abort）
  const { session } = fakeSession([
    consentSnap([listItem(1307)]),
    consentSnap([listItem(1307), ...banner(1557)]),
    consentSnap([listItem(1307)]),
    consentSnap([]),
  ])
  const op = createBossAcceptResumeOperation(async () => session)
  const r = await op.execute({ limit: 5, preview: false }, ctx)
  assert.equal(r.success, false)
  assert.equal(r.code, 'CANCELLED')
  assert.equal(r.effect, 'partial')
  assert.equal(r.data.completed, 1)
  const progressEvents = events.filter((e) => e.stage === 'execute' && e.current !== undefined)
  assert.deepEqual(
    progressEvents.map((e) => [e.current, e.total]),
    [[1, 5]],
  )
})

test('入口前已 abort：不触达 Chrome，直接 CANCELLED + effect=none', async () => {
  const ac = new AbortController()
  ac.abort()
  const ctx: OpContext = { signal: ac.signal, progress: () => {} }
  let factoryCalled = 0
  const op = createBossGreetOperation(async () => {
    factoryCalled++
    throw new Error('不应被调用')
  })
  const r = await op.execute({ limit: 1 }, ctx)
  assert.equal(r.code, 'CANCELLED')
  assert.equal(r.effect, 'none')
  assert.equal(factoryCalled, 0)
})
