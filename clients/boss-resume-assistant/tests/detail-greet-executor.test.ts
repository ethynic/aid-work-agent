/**
 * DetailGreetExecutor 单测：详情页「校验姓名 → 容器消歧定位 → Win32 点击 → 翻转校验 →
 * Escape 关闭」链路（fake 注入 snapshot/click/pressEscape/scroll/sleep，参照
 * filter-setter.test.ts 的 buildSnap 范式 + greet-executor.test.ts 的滚动 fake）。
 *
 * 核心回归点（2026-09-28 真机探查结论，plan-boss-detail-greet.md）：
 * - 列表「打招呼」诱饵与详情按钮纯几何不可区分（cy 仅差 13px）——必须 LCA 容器结构级消歧，
 *   容器外多个诱饵也只认容器内按钮；
 * - 打招呼前强校验详情属于预期候选人（canvas 顶区姓名），不符零点击 fail-loud；
 * - 点击后按钮未翻转 → fail-loud（unknown，不重试）；already-greeted 幂等不点击；
 * - close 幂等；open-detail 当前屏找人/滚动找人/找不到 fail-loud 列出可见姓名。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { DetailGreetExecutor, DetailGreetError, type DetailGreetDeps } from '../src/main/boss/DetailGreetExecutor.js'
import type { ClickPoint, DomSnapshot } from '../src/main/boss/domSnapshot.js'
import {
  BAR_GREET_CENTER,
  buildSnap,
  CANVAS_BOUNDS,
  closedListSnap,
  detailSnap,
  GREET_STRING,
  listSnap,
  VIEWPORT,
  type SnapItem,
} from './detailGreetFixture.js'

/** 依调用序返回 snapshot 队列，用完后重复最后一个（greet-executor.test.ts 同款） */
function snapshotQueue(snaps: DomSnapshot[]) {
  let i = 0
  return async (): Promise<DomSnapshot> => snaps[Math.min(i++, snaps.length - 1)]!
}

/** fake 依赖记录器：clicks/escapes/wheels 全记录，sleep 即返（测试不等待真延时） */
function makeDeps(snaps: DomSnapshot[], opts: { withScroll?: boolean } = {}) {
  const clicks: ClickPoint[] = []
  const wheels: Array<{ x: number; y: number; deltaY: number }> = []
  let escapes = 0
  const deps: DetailGreetDeps = {
    snapshot: snapshotQueue(snaps),
    click: async (p) => {
      clicks.push(p)
    },
    pressEscape: async () => {
      escapes++
    },
    sleep: async () => {},
    ...(opts.withScroll
      ? {
          scroll: async (x: number, y: number, deltaY: number) => {
            wheels.push({ x, y, deltaY })
          },
        }
      : {}),
  }
  return { deps, clicks, wheels, escapeCount: () => escapes }
}

const NAME = '刘草威'

// ---------- greet：容器消歧 + 点击 + 翻转 + 关闭 ----------

test('正常链路：姓名校验 → 容器内定位 → 点击 → 翻转 → Escape 关闭；点击点是容器内按钮中心而非列表诱饵', async () => {
  // 快照序列：打开态（容器内=打招呼 + 2 个容器外诱饵）→ 点击后翻转（容器内=继续沟通）→ Escape 后列表态
  const r = makeDeps([detailSnap({ name: NAME, state: 'greet' }), detailSnap({ name: NAME, state: 'continue' }), closedListSnap()])
  const executor = new DetailGreetExecutor(r.deps)
  const out = await executor.greet({ name: NAME })
  assert.deepEqual(out, { greeted: true })
  // 只点了容器内按钮 (1085,156)；诱饵中心 (1162,146)/(1162,330) 绝不点
  assert.equal(r.clicks.length, 1)
  assert.deepEqual(r.clicks[0], BAR_GREET_CENTER)
  assert.notDeepEqual(r.clicks[0], { x: 1162, y: 146 })
  assert.equal(r.escapeCount(), 1)
})

