/**
 * Phase 5 存活热点分段计时（2026-10-20，计划 plan-recruiting-error-containment-and-input-performance）：
 * - FilterSetter 同步分段（stepSync）：筛选结构解析 / 行带几何解析 / 逐行预校验 / 可见区几何 /
 *   年龄值读取——成功与失败路径都记段、失败路径不崩、缺省不注入时行为不变。
 * - operation 级：bossFilter 失败后核验（filter:verify:fallback，此前未包装调用）与前置工具
 *   快照用途分段（snapshot:precheck / snapshot:listJobs）经 [boss-perf] 行可见。
 *
 * stderr 断言模式复用 boss-context-perf.test.ts（临时接管 process.stderr.write）。
 */
import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import { FilterSetter, FilterSetError } from '../src/main/boss/FilterSetter.js'
import { createBossFilterOperation } from '../src/main/operations/bossFilter.js'
import { createBossListJobsOperation } from '../src/main/operations/bossListJobs.js'
import type { BossSession } from '../src/main/operations/bossContext.js'
import { membershipAccountKey } from '../src/main/boss/MembershipInspector.js'
import { withAccount } from './filterFixture.js'
import type { DomSnapshot, ClickPoint } from '../src/main/boss/domSnapshot.js'

// ---------- 精简面板 fixture（无 layout.styles → 走行带几何路径，与 filter-setter.test.ts 同构） ----------

function buildSnap(items: Array<{ text: string; bounds: [number, number, number, number] }>): DomSnapshot {
  const idx = [0, ...items.map((_, i) => i + 1)]
  return {
    strings: ['', ...items.map((i) => i.text)],
    documents: [
      {
        nodes: { nodeValue: { index: idx, value: idx }, contentDocumentIndex: { index: [], value: [] }, parentIndex: idx.map(() => 0) },
        layout: { nodeIndex: idx, bounds: [[0, 0, 1917, 1905], ...items.map((i) => i.bounds)] },
      },
    ],
  }
}

const panelItems = [
  { text: '筛选', bounds: [1514, 33.5, 42, 23] as [number, number, number, number] },
  { text: '经验要求', bounds: [600, 629, 75, 22] as [number, number, number, number] },
  { text: '5-10年', bounds: [874.5, 629, 58.5, 22] as [number, number, number, number] },
  { text: '清除', bounds: [1633, 988.5, 42, 23] as [number, number, number, number] },
  { text: '确定', bounds: [1738.5, 988.5, 42, 23] as [number, number, number, number] },
]
const closedSnap = buildSnap([{ text: '筛选', bounds: [1514, 33.5, 42, 23] }])
const panelSnap = buildSnap(panelItems)
const doneSnap1 = buildSnap([{ text: '筛选·1', bounds: [1514, 33.5, 60, 23] }])

function snapshotQueue(snaps: DomSnapshot[]) {
  let i = 0
  return async () => snaps[Math.min(i++, snaps.length - 1)]!
}

/** 收集 stepSync 分段名与耗时（恒 ≥0），供断言分段存在且失败路径不崩 */
function stepSyncCollector() {
  const names: string[] = []
  const ms = new Map<string, number>()
  return {
    names,
    ms,
    stepSync: <T>(name: string, fn: () => T): T => {
      const start = Date.now()
      try {
        return fn()
      } finally {
        names.push(name)
        ms.set(name, (ms.get(name) ?? 0) + (Date.now() - start))
      }
    },
  }
}

function recorder() {
  const clicks: ClickPoint[] = []
  return { clicks, click: async (p: ClickPoint) => { clicks.push(p) }, sleep: async () => {} }
}

// ---------- FilterSetter 同步分段 ----------

test('成功路径：结构解析/几何解析/逐行预校验/可见区几何分段齐全且耗时不为负', async () => {
  const r = recorder()
  const c = stepSyncCollector()
  const setter = new FilterSetter({
    snapshot: snapshotQueue([closedSnap, ...Array(3).fill(panelSnap), doneSnap1]),
    click: r.click,
    sleep: r.sleep,
    stepSync: c.stepSync,
  })
  const result = await setter.apply({ experience: '5-10年' })
  assert.equal(result.filterCount, 1)
  for (const name of ['filter:parse:structure', 'filter:parse:panelGeometry', 'filter:precheck:rows', 'filter:visible:clip']) {
    assert.ok(c.names.includes(name), `分段 ${name} 应被记录，实际：${c.names.join(', ')}`)
    assert.ok((c.ms.get(name) ?? -1) >= 0, `分段 ${name} 耗时应 ≥ 0`)
  }
  // 可见区几何在清除/确定按钮可视判定中多次执行（每次迭代一段）
  assert.ok(c.names.filter((n) => n === 'filter:visible:clip').length >= 2)
})

