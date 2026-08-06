import assert from 'node:assert/strict'
import test from 'node:test'
import { ResumeConsentExecutor, ConsentError } from '../src/main/boss/ResumeConsentExecutor.js'
import type { DomSnapshot, ClickPoint } from '../src/main/boss/domSnapshot.js'

/**
 * 构造单文档 snapshot（视口 1917x1905）。相同文案共享 string 下标（真实 interning）。
 * 真机参考（2026-08-06 沟通页实测）：
 * - 左列会话预览文案 cx≈549~567（x<850 才算目标）
 * - 右侧处理条：文案 (1013,1557) + 「同意」(1335,1557) 同行；消息卡片按钮在文案下方 ~95px（灰化，不能点）
 */
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
        layout: {
          nodeIndex: idx,
          bounds: [[0, 0, 1917, 1905], ...items.map((it) => it.bounds)],
        },
      },
    ],
  }
}

const MARKER = '对方想发送附件简历给您，您是否同意'
const MARKER_ENC = '对方想发送加密附件简历给您，您是否同意'
/** 左列会话预览（cx=549, cy 可变） */
const listItem = (y: number, text = MARKER): Item => ({ text, bounds: [400, y - 16, 298, 32] })
/** 右侧处理条：文案 + 同行「同意」 */
const banner = (y: number): Item[] => [
  { text: MARKER, bounds: [900, y - 16, 226, 32] }, // cx=1013
  { text: '同意', bounds: [1295, y - 20, 80, 40] }, // cx=1335 同行
]
/** 消息卡片：文案 + 下方 95px 的「同意」（灰化诱饵） */
const cardAgree = (y: number): Item[] => [
  { text: MARKER, bounds: [1055, y - 16, 226, 32] }, // cx=1168
  { text: '同意', bounds: [1147, y + 75, 80, 40] }, // cy=文案+95
]

function recorder(snaps: DomSnapshot[]) {
  let i = 0
  const clicks: ClickPoint[] = []
  return {
    clicks,
    deps: {
      snapshot: async () => snaps[Math.min(i++, snaps.length - 1)]!,
      click: async (p: ClickPoint) => {
        clicks.push(p)
      },
      sleep: async () => {},
    },
  }
}

test('打开会话 → 点处理条「同意」（同行配对，不点消息卡片的灰化按钮）→ 校验消失', async () => {
  // 调用序：定位[左列1个] → 打开后[处理条+卡片诱饵] → 点完校验[无同意] → 定位[无目标] → 滚动前探测
  const r = recorder([
    consentSnap([listItem(1307)]),
    consentSnap([listItem(1307), ...banner(1557), ...cardAgree(921)]),
    consentSnap([listItem(1307)]),
    consentSnap([]),
    consentSnap([]),
  ])
  const executor = new ResumeConsentExecutor(r.deps)
  const result = await executor.acceptAll()
  assert.equal(result.accepted, 1)
  // 第一次点左列会话 (549,1307)，第二次必须点处理条「同意」(1335,1557) 而不是卡片按钮 (1187,1016)
  assert.deepEqual(r.clicks, [
    { x: 549, y: 1307 },
    { x: 1335, y: 1557 },
  ])
})

test('右侧面板的同文案不算目标（x≥850 排除），只有左列才打开', async () => {
  const r = recorder([
    consentSnap([...banner(1557)]), // 只有右侧处理条，左列无目标
    consentSnap([]),
    consentSnap([]),
  ])
  const executor = new ResumeConsentExecutor(r.deps)
  const result = await executor.acceptAll()
  assert.equal(result.accepted, 0)
  assert.equal(r.clicks.length, 0)
})

test('加密附件同样识别', async () => {
  const r = recorder([
    consentSnap([listItem(1424, MARKER_ENC)]),
    consentSnap([listItem(1424, MARKER_ENC), ...banner(1557)]),
    consentSnap([]),
    consentSnap([]),
    consentSnap([]),
  ])
  const executor = new ResumeConsentExecutor(r.deps)
  const result = await executor.acceptAll()
  assert.equal(result.accepted, 1)
  assert.deepEqual(r.clicks[0], { x: 549, y: 1424 })
})

