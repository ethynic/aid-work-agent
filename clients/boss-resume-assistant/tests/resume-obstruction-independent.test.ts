/** A stable canvas and repeated pixels do not prove that screenshots contain the resume. */
import assert from 'node:assert/strict'
import test from 'node:test'
import { mkdtemp, rm, writeFile } from 'node:fs/promises'
import { existsSync } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { EventEmitter } from 'node:events'
import { FILTER_SNAPSHOT_OPTIONS } from '../src/main/boss/FilterSetter.js'
import { ResumeReader, ResumeReadError, ResumeObstructedError, assertResumeUnobstructed, locateResumeCanvas } from '../src/main/boss/ResumeReader.js'
import type { DomDocument, DomSnapshot } from '../src/main/boss/domSnapshot.js'
import { CdpGateway } from '../src/main/cdp/CdpGateway.js'
import type { CdpWebSocketLike } from '../src/main/cdp/CdpSocket.js'
import { listSnap, detailSnap } from './detailGreetFixture.js'
import { createBossResumeBatchOperation } from '../src/main/operations/bossResumeBatch.js'
import { withLocalRecovery } from '../src/main/boss/localOperationRecovery.js'
import type { BossSession } from '../src/main/operations/bossContext.js'
import { okResult, type BossOperation } from '../src/main/operations/types.js'
import type { VisualInspectArgs } from '../src/main/boss/visualRecovery.js'

type Rect = [number, number, number, number]
interface Node { name?: string; parent?: number; rect: Rect; paint: number; attrs?: Record<string, string>; style?: Record<string, string>; child?: number }
interface FixtureOptions { where?: 'root' | 'inner' | 'canvas' | 'hidden'; rect?: Rect; paint?: number; styles?: Record<string, string>; className?: string; role?: string; parentHidden?: boolean; parentClip?: boolean; noPaint?: boolean }
function fixture(options: FixtureOptions = {}): DomSnapshot {
  const strings: string[] = []
  const intern = (value: string) => { const found = strings.indexOf(value); if (found >= 0) return found; strings.push(value); return strings.length - 1 }
  const doc = (nodes: Node[]): DomDocument => ({
    nodes: { nodeValue: nodes.map(() => -1), nodeName: nodes.map(n => intern(n.name ?? 'DIV')),
      parentIndex: nodes.map(n => n.parent ?? -1), attributes: nodes.map(n => Object.entries(n.attrs ?? {}).flatMap(([k, v]) => [intern(k), intern(v)])),
      contentDocumentIndex: { index: nodes.flatMap((n, i) => n.child === undefined ? [] : [i]), value: nodes.flatMap(n => n.child === undefined ? [] : [n.child]) } },
    layout: { nodeIndex: nodes.map((_, i) => i), bounds: nodes.map(n => n.rect), paintOrders: nodes.map(n => n.paint),
      styles: nodes.map(n => FILTER_SNAPSHOT_OPTIONS.computedStyles.map(key => intern(n.style?.[key] ?? ({ display: 'block', position: 'static', visibility: 'visible', opacity: '1' } as Record<string, string>)[key] ?? 'auto'))) },
    scrollOffsetX: 0, scrollOffsetY: 0,
  })
  const root: Node[] = [
    { name: 'HTML', rect: [0, 0, 1261, 944], paint: 0 },
    { name: 'BODY', parent: 0, rect: [0, 0, 1261, 944], paint: 1 },
    { name: 'IFRAME', parent: 1, rect: [168, 40, 1093, 904], paint: 50, child: 1, attrs: { src: '/web/frame/recommend/' } },
  ]
  const inner: Node[] = [
    { name: 'HTML', rect: [0, 0, 1093, 904], paint: 1 },
    { name: 'BODY', parent: 0, rect: [0, 0, 1093, 904], paint: 2 },
    { parent: 1, rect: [0, 0, 1093, 904], paint: 100, attrs: { class: 'dialog-wrap active', 'data-type': 'boss-dialog' } },
    { parent: 2, rect: [40, 0, 1000, 904], paint: 101, attrs: { class: 'boss-popup__wrapper boss-dialog boss-dialog__wrapper dialog-lib-resume recommendV2', style: 'z-index:2008;' }, style: { position: 'fixed' } },
    { name: 'IFRAME', parent: 3, rect: [40, 0, 740, 904], paint: 105, child: 2, attrs: { src: '/web/frame/c-resume/' } },
  ]
  const canvas: Node[] = [
    { name: 'HTML', rect: [0, 0, 740, 904], paint: 10000 },
    { name: 'BODY', parent: 0, rect: [0, 0, 740, 904], paint: 10001 },
    { name: 'CANVAS', parent: 1, rect: [0, 0, 740, 904], paint: 10002 },
  ]
  const documents: Node[][] = [root, inner, canvas]
  if (options.where) {
    let target = options.where === 'root' ? root : options.where === 'inner' ? inner : canvas
    if (options.where === 'hidden') {
      root.push({ name: 'IFRAME', parent: 1, rect: [0, 0, 0, 0], paint: 999, child: 3 })
      target = [{ name: 'HTML', rect: [0, 0, 1261, 944], paint: 0 }, { name: 'BODY', parent: 0, rect: [0, 0, 1261, 944], paint: 1 }]
      documents.push(target)
    }
    let parent = 1
    if (options.parentHidden) {
      parent = target.length
      target.push({ parent: 1, rect: [0, 0, 1261, 944], paint: 999, style: { display: 'none' } })
    }
    if (options.parentClip) {
      parent = target.length
      target.push({ parent: 1, rect: [0, 0, 150, 100], paint: 999, style: { 'overflow-x': 'hidden', 'overflow-y': 'hidden', position: 'relative' } })
    }
    target.push({ parent, rect: options.rect ?? [250, 120, 600, 600], paint: options.paint ?? 20000,
      attrs: { class: options.className ?? 'boss-popup__wrapper boss-dialog boss-dialog__wrapper dialog-chatbot', role: options.role ?? 'dialog' },
      style: { position: 'fixed', ...options.styles } })
  }
  const result: DomSnapshot = { strings, documents: documents.map(doc) }
  if (options.noPaint) for (const document of result.documents) delete document.layout.paintOrders
  return result
}
const rectOf = (snap: DomSnapshot) => { const rect = locateResumeCanvas(snap); assert.ok(rect); return rect }

