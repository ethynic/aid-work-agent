/**
 * JobSwitcher 单测：职位切换链路（点职位框 → 等下拉渲染 → 解析/点职位项 → 校验）。
 * fake 注入 snapshot/click。
 *
 * 真机关键（2026-08-13）：点职位框后下拉异步渲染，需等 2500ms 项才有 layout bounds；
 * 下拉关闭时视口内仅 1 个职位格式节点（职位框）；打开时职位框 trigger **仍带 bounds** 显示当前职位，
 * 另有 N 个下拉项在下方（当前职位在下拉项里也高亮出现，与框同名）→ 共 N+1 个节点。真机 N=4。
 * 故 selectJob 排除职位框节点本身、只在下拉项里精确匹配（切到当前职位时框+项同名，不排除会命中 2 个）。
 * 解析遍历 nodes.nodeValue（所有节点），无 bounds 的文本节点跳过（=未渲染/等待不足）。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { JobSwitcher, JobSwitchError } from '../src/main/boss/JobSwitcher.js'
import type { DomSnapshot, ClickPoint } from '../src/main/boss/domSnapshot.js'

interface SnapItem {
  text: string
  bounds: [number, number, number, number]
}

/**
 * 构造 snapshot：根视口 1249x1277（doc0，owner 偏移 0）。
 * - filter：筛选按钮 bounds
 * - box：职位框 {text,bounds}（关闭态，与筛选同行左侧）
 * - items：职位项（打开态下拉项）
 * - pendingMarks：「待」徽章 bounds 数组（未开放职位项右侧；真机 x≈职位项+120-136，同 y）
 * - noBoundsItems：职位格式字符串进 nodeValue 但不进 layout（无 bounds，时机测试）
 */
function jobSnap(opts: {
  filter?: [number, number, number, number]
  box?: { text: string; bounds: [number, number, number, number] }
  items?: SnapItem[]
  pendingMarks?: [number, number, number, number][]
  noBoundsItems?: string[]
} = {}): DomSnapshot {
  const strings: string[] = ['', '#text']
  const nvIndex: number[] = [0] // 根节点 nodeIndex 0
  const nvValue: number[] = [0] // 根节点 nodeValue 指向 strings[0]=''
  const nvName: number[] = [0] // 根节点 nodeName 指向 strings[0]=''
  const layoutNodeIndex: number[] = [0]
  const layoutBounds: [number, number, number, number][] = [[0, 0, 1249, 1277]]

  let nextNi = 1
  const addText = (text: string, b: [number, number, number, number] | null) => {
    let si = strings.indexOf(text)
    if (si < 0) {
      strings.push(text)
      si = strings.length - 1
    }
    const ni = nextNi++
    nvIndex.push(ni)
    nvValue.push(si)
    nvName.push(1) // '#text'
    if (b !== null) {
      layoutNodeIndex.push(ni)
      layoutBounds.push(b)
    }
  }

  if (opts.filter) addText('筛选', opts.filter)
  if (opts.box) addText(opts.box.text, opts.box.bounds)
  for (const it of opts.items ?? []) addText(it.text, it.bounds)
  for (const b of opts.pendingMarks ?? []) addText('待', b)
  for (const text of opts.noBoundsItems ?? []) addText(text, null)

  return {
    strings,
    documents: [
      {
        nodes: {
          nodeValue: { index: nvIndex, value: nvValue },
          nodeName: { index: nvIndex, value: nvName },
          contentDocumentIndex: { index: [], value: [] },
        },
        layout: { nodeIndex: layoutNodeIndex, bounds: layoutBounds },
        scrollOffsetY: 0,
      },
    ],
  }
}

function snapshotQueue(snaps: DomSnapshot[]): () => Promise<DomSnapshot> {
  let i = 0
  return async () => snaps[Math.min(i++, snaps.length - 1)]!
}

