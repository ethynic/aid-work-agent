/**
 * operations 参数校验 fail-fast + 错误映射全路径 + effect/retryable 语义。
 *
 * 用 fake BossSessionFactory 注入替身（不连 Chrome）：参数校验失败时工厂不得被调用；
 * 执行期错误经 errorMapping 映射为稳定 code/effect/retryable。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { mapExecutorError } from '../src/main/operations/errorMapping.js'
import { CancelledError, CodedOperationError } from '../src/main/operations/types.js'
import { createBossFilterOperation, createBossClearFilterOperation } from '../src/main/operations/bossFilter.js'
import { createBossGotoOperation } from '../src/main/operations/bossGoto.js'
import { createBossGreetOperation } from '../src/main/operations/bossGreet.js'
import { createBossAcceptResumeOperation } from '../src/main/operations/bossAcceptResume.js'
import { createBossRejectCurrentOperation } from '../src/main/operations/bossRejectCurrent.js'
import { createBossInterviewDemoOperation } from '../src/main/operations/bossInterviewDemo.js'
import { createBossResumeDetailOperation } from '../src/main/operations/bossResumeDetail.js'
import { createBossResumeBatchOperation } from '../src/main/operations/bossResumeBatch.js'
import type { BossSession } from '../src/main/operations/bossContext.js'
import type { OpContext } from '../src/main/operations/types.js'
import type { DomSnapshot, ClickPoint } from '../src/main/boss/domSnapshot.js'
import { FilterSetError } from '../src/main/boss/FilterSetter.js'
import { GreetError } from '../src/main/boss/GreetExecutor.js'
import { ConsentError } from '../src/main/boss/ResumeConsentExecutor.js'
import { NavError } from '../src/main/boss/PageNavigator.js'
import { ChatRejectError } from '../src/main/boss/ChatRejectExecutor.js'
import { InterviewDemoError } from '../src/main/boss/InterviewDemoExecutor.js'
import { JobSwitchError } from '../src/main/boss/JobSwitcher.js'
import { ResumeReadError } from '../src/main/boss/ResumeReader.js'
import { ResumeBatchError } from '../src/main/boss/ResumeBatchReader.js'
import { WinClickError } from '../src/main/input/WinMouseClicker.js'

function silentCtx(): OpContext {
  return { signal: new AbortController().signal, progress: () => {} }
}

/** 记录调用次数的 fake 工厂：返回脚本化 session */
function fakeFactory(snaps: DomSnapshot[], opts: { url?: string; throwOnCreate?: unknown } = {}) {
  const calls: string[] = []
  const clicks: ClickPoint[] = []
  let i = 0
  const session: BossSession = {
    snapshot: async () => snaps[Math.min(i++, Math.max(snaps.length - 1, 0))]!,
    click: async (p) => {
      clicks.push(p)
    },
    clickBrowse: async () => {},
    mouseWheel: async () => {},
    pressEscape: async () => {},
    typeChar: async () => {},
    captureFullpage: async () => Buffer.alloc(0),
    getUrl: async () => opts.url ?? 'https://www.zhipin.com/web/chat/recommend',
    close: async () => {},
  }
  const factory = async () => {
    calls.push('create')
    if (opts.throwOnCreate) throw opts.throwOnCreate
    return session
  }
  return { factory, calls, clicks, session }
}

/** 空 snapshot（只有根节点 + 给定文案） */
function snapWithTexts(texts: string[]): DomSnapshot {
  const idx = [0, ...texts.map((_, i) => i + 1)]
  return {
    strings: ['', ...texts],
    documents: [
      {
        nodes: {
          nodeValue: { index: idx, value: [0, ...texts.map((_, i) => i + 1)] },
          contentDocumentIndex: { index: [], value: [] },
        },
        layout: {
          nodeIndex: idx,
          bounds: [[0, 0, 1917, 1905], ...texts.map((): [number, number, number, number] => [100, 200, 50, 20])],
        },
      },
    ],
  }
}