test('independent: the resume own modal and its nested iframe are valid content, not obstruction', () => {
  const snap = fixture(); assert.doesNotThrow(() => assertResumeUnobstructed(snap, rectOf(snap)))
})

test('independent: root popup above target iframe blocks even if child-local canvas paint order is numerically larger', () => {
  const snap = fixture({ where: 'root', paint: 51 })
  assert.throws(() => assertResumeUnobstructed(snap, rectOf(snap)), ResumeReadError)
})

test('independent: root dialog below target iframe is harmless despite a higher unrelated descendant order', () => {
  const snap = fixture({ where: 'root', paint: 49 })
  assert.doesNotThrow(() => assertResumeUnobstructed(snap, rectOf(snap)))
})

test('independent: popup in owning recommendation frame compares with canvas iframe owner, not cross-frame order', () => {
  const snap = fixture({ where: 'inner', rect: [100, 100, 650, 650], paint: 106 })
  assert.throws(() => assertResumeUnobstructed(snap, rectOf(snap)), ResumeReadError)
})

test('independent: a same-document modal above the canvas must not enter the stitched artifact', () => {
  const snap = fixture({ where: 'canvas', rect: [0, 100, 700, 700], paint: 10003 })
  assert.throws(() => assertResumeUnobstructed(snap, rectOf(snap)), ResumeReadError)
})

test('independent: a confirmed upper modal covering only a thin edge still contaminates resume pixels', () => {
  const snap = fixture({ where: 'root', rect: [208, 900, 740, 10], paint: 51 })
  assert.throws(() => assertResumeUnobstructed(snap, rectOf(snap)), ResumeReadError)
})

test('independent: pointer-events none does not make a visibly covering modal safe for screenshots', () => {
  const snap = fixture({ where: 'root', paint: 51, styles: { 'pointer-events': 'none' } })
  assert.throws(() => assertResumeUnobstructed(snap, rectOf(snap)), ResumeReadError)
})

