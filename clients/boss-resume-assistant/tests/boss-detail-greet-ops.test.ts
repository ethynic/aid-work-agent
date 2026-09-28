/**
 * boss_open_detail / boss_greet_detail / boss_close_detail operation 测试（仿 boss-open-chat.test.ts）：
 * fake session 注入替身，覆盖 URL 前置校验（open-detail）、参数校验（greet-detail 缺 name）、
 * dry-run/幂等/真打的消息与 effect、错误映射（姓名不符 → WRONG_PAGE 可重试）。
 *
 * 注意：operation 层 sleep 为 perf 包装的真延时（无法注入 fake）——本文件只挑选无/短延时路径；
 * 真打全链路（含 800ms 翻转轮询）在 detail-greet-executor.test.ts 用 fake sleep 已覆盖，
 * 此处仅验编排与结果契约（真打一条约 1.3s 真延时，验证 writeEffect=applied 接线）。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { createBossOpenDetailOperation } from '../src/main/operations/bossOpenDetail.js'
import { createBossGreetDetailOperation } from '../src/main/operations/bossGreetDetail.js'
import { createBossCloseDetailOperation } from '../src/main/operations/bossCloseDetail.js'
import type { BossSession } from '../src/main/operations/bossContext.js'
import type { OpContext } from '../src/main/operations/types.js'
import type { DomSnapshot } from '../src/main/boss/domSnapshot.js'
import { closedListSnap, detailSnap, listSnap } from './detailGreetFixture.js'

function silentCtx(): OpContext {
  return { signal: new AbortController().signal, progress: () => {} }
}

const RECOMMEND_URL = 'https://www.zhipin.com/web/chat/recommend'
const OTHER_URL = 'https://www.zhipin.com/web/chat/index'

/** fake 工厂：固定 URL + snapshot 队列（用完后重复最后一个） */
function fakeFactory(url: string, snaps: DomSnapshot[]) {
  const calls: string[] = []
  let i = 0
  const session: BossSession = {
    snapshot: async () => {
      calls.push('snapshot')
      return snaps[Math.min(i++, snaps.length - 1)]!
    },
    click: async () => {
      calls.push('click')
    },
    clickBrowse: async () => {},
    mouseWheel: async () => {
      calls.push('wheel')
    },
    pressEscape: async () => {
      calls.push('escape')
    },
    clickAndType: async () => {},
    captureFullpage: async () => Buffer.alloc(0),
    getUrl: async () => {
      calls.push('getUrl')
      return url
    },
    close: async () => {
      calls.push('close')
    },
  }
  return { factory: async () => session, calls }
}

const NAME = '刘草威'
// 目标行在 y=514（姓名中心 (342,506) 在 canvas 顶区之外，避免打开后同名歧义，见 fixture 注释）
const TARGET_LIST = listSnap([
  { name: '张三丰', buttonY: 146 },
  { name: NAME, buttonY: 514 },
])

// ---------- boss_open_detail ----------

test('open-detail：非推荐页 URL → WRONG_PAGE（可重试），提示 goto recommend，不点任何东西', async () => {
  const f = fakeFactory(OTHER_URL, [TARGET_LIST])
  const r = await createBossOpenDetailOperation(f.factory).execute({ name: NAME }, silentCtx())
  assert.equal(r.success, false)
  assert.equal(r.code, 'WRONG_PAGE')
  assert.equal(r.retryable, true)
  assert.equal(r.effect, 'none')
  assert.match(r.message, /goto recommend/)
  assert.deepEqual(f.calls, ['getUrl', 'close'])
})

test('open-detail：缺 name → INVALID_ARGUMENT，且不连 Chrome（validate 在 connect 前）', async () => {
  const f = fakeFactory(RECOMMEND_URL, [TARGET_LIST])
  const r = await createBossOpenDetailOperation(f.factory).execute({ name: '   ' }, silentCtx())
  assert.equal(r.success, false)
  assert.equal(r.code, 'INVALID_ARGUMENT')
  assert.equal(r.effect, 'none')
  assert.deepEqual(f.calls, [])
})

test('open-detail：推荐页当前屏找到 → 点击 → canvas 出现 → 姓名匹配 → 成功，effect=none', async () => {
  const f = fakeFactory(RECOMMEND_URL, [TARGET_LIST, TARGET_LIST, detailSnap({ name: NAME, state: 'greet' })])
  const r = await createBossOpenDetailOperation(f.factory).execute({ name: NAME }, silentCtx())
  assert.equal(r.success, true)
  assert.equal(r.code, 'OK')
  assert.equal(r.effect, 'none')
  assert.equal(r.data.opened, true)
  assert.equal(r.data.name, NAME)
  assert.match(r.message, /已打开「刘草威」/)
  assert.match(r.message, /保持打开/)
  assert.match(r.message, /boss_resume_detail/)
})