/** greet 用 snapshot：根 + 「筛选」+ N 个「打招呼」按钮 + 附加文案（相同文案共享 string 下标） */
function greetSnap(buttons: Array<[number, number, number, number]>, extraTexts: string[] = []): DomSnapshot {
  const nodeCount = 2 + buttons.length + extraTexts.length
  const idx = Array.from({ length: nodeCount }, (_, i) => i)
  const values = [0, 1, ...buttons.map(() => 2), ...extraTexts.map((_, i) => 3 + i)]
  const bounds: Array<[number, number, number, number]> = [
    [0, 0, 1917, 1905],
    [100, 100, 50, 20],
    ...buttons,
    ...extraTexts.map((): [number, number, number, number] => [100, 300, 50, 20]),
  ]
  return {
    strings: ['', '筛选', '打招呼', ...extraTexts],
    documents: [
      {
        nodes: {
          nodeValue: { index: idx, value: values },
          contentDocumentIndex: { index: [], value: [] },
        },
        layout: { nodeIndex: idx, bounds },
      },
    ],
  }
}

/** resume-batch 用 snapshot：根 + 文本节点 + 大 CANVAS 元素（沟通页简历预览 / 推荐页残留详情弹层态） */
function snapWithCanvas(texts: string[]): DomSnapshot {
  const canvasSi = texts.length + 1
  const nodeCount = 1 + texts.length + 1
  const idx = Array.from({ length: nodeCount }, (_, i) => i)
  return {
    strings: ['', ...texts, 'CANVAS'],
    documents: [
      {
        nodes: {
          nodeValue: { index: idx, value: [0, ...texts.map((_t, i) => i + 1), 0] },
          nodeName: { index: [nodeCount - 1], value: [canvasSi] },
          contentDocumentIndex: { index: [], value: [] },
        },
        layout: {
          nodeIndex: idx,
          bounds: [
            [0, 0, 1917, 1905],
            ...texts.map((): [number, number, number, number] => [100, 200, 50, 20]),
            [168, 40, 760, 1264],
          ],
        },
      },
    ],
  }
}

// ---------- 参数校验 fail-fast（不连 Chrome） ----------

test('boss_filter：无筛选条件 → INVALID_ARGUMENT，且不触达 Chrome', async () => {
  const f = fakeFactory([])
  const op = createBossFilterOperation(f.factory)
  const r = await op.execute({}, silentCtx())
  assert.equal(r.success, false)
  assert.equal(r.code, 'INVALID_ARGUMENT')
  assert.equal(r.effect, 'none')
  assert.equal(r.retryable, false)
  assert.ok(r.run_id.length > 0)
  assert.equal(f.calls.length, 0)
})

test('boss_filter：educations 含空串 → INVALID_ARGUMENT', async () => {
  const f = fakeFactory([])
  const op = createBossFilterOperation(f.factory)
  const r = await op.execute({ educations: ['本科', '  '] }, silentCtx())
  assert.equal(r.code, 'INVALID_ARGUMENT')
  assert.equal(f.calls.length, 0)
})

test('boss_goto：非法 target → INVALID_ARGUMENT', async () => {
  const f = fakeFactory([])
  const op = createBossGotoOperation(f.factory)
  // @ts-expect-error 故意传非法值模拟 Host 侧绕过 schema
  const r = await op.execute({ target: 'nope' }, silentCtx())
  assert.equal(r.code, 'INVALID_ARGUMENT')
  assert.equal(f.calls.length, 0)
})

test('boss_greet：limit 超上限/非整数 → INVALID_ARGUMENT', async () => {
  const f = fakeFactory([])
  const op = createBossGreetOperation(f.factory)
  for (const limit of [0, 101, 1.5, -3]) {
    const r = await op.execute({ limit }, silentCtx())
    assert.equal(r.code, 'INVALID_ARGUMENT', `limit=${limit}`)
    assert.equal(f.calls.length, 0)
  }
})