interface FakeDeps {
  clicks: ClickPoint[]
  click: (p: ClickPoint) => Promise<void>
  sleep: (ms: number) => Promise<void>
}

function recorder(): FakeDeps {
  const clicks: ClickPoint[] = []
  return {
    clicks,
    click: async (p: ClickPoint) => {
      clicks.push(p)
    },
    sleep: async () => {},
  }
}

// 真机参考坐标（窗口 1249x1277）
const FILTER: [number, number, number, number] = [1100, 56, 112, 28] // 筛选按钮 center (1156,70)
const BOX_BOUNDS: [number, number, number, number] = [800, 56, 232, 28] // 职位框 center (916,70)
const BOX_POINT: ClickPoint = { x: 916, y: 70 }
const BOX_PHP = { text: 'PHP开发工程师 _ 上海 8-12K', bounds: BOX_BOUNDS }
const FRONTEND_POINT: ClickPoint = { x: 916, y: 162 }
// 打开态下拉 4 个职位项（真机 N=4）。注意：打开态职位框 trigger 仍带 bounds 显示当前职位（PHP），
// 故打开态 snapshot 同时含 box(BOX_PHP) + 这 4 个下拉项（共 5 个职位格式节点）。下拉项里 PHP 与框同名（高亮当前项）。
const OPEN_ITEMS: SnapItem[] = [
  { text: 'PHP开发工程师 _ 上海 8-12K', bounds: [800, 110, 232, 28] }, // (916,124) 高亮当前项，与框同名
  { text: '前端开发 _ 上海 15-25K', bounds: [800, 148, 232, 28] }, // (916,162)
  { text: 'Java开发 _ 北京 18-30K', bounds: [800, 186, 232, 28] }, // (916,200)
  { text: '测试 _ 深圳 10-15K', bounds: [800, 224, 232, 28] }, // (916,238)
]
const PHP_ITEM_POINT: ClickPoint = { x: 916, y: 124 } // 下拉项 PHP 的点击点（区别于职位框 PHP 的 BOX_POINT 916,70）
// 「待」徽章 bounds：在职位项右侧（同 y，x+≈108）。真机 dx≈120-136，这里用 1024 vs 职位项 916
const PENDING_AT_FRONTEND: [number, number, number, number] = [1010, 148, 28, 28] // 「待」@(1024,162)，前端项右侧
const PENDING_AT_JAVA: [number, number, number, number] = [1010, 186, 28, 28] // 「待」@(1024,200)，Java 项右侧

test('listJobs 正常：点职位框 → 下拉打开 → 解析 4 个职位项（打开态含框+项，去重后 4 个）', async () => {
  const r = recorder()
  const switcher = new JobSwitcher({
    snapshot: snapshotQueue([
      jobSnap({ filter: FILTER, box: BOX_PHP }), // snap0：关闭态（职位框 + 筛选）
      jobSnap({ filter: FILTER, box: BOX_PHP, items: OPEN_ITEMS }), // snap1：打开态（框 + 4 个项，共 5 节点）
    ]),
    click: r.click,
    sleep: r.sleep,
  })
  const jobs = await switcher.listJobs()
  assert.equal(jobs.length, 4)
  assert.deepEqual(
    jobs.map((j) => j.name),
    ['PHP开发工程师', '前端开发', 'Java开发', '测试'],
  )
  assert.deepEqual(jobs[1]!.point, FRONTEND_POINT)
  assert.deepEqual(r.clicks, [BOX_POINT])
})

test('listJobs 等不够（项无 bounds）：下拉职位字符串在 nodeValue 但无 layout → 抛错，只点框', async () => {
  const r = recorder()
  const switcher = new JobSwitcher({
    snapshot: snapshotQueue([
      jobSnap({ filter: FILTER, box: BOX_PHP }),
      // snap1：4 个职位字符串进 nodeValue 但不进 layout（未渲染/等待不足）
      jobSnap({ filter: FILTER, noBoundsItems: OPEN_ITEMS.map((i) => i.text) }),
    ]),
    click: r.click,
    sleep: r.sleep,
  })
  await assert.rejects(switcher.listJobs(), /未解析到任何职位项/)
  assert.deepEqual(r.clicks, [BOX_POINT])
})