test('independent: an ARIA alertdialog above the original resume is a modal without BOSS class names', () => {
  const snap = fixture({ where: 'root', paint: 51, className: 'notification', role: 'alertdialog' })
  assert.throws(() => assertResumeUnobstructed(snap, rectOf(snap)), ResumeReadError)
})

for (const [name, options] of [
  ['nonintersecting popup', { where: 'root', rect: [1100, 100, 100, 400] }],
  ['hidden popup', { where: 'root', styles: { display: 'none' } }],
  ['visibility-hidden popup', { where: 'root', styles: { visibility: 'hidden' } }],
  ['transparent popup', { where: 'root', styles: { opacity: '0' } }],
  ['hidden ancestor', { where: 'root', parentHidden: true }],
  ['invisible iframe owner', { where: 'hidden' }],
  ['missing layer metadata', { where: 'root', noPaint: true }],
  ['ordinary nonmodal corner promotion', { where: 'root', rect: [208, 900, 120, 20], className: 'promotion-banner', role: 'note' }],
  ['popup entirely clipped away by its own overflow ancestor', { where: 'root', parentClip: true, styles: { position: 'absolute' } }],
] as Array<[string, FixtureOptions]>) {
  test(`independent: ${name} cannot falsely reject a readable resume`, () => {
    const snap = fixture(options); assert.doesNotThrow(() => assertResumeUnobstructed(snap, rectOf(snap)))
  })
}

function readerHarness(snapshot: () => DomSnapshot, onCapture?: () => void, onWheel?: (count: number) => void) {
  let captures = 0, wheels = 0, stitches = 0
  const reader = new ResumeReader({ snapshot: async () => snapshot(),
    captureFullpage: async () => { captures++; onCapture?.(); return Buffer.alloc(1000) },
    wheel: async () => { wheels++; onWheel?.(wheels) }, sameView: async () => true, sleep: async () => {},
    stitch: async (_parts, _rect, output) => { stitches++; await writeFile(output, 'fake-image'); return { width: 740, height: 904, overlaps: [], seamMis: [] } } })
  return { reader, counts: () => ({ captures, wheels, stitches }) }
}

test('independent: initial obstruction rejects before scrolling, capture, stitching or saved output', async () => {
  const snap = fixture({ where: 'root', paint: 51 }), h = readerHarness(() => snap)
  await assert.rejects(h.reader.readResume(), ResumeReadError)
  assert.deepEqual(h.counts(), { captures: 0, wheels: 0, stitches: 0 })
})

test('independent: popup appearing during capture discards the image instead of certifying false bottom', async () => {
  let blocked = false
  const clear = fixture(), obstruction = fixture({ where: 'root', paint: 51 })
  const h = readerHarness(() => blocked ? obstruction : clear, () => { blocked = true })
  const directory = await mkdtemp(join(tmpdir(), 'resume-obstruction-test-')), output = join(directory, 'must-not-save.png')
  try {
    await assert.rejects(h.reader.readResume({ saveImageTo: output }), ResumeReadError)
    assert.equal(h.counts().captures, 1); assert.equal(h.counts().stitches, 0); assert.equal(existsSync(output), false)
  } finally { await rm(directory, { recursive: true, force: true }) }
})

test('independent: popup appearing after a segment scroll blocks the next capture before any contaminated frame', async () => {
  let blocked = false
  const clear = fixture(), obstruction = fixture({ where: 'root', paint: 51 })
  const h = readerHarness(() => blocked ? obstruction : clear, undefined, count => { if (count === 2) blocked = true })
  await assert.rejects(h.reader.readResume(), ResumeReadError)
  assert.deepEqual(h.counts(), { captures: 1, wheels: 2, stitches: 0 })
})

test('independent: a genuinely short, unobstructed resume can still confirm bottom using repeated pixels', async () => {
  const snap = fixture(), h = readerHarness(() => snap)
  const result = await h.reader.readResume()
  assert.equal(result.bottomReached, true); assert.equal(result.segments, 1)
  assert.equal(h.counts().captures, 3); assert.equal(h.counts().stitches, 1)
})