test('boss_accept_resume：limit 非法 → INVALID_ARGUMENT', async () => {
  const f = fakeFactory([])
  const op = createBossAcceptResumeOperation(f.factory)
  const r = await op.execute({ limit: 200 }, silentCtx())
  assert.equal(r.code, 'INVALID_ARGUMENT')
  assert.equal(f.calls.length, 0)
})

test('boss_reject_current / boss_clear_filter：无参数校验，正常进入工厂', async () => {
  const f1 = fakeFactory([], { throwOnCreate: new Error('fetch failed') })
  const r1 = await createBossRejectCurrentOperation(f1.factory).execute({}, silentCtx())
  assert.equal(r1.code, 'CHROME_UNAVAILABLE')
  assert.equal(f1.calls.length, 1)
  const f2 = fakeFactory([], { throwOnCreate: new Error('fetch failed') })
  const r2 = await createBossClearFilterOperation(f2.factory).execute({}, silentCtx())
  assert.equal(r2.code, 'CHROME_UNAVAILABLE')
  assert.equal(f2.calls.length, 1)
})

test('boss_interview_demo：remark 为空/超 140 字 → INVALID_ARGUMENT', async () => {
  const f = fakeFactory([])
  const op = createBossInterviewDemoOperation(f.factory)
  assert.equal((await op.execute({ remark: '   ' }, silentCtx())).code, 'INVALID_ARGUMENT')
  assert.equal((await op.execute({ remark: 'x'.repeat(141) }, silentCtx())).code, 'INVALID_ARGUMENT')
  assert.equal(f.calls.length, 0)
})

test('boss_resume_detail：save_image_to 空白 → INVALID_ARGUMENT，不连 Chrome', async () => {
  const f = fakeFactory([])
  const op = createBossResumeDetailOperation(f.factory)
  const r = await op.execute({ save_image_to: '   ' }, silentCtx())
  assert.equal(r.code, 'INVALID_ARGUMENT')
  assert.equal(f.calls.length, 0)
})

test('boss_resume_detail：未打开简历详情（无大 canvas）→ WRONG_PAGE，不滚不截', async () => {
  const f = fakeFactory([snapWithTexts(['沟通', '消息'])])
  const op = createBossResumeDetailOperation(f.factory)
  const r = await op.execute({}, silentCtx())
  assert.equal(r.success, false)
  assert.equal(r.code, 'WRONG_PAGE')
  assert.equal(r.retryable, true)
  assert.equal(r.effect, 'none')
  assert.match(r.message, /未找到简历画布/)
  assert.equal(f.calls.length, 1) // 工厂已连上（前置校验在 body 内），但 wheel/截图未被触达
})

test('boss_resume_batch：limit 非法（0/11/1.5）→ INVALID_ARGUMENT，不连 Chrome', async () => {
  const f = fakeFactory([])
  const op = createBossResumeBatchOperation(f.factory)
  for (const limit of [0, 11, 1.5, -3]) {
    const r = await op.execute({ limit }, silentCtx())
    assert.equal(r.code, 'INVALID_ARGUMENT', `limit=${limit}`)
    assert.equal(f.calls.length, 0)
  }
})

test('boss_resume_batch：save_dir 空白 → INVALID_ARGUMENT，不连 Chrome', async () => {
  const f = fakeFactory([])
  const op = createBossResumeBatchOperation(f.factory)
  const r = await op.execute({ save_dir: '   ' }, silentCtx())
  assert.equal(r.code, 'INVALID_ARGUMENT')
  assert.equal(f.calls.length, 0)
})

test('boss_resume_batch：非推荐页且无简历详情（无筛选无 canvas）→ WRONG_PAGE', async () => {
  const f = fakeFactory([snapWithTexts(['沟通', '消息'])])
  const op = createBossResumeBatchOperation(f.factory)
  const r = await op.execute({ limit: 1 }, silentCtx())
  assert.equal(r.success, false)
  assert.equal(r.code, 'WRONG_PAGE')
  assert.equal(r.retryable, true)
  assert.equal(r.effect, 'none')
  assert.match(r.message, /推荐牛人列表页/)
  assert.match(r.message, /boss_goto/)
  assert.equal(f.calls.length, 1) // 前置校验在 body 内，工厂已连上，但未点击任何卡片
})