test('列表诱饵在容器外不误点：诱饵再多也只认容器内的「打招呼」（结构级消歧）', async () => {
  const decoys = [
    { name: '张三丰', buttonY: 146 },
    { name: '李建国', buttonY: 330 },
    { name: '王小明', buttonY: 514 },
    { name: '赵铁柱', buttonY: 698 },
  ]
  const r = makeDeps([
    detailSnap({ name: NAME, state: 'greet', decoyRows: decoys }),
    detailSnap({ name: NAME, state: 'continue', decoyRows: decoys }),
    closedListSnap(),
  ])
  const out = await new DetailGreetExecutor(r.deps).greet({ name: NAME })
  assert.equal(out.greeted, true)
  assert.deepEqual(r.clicks, [BAR_GREET_CENTER])
})

test('alreadyGreeted 幂等：容器内已是「继续沟通」→ 不点击直接关闭', async () => {
  const r = makeDeps([detailSnap({ name: NAME, state: 'continue' }), closedListSnap()])
  const out = await new DetailGreetExecutor(r.deps).greet({ name: NAME })
  assert.deepEqual(out, { greeted: false, already: true })
  assert.equal(r.clicks.length, 0)
  assert.equal(r.escapeCount(), 1)
})

test('姓名不符 fail-loud：详情头部是别人（李四）→ 零点击拒绝打招呼', async () => {
  const r = makeDeps([detailSnap({ name: '李四', state: 'greet' })])
  await assert.rejects(new DetailGreetExecutor(r.deps).greet({ name: NAME }), (e: unknown) => {
    assert.ok(e instanceof DetailGreetError)
    assert.match(e.message, /姓名与预期不符/)
    assert.match(e.message, /防打错人/)
    assert.match(e.message, /刘草威/)
    return true
  })
  assert.equal(r.clicks.length, 0)
})

test('canvas 顶区同名多命中（歧义）→ fail-loud 零点击，绝不猜', async () => {
  const r = makeDeps([
    detailSnap({
      name: NAME,
      state: 'greet',
      // 顶区内再加一个同名文本（如覆盖层复制品），中心 (520,210) 仍在 [40,340] 区内
      extraItems: [{ text: NAME, bounds: [500, 200, 40, 20], parent: 0 }],
    }),
  ])
  await assert.rejects(new DetailGreetExecutor(r.deps).greet({ name: NAME }), /命中 2 个/)
  assert.equal(r.clicks.length, 0)
})

test('详情未打开（无 canvas）→ fail-loud「详情未打开」，零点击', async () => {
  const r = makeDeps([closedListSnap()])
  await assert.rejects(new DetailGreetExecutor(r.deps).greet({ name: NAME }), (e: unknown) => {
    assert.ok(e instanceof DetailGreetError)
    assert.match(e.message, /详情未打开/)
    return true
  })
  assert.equal(r.clicks.length, 0)
})

test('dry-run：只定位不点击，详情保持打开（零 Escape）', async () => {
  const r = makeDeps([detailSnap({ name: NAME, state: 'greet' })])
  const out = await new DetailGreetExecutor(r.deps).greet({ name: NAME, dryRun: true })
  assert.equal(out.greeted, false)
  assert.equal(out.dryRun, true)
  assert.deepEqual(out.point, BAR_GREET_CENTER)
  assert.equal(r.clicks.length, 0)
  assert.equal(r.escapeCount(), 0)
})

test('点击后按钮不翻转（3 轮轮询仍可点）→ fail-loud（unknown，不重试），只点一次', async () => {
  const open = detailSnap({ name: NAME, state: 'greet' })
  const r = makeDeps([open, open, open, open]) // 初始 + 3 轮轮询全未翻转
  await assert.rejects(new DetailGreetExecutor(r.deps).greet({ name: NAME }), (e: unknown) => {
    assert.ok(e instanceof DetailGreetError)
    assert.match(e.message, /未翻转/)
    assert.match(e.message, /不要重试/)
    return true
  })
  assert.equal(r.clicks.length, 1)
  assert.equal(r.escapeCount(), 0)
})