test('independent: paint metadata is an explicit read-only snapshot option and stays off by default', async () => {
  class Socket extends EventEmitter {
    readyState = 1
    CLOSED = 3
    sent: Array<{ method: string; params: Record<string, unknown> }> = []
    constructor() { super(); setTimeout(() => this.emit('open'), 0) }
    send(text: string) {
      const msg = JSON.parse(text); this.sent.push(msg)
      const result = msg.method === 'Target.attachToTarget' ? { sessionId: 'offline-test' } : { documents: [], strings: [] }
      queueMicrotask(() => this.emit('message', Buffer.from(JSON.stringify({ id: msg.id, result, ...(msg.sessionId ? { sessionId: msg.sessionId } : {}) }))))
    }
    close() { this.readyState = 3; queueMicrotask(() => this.emit('close')) }
    terminate() { this.readyState = 3 }
  }
  const socket = new Socket()
  const gateway = new CdpGateway({ socketFactory: () => socket as unknown as CdpWebSocketLike,
    fetchImpl: (async () => ({ json: async () => ({ webSocketDebuggerUrl: 'ws://127.0.0.1/offline-fake' }) })) as unknown as typeof fetch })
  try {
    await gateway.connect('http://127.0.0.1/offline-fake'); await gateway.attachToTarget('offline-target')
    await gateway.captureDomSnapshot(); await gateway.captureDomSnapshot({ includePaintOrder: true })
    const snapshots = socket.sent.filter(m => m.method === 'DOMSnapshot.captureSnapshot')
    assert.equal(snapshots.length, 2)
    assert.equal(snapshots[0]!.params.includePaintOrder, false)
    assert.equal(snapshots[1]!.params.includePaintOrder, true)
    assert.ok(socket.sent.every(m => !m.method.startsWith('Input.')))
  } finally { await gateway.close() }
})

/** Keep real BatchReader + operation + local wrapper; mock only image/desktop boundary read. */
async function batchOperationScenario(kind: 'first_blocked' | 'second_blocked' | 'ordinary_failure') {
  const names = ['甲小明', '乙小明'], rows = names.map((name, i) => ({ name, buttonY: 146 + i * 184 }))
  let detail = false, current = 0, repaired = false, reads = 0, rawCalls = 0, repairs = 0, paidObservations = 0
  const readNames: string[] = [], escapes: string[] = [], clickedNames: string[] = []
  const artifact = Buffer.from('first-valid-resume-is-preserved')
  const originalRead = ResumeReader.prototype.readResume
  ResumeReader.prototype.readResume = async () => {
    reads++; readNames.push(names[current]!)
    if (kind === 'ordinary_failure' && current === 0) throw new ResumeReadError('当前份拼接失败')
    if (!repaired && (kind === 'first_blocked' || kind === 'second_blocked' && current === 1)) throw new ResumeObstructedError('当前简历被外部客服弹窗遮挡')
    return { segments: 1, bottomReached: true, width: 740, height: 904, imageBuffer: artifact, seamMis: [], suspectSeams: [] }
  }
  const session: BossSession = {
    snapshot: async () => {
      const snap = detail ? detailSnap({ name: names[current]!, state: 'greet', canvasNameOnly: true, decoyRows: rows }) : listSnap(rows)
      snap.strings.push('筛选'); return snap
    },
    click: async point => { current = Math.abs(point.y - 138) < Math.abs(point.y - 322) ? 0 : 1; clickedNames.push(names[current]!); detail = true },
    pressEscape: async () => { escapes.push(names[current]!); detail = false },
    getTargetId: async () => 'offline-resume-obstruction-test',
    getUrl: async () => 'https://www.zhipin.com/web/chat/recommend', close: async () => {},
    clickBrowse: async () => assert.fail('no extra click channel'), clickAndType: async () => assert.fail('no typing'),
    mouseWheel: async () => assert.fail('read boundary is mocked; no desktop wheel'),
    captureFullpage: async () => { throw Error('read boundary is mocked; no browser capture') },
  }
  const raw = createBossResumeBatchOperation(async () => session)
  const counted: typeof raw = { name: raw.name, execute: async (args, ctx) => { rawCalls++; return raw.execute(args, ctx) } }
  const inspect: BossOperation<VisualInspectArgs> = { name: 'boss_overlay_inspect', execute: async args => {
    paidObservations++
    if (args.visual_context?.purpose === 'diagnose') assert.ok(!repaired)
    return okResult('offline-vision', '离线诊断', 'none', { ...(repaired ? {} : { recovery_ref: 'offline-close-ref' }), visual: {
      http_status: 200, state: 'completed', diagnosis: { obstruction: repaired ? 'absent' : 'present',
        goal_state: repaired ? 'satisfied' : 'not_satisfied', proposed_action: repaired ? null : { kind: 'dismiss' } } } })
  } }
  const dismiss: BossOperation<{ recovery_ref?: string }> = { name: 'boss_overlay_dismiss', execute: async () => {
    repairs++; repaired = true; return okResult('offline-click', '输入正常返回', 'none')
  } }
  try {
    const result = await withLocalRecovery(counted, { inspect, dismiss, env: {
      AIDWORK_RUNTIME_SERVER: 'https://offline.invalid', AIDWORK_RUNTIME_DEVICE_TOKEN: 'test-only' } }).execute(
      { limit: kind === 'first_blocked' ? 1 : 2 }, { signal: new AbortController().signal, progress: () => {} })
    return { result, reads, readNames, rawCalls, repairs, paidObservations, escapes, clickedNames, artifact }
  } finally { ResumeReader.prototype.readResume = originalRead }
}