test('listJobs 下拉未开：点框后无可解析职位节点 → 抛错，只点框', async () => {
  const r = recorder()
  const switcher = new JobSwitcher({
    snapshot: snapshotQueue([
      jobSnap({ filter: FILTER, box: BOX_PHP }),
      // snap1：下拉未弹出，职位框文本在 nodeValue 但无 layout bounds（无可解析节点）
      jobSnap({ filter: FILTER, noBoundsItems: [BOX_PHP.text] }),
    ]),
    click: r.click,
    sleep: r.sleep,
  })
  await assert.rejects(switcher.listJobs(), /未解析到任何职位项/)
  assert.deepEqual(r.clicks, [BOX_POINT])
})

test('selectJob 正常：点框 → 点目标项 → 校验职位框已变更 → switched=true', async () => {
  const r = recorder()
  const switcher = new JobSwitcher({
    snapshot: snapshotQueue([
      jobSnap({ filter: FILTER, box: BOX_PHP }), // snap0：关闭态
      jobSnap({ filter: FILTER, box: BOX_PHP, items: OPEN_ITEMS }), // snap1：打开态（框 + 项，含「前端开发」）
      jobSnap({ filter: FILTER, box: { text: '前端开发 _ 上海 15-25K', bounds: BOX_BOUNDS } }), // snap2：校验态（已切到前端）
    ]),
    click: r.click,
    sleep: r.sleep,
  })
  const res = await switcher.selectJob({ jobName: '前端开发' })
  assert.equal(res.switched, true)
  assert.deepEqual(r.clicks, [BOX_POINT, FRONTEND_POINT])
})

test('selectJob 切到当前职位（框+项同名）：排除职位框，点下拉项里的同名项（真机回归 2026-08-13）', async () => {
  // 真机实证：下拉打开时职位框 trigger 仍带 bounds 显示当前职位 PHP，下拉项里 PHP 也高亮出现 → 同名 2 个。
  // 不排除框会命中 2 个 fail-loud；排除框后下拉项里精确 1 个，点击该校验通过。
  const r = recorder()
  const switcher = new JobSwitcher({
    snapshot: snapshotQueue([
      jobSnap({ filter: FILTER, box: BOX_PHP }), // snap0：关闭态（当前职位 PHP）
      jobSnap({ filter: FILTER, box: BOX_PHP, items: OPEN_ITEMS }), // snap1：打开态（框 PHP + 下拉项 PHP 高亮 + …）
      jobSnap({ filter: FILTER, box: BOX_PHP }), // snap2：校验态（仍是 PHP，切换即切到自身）
    ]),
    click: r.click,
    sleep: r.sleep,
  })
  const res = await switcher.selectJob({ jobName: 'PHP开发工程师' })
  assert.equal(res.switched, true)
  // 点框打开下拉 + 点【下拉项】PHP（916,124），不是职位框 PHP（916,70）
  assert.deepEqual(r.clicks, [BOX_POINT, PHP_ITEM_POINT])
})

test('listJobs 标注待开放职位：项右侧有「待」徽章 → pending=true（真机 2026-08-13）', async () => {
  const r = recorder()
  const switcher = new JobSwitcher({
    snapshot: snapshotQueue([
      jobSnap({ filter: FILTER, box: BOX_PHP }),
      // 打开态：框 PHP（开放）+ 4 项；前端、Java 右侧有「待」（待开放），PHP 高亮项与测试项无「待」（开放）
      jobSnap({
        filter: FILTER,
        box: BOX_PHP,
        items: OPEN_ITEMS,
        pendingMarks: [PENDING_AT_FRONTEND, PENDING_AT_JAVA],
      }),
    ]),
    click: r.click,
    sleep: r.sleep,
  })
  const jobs = await switcher.listJobs()
  const byName = new Map(jobs.map((j) => [j.name, j]))
  assert.equal(byName.get('PHP开发工程师')!.pending, false) // 开放（当前职位）
  assert.equal(byName.get('前端开发')!.pending, true) // 待开放
  assert.equal(byName.get('Java开发')!.pending, true) // 待开放
  assert.equal(byName.get('测试')!.pending, false) // 开放（无「待」徽章）
})

