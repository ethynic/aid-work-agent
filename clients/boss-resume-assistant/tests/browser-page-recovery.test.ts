import test, { type TestContext } from 'node:test'
import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import { createHash, randomUUID } from 'node:crypto'
import { rm } from 'node:fs/promises'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { CdpGateway } from '../src/main/cdp/CdpGateway.js'
import { CdpSocket, type CdpWebSocketLike } from '../src/main/cdp/CdpSocket.js'
import { CdpConnectionError, CdpTargetUnavailableError } from '../src/main/cdp/errors.js'
import { defaultSessionFactory, runBossOperation, type BossSession } from '../src/main/operations/bossContext.js'
import { okResult, type BossOperation, type OpContext } from '../src/main/operations/types.js'
import { rememberBrowserTarget, resolveBrowserTarget, updateBrowserTarget, type BrowserPageContext } from '../src/main/boss/browserTargetBinding.js'
import { JobSwitcher, JobSwitchError } from '../src/main/boss/JobSwitcher.js'
import { PageNavigator, type NavTarget } from '../src/main/boss/PageNavigator.js'
import { FilterSetter, type FilterSpec, type FilterApplyResult } from '../src/main/boss/FilterSetter.js'
import { WinMouseClicker } from '../src/main/input/WinMouseClicker.js'
import { withLocalRecovery } from '../src/main/boss/localOperationRecovery.js'
import type { DomSnapshot } from '../src/main/boss/domSnapshot.js'

const entry = 'https://www.zhipin.com/web/chat/recommend'
const snapshot: DomSnapshot = { documents: [], strings: ['筛选'] }

// Exercise the real factory, context guards and disk binding CAS. Only Chrome
// transport and job/filter input primitives are replaced; no browser is launched.
async function fixture(t: TestContext, saved?: BrowserPageContext) {
  const previous = process.env.AID_BOSS_AUTO_CHROME
  process.env.AID_BOSS_AUTO_CHROME = '0'
  t.after(() => { if (previous === undefined) delete process.env.AID_BOSS_AUTO_CHROME; else process.env.AID_BOSS_AUTO_CHROME = previous })
  const port = 56219, original = 'test-' + randomUUID()
  const targets = new Set([original])
  const ref = await rememberBrowserTarget(original, port)
  t.after(async () => {
    const root = join(tmpdir(), 'aid-boss-targets')
    await rm(join(root, ref + '.json'), { force: true })
    for (const target of targets) await rm(join(root, createHash('sha256').update(`${port}:${target}`).digest('hex') + '.target'), { force: true })
  })
  if (saved) await updateBrowserTarget(ref, port, original, b => ({ ...b, page_context: saved }))
  const state = { missing: false, connectFails: false, current: original, url: entry,
    created: 0, closed: 0, job: 'Default job', selected: [] as string[], filters: [] as unknown[],
    pendingAttach: [] as boolean[], createUrl: entry, loadingSnapshots: 0, snapshots: 0, navigations: [] as NavTarget[],
    onSnapshot: undefined as (() => void) | undefined, toolbar: '筛选', jobReadable: true,
    applyResult: { applied: true, skippedFilters: [], substitutions: [], filterCount: 1,
      appliedRows: [], firstDegreeOnly: false,
      membership: { status: 'unknown', source: 'unknown', evidence: [] } } as FilterApplyResult }
  t.mock.method(CdpGateway.prototype, 'connect', async () => { if (state.connectFails) throw new CdpConnectionError() })
  t.mock.method(CdpGateway.prototype, 'attachToBoundBossTarget', async (target: string, allowLogin = false) => {
    state.pendingAttach.push(allowLogin)
    if (state.missing && target === original) throw new CdpTargetUnavailableError()
    state.current = target
    return { targetId: target, sessionId: 'fixture-session' }
  })
  t.mock.method(CdpGateway.prototype, 'attachToRecommendPage', async () => ({ targetId: state.current, sessionId: 'fixture-session' }))
  t.mock.method(CdpGateway.prototype, 'createBossPage', async (_endpoint: string, url: string) => {
    assert.equal(url, entry)
    state.created++
    state.current = 'rebuilt-' + randomUUID()
    targets.add(state.current)
    state.url = state.createUrl
    return { targetId: state.current, sessionId: 'fixture-session' }
  })
  t.mock.method(CdpGateway.prototype, 'getTargetId', () => state.current)
  t.mock.method(CdpGateway.prototype, 'getBrowserInstanceId', () => 'b'.repeat(64))
  t.mock.method(CdpGateway.prototype, 'getTargets', async () => [{ targetId: state.current, type: 'page', title: '', url: state.url }])
  t.mock.method(CdpGateway.prototype, 'captureDomSnapshot', async () => {
    state.snapshots++
    state.onSnapshot?.()
    return state.loadingSnapshots-- > 0 ? { documents: [], strings: [] } : { ...snapshot, strings: [state.toolbar] }
  })
  t.mock.method(CdpGateway.prototype, 'close', async () => { state.closed++ })
  t.mock.method(WinMouseClicker.prototype, 'click', async () => { assert.fail('factory must not send unguarded native input') })
  t.mock.method(JobSwitcher.prototype, 'locateJobBox', (snap: DomSnapshot) => {
    if (!snap.strings.length || !state.jobReadable) throw new JobSwitchError('fixture toolbar still loading')
    return { name: state.job, city: '', salary: '', point: { x: 0, y: 0 }, pending: false }
  })
  t.mock.method(JobSwitcher.prototype, 'selectJob', async (args: { jobName: string }) => {
    state.selected.push(args.jobName); state.job = args.jobName; return { switched: true }
  })
  t.mock.method(FilterSetter.prototype, 'clear', async () => { state.filters.push({ clear: true }) })
  t.mock.method(FilterSetter.prototype, 'apply', async (spec: FilterSpec) => {
    state.filters.push(spec); return state.applyResult
  })
  t.mock.method(PageNavigator.prototype, 'navigate', async (target: NavTarget) => {
    state.navigations.push(target)
    state.url = target === 'recommend' ? entry : 'https://www.zhipin.com/web/chat/index'
    return { clicked: true, via: 'menu' as const }
  })
  const ctx: OpContext = { browserTargetRef: ref, cdpPort: port, recoverBrowserTarget: true,
    signal: new AbortController().signal, progress() {} }
  return { state, ref, port, original, ctx }
}

