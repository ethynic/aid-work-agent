import assert from 'node:assert/strict'
import test from 'node:test'
import { DetailGreetExecutor, type DetailGreetDeps } from '../src/main/boss/DetailGreetExecutor.js'
import { loadedSnapshot, type Card } from './loadedCandidateFixture.js'
import { detailSnap } from './detailGreetFixture.js'
import { WinClickError } from '../src/main/input/WinMouseClicker.js'

const NAME = '合成目标'
function loadedNavigation(opts: {
  y: number; initialScroll?: number; sent?: boolean; freeze?: boolean;
  afterScroll?: 'replace' | 'disappear'; duplicate?: boolean; boundaryBelow?: boolean; guard?: boolean;
}) {
  let scroll = opts.initialScroll ?? 0, opened = false
  const wheels: Array<{ x: number; y: number; deltaY: number }> = []
  const clicks: Array<{ x: number; y: number }> = []
  const deps: DetailGreetDeps = {
    snapshot: async () => {
      if (opened) return detailSnap({ name: NAME, state: opts.sent ? 'continue' : 'greet', canvasNameOnly: true })
      const changed = wheels.length > 0
      const cards: Card[] = [{ name: changed && opts.afterScroll === 'disappear' ? '另一候选人' : NAME,
        y: opts.y, sent: opts.sent, advantage: changed && opts.afterScroll === 'replace' ? '另一人的资料' : '稳定的候选人资料' }]
      if (opts.duplicate) cards.push({ name: NAME, y: opts.y + 900, advantage: '另一同名人的资料' })
      if (opts.boundaryBelow) cards.push({ name: '边界外推荐', y: 2000, advantage: '推荐资料' })
      return loadedSnapshot(cards, scroll, opts.boundaryBelow ? 1 : undefined)
    },
    scroll: async (x, y, deltaY) => {
      wheels.push({ x, y, deltaY })
      // Fixture layout is device pixels and root clientRects are CSS pixels at DPR 2.
      if (!opts.freeze) scroll = Math.max(0, scroll + deltaY * 2)
    },
    click: async point => { clicks.push(point); if (opts.guard) throw new WinClickError('guard refused', 2); opened = true },
    pressEscape: async () => { throw new Error('No residual detail exists before navigation') },
    sleep: async () => {},
  }
  return { deps, wheels, clicks }
}

test('independent: opening a loaded target above the viewport scrolls upward instead of scanning down', async () => {
  const h = loadedNavigation({ y: 300, initialScroll: 1200 })
  assert.deepEqual(await new DetailGreetExecutor(h.deps).openDetail({ name: NAME }), { name: NAME })
  assert.equal(h.wheels.length, 1)
  assert.ok(h.wheels[0]!.deltaY < 0)
  assert.equal(h.clicks.length, 1)
  assert.ok(h.clicks[0]!.y > 160 && h.clicks[0]!.y < 800)
})

test('independent: opening a loaded target below the viewport uses DPR 2 wheel conversion and fresh name position', async () => {
  const h = loadedNavigation({ y: 1600 })
  await new DetailGreetExecutor(h.deps).openDetail({ name: NAME })
  assert.deepEqual(h.wheels, [{ x: 275, y: 260, deltaY: 600 }])
  assert.deepEqual(h.clicks, [{ x: 320, y: 475 }])
})

test('independent: an offscreen continue-contact card remains an eligible detail target', async () => {
  const h = loadedNavigation({ y: 1600, sent: true })
  await new DetailGreetExecutor(h.deps).openDetail({ name: NAME })
  assert.equal(h.wheels.length, 1)
  assert.equal(h.clicks.length, 1)
})

test('independent: all loaded same-name cards participate in ambiguity, including an offscreen duplicate', async () => {
  const h = loadedNavigation({ y: 300, duplicate: true })
  await assert.rejects(new DetailGreetExecutor(h.deps).openDetail({ name: NAME }), /姓名不唯一/)
  assert.equal(h.wheels.length, 0)
  assert.equal(h.clicks.length, 0)
})

for (const afterScroll of ['replace', 'disappear'] as const) {
  test(`independent: a target that ${afterScroll}s during positioning cannot authorize a cached click`, async () => {
    const h = loadedNavigation({ y: 1600, afterScroll })
    await assert.rejects(new DetailGreetExecutor(h.deps).openDetail({ name: NAME }), /卡片已变化/)
    assert.equal(h.wheels.length, 1)
    assert.equal(h.clicks.length, 0)
  })
}

test('independent: a loaded target that never advances stops after three positioning inputs without clicking', async () => {
  const h = loadedNavigation({ y: 1600, freeze: true }), executor = new DetailGreetExecutor(h.deps)
  await assert.rejects(executor.openDetail({ name: NAME }), /无法进入可操作视窗/)
  assert.equal(h.wheels.length, 3)
  assert.equal(h.clicks.length, 0)
  assert.equal(executor.openDetailExecution.ordinaryRetriesUsed, 0)
})

test('independent: a visible recommendation boundary does not discard the original loaded target above it', async () => {
  const h = loadedNavigation({ y: 300, initialScroll: 1600, boundaryBelow: true })
  await new DetailGreetExecutor(h.deps).openDetail({ name: NAME })
  assert.equal(h.wheels.length, 1)
  assert.ok(h.wheels[0]!.deltaY < 0)
  assert.equal(h.clicks.length, 1)
})

test('independent: a guarded name click retains prior scroll input facts but cannot claim detail ownership', async () => {
  const h = loadedNavigation({ y: 1600, guard: true }), executor = new DetailGreetExecutor(h.deps)
  await assert.rejects(executor.openDetail({ name: NAME }), /guard refused/)
  assert.equal(h.wheels.length, 1)
  assert.equal(h.clicks.length, 1)
  assert.equal(executor.openDetailExecution.inputStatus, 'attempted')
  assert.equal((await executor.verifyOpenDetail()).status, 'inconclusive')
})