test('boss_resume_batch：非推荐页但有简历 canvas（沟通页简历预览）→ 仍 WRONG_PAGE（canvas 不豁免）', async () => {
  const f = fakeFactory([snapWithCanvas(['沟通', '消息'])])
  const op = createBossResumeBatchOperation(f.factory)
  const r = await op.execute({ limit: 1 }, silentCtx())
  assert.equal(r.success, false)
  assert.equal(r.code, 'WRONG_PAGE')
  assert.match(r.message, /boss_goto/)
  assert.equal(f.clicks.length, 0)
})

// ---------- operation 执行期错误映射 ----------

test('connect 失败（ECONNREFUSED cause）→ CHROME_UNAVAILABLE，retryable=true', async () => {
  const err = new TypeError('fetch failed', { cause: Object.assign(new Error('connect ECONNREFUSED'), { code: 'ECONNREFUSED' }) })
  const f = fakeFactory([], { throwOnCreate: err })
  const r = await createBossGotoOperation(f.factory).execute({ target: 'chat' }, silentCtx())
  assert.equal(r.success, false)
  assert.equal(r.code, 'CHROME_UNAVAILABLE')
  assert.equal(r.retryable, true)
  assert.equal(r.effect, 'none')
})

test('greet 前置校验非推荐页 → WRONG_PAGE，retryable=true', async () => {
  const f = fakeFactory([snapWithTexts(['沟通', '消息'])])
  const r = await createBossGreetOperation(f.factory).execute({ limit: 1 }, silentCtx())
  assert.equal(r.success, false)
  assert.equal(r.code, 'WRONG_PAGE')
  assert.equal(r.retryable, true)
  assert.equal(r.effect, 'none')
})

test('greet 付费墙（未成功任何人）→ PAYWALL，effect=none，retryable=false', async () => {
  // probe → locate(1btn) → 点完出现付费墙弹层
  const f = fakeFactory([
    greetSnap([[1690, 208, 64, 32]]),
    greetSnap([[1690, 208, 64, 32]]),
    greetSnap([[1690, 208, 64, 32]], ['该职位无开聊权益']),
  ])
  const r = await createBossGreetOperation(f.factory).execute({ limit: 1 }, silentCtx())
  assert.equal(r.success, false)
  assert.equal(r.code, 'PAYWALL')
  assert.equal(r.effect, 'none')
  assert.equal(r.retryable, false)
})

test('greet 付费墙（已成功 1 人后触发）→ PAYWALL，effect=partial', async () => {
  const BTN1: [number, number, number, number] = [1690, 208, 64, 32]
  const BTN2: [number, number, number, number] = [1690, 484, 64, 32]
  // probe → locate(2btn) → 点完剩 1 → locate(1btn) → 点完付费墙
  const f = fakeFactory([
    greetSnap([BTN1, BTN2]),
    greetSnap([BTN1, BTN2]),
    greetSnap([BTN2]),
    greetSnap([BTN2]),
    greetSnap([BTN2], ['该职位无开聊权益']),
  ])
  const r = await createBossGreetOperation(f.factory).execute({ limit: 3 }, silentCtx())
  assert.equal(r.success, false)
  assert.equal(r.code, 'PAYWALL')
  assert.equal(r.effect, 'partial')
  assert.equal(r.retryable, false)
  assert.equal(r.data.completed, 1)
})

