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
import { DetailGreetExecutor, DetailGreetError, DetailOpenPostconditionError, type DetailGreetDeps } from '../src/main/boss/DetailGreetExecutor.js'
import { WinClickError } from '../src/main/input/WinMouseClicker.js'
import { PagePostconditionError } from '../src/main/operations/pageOperation.js'
import type { ClickPoint, DomSnapshot } from '../src/main/boss/domSnapshot.js'
import {
  BAR_GREET_CENTER,
  buildSnap,
  CANVAS_BOUNDS,
  closedListSnap,
  detailSnap,
  GREET_STRING,
  listSnap,
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

/** Real shape: the resume iframe has only canvas; the overlapping name belongs to the background list. */
function canvasOnlyDetail(backgroundName = NAME) {
  const snap = detailSnap({ name: NAME, state: 'greet', canvasNameOnly: true,
    decoyRows: [{ name: backgroundName, buttonY: 146 }] })
  const doc = snap.documents[0]!, tags = doc.nodes.nodeName as { index: number[]; value: number[] }
  tags.value[tags.index.indexOf(1)] = snap.strings.push('IFRAME') - 1
  doc.nodes.contentDocumentIndex = { index: [1], value: [1] }
  snap.documents.push({ nodes: { nodeValue: [-1, -1], nodeName: [-1, snap.strings.indexOf('CANVAS')],
    parentIndex: [-1, 0], contentDocumentIndex: [] },
    layout: { nodeIndex: [0, 1], bounds: [[0, 0, 760, 1264], [0, 0, 760, 1264]] } })
  return snap
}

function causalDeps(opts: { duplicate?: boolean; noFlip?: boolean; blockOpen?: boolean; initialTitle?: boolean } = {}) {
  let phase = 'initial', clicks = 0, greets = 0, escapes = 0
  const detail = canvasOnlyDetail()
  const list = listSnap([{ name: NAME, buttonY: 514 }, ...(opts.duplicate ? [{ name: NAME, buttonY: 698 }] : [])])
  const flipped = detailSnap({ name: NAME, state: 'continue', canvasNameOnly: true, decoyRows: [] })
  const deps: DetailGreetDeps = {
    snapshot: async () => phase === 'initial' ? opts.initialTitle
      ? detailSnap({ name: '简历标题', state: 'greet', decoyRows: [] }) : detail
      : phase === 'list' ? list : phase === 'flipped' ? flipped : phase === 'closed' ? closedListSnap() : detail,
    click: async (point) => { clicks++; if (point.x < 900) { if (!opts.blockOpen) phase = 'opened' }
      else { greets++; if (!opts.noFlip) phase = 'flipped' } },
    pressEscape: async () => { escapes++; phase = phase === 'initial' || phase === 'opened' ? 'list' : 'closed' },
    sleep: async () => {},
  }
  return { deps, counts: () => ({ clicks, greets, escapes }) }
}

// ---------- greet：容器消歧 + 点击 + 翻转 + 关闭 ----------

test('正常链路：姓名校验 → 容器内定位 → 点击 → 翻转 → Escape 关闭；点击点是容器内按钮中心而非列表诱饵', async () => {
  // 快照序列：打开态（容器内=打招呼 + 2 个容器外诱饵）→ 点击后翻转（容器内=继续沟通）→ Escape 后列表态
  const r = makeDeps([detailSnap({ name: NAME, state: 'greet' }), detailSnap({ name: NAME, state: 'continue' }), detailSnap({ name: NAME, state: 'continue' }), closedListSnap()])
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
    detailSnap({ name: NAME, state: 'continue', decoyRows: decoys }),
    closedListSnap(),
  ])
  const out = await new DetailGreetExecutor(r.deps).greet({ name: NAME })
  assert.equal(out.greeted, true)
  assert.deepEqual(r.clicks, [BAR_GREET_CENTER])
})

test('alreadyGreeted 幂等：容器内已是「继续沟通」→ 不点击直接关闭', async () => {
  const r = makeDeps([detailSnap({ name: NAME, state: 'continue' }), detailSnap({ name: NAME, state: 'continue' }), closedListSnap()])
  const out = await new DetailGreetExecutor(r.deps).greet({ name: NAME })
  assert.deepEqual(out, { greeted: false, already: true })
  assert.equal(r.clicks.length, 0)
  assert.equal(r.escapeCount(), 1)
})

