import assert from 'node:assert/strict'
import test from 'node:test'
import { mkdtemp, readFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { createBossScreenshotOperation } from '../src/main/operations/bossScreenshot.js'
import { ResumeConsentExecutor, attachmentPreviewState } from '../src/main/boss/ResumeConsentExecutor.js'
import type { BossSession } from '../src/main/operations/bossContext.js'
import { actionSnapshot, text } from './chatActionFixture.js'
import { buildChatSnapshot } from './chatReadFixture.js'
import type { DomSnapshot } from '../src/main/boss/domSnapshot.js'
import { DetailGreetExecutor, type DetailGreetDeps } from '../src/main/boss/DetailGreetExecutor.js'
import { WinClickError } from '../src/main/input/WinMouseClicker.js'
import { detailSnap, listSnap, GREET_STRING, CONTINUE_STRING } from './detailGreetFixture.js'

const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a9gAAAABJRU5ErkJggg==', 'base64')
const ctx = () => ({ signal: new AbortController().signal, progress: () => {} })

test('independent: screenshot omits the extra DOM read unless export is explicitly requested', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'boss-independent-'))
  let reads = 0, closed = 0
  const op = createBossScreenshotOperation(async () => ({ captureFullpage: async () => png,
    snapshot: async () => { reads++; throw new Error('should not read page text') },
    close: async () => { closed++ },
  }) as unknown as BossSession)
  try {
    assert.equal((await op.execute({ output: join(dir, 'shot.png') }, ctx())).success, true)
    assert.equal(reads, 0)
    assert.equal(closed, 1)
  } finally { await rm(dir, { recursive: true, force: true }) }
})

test('independent: requested DOM export preserves textValue locally and excludes page text from result', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'boss-independent-'))
  const snapshot = actionSnapshot()
  snapshot.strings.push('synthetic private textarea value')
  Object.assign(snapshot.documents[0]!.nodes, { textValue: { index: [1], value: [snapshot.strings.length - 1] } })
  const op = createBossScreenshotOperation(async () => ({ captureFullpage: async () => png,
    snapshot: async () => snapshot, close: async () => {},
  }) as unknown as BossSession)
  try {
    const snapshot_output = join(dir, 'shot.json')
    const result = await op.execute({ output: join(dir, 'shot.png'), snapshot_output }, ctx())
    assert.equal(result.success, true)
    assert.deepEqual(JSON.parse(await readFile(snapshot_output, 'utf8')), snapshot)
    assert.equal(JSON.stringify(result).includes('synthetic private textarea value'), false)
  } finally { await rm(dir, { recursive: true, force: true }) }
})

test('independent: a failed requested snapshot is reported as failure and always disconnects', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'boss-independent-'))
  let closed = 0
  const op = createBossScreenshotOperation(async () => ({ captureFullpage: async () => png,
    snapshot: async () => { throw new Error('snapshot unavailable') }, close: async () => { closed++ },
  }) as unknown as BossSession)
  try {
    const result = await op.execute({ output: join(dir, 'shot.png'), snapshot_output: join(dir, 'shot.json') }, ctx())
    assert.equal(result.success, false)
    assert.equal(closed, 1)
  } finally { await rm(dir, { recursive: true, force: true }) }
})

test('independent: invalid or colliding export paths are rejected before session attachment', async () => {
  let attached = 0
  const op = createBossScreenshotOperation(async () => { attached++; throw new Error('must not attach') })
  for (const snapshot_output of ['', '  ', './same.png']) {
    assert.equal((await op.execute({ output: './same.png', snapshot_output }, ctx())).code, 'INVALID_ARGUMENT')
  }
  assert.equal(attached, 0)
})

const marker = '对方想发送加密附件简历给您，您是否同意'
const listRequest = text(marker, 230, 480, 300, 25)
const bar = [text(marker, 650, 1050, 300, 25), text('同意', 1050, 1050, 60, 25)]
const historical = [text(marker, 650, 400, 200, 25), text('同意', 820, 475, 50, 25)]

test('independent: encrypted processing acceptance can use new explicit notice while grey historical card remains', async () => {
  let phase = 0
  const clicks: { x: number; y: number }[] = []
  const executor = new ResumeConsentExecutor({
    snapshot: async () => actionSnapshot({ extra: phase === 0 ? [listRequest]
      : phase === 1 ? [listRequest, ...historical, ...bar]
      : [...historical, text('已同意接收简历', 700, 750, 200, 25)] }),
    click: async point => { clicks.push(point); phase++ }, sleep: async () => {},
  })
  assert.equal((await executor.acceptAll({ limit: 1 })).accepted, 1)
  assert.equal(clicks.length, 2)
  assert.ok(clicks[1]!.y > 1000, 'final submission must use the live processing bar, not the grey history card')
  assert.equal(executor.state.businessEffect.status, 'confirmed')
})

test('independent: new notice plus lingering live processing bar is unknown and is not submitted twice', async () => {
  let phase = 0, clicks = 0
  const executor = new ResumeConsentExecutor({
    snapshot: async () => actionSnapshot({ extra: phase === 0 ? [listRequest]
      : phase === 1 ? [listRequest, ...historical, ...bar]
      : [...historical, ...bar, text('已同意接收简历', 700, 750, 200, 25)] }),
    click: async () => { clicks++; phase++ }, sleep: async () => {},
  })
  await assert.rejects(executor.acceptAll({ limit: 1 }), /无法确认/)
  assert.equal(clicks, 2)
  assert.equal(executor.state.businessEffect.status, 'unknown')
  assert.equal(executor.state.execution.ordinaryRetriesUsed, 0)
})