test('greet 成功路径：effect=applied，data 含 greeted/reached_end', async () => {
  const BTN1: [number, number, number, number] = [1690, 208, 64, 32]
  // probe → locate(1btn) → 点完无按钮 → 滚动探测(2 次签名不变=到底)
  const f = fakeFactory([
    greetSnap([BTN1]),
    greetSnap([BTN1]),
    greetSnap([]),
    greetSnap([]),
    greetSnap([]),
  ])
  const r = await createBossGreetOperation(f.factory).execute({ limit: 5 }, silentCtx())
  assert.equal(r.success, true)
  assert.equal(r.code, 'OK')
  assert.equal(r.effect, 'applied')
  assert.equal(r.data.greeted, 1)
  assert.equal(r.data.reached_end, true)
  assert.ok(r.run_id.length > 0)
})

/** 定向 greet 用 snapshot：根视口 1917x1905 + 「筛选」+ 卡片行（姓名/活跃状态/按钮，真机锚定坐标） */
function greetNamedSnap(rows: Array<{ name: string | null; buttonY: number }>): DomSnapshot {
  // node 0 根 / node 1 = 「筛选」（probe 前置校验用），其余为卡片行文本节点
  const strings: string[] = ['', '筛选']
  const nvIndex: number[] = [0, 1]
  const nvValue: number[] = [0, 1]
  const layoutNodeIndex: number[] = [0, 1]
  const layoutBounds: Array<[number, number, number, number]> = [[0, 0, 1917, 1905], [100, 100, 50, 20]]
  const intern = (s: string): number => {
    let i = strings.indexOf(s)
    if (i < 0) {
      strings.push(s)
      i = strings.length - 1
    }
    return i
  }
  let nextNi = 2
  const addText = (s: string, bounds: [number, number, number, number]): void => {
    const ni = nextNi++
    nvIndex.push(ni)
    nvValue.push(intern(s))
    layoutNodeIndex.push(ni)
    layoutBounds.push(bounds)
  }
  for (const row of rows) {
    if (row.name !== null) addText(row.name, [317, row.buttonY - 18, 50, 20]) // 中心 (342, y-8)
    addText('刚刚活跃', [370, row.buttonY - 18, 60, 20]) // 中心 (400, y-8)
    addText('本科', [317, row.buttonY + 30, 40, 20]) // 噪音
    addText('\n                  打招呼', [1130, row.buttonY - 16, 64, 32]) // 中心 (1162, y)
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
      },
    ],
  }
}

test('boss_greet：names 非法（空数组/超 3 个/含空串）→ INVALID_ARGUMENT，不连 Chrome', async () => {
  const f = fakeFactory([])
  const op = createBossGreetOperation(f.factory)
  assert.equal((await op.execute({ names: [] }, silentCtx())).code, 'INVALID_ARGUMENT')
  assert.equal((await op.execute({ names: ['刘草威', '张三丰', '王五', '赵六'] }, silentCtx())).code, 'INVALID_ARGUMENT')
  assert.equal((await op.execute({ names: ['刘草威', '  '] }, silentCtx())).code, 'INVALID_ARGUMENT')
  assert.equal(f.calls.length, 0)
})

test('greet 定向成功：names=[张三丰] 只点张三丰的按钮，data 含 greeted_names/missing_names', async () => {
  const A = { name: '刘草威', buttonY: 146 }
  const B = { name: '张三丰', buttonY: 330 }
  // probe（含筛选）→ 定位[A,B]（只点 B）→ 校验[A]（B 变继续沟通）→ 名单完成
  const f = fakeFactory([greetNamedSnap([A, B]), greetNamedSnap([A, B]), greetNamedSnap([A])])
  const r = await createBossGreetOperation(f.factory).execute({ names: ['张三丰'] }, silentCtx())
  assert.equal(r.success, true)
  assert.equal(r.effect, 'applied')
  // 只点了 B 行按钮 (1162, 330)：顶部的 A 被跳过（修复前会点 A——打错人）
  assert.deepEqual(f.clicks, [{ x: 1162, y: 330 }])
  assert.equal(r.data.greeted, 1)
  assert.deepEqual(r.data.greeted_names, ['张三丰'])
  assert.deepEqual(r.data.missing_names, [])
  assert.match(r.message, /张三丰/)
})