test('打开会话后找不到「同意」处理条 → fail-loud 停止，不再处理下一个', async () => {
  const r = recorder([
    consentSnap([listItem(1307), listItem(1658, MARKER_ENC)]),
    consentSnap([listItem(1307), listItem(1658, MARKER_ENC)]), // 打开后无处理条
    consentSnap([listItem(1307), listItem(1658, MARKER_ENC)]), // 重试仍无
  ])
  const executor = new ResumeConsentExecutor(r.deps)
  await assert.rejects(executor.acceptAll(), (e: unknown) => {
    assert.ok(e instanceof ConsentError)
    assert.match(e.message, /未找到「同意」处理条/)
    return true
  })
  assert.equal(r.clicks.length, 1) // 只打开了会话，没乱点
})

test('点「同意」后按钮未消失（二次确认/被拦截）→ fail-loud', async () => {
  const r = recorder([
    consentSnap([listItem(1307)]),
    consentSnap([listItem(1307), ...banner(1557)]),
    consentSnap([listItem(1307), ...banner(1557)]), // 点完「同意」还在
  ])
  const executor = new ResumeConsentExecutor(r.deps)
  await assert.rejects(executor.acceptAll(), (e: unknown) => {
    assert.ok(e instanceof ConsentError)
    assert.match(e.message, /未消失/)
    return true
  })
  assert.equal(r.clicks.length, 2)
})

test('--limit 限制处理数量（reachedEnd=false）', async () => {
  const r = recorder([
    consentSnap([listItem(1307), listItem(1658, MARKER_ENC)]),
    consentSnap([listItem(1307), listItem(1658, MARKER_ENC), ...banner(1557)]),
    consentSnap([listItem(1658, MARKER_ENC)]), // 第 1 个处理完，剩 1 个
  ])
  const executor = new ResumeConsentExecutor(r.deps)
  const result = await executor.acceptAll({ limit: 1 })
  assert.equal(result.accepted, 1)
  assert.equal(result.reachedEnd, false)
})

test('可见处理完自动滚动左列：签名变化继续，签名不变（含一次重试）到底', async () => {
  // 调用序：定位 → 打开 → 校验 → 定位(空) → 滚动前 → 滚动后(签名变，出现新目标)
  //   → 定位 → 打开 → 校验 → 定位(空) → 滚动前 → 滚动后(不变) → 滚动前 → 滚动后(不变)=到底
  const r = recorder([
    consentSnap([listItem(1307)]), // 定位：1 个目标
    consentSnap([listItem(1307), ...banner(1557)]),
    consentSnap([]), // 点完校验：同意消失
    consentSnap([]), // 定位：无目标 → 滚动
    consentSnap([]), // 滚动前
    consentSnap([listItem(300)]), // 滚动后：签名变化（出现新目标）→ 回内层
    consentSnap([listItem(300)]), // 定位：找到新目标
    consentSnap([listItem(300), ...banner(1557)]), // 打开后
    consentSnap([]), // 点完校验
    consentSnap([]), // 定位：无目标 → 滚动
    consentSnap([]), // 滚动前
    consentSnap([]), // 滚动后签名不变（重试 1）
    consentSnap([]), // 滚动前
    consentSnap([]), // 滚动后签名不变 → 到底
  ])
  const scrolls: number[] = []
  const executor = new ResumeConsentExecutor({
    ...r.deps,
    scroll: async (d) => {
      scrolls.push(d)
    },
  })
  const result = await executor.acceptAll()
  assert.equal(result.accepted, 2)
  assert.equal(result.reachedEnd, true)
  assert.equal(scrolls.length, 3)
})