test('independent: first blocked batch item propagates to local recovery before a valid resume can be read', async () => {
  const h = await batchOperationScenario('first_blocked'), v = h.result.data.visual_recovery as Record<string, unknown>
  assert.equal(h.result.success, true); assert.equal(v.state, 'recovered')
  assert.equal(h.rawCalls, 1, 'the retained invocation resumes its own checkpoint without entering raw execute again')
  assert.equal(h.repairs, 1); assert.equal(h.paidObservations, 2)
  assert.deepEqual(h.readNames, ['甲小明', '甲小明'])
  assert.ok(h.clickedNames.every(name => name === '甲小明'), 'the failed first target cannot advance to a different candidate')
  assert.equal((h.result.data.resumes as unknown[]).length, 1)
})

test('independent: later blocked batch item repairs page while preserving first image without rereading or advancing', async () => {
  const h = await batchOperationScenario('second_blocked'), v = h.result.data.visual_recovery as Record<string, unknown>
  assert.equal(h.result.success, true); assert.equal(v.state, 'recovered')
  assert.equal(h.rawCalls, 1); assert.equal(h.repairs, 1); assert.equal(h.paidObservations, 2)
  assert.deepEqual(h.readNames, ['甲小明', '乙小明', '乙小明'])
  assert.equal(h.clickedNames.filter(name => name === '甲小明').length, 1, 'the confirmed first candidate must not be clicked again during continuation')
  assert.equal(h.readNames.filter(name => name === '甲小明').length, 1, 'the confirmed image must be retained rather than regenerated')
  const resumes = h.result.data.resumes as Array<{ candidate_name: string; images: Array<{ base64: string }> }>
  assert.deepEqual(resumes.map(resume => resume.candidate_name), ['甲小明', '乙小明'])
  assert.deepEqual(Buffer.from(resumes[0]!.images[0]!.base64, 'base64'), h.artifact)
  // Phase 8 retains both the completed image and the unfinished original target.
  // A successful continuation reaches the requested quantity, rather than retaining the obstruction stop.
  assert.equal(v.page_restored, true)
  assert.equal(h.result.data.stop_reason, 'limit_reached')
})

test('independent: ordinary individual image error still closes safely and continues without paid recovery', async () => {
  const h = await batchOperationScenario('ordinary_failure'), v = h.result.data.visual_recovery as Record<string, unknown>
  assert.equal(h.result.success, true); assert.equal(v.state, 'not_needed')
  assert.equal(h.repairs, 0); assert.equal(h.paidObservations, 0); assert.equal(h.rawCalls, 1)
  assert.deepEqual(h.readNames, ['甲小明', '乙小明']); assert.deepEqual(h.escapes, ['甲小明', '乙小明'])
  const resumes = h.result.data.resumes as Array<{ candidate_name: string }>
  assert.equal(resumes.length, 1); assert.equal(resumes[0]!.candidate_name, '乙小明')
  assert.equal((h.result.data.failures as unknown[]).length, 1)
})