test('greet 定向：MCP 默认 limit=1 不截断名单（operation 钳到 ≥ names 数量，2 人全打）', async () => {
  const A = { name: '刘草威', buttonY: 146 }
  const B = { name: '张三丰', buttonY: 330 }
  // MCP schema 对省略的 limit 注入默认 1（zod .default(1)），SUBAGENT 链路只传 names——
  // 修复前 limit=1 + names 2 人会在打完第 1 人后截断，第 2 人误报进 missing_names。
  // probe → 定位[A,B] → 点 A → 校验[B] → 定位[B] → 点 B → 校验[] → 名单完成
  const f = fakeFactory([
    greetNamedSnap([A, B]),
    greetNamedSnap([A, B]),
    greetNamedSnap([B]),
    greetNamedSnap([B]),
    greetNamedSnap([]),
  ])
  const r = await createBossGreetOperation(f.factory).execute({ limit: 1, names: ['刘草威', '张三丰'] }, silentCtx())
  assert.equal(r.success, true)
  assert.equal(r.data.greeted, 2)
  assert.deepEqual(r.data.greeted_names, ['刘草威', '张三丰'])
  assert.deepEqual(r.data.missing_names, [])
  assert.deepEqual(f.clicks, [
    { x: 1162, y: 146 },
    { x: 1162, y: 330 },
  ])
})

test('greet 定向缺人：页面只有 A 但要找王五 → 0 人到底，missing_names=[王五]，message 如实说明', async () => {
  const A = { name: '刘草威', buttonY: 146 }
  // probe → 定位[A]（A 不在名单，无目标）→ 滚动两次 offset 均不变（0→0）→ 判到底返回
  const f = fakeFactory([greetNamedSnap([A]), greetNamedSnap([A])])
  const r = await createBossGreetOperation(f.factory).execute({ names: ['王五'] }, silentCtx())
  assert.equal(r.success, true)
  assert.equal(r.data.greeted, 0)
  assert.equal(r.data.reached_end, true)
  assert.deepEqual(r.data.greeted_names, [])
  assert.deepEqual(r.data.missing_names, ['王五'])
  assert.equal(f.clicks.length, 0)
  assert.match(r.message, /王五/)
  assert.match(r.message, /未找到/)
})

// ---------- errorMapping 全路径（每种 executor Error + 消息标记） ----------

test('errorMapping：取消与主动 code', () => {
  assert.equal(mapExecutorError(new CancelledError()).code, 'CANCELLED')
  assert.equal(mapExecutorError(new CodedOperationError('WRONG_PAGE', '非推荐页')).code, 'WRONG_PAGE')
})

test('errorMapping：Chrome 连接失败', () => {
  const withCause = new TypeError('fetch failed', { cause: Object.assign(new Error('x'), { code: 'ECONNREFUSED' }) })
  assert.equal(mapExecutorError(withCause).code, 'CHROME_UNAVAILABLE')
  assert.equal(mapExecutorError(new Error('fetch failed')).code, 'CHROME_UNAVAILABLE')
  assert.equal(mapExecutorError(new Error('CDP request timed out after 10000ms')).code, 'CHROME_UNAVAILABLE')
})

test('errorMapping：未登录/未打开 BOSS 页面', () => {
  assert.equal(mapExecutorError(new Error('no BOSS page target found among 5 targets')).code, 'NOT_LOGGED_IN')
  assert.equal(mapExecutorError(new NavError('页面上找不到菜单文案「沟通」（不在 DOM 文本表中），请确认已登录 BOSS')).code, 'NOT_LOGGED_IN')
})

test('errorMapping：付费墙与参数类', () => {
  assert.equal(mapExecutorError(new GreetError('第 1 个打招呼触发付费墙：当前职位无开聊权益')).code, 'PAYWALL')
  assert.equal(mapExecutorError(new FilterSetError('学历要求 行为单选，收到 2 个选项')).code, 'INVALID_ARGUMENT')
  assert.equal(mapExecutorError(new FilterSetError('未提供任何筛选条件')).code, 'INVALID_ARGUMENT')
})