test('healthy old binding reads normally without any optional page context', async t => {
  const f = await fixture(t)
  let reads = 0
  const result = await runBossOperation({ name: 'boss_resume_batch', kind: 'readonly' }, f.ctx,
    defaultSessionFactory, () => null, async session => { await session.snapshot(); reads++; return { message: 'read' } })
  assert.equal(result.success, true)
  assert.equal(reads, 1)
  assert.equal(f.state.created, 0)
  assert.deepEqual(f.state.selected, [])
  assert.deepEqual(f.state.filters, [])
})

for (const withRef of [true, false]) {
  test('ordinary read preserves effective filters with no avatar and a filter badge, caller reference=' + withRef, async t => {
    const filters = { spec: { filters: [{ label: '学历要求', options: ['本科'] }] } }
    const f = await fixture(t, { entry_url: entry, job_name: 'Original job', filters })
    f.state.toolbar = '筛选·1'; f.state.job = 'Original job'
    const result = await runBossOperation({ name: 'boss_resume_batch', kind: 'readonly' },
      { ...f.ctx, browserTargetRef: withRef ? f.ref : undefined, recoverBrowserTarget: false }, defaultSessionFactory,
      () => null, async session => { await session.snapshot(); return { message: 'ordinary read' } })
    assert.equal(result.success, true)
    assert.equal(result.data.browser_target_ref, f.ref, 'discovering a target must recover the same saved context')
    assert.deepEqual((await resolveBrowserTarget(f.ref, f.port)).page_context?.filters, filters)
    assert.deepEqual(f.state.filters, [], 'capturing recovery metadata must not reset active page filters')
    assert.deepEqual(f.state.selected, [])
  })
}

test('successful boss_filter with a null optional account saves effective rows through the real operation lifecycle', async t => {
  const f = await fixture(t, { entry_url: entry, job_name: 'Original job', filters: { clear: true } })
  f.state.toolbar = '筛选·1'; f.state.job = 'Original job'
  const rows = [{ label: '学历要求', options: ['本科'] }]
  const result = await runBossOperation({ name: 'boss_filter', kind: 'write' },
    { ...f.ctx, recoverBrowserTarget: false }, defaultSessionFactory, () => null,
    async () => ({ message: 'applied', data: { applied: true, applied_rows: rows, account_ref: null,
      age: { min: 25, max: 40 }, first_degree_only: true } }))
  assert.equal(result.success, true)
  assert.deepEqual((await resolveBrowserTarget(f.ref, f.port)).page_context?.filters, { spec: {
    filters: rows, age: { min: 25, max: 40 }, first_degree_only: true,
  } })
})