/** 右侧附件卡片「点击预览附件简历」按钮 */
const previewBtn = (y: number): Item => ({ text: '点击预览附件简历', bounds: [1050, y - 16, 168, 32] }) // cx=1134
/** 预览弹层打开时的标志文案（弹层里成组出现） */
const modalMarkers = (): Item[] => [
  { text: '个人优势', bounds: [500, 300, 100, 30] },
  { text: '工作经历', bounds: [500, 550, 100, 30] },
]

test('preview 全流程：同意后点最下方预览按钮 → 弹层打开校验 → Escape 关闭', async () => {
  const escapes: number[] = []
  // 调用序：定位 → 打开(处理条) → 校验(同意消失) → 找预览按钮(含旧附件诱饵) → 点击前快照 → 弹层开 → Escape 后关
  const r = recorder([
    consentSnap([listItem(1307)]),
    consentSnap([listItem(1307), ...banner(1557)]),
    consentSnap([]),
    consentSnap([previewBtn(800), previewBtn(1286)]), // 历史旧附件 + 最新送达（必须点最下方）
    consentSnap([previewBtn(800), previewBtn(1286)]),
    consentSnap([...modalMarkers()]), // 弹层打开
    consentSnap([]), // Escape 后关闭
  ])
  const executor = new ResumeConsentExecutor({
    ...r.deps,
    pressEscape: async () => {
      escapes.push(1)
    },
  })
  const result = await executor.acceptAll({ preview: true })
  assert.equal(result.accepted, 1)
  assert.equal(result.previewed, 1)
  assert.deepEqual(r.clicks, [
    { x: 549, y: 1307 }, // 打开会话
    { x: 1335, y: 1557 }, // 处理条「同意」
    { x: 1134, y: 1286 }, // 最下方的预览按钮（不是 800 的旧附件）
  ])
  assert.equal(escapes.length, 1)
})

test('同意后等不到预览按钮（附件未送达）→ fail-loud', async () => {
  const r = recorder([
    consentSnap([listItem(1307)]),
    consentSnap([listItem(1307), ...banner(1557)]),
    consentSnap([]),
    consentSnap([]), // 3 次都找不到预览按钮
    consentSnap([]),
    consentSnap([]),
  ])
  const executor = new ResumeConsentExecutor({ ...r.deps, pressEscape: async () => {} })
  await assert.rejects(executor.acceptAll({ preview: true }), (e: unknown) => {
    assert.ok(e instanceof ConsentError)
    assert.match(e.message, /未出现「点击预览附件简历」/)
    return true
  })
})

test('点预览后弹层未打开 → fail-loud；Escape 后未关闭 → fail-loud', async () => {
  // 弹层未打开
  const r1 = recorder([
    consentSnap([listItem(1307)]),
    consentSnap([listItem(1307), ...banner(1557)]),
    consentSnap([]),
    consentSnap([previewBtn(1286)]),
    consentSnap([previewBtn(1286)]),
    consentSnap([]), // 点完弹层标志 <2
  ])
  const executor1 = new ResumeConsentExecutor({ ...r1.deps, pressEscape: async () => {} })
  await assert.rejects(executor1.acceptAll({ preview: true }), /弹层未打开/)

  // Escape 后未关闭
  const r2 = recorder([
    consentSnap([listItem(1307)]),
    consentSnap([listItem(1307), ...banner(1557)]),
    consentSnap([]),
    consentSnap([previewBtn(1286)]),
    consentSnap([previewBtn(1286)]),
    consentSnap([...modalMarkers()]),
    consentSnap([...modalMarkers()]), // Escape 后标志仍 ≥2
  ])
  const executor2 = new ResumeConsentExecutor({ ...r2.deps, pressEscape: async () => {} })
  await assert.rejects(executor2.acceptAll({ preview: true }), /未关闭/)
})

test('页面没有目标会话 → 0 人到底，不报错', async () => {
  const r = recorder([consentSnap([])])
  const executor = new ResumeConsentExecutor(r.deps)
  const result = await executor.acceptAll()
  assert.equal(result.accepted, 0)
  assert.equal(result.reachedEnd, true)
  assert.equal(r.clicks.length, 0)
})