test('errorMapping：写后校验失败 → EXECUTION_UNKNOWN', () => {
  const cases: Error[] = [
    new GreetError('第 1 个打招呼点击后按钮数未减少（2→2）'),
    new ConsentError('第 1 个「同意」点击后按钮未消失（1→1）'),
    new ConsentError('第 1 个同意后等待 4.5s 仍未出现「点击预览附件简历」按钮'),
    new ConsentError('第 1 个点击预览后弹层未打开'),
    new ConsentError('第 1 个预览弹层 Escape 后未关闭'),
    new FilterSetError('清除筛选校验失败：徽章仍为「筛选·2」（确定可能未生效）'),
    new ChatRejectError('确认后「不合适」按钮仍存在且会话未切换：标记结果无法确认'),
    new NavError('点击左侧菜单「沟通」后页面未跳转（当前 URL: xxx）'),
    new InterviewDemoError('备注逐字输入后字数计数器未显示 16'),
    new JobSwitchError('点击职位项后切换未生效（职位框仍为「PHP开发工程师」，期望「前端开发」），请人工查看页面'),
  ]
  for (const err of cases) {
    assert.equal(mapExecutorError(err).code, 'EXECUTION_UNKNOWN', err.message)
  }
})

test('errorMapping：结构变化/找不到元素 → UI_CHANGED', () => {
  const cases: Error[] = [
    new ConsentError('第 1 个会话打开后未找到「同意」处理条'),
    new NavError('左侧导航栏中找不到「沟通」菜单项（x<200 无命中），页面布局可能已变'),
    new FilterSetError('行标签「经验要求」必须恰好 1 个可见匹配，实际 0 个'),
    new ChatRejectError('当前页面右侧面板没有「不合适」按钮'),
    new InterviewDemoError('未找到唯一的「约面试」按钮'),
    new GreetError('某个结构错误'),
    new WinClickError('win-click.ps1 执行失败(exit=2): 落点被遮挡', 2),
    new JobSwitchError('职位「Java」不在当前招聘者的职位列表中（可用职位：PHP开发工程师、前端开发），请人工查看'),
    new JobSwitchError('打开职位下拉后未解析到任何职位项（项无 layout bounds 可能是等待不足），请人工查看'),
    new ResumeReadError('未找到简历详情画布（页面中无大尺寸 CANVAS）：请先在推荐牛人页点开一个候选人的在线简历详情，再执行读取'),
    new ResumeBatchError('当前视口未找到任何牛人卡片（无「打招呼」按钮）：请确认已打开推荐牛人列表页且列表已加载'),
  ]
  for (const err of cases) {
    assert.equal(mapExecutorError(err).code, 'UI_CHANGED', err.message)
  }
})

test('errorMapping：脚本缺失与兜底 → INTERNAL_ERROR', () => {
  assert.equal(mapExecutorError(new WinClickError('未找到 scripts/win-click.ps1（已从 x 向上探测）')).code, 'INTERNAL_ERROR')
  assert.equal(mapExecutorError(new Error('unexpected')).code, 'INTERNAL_ERROR')
  assert.equal(mapExecutorError('字符串错误').code, 'INTERNAL_ERROR')
})

test('写动作未预期异常（INTERNAL_ERROR）→ effect=unknown（规格 §3 兜底行：不可误报 none 让 Host 误以为无副作用）', async () => {
  const f = fakeFactory([snapWithTexts(['沟通'])])
  // snapshot 抛非 executor Error 的意外异常 → INTERNAL_ERROR；写动作是否落地不可知 → unknown
  f.session.snapshot = async () => {
    throw new Error('boom: unexpected snapshot failure')
  }
  const r = await createBossRejectCurrentOperation(f.factory).execute({}, silentCtx())
  assert.equal(r.success, false)
  assert.equal(r.code, 'INTERNAL_ERROR')
  assert.equal(r.effect, 'unknown')
  assert.equal(r.retryable, false)
})
