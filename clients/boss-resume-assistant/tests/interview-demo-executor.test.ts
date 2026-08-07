import assert from 'node:assert/strict'
import test from 'node:test'
import { InterviewDemoExecutor, InterviewDemoError } from '../src/main/boss/InterviewDemoExecutor.js'
import type { DomSnapshot, ClickPoint } from '../src/main/boss/domSnapshot.js'

/** 构造 snapshot：根节点视口 1917x1905，items 为任意文本及其 bounds（相同文案共享 string 下标） */
function snap(items: Array<{ text: string; bounds: [number, number, number, number] }>): DomSnapshot {
  const strings: string[] = ['']
  const idx: number[] = [0]
  const values: number[] = [0]
  const bounds: [number, number, number, number][] = [[0, 0, 1917, 1905]]
  for (const item of items) {
    let si = strings.indexOf(item.text)
    if (si < 0) {
      strings.push(item.text)
      si = strings.length - 1
    }
    idx.push(idx.length)
    values.push(si)
    bounds.push(item.bounds)
  }
  return {
    strings,
    documents: [
      {
        nodes: {
          nodeValue: { index: idx, value: values },
          contentDocumentIndex: { index: [], value: [] },
        },
        layout: { nodeIndex: idx, bounds },
        scrollOffsetY: 0,
      },
    ],
  }
}

function snapshotQueue(snaps: DomSnapshot[]): () => Promise<DomSnapshot> {
  let i = 0
  return async () => snaps[Math.min(i++, snaps.length - 1)]!
}

function recorder() {
  const clicks: ClickPoint[] = []
  const typed: string[] = []
  return {
    clicks,
    typed,
    click: async (p: ClickPoint) => {
      clicks.push(p)
    },
    typeChar: async (ch: string) => {
      typed.push(ch)
    },
    sleep: async () => {},
  }
}

// 真机校准坐标（2026-08-06）
const INVITE: [number, number, number, number] = [1468, 1597, 76, 36] // 约面试 (1506,1615)
const TITLE: [number, number, number, number] = [580, 595, 160, 30] // 线下面试邀请 (660,610)
const COUNTER: [number, number, number, number] = [1297, 988, 35, 20] // /140 (1314.5,998)
const TIME_LABEL: [number, number, number, number] = [570, 1072, 84, 23] // 面试时间 (612,1083.5)
const CANCEL: [number, number, number, number] = [1121, 1272, 84, 36] // 取消 (1163,1290)

const REMARK = '请带好身份证和简历准时面试' // 13 字
const COUNT_NODE: [number, number, number, number] = [1273, 988, 24, 20] // 「13」计数 (1285,998)，计数器左侧同行