test('selectJob 待开放职位：点击前拦截，不点项（避免切过去致页面异常）', async () => {
  const r = recorder()
  const switcher = new JobSwitcher({
    snapshot: snapshotQueue([
      jobSnap({ filter: FILTER, box: BOX_PHP }),
      // 打开态：前端项右侧有「待」（待开放）
      jobSnap({
        filter: FILTER,
        box: BOX_PHP,
        items: OPEN_ITEMS,
        pendingMarks: [PENDING_AT_FRONTEND],
      }),
    ]),
    click: r.click,
    sleep: r.sleep,
  })
  await assert.rejects(switcher.selectJob({ jobName: '前端开发' }), /待开放.*未发布/)
  assert.deepEqual(r.clicks, [BOX_POINT]) // 只点框开下拉，没点待开放项
})

test('selectJob 职位不存在：打开下拉无目标 → 抛错，只点框不点项', async () => {
  const r = recorder()
  const switcher = new JobSwitcher({
    snapshot: snapshotQueue([
      jobSnap({ filter: FILTER, box: BOX_PHP }),
      jobSnap({ filter: FILTER, box: BOX_PHP, items: OPEN_ITEMS }), // 框 + 4 个职位，无「Python」
    ]),
    click: r.click,
    sleep: r.sleep,
  })
  await assert.rejects(switcher.selectJob({ jobName: 'Python' }), /不在.*职位列表/)
  assert.deepEqual(r.clicks, [BOX_POINT])
})

test('selectJob 多个同名下拉项（排除框后仍 ≥2）：抛错（无法唯一定位），只点框', async () => {
  const r = recorder()
  const switcher = new JobSwitcher({
    snapshot: snapshotQueue([
      jobSnap({ filter: FILTER, box: BOX_PHP }),
      jobSnap({
        filter: FILTER,
        box: BOX_PHP,
        items: [
          { text: '测试 _ 北京 10K', bounds: [800, 110, 232, 28] },
          { text: '测试 _ 北京 10K', bounds: [800, 148, 232, 28] },
        ],
      }),
    ]),
    click: r.click,
    sleep: r.sleep,
  })
  await assert.rejects(switcher.selectJob({ jobName: '测试' }), /匹配到 2 个/)
  assert.deepEqual(r.clicks, [BOX_POINT])
})

test('selectJob 切换未生效：点目标项后职位框仍是旧职位 → 抛错（含「未生效」）', async () => {
  const r = recorder()
  const switcher = new JobSwitcher({
    snapshot: snapshotQueue([
      jobSnap({ filter: FILTER, box: BOX_PHP }),
      jobSnap({ filter: FILTER, box: BOX_PHP, items: OPEN_ITEMS }), // 框 + 含「前端开发」
      jobSnap({ filter: FILTER, box: BOX_PHP }), // snap2：校验态职位框仍是 PHP（切换未生效）
    ]),
    click: r.click,
    sleep: r.sleep,
  })
  await assert.rejects(switcher.selectJob({ jobName: '前端开发' }), /切换未生效/)
  assert.deepEqual(r.clicks, [BOX_POINT, FRONTEND_POINT])
})