test('a temporarily unreadable avatar and job retain the prior known account and filter context', async t => {
  const filters = { spec: { filters: [{ label: '学历要求', options: ['本科'] }] } }
  const f = await fixture(t, { entry_url: entry, job_name: 'Original job', account_ref: 'a'.repeat(64), filters })
  f.state.toolbar = '筛选·1'; f.state.jobReadable = false
  const result = await runBossOperation({ name: 'boss_resume_batch', kind: 'readonly' },
    { ...f.ctx, recoverBrowserTarget: false }, defaultSessionFactory, () => null,
    async session => { await session.snapshot(); return { message: 'ordinary read' } })
  assert.equal(result.success, true)
  const context = (await resolveBrowserTarget(f.ref, f.port)).page_context
  assert.equal(context?.account_ref, 'a'.repeat(64))
  assert.equal(context?.job_name, 'Original job')
  assert.deepEqual(context?.filters, filters)
  assert.deepEqual(f.state.selected, [])
  assert.deepEqual(f.state.filters, [])
})

test('missing target rebuilds the saved job and effective filters on the same opaque reference', async t => {
  const spec = { filters: [{ label: '学历要求', options: ['本科'] }] }
  const saved: BrowserPageContext = { entry_url: entry, job_name: 'Original job', filters: { spec }, managed_profile_dir: 'C:\\fixture-profile' }
  const f = await fixture(t, saved); f.state.missing = true
  const session = await defaultSessionFactory(f.ctx)
  try {
    const binding = await resolveBrowserTarget(f.ref, f.port)
    assert.equal(f.state.created, 1)
    assert.notEqual(binding.target_id, f.original)
    assert.equal(binding.target_id, await session.getTargetId!())
    assert.equal(binding.restore_pending, false)
    assert.deepEqual(f.state.selected, ['Original job'])
    assert.deepEqual(f.state.filters, [spec])
    assert.equal(binding.page_context?.managed_profile_dir, saved.managed_profile_dir)
  } finally { await session.close() }
})

test('login dependency preserves the rebuilt target and resumes it after login without opening another page', async t => {
  const f = await fixture(t, { entry_url: entry, job_name: 'Original job', filters: { clear: true } })
  f.state.missing = true; f.state.createUrl = 'https://www.zhipin.com/web/user/login'
  await assert.rejects(defaultSessionFactory(f.ctx), { code: 'NOT_LOGGED_IN' })
  const waiting = await resolveBrowserTarget(f.ref, f.port)
  assert.equal(waiting.restore_pending, true)
  assert.equal(waiting.target_id, f.state.current)
  assert.deepEqual(f.state.selected, [])
  assert.deepEqual(f.state.filters, [])
  f.state.url = entry
  const session = await defaultSessionFactory(f.ctx)
  try {
    const restored = await resolveBrowserTarget(f.ref, f.port)
    assert.equal(restored.target_id, waiting.target_id)
    assert.equal(restored.restore_pending, false)
    assert.equal(f.state.created, 1)
    assert.deepEqual(f.state.pendingAttach, [false, true])
    assert.deepEqual(f.state.selected, ['Original job'])
    assert.deepEqual(f.state.filters, [{ clear: true }])
  } finally { await session.close() }
})

test('bound browser connection failure without an original profile reports the missing dependency', async t => {
  const f = await fixture(t); f.state.connectFails = true
  await assert.rejects(defaultSessionFactory(f.ctx), error => {
    assert.equal((error as { code: string }).code, 'UI_CHANGED')
    assert.match((error as Error).message, /无法确定原持久 profile/)
    return true
  })
  assert.equal(f.state.created, 0)
  assert.equal(f.state.closed, 1)
  assert.deepEqual(f.state.selected, [])
})

test('an unavailable optional account hash does not pretend the original account logged out', async t => {
  const f = await fixture(t, { entry_url: entry, account_ref: 'a'.repeat(64), job_name: 'Original job', filters: { clear: true } })
  f.state.missing = true
  const session = await defaultSessionFactory(f.ctx)
  try {
    assert.equal((await resolveBrowserTarget(f.ref, f.port)).restore_pending, false)
    assert.deepEqual(f.state.filters, [{ clear: true }])
  } finally { await session.close() }
})