test('翻转轮询容忍单轮定位失败：第 1 轮操作列缺失（弹层重绘过渡态）→ 继续轮询，第 2 轮翻转成功', async () => {
  // 点击后第 1 轮快照丢失操作列（locateActionBar 会抛）→ 不得立即定论，第 2 轮翻转为「继续沟通」
  const transient = buildSnap([
    { canvas: true, bounds: CANVAS_BOUNDS, parent: 0 },
    { text: NAME, bounds: [300, 119, 36, 20], parent: 0 },
  ])
  const r = makeDeps([
    detailSnap({ name: NAME, state: 'greet' }),
    transient,
    detailSnap({ name: NAME, state: 'continue' }),
    closedListSnap(),
  ])
  const out = await new DetailGreetExecutor(r.deps).greet({ name: NAME })
  assert.deepEqual(out, { greeted: true })
  assert.equal(r.clicks.length, 1)
  assert.equal(r.escapeCount(), 1)
})

test('翻转轮询全为定位失败（点击后页面异常）→ 统一按 unknown fail-loud，不重试', async () => {
  const open = detailSnap({ name: NAME, state: 'greet' })
  const broken = buildSnap([
    { canvas: true, bounds: CANVAS_BOUNDS, parent: 0 },
    { text: NAME, bounds: [300, 119, 36, 20], parent: 0 },
  ])
  const r = makeDeps([open, broken, broken, broken]) // 3 轮全定位失败
  await assert.rejects(new DetailGreetExecutor(r.deps).greet({ name: NAME }), (e: unknown) => {
    assert.ok(e instanceof DetailGreetError)
    assert.match(e.message, /未翻转/)
    assert.match(e.message, /不要重试/)
    return true
  })
  assert.equal(r.clicks.length, 1)
})

test('操作列缺失（无 收藏/举报/不合适）→ fail-loud「操作列未定位到」，零点击', async () => {
  // canvas + 头部姓名 + 一个挂根节点的「打招呼」（无操作列三文本）——LCA 无从谈起
  const snap = buildSnap([
    { canvas: true, bounds: CANVAS_BOUNDS, parent: 0 },
    { text: NAME, bounds: [300, 119, 36, 20], parent: 0 },
    { text: GREET_STRING, bounds: [1064, 148, 42, 16], parent: 0 },
  ])
  const r = makeDeps([snap])
  await assert.rejects(new DetailGreetExecutor(r.deps).greet({ name: NAME }), (e: unknown) => {
    assert.ok(e instanceof DetailGreetError)
    assert.match(e.message, /操作列未定位到/)
    return true
  })
  assert.equal(r.clicks.length, 0)
})

test('容器内多个「打招呼」（状态歧义）→ fail-loud，绝不盲点', async () => {
  const items: SnapItem[] = [
    { canvas: true, bounds: CANVAS_BOUNDS, parent: 0 },
    { text: NAME, bounds: [300, 119, 36, 20], parent: 0 },
    { bounds: [1040, 80, 120, 140], parent: 0 },
    { text: '收藏', bounds: [1044, 99, 32, 20], parent: 3 },
    { text: '举报', bounds: [1080, 99, 32, 20], parent: 3 },
    { text: '不合适', bounds: [1112, 99, 44, 20], parent: 3 },
    { text: GREET_STRING, bounds: [1064, 148, 42, 16], parent: 3 },
    { text: GREET_STRING, bounds: [1064, 170, 42, 16], parent: 3 }, // 容器内第二个「打招呼」（异常结构）
  ]
  const r = makeDeps([buildSnap(items)])
  await assert.rejects(new DetailGreetExecutor(r.deps).greet({ name: NAME }), /状态不明确/)
  assert.equal(r.clicks.length, 0)
})

// ---------- close（boss_close_detail） ----------

test('close 幂等：详情本来就没开 → 直接成功返回，零 Escape', async () => {
  const r = makeDeps([closedListSnap()])
  const out = await new DetailGreetExecutor(r.deps).close()
  assert.deepEqual(out, { wasOpen: false })
  assert.equal(r.escapeCount(), 0)
})

test('close 真关：详情打开 → Escape 后 canvas 消失 → 成功', async () => {
  const r = makeDeps([detailSnap({ name: NAME, state: 'greet' }), closedListSnap()])
  const out = await new DetailGreetExecutor(r.deps).close()
  assert.deepEqual(out, { wasOpen: true })
  assert.equal(r.escapeCount(), 1)
})