test('失败路径：预校验抛错仍记段且不崩（分段计时不得吞掉/改变异常）', async () => {
  const r = recorder()
  const c = stepSyncCollector()
  const setter = new FilterSetter({
    snapshot: snapshotQueue([closedSnap, panelSnap]),
    click: r.click,
    sleep: r.sleep,
    stepSync: c.stepSync,
  })
  // 「应届生」非数值且页面无该选项 → 预校验 FilterSetError（清除前失败，不点击选项）
  await assert.rejects(setter.apply({ experience: '应届生' }), (error: unknown) => error instanceof FilterSetError)
  assert.ok(c.names.includes('filter:precheck:rows'), `失败路径也应记录 filter:precheck:rows，实际：${c.names.join(', ')}`)
  assert.ok(c.names.includes('filter:parse:structure'))
  assert.equal(r.clicks.length, 1) // 仅开面板的筛选按钮，异常路径行为与无分段时一致
})

test('缺省不注入 stepSync：apply 行为不变', async () => {
  const r = recorder()
  const setter = new FilterSetter({
    snapshot: snapshotQueue([closedSnap, ...Array(3).fill(panelSnap), doneSnap1]),
    click: r.click,
    sleep: r.sleep,
  })
  const result = await setter.apply({ experience: '5-10年' })
  assert.equal(result.filterCount, 1)
  assert.equal(r.clicks.length, 4) // 开面板 + 清除 + 选项 + 确定
})

test('年龄值读取分段 filter:age:read 在 VIP 拖动链路记录', async () => {
  const top = withAccount(JSON.parse(fs.readFileSync(new URL('../../tests/fixtures/boss-vip-panel-top.json', import.meta.url), 'utf8')))
  const age25 = structuredClone(top)
  age25.strings = age25.strings.map((s) => (s === '16' ? '25' : s))
  const done = withAccount({ strings: ['', '筛选·1'], documents: [{ nodes: { nodeValue: [0, 1], contentDocumentIndex: [] }, layout: { nodeIndex: [0, 1], bounds: [[0, 0, 2506, 1476], [1514, 33.5, 60, 23]] } }] })
  const c = stepSyncCollector()
  const setter = new FilterSetter({
    snapshot: snapshotQueue([top, top, age25, age25, done]),
    click: async () => {},
    sleep: async () => {},
    mouseWheel: async () => {},
    drag: async () => {},
    stepSync: c.stepSync,
  })
  const result = await setter.apply({ membership_hint: 'vip', membership_account_ref: membershipAccountKey(top)!, age: { min: 25, max: null } })
  assert.deepEqual(result.age, { min: 25, max: null })
  assert.ok(c.names.includes('filter:age:read'), `应记录 filter:age:read，实际：${[...new Set(c.names)].join(', ')}`)
})

// ---------- operation 级：[boss-perf] 行新增分段 ----------

/** 临时接管 process.stderr.write 收集输出（finally 还原） */
async function captureStderr(fn: () => Promise<void>): Promise<string[]> {
  const chunks: string[] = []
  const orig = process.stderr.write.bind(process.stderr)
  process.stderr.write = ((chunk: Uint8Array | string) => {
    chunks.push(typeof chunk === 'string' ? chunk : Buffer.from(chunk).toString())
    return true
  }) as unknown as typeof process.stderr.write
  try {
    await fn()
  } finally {
    process.stderr.write = orig
  }
  return chunks
}

function perfLineOf(chunks: string[]): string {
  const line = chunks.join('').split('\n').find((l) => l.startsWith('[boss-perf]'))
  assert.ok(line, `应输出 [boss-perf] 行，实际输出：${chunks.join('')}`)
  return line!
}

const context = () => ({ signal: new AbortController().signal, progress: () => {} })

function stateSession(url: string, state: () => DomSnapshot, click: (p: ClickPoint) => Promise<void>): BossSession {
  return {
    snapshot: async () => state(),
    getUrl: async () => url,
    click,
    drag: async () => {},
    mouseWheel: async () => {},
    clickBrowse: async () => {},
    clickAndType: async () => {},
    pressEscape: async () => {},
    captureFullpage: async () => Buffer.alloc(0),
    close: async () => {},
  }
}

