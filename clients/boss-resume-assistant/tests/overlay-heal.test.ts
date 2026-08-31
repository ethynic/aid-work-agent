/**
 * 弹层自愈原语测试（2026-08-31）：OverlayInspector 候选提取（文本+icon 关闭控件）/
 * OverlayDismissExecutor 白名单双保险与消失校验（CDP 主通道 + Win32 兜底）/
 * 两个 operation 的编排。全程离线 fixture，不连 Chrome。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { collectOverlayCandidates, DISMISS_TEXT_WHITELIST, isDismissText } from '../src/main/boss/OverlayInspector.js'
import { OverlayDismissExecutor, OverlayDismissError } from '../src/main/boss/OverlayDismissExecutor.js'
import { createBossOverlayInspectOperation } from '../src/main/operations/bossOverlayInspect.js'
import { createBossOverlayDismissOperation } from '../src/main/operations/bossOverlayDismiss.js'
import type { BossSession } from '../src/main/operations/bossContext.js'
import type { OpContext } from '../src/main/operations/types.js'
import type { DomSnapshot } from '../src/main/boss/domSnapshot.js'

/** 简单 snap：根节点视口 1249x1277，items 为文本+bounds（无 attributes → cls 为空串） */
function snap(items: Array<{ text: string; bounds: [number, number, number, number] }>): DomSnapshot {
  const strings: string[] = ['']
  const idx: number[] = [0]
  const values: number[] = [0]
  const bounds: [number, number, number, number][] = [[0, 0, 1249, 1277]]
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

/** 带 attributes 的 snap：一个 DIV cls="boss-popup__close" icon 元素（无文字，2026-08-31 真机形态） */
function iconSnap(): DomSnapshot {
  const strings: string[] = ['', 'DIV', 'class', 'boss-popup__close']
  const idx = [0, 1]
  const values = [0, -1]
  const bounds: [number, number, number, number][] = [[0, 0, 1249, 1277], [986, 372, 24, 26]]
  const attributes: number[][] = [[], [2, 3]]
  return {
    strings,
    documents: [
      {
        nodes: {
          nodeValue: { index: idx, value: values },
          contentDocumentIndex: { index: [], value: [] },
          attributes: { index: idx, value: attributes },
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

function silentCtx(): OpContext {
  return { signal: new AbortController().signal, progress: () => {} }
}

// ==================== OverlayInspector ====================

test('inspect：提取文本节点+坐标，按 y,x 排序，过滤视口外与空文本', () => {
  const s = snap([
    { text: '关闭', bounds: [600, 300, 50, 24] },
    { text: '新人礼包', bounds: [500, 200, 200, 40] }, // 弹层标题（y 更小）
    { text: '   ', bounds: [100, 100, 80, 20] }, // 纯空白 → 过滤
    { text: '视口外', bounds: [-50, 50, 60, 20] }, // 视口外 → 过滤
    { text: '底部', bounds: [100, 800, 60, 20] },
  ])
  const r = collectOverlayCandidates(s)
  assert.equal(r.viewport.width, 1249)
  assert.equal(r.viewport.height, 1277)
  assert.deepEqual(
    r.candidates.map((c) => c.text),
    ['新人礼包', '关闭', '底部'],
  )
  const close = r.candidates.find((c) => c.text === '关闭')!
  assert.deepEqual({ x: close.x, y: close.y, w: close.w, h: close.h }, { x: 600, y: 300, w: 50, h: 24 })
})

test('inspect：icon_candidates 捕获无文字关闭控件（class 含 close/guanbi）', () => {
  const r = collectOverlayCandidates(iconSnap())
  assert.equal(r.icon_candidates.length, 1)
  const icon = r.icon_candidates[0]!
  assert.equal(icon.icon_cls, 'boss-popup__close')
  assert.deepEqual({ x: icon.x, y: icon.y }, { x: 986, y: 372 })
})

test('inspect：超长文本截断到 24 字符加省略号', () => {
  const long = '甲'.repeat(30)
  const s = snap([{ text: long, bounds: [10, 10, 300, 30] }])
  const r = collectOverlayCandidates(s)
  assert.equal(r.candidates[0]!.text, '甲'.repeat(24) + '…')
})

// ==================== 白名单 ====================

test('白名单：关闭语义命中，领取/开通类拒绝', () => {
  for (const t of ['关闭', '知道了', '以后再说', '取消', '跳过', '×', '暂不需要']) {
    assert.equal(isDismissText(t), true, t)
  }
  for (const t of ['立即领取', '立即打开', '开通会员', '查看详情', '去下载']) {
    assert.equal(isDismissText(t), false, t)
  }
  assert.ok(DISMISS_TEXT_WHITELIST.length >= 15)
})

// ==================== OverlayDismissExecutor ====================

function clickRecorder() {
  const clicks: Array<{ point: { x: number; y: number }; viewport: { width: number; height: number } }> = []
  const browseClicks: Array<{ x: number; y: number }> = []
  return {
    clicks,
    browseClicks,
    click: async (point: { x: number; y: number }, viewport: { width: number; height: number }) => {
      clicks.push({ point, viewport })
    },
    clickBrowse: async (point: { x: number; y: number }) => {
      browseClicks.push(point)
    },
  }
}

test('dismiss：icon 关闭控件（无文字）→ CDP 主通道点击 → 消失校验通过', async () => {
  // 2026-08-31 真机实证：广告弹窗关闭 × 无文字，class 即其语义；CDP 合成点击有效、Win32 无效
  const r = clickRecorder()
  const executor = new OverlayDismissExecutor({
    snapshot: snapshotQueue([iconSnap(), snap([])]),
    clickBrowse: r.clickBrowse,
    click: r.click,
    sleep: async () => {},
  })
  const result = await executor.dismiss({ text: 'icon:boss-popup__close' })
  assert.equal(result.dismissed, true)
  assert.deepEqual(r.browseClicks, [{ x: 998, y: 385 }]) // bounds 中心
  assert.equal(r.clicks.length, 0) // CDP 命中，无需 Win32 兜底
})

test('dismiss：icon 引用 class 未命中关闭语义 → 拒绝', async () => {
  const r = clickRecorder()
  const executor = new OverlayDismissExecutor({
    snapshot: async () => snap([]),
    clickBrowse: r.clickBrowse,
    click: r.click,
    sleep: async () => {},
  })
  await assert.rejects(
    executor.dismiss({ text: 'icon:btn-submit-gold' }),
    (e: Error) => e instanceof OverlayDismissError && /close\/guanbi/.test(e.message),
  )
  assert.equal(r.browseClicks.length, 0)
})

test('dismiss：白名单文案 → CDP 点击 → 消失校验通过', async () => {
  const r = clickRecorder()
  const executor = new OverlayDismissExecutor({
    snapshot: snapshotQueue([
      snap([
        { text: '新人礼包', bounds: [500, 200, 200, 40] },
        { text: '关闭', bounds: [600, 300, 50, 24] },
        { text: '关闭', bounds: [1100, 30, 30, 30] }, // 页面右上角同名控件（文档序在后）
      ]),
      snap([{ text: '新人礼包', bounds: [500, 200, 200, 40] }]), // 点击后弹层关闭控件消失
    ]),
    clickBrowse: r.clickBrowse,
    click: r.click,
    sleep: async () => {},
  })
  const result = await executor.dismiss({ text: '关闭' })
  assert.equal(result.dismissed, true)
  // 取文档序最后一个匹配（右上角 × 形态的关闭）；CDP 主通道命中无需兜底
  assert.deepEqual(r.browseClicks, [{ x: 1115, y: 45 }])
  assert.equal(r.clicks.length, 0)
})

test('dismiss：CDP 点击未生效 → Win32 兜底 → 消失校验通过', async () => {
  const r = clickRecorder()
  const frameWithClose = snap([{ text: '关闭', bounds: [600, 300, 50, 24] }])
  const executor = new OverlayDismissExecutor({
    snapshot: snapshotQueue([frameWithClose, frameWithClose, snap([])]),
    clickBrowse: r.clickBrowse,
    click: r.click,
    sleep: async () => {},
  })
  const result = await executor.dismiss({ text: '关闭' })
  assert.equal(result.dismissed, true)
  assert.equal(r.browseClicks.length, 1) // 第一击 CDP
  assert.equal(r.clicks.length, 1) // 第二击 Win32 兜底
})

test('dismiss：非白名单文案直接拒绝，不点击', async () => {
  const r = clickRecorder()
  const executor = new OverlayDismissExecutor({
    snapshot: async () => snap([{ text: '立即领取', bounds: [600, 300, 80, 30] }]),
    clickBrowse: r.clickBrowse,
    click: r.click,
    sleep: async () => {},
  })
  await assert.rejects(
    executor.dismiss({ text: '立即领取' }),
    (e: Error) => e instanceof OverlayDismissError && /白名单/.test(e.message),
  )
  assert.equal(r.clicks.length, 0)
  assert.equal(r.browseClicks.length, 0)
})

test('dismiss：快照中无该控件 → 报错未找到', async () => {
  const r = clickRecorder()
  const executor = new OverlayDismissExecutor({
    snapshot: async () => snap([{ text: '新人礼包', bounds: [500, 200, 200, 40] }]),
    clickBrowse: r.clickBrowse,
    click: r.click,
    sleep: async () => {},
  })
  await assert.rejects(
    executor.dismiss({ text: '关闭' }),
    (e: Error) => e instanceof OverlayDismissError && /未找到/.test(e.message),
  )
  assert.equal(r.clicks.length, 0)
})

test('dismiss：双通道点击后控件仍在 → 报错未关闭', async () => {
  const r = clickRecorder()
  const frame = snap([
    { text: '新人礼包', bounds: [500, 200, 200, 40] },
    { text: '关闭', bounds: [600, 300, 50, 24] },
  ])
  const executor = new OverlayDismissExecutor({
    snapshot: snapshotQueue([frame, frame, frame]),
    clickBrowse: r.clickBrowse,
    click: r.click,
    sleep: async () => {},
  })
  await assert.rejects(
    executor.dismiss({ text: '关闭' }),
    (e: Error) => e instanceof OverlayDismissError && /未关闭/.test(e.message),
  )
  assert.equal(r.browseClicks.length, 1)
  assert.equal(r.clicks.length, 1)
})

// ==================== operations ====================

function fakeFactory(snaps: DomSnapshot[]) {
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
    clickBrowse: async () => {
      calls.push('browse')
    },
    mouseWheel: async () => {},
    pressEscape: async () => {},
    clickAndType: async () => {},
    captureFullpage: async () => Buffer.alloc(0),
    getUrl: async () => 'https://www.zhipin.com/web/chat/index',
    close: async () => {
      calls.push('close')
    },
  }
  return { factory: async () => session, calls }
}

test('operation boss_overlay_inspect：返回候选清单（含 icon）与视口，effect=none', async () => {
  const f = fakeFactory([iconSnap()])
  const op = createBossOverlayInspectOperation(f.factory)
  const r = await op.execute({}, silentCtx())
  assert.equal(r.success, true)
  assert.equal(r.effect, 'none')
  const data = r.data as {
    viewport: { width: number }
    candidates: Array<{ text: string }>
    icon_candidates: Array<{ icon_cls: string }>
  }
  assert.equal(data.viewport.width, 1249)
  assert.deepEqual(data.icon_candidates.map((c) => c.icon_cls), ['boss-popup__close'])
  assert.equal(f.calls.includes('click'), false)
})

test('operation boss_overlay_dismiss：happy 路径走 CDP 通道，非白名单 INVALID_ARGUMENT', async () => {
  const frame = snap([
    { text: '新人礼包', bounds: [500, 200, 200, 40] },
    { text: '关闭', bounds: [600, 300, 50, 24] },
  ])
  const f = fakeFactory([frame, snap([{ text: '新人礼包', bounds: [500, 200, 200, 40] }])])
  const op = createBossOverlayDismissOperation(f.factory)
  const r = await op.execute({ text: '关闭' }, silentCtx())
  assert.equal(r.success, true)
  assert.equal((r.data as { dismissed: boolean }).dismissed, true)
  assert.equal(f.calls.filter((c) => c === 'browse').length, 1)
  assert.equal(f.calls.filter((c) => c === 'click').length, 0)

  const f2 = fakeFactory([frame])
  const op2 = createBossOverlayDismissOperation(f2.factory)
  const r2 = await op2.execute({ text: '立即领取' }, silentCtx())
  assert.equal(r2.success, false)
  assert.equal(r2.code, 'INVALID_ARGUMENT')
  assert.equal(f2.calls.filter((c) => c === 'click').length, 0)
})