test('close 关不掉（Escape 后 canvas 仍在）→ fail-loud', async () => {
  const open = detailSnap({ name: NAME, state: 'greet' })
  const r = makeDeps(Array(12).fill(open)) // 轮询全命中 canvas
  await assert.rejects(new DetailGreetExecutor(r.deps).close(), (e: unknown) => {
    assert.ok(e instanceof DetailGreetError)
    assert.match(e.message, /未能关闭/)
    return true
  })
  assert.equal(r.escapeCount(), 1)
})

// ---------- openDetail（boss_open_detail 的找人+点开+校验） ----------

test('open-detail：当前屏找到目标 → 点击姓名节点中心 → canvas 出现 → 姓名匹配 → 成功', async () => {
  // 目标行在 y=514（姓名中心 (342,506) 在 canvas 顶区之外——打开后顶区只命中头部姓名，无同名歧义）
  const list = listSnap([
    { name: '张三丰', buttonY: 146 },
    { name: NAME, buttonY: 514 },
  ])
  const open = detailSnap({ name: NAME, state: 'greet' })
  const r = makeDeps([list, list, open])
  const out = await new DetailGreetExecutor(r.deps).openDetail({ name: NAME })
  assert.deepEqual(out, { name: NAME })
  // 只点了目标行姓名节点中心 (342,506)，不是按钮、不是顶部张三丰
  assert.deepEqual(r.clicks, [{ x: 342, y: 506 }])
})

test('open-detail 找不到人（无滚动能力）→ fail-loud 列出当前屏可见姓名', async () => {
  const list = listSnap([{ name: '张三丰', buttonY: 146 }])
  const r = makeDeps([list, list])
  await assert.rejects(new DetailGreetExecutor(r.deps).openDetail({ name: NAME }), (e: unknown) => {
    assert.ok(e instanceof DetailGreetError)
    assert.match(e.message, /未找到「刘草威」/)
    assert.match(e.message, /张三丰/)
    return true
  })
  assert.equal(r.clicks.length, 0)
})

test('open-detail 滚动到底仍找不到 → fail-loud 列出可见姓名（含一次滚不动重试）', async () => {
  const list = listSnap([{ name: '张三丰', buttonY: 146 }])
  const r = makeDeps(Array(6).fill(list), { withScroll: true })
  await assert.rejects(new DetailGreetExecutor(r.deps).openDetail({ name: NAME }), /滚动到列表底部/)
  // 到底判定含一次重试：滚 2 次；滚动点取视口中心（624,638）、距离 min(800, 视口半高)
  assert.equal(r.wheels.length, 2)
  assert.deepEqual(r.wheels[0], { x: 624, y: 638, deltaY: 638 })
  assert.equal(r.clicks.length, 0)
})

test('open-detail 点击后 canvas 未出现（点击被吞/无在线简历）→ fail-loud', async () => {
  const list = listSnap([{ name: NAME, buttonY: 146 }])
  const r = makeDeps(Array(12).fill(list)) // 打开轮询全无 canvas
  await assert.rejects(new DetailGreetExecutor(r.deps).openDetail({ name: NAME }), /未检出简历画布/)
  // 首击 + 第 5 次轮询时同点重击一次（ResumeBatchReader 同源：首击偶发被吞，重击即开）
  assert.equal(r.clicks.length, 2)
})

test('open-detail 起点防残留：带着已打开的详情 → 先 Escape 关闭再找人点开', async () => {
  // 快照序：残留打开态（别人「李四」的详情）→ Escape 后列表 → 找到目标 → 打开目标详情
  const list = listSnap([{ name: NAME, buttonY: 146 }])
  const open = detailSnap({ name: NAME, state: 'greet' })
  const r = makeDeps([detailSnap({ name: '李四', state: 'greet' }), list, list, open])
  const out = await new DetailGreetExecutor(r.deps).openDetail({ name: NAME })
  assert.deepEqual(out, { name: NAME })
  // 第一次 Escape 是关残留弹层；点击是目标姓名节点 (342,138)
  assert.equal(r.escapeCount(), 1)
  assert.deepEqual(r.clicks, [{ x: 342, y: 138 }])
})

// 视口常量回归（fixture 与真机探查一致）
test('fixture 视口与真机探查一致', () => {
  assert.deepEqual(VIEWPORT, { width: 1249, height: 1277 })
})