test('没有可确认归属且旧详情无法关闭：零打招呼，不把其他顶部文本猜成目标身份', async () => {
  const r = makeDeps([detailSnap({ name: '李四', state: 'greet' })])
  await assert.rejects(new DetailGreetExecutor(r.deps).greet({ name: NAME }), (e: unknown) => {
    assert.ok(e instanceof DetailGreetError)
    assert.match(e.message, /未能关闭/)
    return true
  })
  assert.equal(r.clicks.length, 0)
  assert.equal(r.escapeCount(), 1)
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

test('canvas-only姓名：背景同名不能直接授权，dry-run复用清残留及唯一原卡片重开而不外发', async () => {
  const h = causalDeps(), executor = new DetailGreetExecutor(h.deps)
  assert.throws(() => DetailGreetExecutor.verifyDetailName(canvasOnlyDetail(), NAME), /姓名与预期不符/)
  const result = await executor.greet({ name: NAME, dryRun: true })
  assert.equal(result.dryRun, true)
  assert.deepEqual(h.counts(), { clicks: 1, greets: 0, escapes: 1 })
  assert.equal(executor.state.execution.inputStatus, 'attempted')
  assert.equal(executor.state.businessEffect.status, 'not_started')
  assert.equal(executor.state.execution.ordinaryRetriesUsed, 0)
})

test('其他顶部文本不能当作姓名冲突；通过本次唯一原卡片因果后仅提交一次', async () => {
  const h = causalDeps({ initialTitle: true }), executor = new DetailGreetExecutor(h.deps)
  assert.equal((await executor.greet({ name: NAME })).greeted, true)
  assert.deepEqual(h.counts(), { clicks: 2, greets: 1, escapes: 2 })
  assert.equal(executor.state.businessEffect.completed_count, 1)
})

test('身份重建首轮也要求原姓名卡片唯一，多名只关闭残留而不点任何卡片或外发', async () => {
  const h = causalDeps({ duplicate: true })
  await assert.rejects(new DetailGreetExecutor(h.deps).greet({ name: NAME }), /姓名不唯一/)
  assert.deepEqual(h.counts(), { clicks: 0, greets: 0, escapes: 1 })
})

test('身份重建准备输入直接报错不消耗普通重试，也不尝试打招呼', async () => {
  const h = causalDeps(), executor = new DetailGreetExecutor({ ...h.deps,
    click: async () => { throw new WinClickError('guard rejected', 2) } })
  await assert.rejects(executor.greet({ name: NAME }), /guard rejected/)
  assert.equal(executor.state.execution.ordinaryRetriesUsed, 0)
  assert.equal(executor.state.businessEffect.status, 'not_started')
  assert.equal(h.counts().greets, 0)
})

test('身份重建的正常失败只消费共用一次重试，不额外叠加open预算', async () => {
  const h = causalDeps({ blockOpen: true }), executor = new DetailGreetExecutor(h.deps)
  await assert.rejects(executor.greet({ name: NAME }), /一次普通重试已用尽/)
  assert.equal(h.counts().clicks, 2); assert.equal(h.counts().greets, 0)
  assert.equal(executor.state.execution.ordinaryRetriesUsed, 1)
  assert.equal(executor.state.businessEffect.status, 'not_started')
})

test('此前已消耗普通重试时身份重建不能再点击第二次', async () => {
  const h = causalDeps({ blockOpen: true }), executor = new DetailGreetExecutor(h.deps)
  executor.state.retry()
  await assert.rejects(executor.greet({ name: NAME }), /一次普通重试已用尽/)
  assert.equal(h.counts().clicks, 1); assert.equal(h.counts().greets, 0)
  assert.equal(executor.state.execution.ordinaryRetriesUsed, 1)
})

test('本次身份重建后最终外发unknown不重放，重复调用也不能重开并再发', async () => {
  const h = causalDeps({ noFlip: true }), executor = new DetailGreetExecutor(h.deps)
  await assert.rejects(executor.greet({ name: NAME }), /不要重试/)
  assert.equal(h.counts().greets, 1)
  const counts = h.counts()
  await assert.rejects(executor.greet({ name: NAME }), /禁止重新打开或再次发送/)
  assert.deepEqual(h.counts(), counts)
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
    assert.ok(e instanceof PagePostconditionError)
    assert.equal(e.verification.status, 'unmet')
    assert.match(e.message, /画布仍存在/)
    return true
  })
  assert.equal(r.escapeCount(), 1)
})