test('ordinary filters with historical account hints restore without treating them as fresh membership evidence', async t => {
  const rows = [{ label: '学历要求', options: ['本科'] }]
  const saved: BrowserPageContext = { entry_url: entry, account_ref: 'a'.repeat(64), job_name: 'Original job',
    filters: { spec: { filters: rows, membership_account_ref: 'a'.repeat(64), membership_hint: 'vip' } } }
  const f = await fixture(t, saved); f.state.missing = true
  let reads = 0
  const result = await runBossOperation({ name: 'boss_resume_batch', kind: 'readonly' }, f.ctx,
    defaultSessionFactory, () => null, async session => { await session.snapshot(); reads++; return { message: 'read' } })
  assert.equal(result.success, true)
  assert.equal(reads, 1)
  assert.deepEqual(f.state.filters, [{ filters: rows }], 'only original conditions reach the setter; current membership is its responsibility')
  assert.equal((await resolveBrowserTarget(f.ref, f.port)).page_context?.account_ref, saved.account_ref)
})

const incompleteFilters: Array<{ name: string; result: Partial<FilterApplyResult>; code: string }> = [
  { name: 'not applied', result: { applied: false }, code: 'MEMBERSHIP_REQUIRED' },
  { name: 'VIP condition skipped', result: { skippedFilters: [{ label: '年龄', reason: 'non_vip' }] }, code: 'MEMBERSHIP_REQUIRED' },
  { name: 'option substituted', result: { substitutions: [{ row: '学历要求', requested: '本科', matched: '大专' }] }, code: 'UI_CHANGED' },
]
for (const scenario of incompleteFilters) {
  test('incomplete filter restoration blocks all body reads: ' + scenario.name, async t => {
    const f = await fixture(t, { entry_url: entry, job_name: 'Original job',
      filters: { spec: { filters: [{ label: '学历要求', options: ['本科'] }] } } })
    f.state.missing = true
    Object.assign(f.state.applyResult, scenario.result)
    let reads = 0
    const result = await runBossOperation({ name: 'boss_resume_batch', kind: 'readonly' }, f.ctx,
      defaultSessionFactory, () => null, async session => { await session.snapshot(); reads++; return { message: 'must not read relaxed conditions' } })
    assert.equal(result.success, false)
    assert.equal(result.code, scenario.code)
    assert.equal(reads, 0)
    assert.equal(f.state.filters.length, 1)
    assert.equal((await resolveBrowserTarget(f.ref, f.port)).restore_pending, true)
  })
}

test('missing original context refuses a closed old target before creating or reading a default job', async t => {
  const f = await fixture(t); f.state.missing = true
  await assert.rejects(defaultSessionFactory(f.ctx), error => {
    assert.match((error as Error).message, /缺少原职位及有效筛选记录/)
    return true
  })
  assert.equal(f.state.created, 0)
  assert.deepEqual(f.state.selected, [])
  assert.deepEqual(f.state.filters, [])
  assert.equal((await resolveBrowserTarget(f.ref, f.port)).target_id, f.original)
})

test('rebuilt page waits through loading snapshots before restoring the original job and filters', async t => {
  const f = await fixture(t, { entry_url: entry, job_name: 'Original job', filters: { clear: true } })
  f.state.missing = true; f.state.loadingSnapshots = 3
  const session = await defaultSessionFactory(f.ctx)
  try {
    assert.ok(f.state.snapshots >= 4)
    assert.equal(f.state.created, 1)
    assert.deepEqual(f.state.selected, ['Original job'])
    assert.deepEqual(f.state.filters, [{ clear: true }])
    assert.equal((await resolveBrowserTarget(f.ref, f.port)).restore_pending, false)
  } finally { await session.close() }
})

test('cancelling page readiness leaves the same pending target and sends no job or filter input', async t => {
  const f = await fixture(t, { entry_url: entry, job_name: 'Original job', filters: { clear: true } })
  f.state.missing = true; f.state.loadingSnapshots = 1000
  const controller = new AbortController()
  let timer: NodeJS.Timeout | undefined
  f.state.onSnapshot = () => { timer ??= setTimeout(() => controller.abort(), 10) }
  try {
    await assert.rejects(defaultSessionFactory({ ...f.ctx, signal: controller.signal }), /cancel/i)
    assert.equal(f.state.created, 1)
    assert.equal((await resolveBrowserTarget(f.ref, f.port)).restore_pending, true)
    assert.deepEqual(f.state.selected, [])
    assert.deepEqual(f.state.filters, [])
    assert.equal(f.state.closed, 1)
  } finally { clearTimeout(timer) }
})