function appendDocument(target: DomSnapshot, source: DomSnapshot): number {
  const indices = source.strings.map(value => {
    const found = target.strings.indexOf(value)
    return found >= 0 ? found : target.strings.push(value) - 1
  })
  const doc = source.documents[0]!
  doc.nodes.nodeValue = (doc.nodes.nodeValue as number[]).map(si => si < 0 ? si : indices[si]!)
  doc.nodes.nodeName = (doc.nodes.nodeName as number[]).map(si => indices[si]!)
  doc.nodes.attributes = (doc.nodes.attributes as number[][]).map(pairs => pairs.map(si => indices[si]!))
  return target.documents.push(doc) - 1
}

function independentPdf(opts: { attachmentCanvas?: boolean; frameY?: number; backgroundSections?: boolean } = {}): DomSnapshot {
  const snap = actionSnapshot({ extra: [
    { tag: 'IFRAME', cls: 'ordinary-frame', bounds: [10, 20, 200, 200] },
    { tag: 'IFRAME', cls: 'attachment-iframe', bounds: [248, opts.frameY ?? 50, 765, 894] },
    ...(opts.backgroundSections ? [text('个人优势', 700, 400), text('工作经历', 700, 450)] : []),
  ] })
  appendDocument(snap, buildChatSnapshot([{ tag: 'CANVAS', bounds: [0, 0, 1000, 1100] }]))
  appendDocument(snap, buildChatSnapshot(opts.attachmentCanvas ? [{ tag: 'CANVAS', bounds: [0, 0, 749, 1059] }] : []))
  const doc = snap.documents[0]!
  const names = doc.nodes.nodeName as number[]
  const owners = Array(names.length).fill(-1)
  let child = 1
  names.forEach((si, ni) => { if (snap.strings[si] === 'IFRAME') owners[ni] = child++ })
  doc.nodes.contentDocumentIndex = owners
  return snap
}

test('independent: dense document ownership binds PDF readiness to its actual child rather than another iframe', () => {
  assert.equal(attachmentPreviewState(independentPdf()), 'loading')
  assert.equal(attachmentPreviewState(independentPdf({ attachmentCanvas: true })), 'ready')
})

test('independent: background HTML sections cannot turn an unready attachment into a loaded preview', () => {
  assert.equal(attachmentPreviewState(independentPdf({ backgroundSections: true })), 'loading')
})

test('independent: document scrolling can hide an otherwise loaded attachment and proves it closed', () => {
  const snap = independentPdf({ attachmentCanvas: true })
  snap.documents[0]!.scrollOffsetY = 1500
  assert.equal(attachmentPreviewState(snap), 'closed')
})

function independentReopen(opts: { already?: boolean; blockOpen?: boolean; nativeUnknown?: boolean } = {}) {
  const name = '合成目标'
  let phase: 'initial' | 'list' | 'opened' | 'closed' = 'initial'
  const events: string[] = []
  const opened = detailSnap({ name, state: opts.already ? 'continue' : 'greet', canvasNameOnly: true, decoyRows: [] })
  const list = listSnap([{ name, buttonY: 514 }])
  if (opts.already) list.strings = list.strings.map(value => value === GREET_STRING ? CONTINUE_STRING : value)
  const deps: DetailGreetDeps = {
    snapshot: async () => phase === 'initial' || phase === 'opened' ? opened : list,
    click: async point => {
      if (point.x < 900) { events.push('open-card'); if (!opts.blockOpen) phase = 'opened' }
      else { events.push('submit-greet'); if (opts.nativeUnknown) throw new WinClickError('[BOSS_INPUT_UNKNOWN] native result lost', 1) }
    },
    pressEscape: async () => { events.push('escape'); phase = phase === 'initial' ? 'list' : 'closed' },
    sleep: async () => {},
  }
  return { name, deps, events }
}

test('independent: canvas-only identity can reopen a continue-contact card without greeting again', async () => {
  const h = independentReopen({ already: true })
  assert.deepEqual(await new DetailGreetExecutor(h.deps).greet({ name: h.name }), { greeted: false, already: true })
  assert.deepEqual(h.events, ['escape', 'open-card', 'escape'])
})

test('independent: reopening cannot replenish an already spent ordinary retry budget', async () => {
  const h = independentReopen({ blockOpen: true }), executor = new DetailGreetExecutor(h.deps)
  executor.state.retry()
  await assert.rejects(executor.greet({ name: h.name, dryRun: true }), /一次普通重试已用尽/)
  assert.deepEqual(h.events, ['escape', 'open-card'])
  assert.equal(executor.state.businessEffect.status, 'not_started')
})

test('independent: unknown final native greeting prevents all later identity rebuild inputs', async () => {
  const h = independentReopen({ nativeUnknown: true }), executor = new DetailGreetExecutor(h.deps)
  await assert.rejects(executor.greet({ name: h.name }), /BOSS_INPUT_UNKNOWN/)
  const events = [...h.events]
  assert.deepEqual(events, ['escape', 'open-card', 'submit-greet'])
  assert.equal(executor.state.businessEffect.status, 'unknown')
  await assert.rejects(executor.greet({ name: h.name }), /禁止重新打开或再次发送/)
  assert.deepEqual(h.events, events)
})