test('boss_filter 失败核验：[boss-perf] 行含 filter:verify:fallback 段', async () => {
  const panel = withAccount(JSON.parse(fs.readFileSync(new URL('../../tests/fixtures/boss-vip-panel-top.json', import.meta.url), 'utf8')), 'account-a')
  const badgeWrong = withAccount({ strings: ['', '筛选·2'], documents: [{ nodes: { nodeValue: [0, 1], contentDocumentIndex: [] }, layout: { nodeIndex: [0, 1], bounds: [[0, 0, 2506, 1476], [2200, 24, 60, 31]] } }] }, 'account-a')
  const selectedPanel = structuredClone(panel)
  selectedPanel.strings.push('class', 'option active')
  const nameIdx = selectedPanel.strings.length - 2, valueIdx = selectedPanel.strings.length - 1
  for (const doc of selectedPanel.documents) {
    const values = doc.nodes.nodeValue as number[], parents = doc.nodes.parentIndex as number[], attrs = (doc.nodes.attributes ??= values.map(() => [])) as number[][]
    for (let i = 0; i < values.length; i++) if (selectedPanel.strings[values[i]!]?.trim() === '20-50K') attrs[parents[i]!] = [nameIdx, valueIdx]
  }
  let state: DomSnapshot = panel
  const pointNear = (snap: DomSnapshot, text: string, p: ClickPoint): boolean => {
    for (let si = 0; si < snap.strings.length; si++) {
      if (snap.strings[si]?.trim() !== text) continue
      for (const doc of snap.documents) {
        for (let ni = 0; ni < (doc.nodes.nodeValue as number[]).length; ni++) {
          if ((doc.nodes.nodeValue as number[])[ni] !== si) continue
          const li = doc.layout.nodeIndex.indexOf(ni)
          const b = li >= 0 ? doc.layout.bounds[li] : undefined
          if (!b || b[2]! <= 0 || b[3]! <= 0) continue
          const cx = b[0]! + b[2]! / 2, cy = b[1]! + b[3]! / 2
          if (Math.abs(cx - p.x) < 40 && Math.abs(cy - p.y) < 40) return true
        }
      }
    }
    return false
  }
  const session = stateSession('https://www.zhipin.com/web/chat/recommend', () => state, async (p) => {
    if (pointNear(state, '清除', p) || pointNear(state, '筛选·2', p) || pointNear(state, '筛选', p)) { state = panel; return }
    if (pointNear(state, '20-50K', p)) { state = selectedPanel; return }
    if (pointNear(state, '确定', p)) { state = badgeWrong; return }
    assert.fail(`出现未确认落点 ${JSON.stringify(p)}`)
  })
  const chunks = await captureStderr(async () => {
    const result = await createBossFilterOperation(async () => session).execute({ salary: '20-50K' }, context())
    assert.equal(result.success, false, '徽章不符的终态应失败（重试后仍失败）')
  })
  const line = perfLineOf(chunks)
  assert.ok(line.includes('filter:verify:fallback{n='), `[boss-perf] 行应含 filter:verify:fallback，实际：${line}`)
  assert.ok(line.includes('filter:parse:structure{n='), `[boss-perf] 行应含 filter:parse:structure，实际：${line}`)
})

test('boss_list_jobs：[boss-perf] 行含 snapshot:precheck 与 snapshot:listJobs 用途分段', async () => {
  function jobSnap(open: boolean): DomSnapshot {
    const items: Array<{ text: string; bounds: [number, number, number, number] }> = [
      { text: '筛选', bounds: [1156, 70, 42, 23] },
      { text: 'PHP开发工程师 _ 上海 8-12K', bounds: [916, 70, 200, 23] },
    ]
    if (open) {
      items.push({ text: '前端开发工程师 _ 上海 10-15K', bounds: [500, 150, 220, 23] })
      items.push({ text: '后端开发工程师 _ 上海 12-18K', bounds: [500, 200, 220, 23] })
    }
    return buildSnap(items)
  }
  const closed = jobSnap(false), open = jobSnap(true)
  let i = 0
  const queue = [closed, open, open, closed, closed]
  const session: BossSession = {
    snapshot: async () => queue[Math.min(i++, queue.length - 1)]!,
    getUrl: async () => 'https://www.zhipin.com/web/chat/recommend',
    click: async () => {},
    clickBrowse: async () => {},
    clickAndType: async () => {},
    mouseWheel: async () => {},
    pressEscape: async () => {},
    captureFullpage: async () => Buffer.alloc(0),
    close: async () => {},
  }
  const chunks = await captureStderr(async () => {
    const result = await createBossListJobsOperation(async () => session).execute({}, context())
    assert.equal(result.success, true)
    assert.equal((result.data?.jobs as unknown[]).length, 2)
  })
  const line = perfLineOf(chunks)
  assert.ok(line.includes('snapshot:precheck{n=1,'), `[boss-perf] 行应含 snapshot:precheck，实际：${line}`)
  assert.ok(line.includes('snapshot:listJobs{n='), `[boss-perf] 行应含 snapshot:listJobs，实际：${line}`)
})