test('login redirect to chat navigates back to the saved recommendation entry before restoring filters', async t => {
  const f = await fixture(t, { entry_url: entry, job_name: 'Original job', filters: { clear: true } })
  f.state.missing = true; f.state.createUrl = 'https://www.zhipin.com/web/user/login'
  await assert.rejects(defaultSessionFactory(f.ctx), { code: 'NOT_LOGGED_IN' })
  f.state.url = 'https://www.zhipin.com/web/chat/index'
  const session = await defaultSessionFactory(f.ctx)
  try {
    assert.deepEqual(f.state.navigations, ['recommend'])
    assert.equal(await session.getUrl(), entry)
    assert.equal(f.state.created, 1)
    assert.deepEqual(f.state.filters, [{ clear: true }])
  } finally { await session.close() }
})

test('rebuilt target is indexed to the original reference so a later batch and another rebuild retain filters', async t => {
  const spec = { filters: [{ label: '学历要求', options: ['本科'] }] }
  const f = await fixture(t, { entry_url: entry, job_name: 'Original job', filters: { spec } })
  f.state.missing = true
  const first = await defaultSessionFactory(f.ctx)
  const rebuiltTarget = (await resolveBrowserTarget(f.ref, f.port)).target_id
  await first.close()
  assert.equal(await rememberBrowserTarget(rebuiltTarget, f.port), f.ref)
  assert.deepEqual((await resolveBrowserTarget(f.ref, f.port)).page_context?.filters, { spec })
  // Retire the rebuilt target too, then exercise the next invocation's factory.
  const attach = CdpGateway.prototype.attachToBoundBossTarget
  t.mock.method(CdpGateway.prototype, 'attachToBoundBossTarget', async function (this: CdpGateway, target: string, allowLogin = false) {
    if (target === rebuiltTarget) throw new CdpTargetUnavailableError()
    return attach.call(this, target, allowLogin)
  })
  const second = await defaultSessionFactory(f.ctx)
  try {
    const latest = await resolveBrowserTarget(f.ref, f.port)
    assert.equal(f.state.created, 2)
    assert.notEqual(latest.target_id, rebuiltTarget)
    assert.deepEqual(latest.page_context?.filters, { spec })
    assert.deepEqual(f.state.filters, [spec, spec])
    assert.equal(await rememberBrowserTarget(latest.target_id, f.port), f.ref)
  } finally { await second.close() }
})

for (const message of ['Session with given id not found', 'No target with given id', 'Target closed', 'Method not found']) {
  test('real socket protocol classification survives operation mapping: ' + message, async () => {
    class ProtocolSocket extends EventEmitter implements CdpWebSocketLike {
      readyState = 1; CLOSED = 3
      send(data: string) {
        const request = JSON.parse(data)
        queueMicrotask(() => this.emit('message', JSON.stringify({ id: request.id, sessionId: request.sessionId,
          error: { code: -32000, message } })))
      }
      close() { this.readyState = 3; this.emit('close') }
    }
    const transport = new ProtocolSocket()
    const socket = new CdpSocket({ socketFactory: () => { queueMicrotask(() => transport.emit('open')); return transport } })
    await socket.connect('ws://fixture.invalid')
    let resumes = 0, executions = 0, diagnoses = 0
    const session = { snapshot: () => socket.send('DOMSnapshot.captureSnapshot', {}, 'old-session'), close: () => socket.close() } as BossSession
    const operation: BossOperation<{}> = { name: 'boss_resume_batch', execute(_args, ctx) {
      executions++
      ctx.recoveryContinuation!.resume = async () => { resumes++; return okResult('original', 'continued', 'none') }
      return runBossOperation({ kind: 'readonly', name: this.name }, ctx, async () => session, () => null,
        async live => { await live.snapshot(); return { message: 'unreachable' } })
    } }
    const result = await withLocalRecovery(operation, { sleep: async () => {}, env: {},
      inspect: { name: 'boss_overlay_inspect', async execute() { diagnoses++; throw Error('no paid diagnosis') } } })
      .execute({}, { signal: new AbortController().signal, progress() {} })
    const transient = message !== 'Method not found'
    assert.equal(result.success, transient)
    assert.equal(executions, 1)
    assert.equal(resumes, transient ? 1 : 0, 'unsupported protocol methods must not loop as transient failures')
    assert.equal(diagnoses, 0)
  })
}