// ---------- boss_greet_detail ----------

test('greet-detail：缺 name → INVALID_ARGUMENT，不连 Chrome（防打错人姓名是必填契约）', async () => {
  const f = fakeFactory(RECOMMEND_URL, [])
  const r = await createBossGreetDetailOperation(f.factory).execute({ name: '' }, silentCtx())
  assert.equal(r.success, false)
  assert.equal(r.code, 'INVALID_ARGUMENT')
  assert.deepEqual(f.calls, [])
})

test('greet-detail：dry-run → 定位成功不点击，effect=none，详情保持打开', async () => {
  const f = fakeFactory(RECOMMEND_URL, [detailSnap({ name: NAME, state: 'greet' })])
  const r = await createBossGreetDetailOperation(f.factory).execute({ name: NAME, dry_run: true }, silentCtx())
  assert.equal(r.success, true)
  assert.equal(r.effect, 'none')
  assert.equal(r.data.greeted, false)
  assert.equal(r.data.dry_run, true)
  assert.match(r.message, /dry-run/)
  assert.match(r.message, /未点击/)
  assert.deepEqual(f.calls, ['snapshot', 'close']) // 零点击零 Escape
})

test('greet-detail：已打过（按钮=继续沟通）→ 幂等成功，effect=none，不重复点击', async () => {
  const f = fakeFactory(RECOMMEND_URL, [detailSnap({ name: NAME, state: 'continue' }), closedListSnap()])
  const r = await createBossGreetDetailOperation(f.factory).execute({ name: NAME }, silentCtx())
  assert.equal(r.success, true)
  assert.equal(r.effect, 'none')
  assert.equal(r.data.greeted, false)
  assert.equal(r.data.already, true)
  assert.match(r.message, /已打过/)
  assert.match(r.message, /未重复点击/)
  assert.deepEqual(f.calls, ['snapshot', 'escape', 'snapshot', 'close'])
})

test('greet-detail：真打成功 → effect=applied，消息注明按钮翻转与详情关闭', async () => {
  const f = fakeFactory(RECOMMEND_URL, [
    detailSnap({ name: NAME, state: 'greet' }),
    detailSnap({ name: NAME, state: 'continue' }),
    closedListSnap(),
  ])
  const r = await createBossGreetDetailOperation(f.factory).execute({ name: NAME }, silentCtx())
  assert.equal(r.success, true)
  assert.equal(r.effect, 'applied')
  assert.equal(r.data.greeted, true)
  assert.match(r.message, /继续沟通/)
  assert.match(r.message, /详情已关闭/)
})

test('greet-detail：详情属于别人（姓名不符）→ WRONG_PAGE 可重试，effect=none，零点击', async () => {
  const f = fakeFactory(RECOMMEND_URL, [detailSnap({ name: '李四', state: 'greet' })])
  const r = await createBossGreetDetailOperation(f.factory).execute({ name: NAME }, silentCtx())
  assert.equal(r.success, false)
  assert.equal(r.code, 'WRONG_PAGE')
  assert.equal(r.retryable, true)
  assert.equal(r.effect, 'none')
  assert.match(r.message, /姓名与预期不符/)
  assert.deepEqual(f.calls, ['snapshot', 'close'])
})

// ---------- boss_close_detail ----------

test('close-detail：详情未打开 → 幂等成功，effect=none', async () => {
  const f = fakeFactory(RECOMMEND_URL, [closedListSnap()])
  const r = await createBossCloseDetailOperation(f.factory).execute({}, silentCtx())
  assert.equal(r.success, true)
  assert.equal(r.effect, 'none')
  assert.equal(r.data.was_open, false)
  assert.match(r.message, /无需关闭/)
})

test('close-detail：详情打开 → Escape 关闭成功', async () => {
  const f = fakeFactory(RECOMMEND_URL, [detailSnap({ name: NAME, state: 'greet' }), closedListSnap()])
  const r = await createBossCloseDetailOperation(f.factory).execute({}, silentCtx())
  assert.equal(r.success, true)
  assert.equal(r.effect, 'none')
  assert.equal(r.data.was_open, true)
  assert.match(r.message, /已关闭/)
})