test('selectJob 切换后 iframe 重载：校验轮询等职位框重现再判定（真机回归 2026-08-13）', async () => {
  // 真机：点击职位项后推荐列表 iframe 重新加载，职位框在前几次 snapshot 暂时消失（重载中），
  // 固定延时校验会误判「未知」；轮询等待 iframe 重载完、职位框重现且文本=目标，再判定成功。
  const r = recorder()
  const switcher = new JobSwitcher({
    snapshot: snapshotQueue([
      jobSnap({ filter: FILTER, box: BOX_PHP }), // snap0：关闭态
      jobSnap({ filter: FILTER, box: BOX_PHP, items: OPEN_ITEMS }), // snap1：打开态（含前端开发）
      jobSnap({ filter: FILTER }), // snap2：重载中（职位框消失，仅剩筛选）→ locateJobBox point=null
      jobSnap({ filter: FILTER }), // snap3：仍重载中
      jobSnap({ filter: FILTER, box: { text: '前端开发 _ 上海 15-25K', bounds: BOX_BOUNDS } }), // snap4：重载完，职位框重现=前端
    ]),
    click: r.click,
    sleep: r.sleep,
  })
  const res = await switcher.selectJob({ jobName: '前端开发' })
  assert.equal(res.switched, true)
  assert.deepEqual(r.clicks, [BOX_POINT, FRONTEND_POINT])
})

test('selectJob 切换后 iframe 一直未重载完（职位框始终不重现）：轮询超时 → 抛错（含「无法确认」）', async () => {
  const r = recorder()
  const switcher = new JobSwitcher({
    snapshot: snapshotQueue([
      jobSnap({ filter: FILTER, box: BOX_PHP }),
      jobSnap({ filter: FILTER, box: BOX_PHP, items: OPEN_ITEMS }),
      jobSnap({ filter: FILTER }), // 重载中，职位框始终消失 → 轮询 10 次均 point=null → 超时
    ]),
    click: r.click,
    sleep: r.sleep,
  })
  await assert.rejects(switcher.selectJob({ jobName: '前端开发' }), /无法确认/)
  assert.deepEqual(r.clicks, [BOX_POINT, FRONTEND_POINT])
})

test('openJobDropdown 已开（back-to-back）：snap0 已 >=2 项 → 不再点框，直接返回', async () => {
  const r = recorder()
  const switcher = new JobSwitcher({
    snapshot: snapshotQueue([
      jobSnap({ filter: FILTER, box: BOX_PHP, items: OPEN_ITEMS }), // snap0：已是打开态（框 + 4 项 = 5 节点）
    ]),
    click: r.click,
    sleep: r.sleep,
  })
  const jobs = await switcher.listJobs()
  assert.equal(jobs.length, 4)
  assert.equal(r.clicks.length, 0) // 不重复点框（避免把已开的下拉点关）
})

test('取消（signal 已 abort）：listJobs / selectJob 入口抛 CancelledError，不点', async () => {
  const r = recorder()
  const signal = AbortSignal.abort()
  const snap = snapshotQueue([jobSnap({ filter: FILTER, box: BOX_PHP })])
  const switcher = new JobSwitcher({ snapshot: snap, click: r.click, sleep: r.sleep, signal })
  await assert.rejects(switcher.listJobs(), (e: unknown) => {
    assert.equal((e as Error).name, 'CancelledError')
    return true
  })
  await assert.rejects(switcher.selectJob({ jobName: '前端开发' }), (e: unknown) => {
    assert.equal((e as Error).name, 'CancelledError')
    return true
  })
  assert.equal(r.clicks.length, 0)
})

test('不在推荐页（locateJobBox 无筛选按钮）：snap0 无筛选 → 抛错（未找到职位框），不点', async () => {
  const r = recorder()
  const switcher = new JobSwitcher({
    snapshot: snapshotQueue([jobSnap({})]), // 空页面（无筛选按钮，非推荐页）
    click: r.click,
    sleep: r.sleep,
  })
  await assert.rejects(switcher.listJobs(), /未找到职位框/)
  assert.equal(r.clicks.length, 0)
})
