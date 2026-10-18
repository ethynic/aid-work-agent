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
import { actionSnapshot, text } from './chatActionFixture.js'
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

/** Same candidate cards stay visible after each verified greeting. */
function greetSnap(buttons: Array<[number, number, number, number]>, continued: Array<[number, number, number, number]> = []): DomSnapshot {
  const texts = ['筛选'], boxes: Array<[number, number, number, number]> = [[100,100,50,20]]
  for (const [bounds, label] of [...buttons.map(b => [b, '打招呼'] as const), ...continued.map(b => [b, '继续沟通'] as const)]) {
    const y = bounds[1] + bounds[3] / 2
    texts.push(bounds[1] < 300 ? '张三丰' : bounds[1] < 600 ? '李四海' : '王五平', '刚刚活跃', label)
    boxes.push([300,y-18,70,20], [390,y-18,70,20], bounds)
  }
  const ids=Array.from({length:texts.length+1},(_,i)=>i), bounds: Array<[number, number, number, number]> = [[0,0,1917,1905],...boxes]
  return {strings:['',...texts,'HTML'],documents:[{nodes:{nodeValue:{index:ids,value:ids},nodeName:{index:[0],value:[texts.length+1]},contentDocumentIndex:{index:[],value:[]}},layout:{nodeIndex:ids,bounds,clientRects:bounds}}]}
}
const BTN1: [number, number, number, number] = [1690,208,64,32]
const BTN2: [number, number, number, number] = [1690,484,64,32]
const BTN3: [number, number, number, number] = [1690,760,64,32]
const MARKER = '对方想发送附件简历给您，您是否同意'
const request=text(MARKER,240,500,290,25), prompt=text(MARKER,650,1000,300,25), agree=text('同意',1050,1000,60,25)

test('greet：onProgress 逐人回调，current/total 逐人推进', async () => {
  const events: ProgressEvent[] = []
  const ac = new AbortController()
  const ctx: OpContext = { signal: ac.signal, progress: (p) => events.push(p) }
  // probe → 3 轮（locate + after）→ 滚动探测
  const { session } = fakeSession(
    [
      greetSnap([BTN1, BTN2, BTN3]),
      greetSnap([BTN1, BTN2, BTN3]),
      greetSnap([BTN2, BTN3], [BTN1]),
      greetSnap([BTN2, BTN3], [BTN1]),
      greetSnap([BTN3], [BTN1, BTN2]),
      greetSnap([BTN3], [BTN1, BTN2]),
      greetSnap([], [BTN1, BTN2, BTN3]),
      greetSnap([], [BTN1, BTN2, BTN3]),
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
      greetSnap([BTN2, BTN3], [BTN1]),
      greetSnap([BTN2, BTN3], [BTN1]),
      greetSnap([BTN3], [BTN1, BTN2]),
      greetSnap([BTN3], [BTN1, BTN2]),
      greetSnap([], [BTN1, BTN2, BTN3]),
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
  assert.equal((r.data.confirmed_items as unknown[]).length, 2)
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
    actionSnapshot({ extra: [request] }),
    actionSnapshot({ extra: [request, prompt, agree] }),
    actionSnapshot({ extra: [text('点击预览附件简历',700,800,180,25)] }),
    actionSnapshot({ extra: [text('点击预览附件简历',700,800,180,25)] }),
  ])
  const op = createBossAcceptResumeOperation(async () => session)
  const r = await op.execute({ limit: 5, preview: false }, ctx)
  assert.equal(r.success, false)
  assert.equal(r.code, 'CANCELLED')
  assert.equal(r.effect, 'partial')
  assert.equal(r.data.completed, 1)
  assert.equal((r.data.confirmed_items as unknown[]).length, 1)
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