function dateStr(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

/** 明天日期单元格 bounds：放在日历区域内（标签 +60~+540 / +40~+520） */
function dayCell(day: number): { text: string; bounds: [number, number, number, number] } {
  return { text: String(day), bounds: [996, 1328, 20, 19] } // (1006,1337.5)
}

/** 完整 happy path 的 6 帧快照 */
function happySnaps(tomorrow: Date): DomSnapshot[] {
  const dateText = dateStr(tomorrow)
  return [
    snap([{ text: '约面试', bounds: INVITE }]), // s0 前置+定位
    snap([
      { text: '线下面试邀请', bounds: TITLE },
      { text: '/140', bounds: COUNTER },
    ]), // s1 表单已开+计数器
    snap([
      { text: '线下面试邀请', bounds: TITLE },
      { text: '/140', bounds: COUNTER },
      { text: String(REMARK.length), bounds: COUNT_NODE },
      { text: '面试时间', bounds: TIME_LABEL },
    ]), // s2 字数校验+时间标签
    snap([
      { text: '线下面试邀请', bounds: TITLE },
      { text: '面试时间', bounds: TIME_LABEL },
      dayCell(tomorrow.getDate()),
    ]), // s3 日历面板
    snap([
      { text: '线下面试邀请', bounds: TITLE },
      { text: dateText, bounds: [700, 1072, 100, 20] },
      { text: '取消', bounds: CANCEL },
    ]), // s4 日期已填+取消
    snap([{ text: '约面试', bounds: INVITE }]), // s5 表单已关
  ]
}

test('完整流程：开表单→逐字填备注→选明天→取消关闭，绝不点发送', async () => {
  const tomorrow = new Date(Date.now() + 86400000)
  const r = recorder()
  const executor = new InterviewDemoExecutor({ snapshot: snapshotQueue(happySnaps(tomorrow)), ...r })
  const result = await executor.run()
  assert.deepEqual(result, { remark: REMARK, date: dateStr(tomorrow) })
  // 逐字输入 13 字
  assert.equal(r.typed.join(''), REMARK)
  // 点击序列：约面试 → 备注聚焦点 → 日期下拉 → 日期单元格 → 取消（无发送）
  const clicks = r.clicks.map((p) => `${Math.round(p.x)},${Math.round(p.y)}`)
  assert.deepEqual(clicks, ['1506,1615', '1015,958', '842,1084', '1006,1338', '1163,1290'])
})

test('跨月：明天是 1 号时先点下月箭头再选「1」', async () => {
  // 2026-08-31 的明天是 2026-09-01
  const r = recorder()
  const executor = new InterviewDemoExecutor({
    snapshot: snapshotQueue(happySnaps(new Date('2026-09-01T12:00:00'))),
    ...r,
    now: () => new Date('2026-08-31T12:00:00').getTime(),
  })
  const result = await executor.run()
  assert.equal(result.date, '2026-09-01')
  const clicks = r.clicks.map((p) => `${Math.round(p.x)},${Math.round(p.y)}`)
  // 第 4 个点是下月箭头（标签 +455/+85），第 5 个是日期「1」
  assert.equal(clicks[3], '1067,1169')
  assert.equal(clicks[4], '1006,1338')
})

test('表单已打开（残留状态）：报错不动作', async () => {
  const r = recorder()
  const executor = new InterviewDemoExecutor({
    snapshot: snapshotQueue([snap([{ text: '线下面试邀请', bounds: TITLE }])]),
    ...r,
  })
  await assert.rejects(executor.run(), (e: Error) => {
    assert.ok(e instanceof InterviewDemoError)
    assert.match(e.message, /已处于打开状态/)
    return true
  })
  assert.equal(r.clicks.length, 0)
  assert.equal(r.typed.length, 0)
})

test('找不到「约面试」（未打开会话）：报错不动作', async () => {
  const r = recorder()
  const executor = new InterviewDemoExecutor({ snapshot: snapshotQueue([snap([])]), ...r })
  await assert.rejects(executor.run(), /未找到唯一的「约面试」/)
  assert.equal(r.clicks.length, 0)
})

test('点击约面试后表单未打开：报错（点击可能被拦截）', async () => {
  const r = recorder()
  const executor = new InterviewDemoExecutor({
    snapshot: snapshotQueue([snap([{ text: '约面试', bounds: INVITE }]), snap([])]),
    ...r,
  })
  await assert.rejects(executor.run(), /表单未打开/)
  assert.equal(r.clicks.length, 1)
  assert.equal(r.typed.length, 0)
})

test('逐字输入后字数不符：报错（输入未完整落地）', async () => {
  const r = recorder()
  const snaps = happySnaps(new Date(Date.now() + 86400000))
  // s2 里的计数改成「9」（如丢了 4 个字）
  snaps[2] = snap([
    { text: '线下面试邀请', bounds: TITLE },
    { text: '/140', bounds: COUNTER },
    { text: '9', bounds: COUNT_NODE },
    { text: '面试时间', bounds: TIME_LABEL },
  ])
  const executor = new InterviewDemoExecutor({ snapshot: snapshotQueue(snaps), ...r })
  await assert.rejects(executor.run(), /输入未完整落地/)
  assert.equal(r.typed.join(''), REMARK) // 字都敲了但页面没接收全
  assert.equal(r.clicks.length, 2) // 约面试 + 聚焦，不继续点日期
})

test('日历里找不到日期单元格：报错且绝不点发送', async () => {
  const tomorrow = new Date(Date.now() + 86400000)
  const r = recorder()
  const snaps = happySnaps(tomorrow)
  snaps[3] = snap([
    { text: '线下面试邀请', bounds: TITLE },
    { text: '面试时间', bounds: TIME_LABEL },
  ]) // 日历没开/无该日期
  const executor = new InterviewDemoExecutor({ snapshot: snapshotQueue(snaps), ...r })
  await assert.rejects(executor.run(), /未找到唯一日期/)
})

test('点完日期但日期框未填入：报错', async () => {
  const tomorrow = new Date(Date.now() + 86400000)
  const r = recorder()
  const snaps = happySnaps(tomorrow)
  snaps[4] = snap([
    { text: '线下面试邀请', bounds: TITLE },
    { text: '取消', bounds: CANCEL },
  ]) // 无日期文本
  const executor = new InterviewDemoExecutor({ snapshot: snapshotQueue(snaps), ...r })
  await assert.rejects(executor.run(), /日期未选中/)
})

test('取消后表单仍未关闭：报错请人工查看', async () => {
  const tomorrow = new Date(Date.now() + 86400000)
  const r = recorder()
  const snaps = happySnaps(tomorrow)
  snaps[5] = snap([{ text: '线下面试邀请', bounds: TITLE }]) // 表单还在
  const executor = new InterviewDemoExecutor({ snapshot: snapshotQueue(snaps), ...r })
  await assert.rejects(executor.run(), /仍未关闭/)
})