// ---------- openDetail（boss_open_detail 的找人+点开+校验） ----------

test('open-detail：当前屏找到目标 → 点击姓名节点中心 → 画布及右侧详情锚点出现 → 成功', async () => {
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

test('open-detail：原卡片已是继续沟通时仍可按同一姓名唯一打开详情', async () => {
  const list = listSnap([{ name: NAME, buttonY: 514 }])
  list.strings = list.strings.map((text) => text === GREET_STRING ? '继续沟通' : text)
  const opened = detailSnap({ name: NAME, state: 'continue', canvasNameOnly: true })
  const h = makeDeps([list, list, opened]), executor = new DetailGreetExecutor(h.deps)
  assert.deepEqual(await executor.openDetail({ name: NAME }), { name: NAME })
  assert.deepEqual(h.clicks, [{ x: 342, y: 506 }])
})

for (const state of ['greet', 'continue'] as const) {
  test(`open-detail：姓名只在canvas内，右侧${state}按钮及经历概览出现即可确认打开`, async () => {
    const list = listSnap([{ name: NAME, buttonY: 514 }])
    const open = detailSnap({ name: NAME, state, canvasNameOnly: true })
    const r = makeDeps([list, list, open])
    assert.deepEqual(await new DetailGreetExecutor(r.deps).openDetail({ name: NAME }), { name: NAME })
    assert.equal(r.clicks.length, 1)
  })
}

test('open-detail：只有画布与按钮、没有经历概览不能误报完整详情，残留未关闭不能重击背景', async () => {
  const list = listSnap([{ name: NAME, buttonY: 514 }])
  const partial = detailSnap({ name: NAME, state: 'greet', canvasNameOnly: true, showOverview: false })
  const r = makeDeps([list, list, partial])
  await assert.rejects(new DetailGreetExecutor(r.deps).openDetail({ name: NAME }), /未检出完整简历详情/)
  assert.equal(r.clicks.length, 1)
})

test('open-detail：列表打招呼诱饵不能代替详情操作列按钮', async () => {
  const list = listSnap([{ name: NAME, buttonY: 514 }])
  const partial = detailSnap({ name: NAME, state: 'missing', canvasNameOnly: true })
  const r = makeDeps([list, list, partial])
  await assert.rejects(new DetailGreetExecutor(r.deps).openDetail({ name: NAME }), /未检出完整简历详情/)
  assert.equal(r.clicks.length, 1)
})

test('open-detail：画布左侧同名经历概览不能冒充右侧详情标题', async () => {
  const list = listSnap([{ name: NAME, buttonY: 514 }])
  const partial = detailSnap({ name: NAME, state: 'greet', canvasNameOnly: true, showOverview: false,
    extraItems: [{ text: '经历概览', bounds: [300, 250, 64, 20], parent: 0 }] })
  const r = makeDeps([list, list, partial])
  await assert.rejects(new DetailGreetExecutor(r.deps).openDetail({ name: NAME }), /未检出完整简历详情/)
})

test('open-detail：画布先出现，右侧锚点延迟出现时继续轮询，不重击背后的卡片', async () => {
  const list = listSnap([{ name: NAME, buttonY: 514 }])
  const partial = detailSnap({ name: NAME, state: 'greet', canvasNameOnly: true, showOverview: false })
  const open = detailSnap({ name: NAME, state: 'greet', canvasNameOnly: true })
  const r = makeDeps([list, list, ...Array(7).fill(partial), open])
  assert.deepEqual(await new DetailGreetExecutor(r.deps).openDetail({ name: NAME }), { name: NAME })
  assert.equal(r.clicks.length, 1)
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
  await assert.rejects(new DetailGreetExecutor(r.deps).openDetail({ name: NAME }), /未检出完整简历详情/)
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

test('open-detail：右栏持续不完整时先关闭残留，再fresh点击原姓名的新位置，普通重试只用一次', async () => {
  const list = listSnap([{ name: NAME, buttonY: 514 }])
  const moved = listSnap([{ name: NAME, buttonY: 698 }])
  const partial = detailSnap({ name: NAME, state: 'greet', canvasNameOnly: true, showOverview: false })
  const ready = detailSnap({ name: NAME, state: 'greet', canvasNameOnly: true })
  const clicks: ClickPoint[] = []
  let escapes = 0
  const executor = new DetailGreetExecutor({ snapshot: async () => clicks.length === 0 ? list : clicks.length === 2 ? ready : escapes ? moved : partial,
    click: async (point) => { clicks.push(point) }, pressEscape: async () => { escapes++ }, sleep: async () => {} })
  assert.deepEqual(await executor.openDetail({ name: NAME }), { name: NAME })
  assert.deepEqual(clicks, [{ x: 342, y: 506 }, { x: 342, y: 690 }])
  assert.equal(escapes, 1)
  const execution = executor.openDetailExecution
  assert.deepEqual({ ...execution, verification: execution.verification.status }, { attemptsUsed: 2, ordinaryRetriesUsed: 1, inputStatus: 'attempted', stage: 'postcheck', verification: 'met' })
  execution.verification.evidence.length = 0
  assert.ok(executor.openDetailExecution.verification.evidence.length > 0)
  assert.equal((await executor.verifyOpenDetail()).status, 'met')
  assert.equal(clicks.length, 2)
})

test('open-detail：不完整详情重试后仍不满足只抛typed后验，不再追加第三击', async () => {
  const list = listSnap([{ name: NAME, buttonY: 514 }])
  const partial = detailSnap({ name: NAME, state: 'greet', canvasNameOnly: true, showOverview: false })
  let clicks = 0
  let escapes = 0
  let closed = false
  const executor = new DetailGreetExecutor({
    snapshot: async () => clicks === 0 || closed ? list : partial,
    click: async () => { clicks++; closed = false }, pressEscape: async () => { escapes++; closed = true }, sleep: async () => {},
  })
  await assert.rejects(executor.openDetail({ name: NAME }), DetailOpenPostconditionError)
  assert.equal(clicks, 2)
  assert.equal(escapes, 1)
  assert.equal(executor.openDetailExecution.ordinaryRetriesUsed, 1)
  assert.equal(executor.openDetailExecution.verification.status, 'unmet')
})

test('open-detail：无画布2.5秒的原重击消耗同一重试预算，成功后不追加普通重试', async () => {
  const list = listSnap([{ name: NAME, buttonY: 514 }])
  const ready = detailSnap({ name: NAME, state: 'greet', canvasNameOnly: true })
  let clicks = 0
  let escapes = 0
  const sleeps: number[] = []
  const executor = new DetailGreetExecutor({ snapshot: async () => clicks < 2 ? list : ready,
    click: async () => { clicks++ }, pressEscape: async () => { escapes++ }, sleep: async (ms) => { sleeps.push(ms) } })
  await executor.openDetail({ name: NAME })
  assert.equal(clicks, 2)
  assert.equal(escapes, 0)
  assert.equal(sleeps.slice(0, 5).reduce((sum, ms) => sum + ms, 0), 2500)
  assert.equal(executor.openDetailExecution.attemptsUsed, 2)
  assert.equal(executor.openDetailExecution.ordinaryRetriesUsed, 1)
})

test('open-detail：点击响应抛错记录unknown且不重试，可通过本次因果只读确认已打开', async () => {
  const list = listSnap([{ name: NAME, buttonY: 514 }])
  const ready = detailSnap({ name: NAME, state: 'greet', canvasNameOnly: true })
  const lost = new Error('input response lost')
  let clicks = 0
  const executor = new DetailGreetExecutor({ snapshot: async () => clicks ? ready : list,
    click: async () => { clicks++; throw lost }, pressEscape: async () => {}, sleep: async () => {} })
  await assert.rejects(executor.openDetail({ name: NAME }), (error: unknown) => error === lost)
  assert.equal(executor.openDetailExecution.inputStatus, 'unknown')
  assert.equal(executor.openDetailExecution.stage, 'input')
  assert.equal(executor.openDetailExecution.ordinaryRetriesUsed, 0)
  assert.equal(executor.openDetailExecution.verification.status, 'inconclusive')
  assert.equal((await executor.verifyOpenDetail()).status, 'met')
  assert.equal(clicks, 1)
})

test('open-detail：Win32落点守卫exit2表示未输入，不能认领随后出现的任意详情', async () => {
  const list = listSnap([{ name: NAME, buttonY: 514 }])
  const ready = detailSnap({ name: '李四', state: 'greet', canvasNameOnly: true })
  const guard = new WinClickError('guard rejected', 2)
  let attempted = false
  const executor = new DetailGreetExecutor({ snapshot: async () => attempted ? ready : list,
    click: async () => { attempted = true; throw guard }, pressEscape: async () => {}, sleep: async () => {} })
  await assert.rejects(executor.openDetail({ name: NAME }), (error: unknown) => error === guard)
  assert.equal(executor.openDetailExecution.inputStatus, 'not_started')
  assert.equal(executor.openDetailExecution.ordinaryRetriesUsed, 0)
  assert.equal((await executor.verifyOpenDetail()).status, 'inconclusive')
})

test('open-detail：直接后验快照异常原样传播，保留attempted/postcheck而不做普通重试', async () => {
  const list = listSnap([{ name: NAME, buttonY: 514 }])
  const disconnected = new Error('snapshot disconnected')
  let clicks = 0
  const executor = new DetailGreetExecutor({ snapshot: async () => { if (clicks) throw disconnected; return list },
    click: async () => { clicks++ }, pressEscape: async () => {}, sleep: async () => {} })
  await assert.rejects(executor.openDetail({ name: NAME }), (error: unknown) => error === disconnected)
  const execution = executor.openDetailExecution
  assert.equal(execution.attemptsUsed, 1)
  assert.equal(execution.ordinaryRetriesUsed, 0)
  assert.equal(execution.inputStatus, 'attempted')
  assert.equal(execution.stage, 'postcheck')
  assert.equal(execution.verification.status, 'inconclusive')
})

test('open-detail：没有本次点击因果时，只读发现别人已开完整详情仍为inconclusive', async () => {
  const ready = detailSnap({ name: '李四', state: 'greet', canvasNameOnly: true })
  const executor = new DetailGreetExecutor({ snapshot: async () => ready, click: async () => assert.fail('不得点击'), pressEscape: async () => assert.fail('不得关闭') })
  assert.equal((await executor.verifyOpenDetail()).status, 'inconclusive')
  assert.equal(executor.openDetailExecution.attemptsUsed, 0)
})

test('open-detail：确定找不到原目标属于正常查找结果，不伪装typed恢复故障', async () => {
  const list = listSnap([{ name: '张三丰', buttonY: 514 }])
  const r = makeDeps([list])
  const executor = new DetailGreetExecutor(r.deps)
  await assert.rejects(executor.openDetail({ name: NAME }), (error: unknown) => error instanceof DetailGreetError && !(error instanceof DetailOpenPostconditionError))
  assert.equal(executor.openDetailExecution.attemptsUsed, 0)
  assert.equal(executor.openDetailExecution.inputStatus, 'not_started')
  assert.equal(executor.openDetailExecution.stage, 'prepared')
  assert.equal((await executor.verifyOpenDetail()).status, 'inconclusive')
})

test('open-detail：重试清理后原卡片消失，不回退旧坐标或别人的卡片', async () => {
  const list = listSnap([{ name: NAME, buttonY: 514 }])
  const other = listSnap([{ name: '李四', buttonY: 514 }])
  const partial = detailSnap({ name: NAME, state: 'greet', canvasNameOnly: true, showOverview: false })
  let clicks = 0
  let escaped = false
  const executor = new DetailGreetExecutor({ snapshot: async () => clicks ? escaped ? other : partial : list,
    click: async () => { clicks++ }, pressEscape: async () => { escaped = true }, sleep: async () => {} })
  await assert.rejects(executor.openDetail({ name: NAME }), /原候选人卡片不唯一或不可确认/)
  assert.equal(clicks, 1)
  assert.equal(executor.openDetailExecution.ordinaryRetriesUsed, 0) // retry preparation failed; no second input happened
  assert.equal((await executor.verifyOpenDetail()).status, 'inconclusive')
})
