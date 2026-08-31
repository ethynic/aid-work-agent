/**
 * M3 operation 契约测试（mock runDriverFn / verifyRefFn，绝不触达真实企微/驱动）。
 *
 * wecom_unread_list：成功聚合 / name 过滤 / 单元素解包防御 / 非法参数 / UI_CHANGED 透传。
 * wecom_history_read：成功（target_ref + 驱动参数）/ 过期 / 非法 max_pages、since_days /
 *   artifact 目录缺失 / UI_CHANGED 透传。
 * wecom_watch_poll：首轮新会话全量事件 + 水位推进 / 同内容不重发（unread 不增 → tick，
 *   不调历史驱动）/ unread 增大取增量尾 / 读取成功 last_unread 归零（读取后来 1 条仍触发）/
 *   会话消失 last_unread 归零 / 直点行 UI_CHANGED 降级搜索 / 读取失败不推进水位 / 状态文件损坏容错。
 * watchState：损坏 JSON / 结构不符 / 单条损坏 / roundtrip。
 */
import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test, { after } from 'node:test'
import { createWecomUnreadListOperation } from '../src/operations/unreadList.js'
import { createWecomHistoryReadOperation } from '../src/operations/historyRead.js'
import {
  computeDelta,
  createWecomWatchPollOperation,
  lastMessageNorm,
  normalizeChatText,
  type WatchEvent,
} from '../src/operations/watchPoll.js'
import { CancelledError, CodedOperationError, type OpContext } from '../src/operations/types.js'
import type { RunPowerShellDriverFn } from '../src/platform/powershell.js'
import { loadWatchState, saveWatchState } from '../src/platform/watchState.js'

function silentCtx(): OpContext {
  return { signal: new AbortController().signal, progress: () => {} }
}

const tempDirs: string[] = []
function tempDir(prefix: string): string {
  const dir = mkdtempSync(join(tmpdir(), prefix))
  tempDirs.push(dir)
  return dir
}
after(() => {
  for (const d of tempDirs) rmSync(d, { recursive: true, force: true })
})

// ---------- wecom_unread_list ----------

test('unread_list：成功聚合 [{name,preview,unread_count,x,y}]，effect=none', async () => {
  const op = createWecomUnreadListOperation({
    runDriverFn: async (opts) => {
      assert.ok(opts.script.endsWith('unread-list.ps1'))
      return {
        unread: [
          { name: '微信客服', preview: '查收昨日企业客服数…', unread_count: 7, x: 254, y: 149 },
          { name: '邮件提醒', preview: '企业已开启邮件功能', unread_count: 1, x: 254, y: 338 },
        ],
      }
    },
  })
  const r = await op.execute({}, silentCtx())
  assert.equal(r.success, true)
  assert.equal(r.effect, 'none')
  const unread = r.data.unread as Array<{ name: string; unread_count: number; x: number }>
  assert.equal(unread.length, 2)
  assert.equal(unread[0]!.name, '微信客服')
  assert.equal(unread[0]!.unread_count, 7)
  assert.equal(unread[0]!.x, 254)
})

test('unread_list：name 过滤（子串）；单元素对象解包防御', async () => {
  const op = createWecomUnreadListOperation({
    runDriverFn: async () => ({
      // PS 5.1 ConvertTo-Json 单元素数组解包成对象
      unread: { name: '微信客服', preview: 'p', unread_count: 7, x: 1, y: 2 },
    }),
  })
  const hit = await op.execute({ name: '客服' }, silentCtx())
  assert.equal((hit.data.unread as unknown[]).length, 1)
  const miss = await op.execute({ name: '不存在' }, silentCtx())
  assert.equal(miss.success, true)
  assert.equal((miss.data.unread as unknown[]).length, 0)
})

test('unread_list：name 非字符串 → INVALID_ARGUMENT，不调驱动', async () => {
  let called = 0
  const op = createWecomUnreadListOperation({
    runDriverFn: async () => {
      called++
      return {}
    },
  })
  // @ts-expect-error 故意传非法值
  const r = await op.execute({ name: 42 }, silentCtx())
  assert.equal(r.code, 'INVALID_ARGUMENT')
  assert.equal(called, 0)
})

test('unread_list：驱动 UI_CHANGED / WECOM_NOT_FOUND 透传，effect=none', async () => {
  for (const code of ['UI_CHANGED', 'WECOM_NOT_FOUND'] as const) {
    const op = createWecomUnreadListOperation({
      runDriverFn: async () => {
        throw new CodedOperationError(code, 'x')
      },
    })
    const r = await op.execute({}, silentCtx())
    assert.equal(r.success, false)
    assert.equal(r.code, code)
    assert.equal(r.effect, 'none')
  }
})

// ---------- wecom_history_read ----------

const VALID_REF = 'valid-ref'
const TARGET = { name: '陆伟', type: 'contact', subtitle: '微信联系人' }

function makeReadOp(runDriverFn: RunPowerShellDriverFn, extra: { verifyRefFn?: () => typeof TARGET; artifactDirFn?: () => string | null } = {}) {
  const dir = tempDir('wecom-history-read-test-')
  return createWecomHistoryReadOperation({
    runDriverFn,
    verifyRefFn: extra.verifyRefFn ?? (() => TARGET),
    artifactDirFn: extra.artifactDirFn ?? (() => dir),
  })
}

test('history_read：成功 → {title,messages,pages_read}，effect=none，驱动参数带 search 模式与 ArtifactDir', async () => {
  const op = makeReadOp(async (opts) => {
    assert.ok(opts.script.endsWith('history-read.ps1'))
    assert.deepEqual(opts.args?.slice(0, 6), ['-TargetName', '陆伟', '-Subtitle', '微信联系人', '-Section', 'contact'])
    assert.ok(opts.args?.includes('-OpenMode'))
    assert.ok(opts.args?.includes('search'))
    assert.ok(opts.args?.includes('-MaxPages'))
    assert.ok(opts.args?.includes('3'))
    assert.ok(opts.args?.includes('-SinceDays'))
    assert.ok(opts.args?.includes('-ArtifactDir'))
    return {
      title: '陆伟 @微信',
      pages_read: 3,
      messages: [
        { side: 'timeline', text: '7月16日 09:01' },
        { side: 'peer', text: '在吗' },
        { side: 'self', text: '在的' },
        { side: 'bogus', text: '非法 side 归并 peer' },
        { side: 'peer', text: '' },
      ],
      screenshot_paths: ['p1.png'],
    }
  })
  const r = await op.execute({ target_ref: VALID_REF, max_pages: 3, since_days: 7 }, silentCtx())
  assert.equal(r.success, true)
  assert.equal(r.effect, 'none')
  assert.equal(r.data.title, '陆伟 @微信')
  assert.equal(r.data.pages_read, 3)
  const messages = r.data.messages as Array<{ side: string; text: string }>
  // 空文本行被过滤；非法 side 归并 peer
  assert.equal(messages.length, 4)
  assert.equal(messages[3]!.side, 'peer')
  assert.deepEqual(r.data.screenshot_paths, ['p1.png'])
})

test('history_read：target_ref 过期 → TARGET_REF_STALE，不调驱动', async () => {
  let called = 0
  const op = makeReadOp(
    async () => {
      called++
      return {}
    },
    {
      verifyRefFn: () => {
        throw new CodedOperationError('TARGET_REF_STALE', 'target_ref 已过期（有效期 5 分钟），请重新搜索获取')
      },
    },
  )
  const r = await op.execute({ target_ref: 'stale-ref' }, silentCtx())
  assert.equal(r.code, 'TARGET_REF_STALE')
  assert.equal(r.effect, 'none')
  assert.equal(called, 0)
})

test('history_read：非法 max_pages / since_days / 缺 target_ref → INVALID_ARGUMENT，不调驱动', async () => {
  let called = 0
  const op = makeReadOp(async () => {
    called++
    return {}
  })
  for (const bad of [
    { target_ref: '' },
    { target_ref: VALID_REF, max_pages: 0 },
    { target_ref: VALID_REF, max_pages: 11 },
    { target_ref: VALID_REF, max_pages: 1.5 },
    { target_ref: VALID_REF, since_days: 0 },
    { target_ref: VALID_REF, since_days: -1 },
    { target_ref: VALID_REF, since_days: 2.5 },
  ]) {
    const r = await op.execute(bad, silentCtx())
    assert.equal(r.code, 'INVALID_ARGUMENT', JSON.stringify(bad))
  }
  assert.equal(called, 0)
})

test('history_read：%LOCALAPPDATA% 缺失（artifactDirFn=null）→ INTERNAL_ERROR，不调驱动', async () => {
  let called = 0
  const op = makeReadOp(
    async () => {
      called++
      return {}
    },
    { artifactDirFn: () => null },
  )
  const r = await op.execute({ target_ref: VALID_REF }, silentCtx())
  assert.equal(r.code, 'INTERNAL_ERROR')
  assert.equal(called, 0)
})

test('history_read：标题不一致 UI_CHANGED / CONTENT_UNAVAILABLE 透传，effect=none', async () => {
  for (const code of ['UI_CHANGED', 'CONTENT_UNAVAILABLE'] as const) {
    const op = makeReadOp(async () => {
      throw new CodedOperationError(code, 'x')
    })
    const r = await op.execute({ target_ref: VALID_REF }, silentCtx())
    assert.equal(r.code, code)
    assert.equal(r.effect, 'none')
  }
})

// ---------- watch_poll 纯函数 ----------

test('normalizeChatText：与 py/PS 同规则（空白/全角/标点/大小写）', () => {
  assert.equal(normalizeChatText(' Hello　World '), 'helloworld')
  assert.equal(normalizeChatText('ＡＢＣ：１２３'), 'abc:123')
  assert.equal(normalizeChatText('你好。世界、再见～'), '你好.世界,再见~')
  assert.equal(normalizeChatText('  '), '')
})

test('computeDelta：水位为空全量；命中取尾部；未命中保守全量；时间线不进增量', () => {
  const msgs = [
    { side: 'timeline' as const, text: '7月16日 09:01' },
    { side: 'peer' as const, text: '第一条' },
    { side: 'self' as const, text: '第二条' },
    { side: 'timeline' as const, text: '08:23' },
    { side: 'peer' as const, text: '第三条' },
  ]
  assert.equal(computeDelta(msgs, '').length, 3)
  const d = computeDelta(msgs, normalizeChatText('第二条'))
  assert.deepEqual(d.map((m) => m.text), ['第三条'])
  assert.equal(computeDelta(msgs, normalizeChatText('第三条')).length, 0)
  assert.equal(computeDelta(msgs, '不存在的水位').length, 3)
  assert.equal(computeDelta([], 'x').length, 0)
})

test('computeDelta：连续重复消息取最后一次出现（不重复推送）', () => {
  const msgs = [
    { side: 'peer' as const, text: 'ok' },
    { side: 'peer' as const, text: 'ok' },
    { side: 'peer' as const, text: '新' },
  ]
  const d = computeDelta(msgs, normalizeChatText('ok'))
  assert.deepEqual(d.map((m) => m.text), ['新'])
})

test('lastMessageNorm：跳过时间线取最后聊天行', () => {
  assert.equal(
    lastMessageNorm([
      { side: 'peer', text: '在吗' },
      { side: 'timeline', text: '08:23' },
    ]),
    normalizeChatText('在吗'),
  )
  assert.equal(lastMessageNorm([{ side: 'timeline', text: '08:23' }]), '')
})

// ---------- wecom_watch_poll ----------

interface MockDriverCall {
  script: string
  args?: string[]
}

function makeWatchOp(opts: {
  stateDir: string
  artifact: string
  unread: unknown[]
  historyImpl: (call: MockDriverCall) => Promise<Record<string, unknown>>
}) {
  const calls: MockDriverCall[] = []
  const runDriverFn: RunPowerShellDriverFn = async (driverOpts) => {
    const call: MockDriverCall = { script: driverOpts.script, args: driverOpts.args }
    calls.push(call)
    if (driverOpts.script.endsWith('unread-list.ps1')) return { unread: opts.unread }
    if (driverOpts.script.endsWith('history-read.ps1')) return opts.historyImpl(call)
    throw new Error(`unexpected driver ${driverOpts.script}`)
  }
  const op = createWecomWatchPollOperation({
    runDriverFn,
    statePathFn: () => join(opts.stateDir, 'watch-state.json'),
    artifactDirFn: () => opts.artifact,
    nowFn: () => new Date('2026-08-31T12:00:00Z'),
  })
  return { op, calls }
}

const UNREAD_LUWEI = [{ name: '陆伟@微信', preview: 'p', unread_count: 3, x: 259, y: 401 }]

test('watch_poll：首轮新会话 → new_messages 全量 + 水位推进落盘；直点行模式带 RowX/RowY', async () => {
  const stateDir = tempDir('wecom-watch-state-')
  const artifact = tempDir('wecom-watch-artifact-')
  const { op, calls } = makeWatchOp({
    stateDir,
    artifact,
    unread: UNREAD_LUWEI,
    historyImpl: async () => ({
      title: '陆伟 @微信',
      pages_read: 1,
      messages: [
        { side: 'timeline', text: '08:23' },
        { side: 'peer', text: '在吗' },
        { side: 'self', text: '在的' },
      ],
    }),
  })
  const r = await op.execute({}, silentCtx())
  assert.equal(r.success, true)
  assert.equal(r.effect, 'none')
  const events = r.data.events as WatchEvent[]
  assert.equal(events.length, 1)
  const ev = events[0]!
  assert.equal(ev.type, 'new_messages')
  if (ev.type === 'new_messages') {
    assert.equal(ev.session, '陆伟@微信')
    assert.equal(ev.unread_count, 3)
    // 时间线不进事件
    assert.deepEqual(ev.messages.map((m) => m.text), ['在吗', '在的'])
  }
  // 直点行模式：RowX/RowY 来自 unread OCR 行坐标
  const histCall = calls.find((c) => c.script.endsWith('history-read.ps1'))!
  const modeIdx = histCall.args!.indexOf('-OpenMode')
  assert.equal(histCall.args![modeIdx + 1], 'row')
  assert.equal(histCall.args![histCall.args!.indexOf('-RowX') + 1], '259')
  assert.equal(histCall.args![histCall.args!.indexOf('-RowY') + 1], '401')
  // 水位落盘（读取成功后 last_unread 归零：进会话已清角标，之后任何角标都是新增）
  const state = loadWatchState(join(stateDir, 'watch-state.json'))
  assert.equal(state.sessions['陆伟@微信']!.last_seen_text_norm, normalizeChatText('在的'))
  assert.equal(state.sessions['陆伟@微信']!.last_unread, 0)
})

test('watch_poll：同内容不重发——unread 未增加 → tick，且不调历史驱动', async () => {
  const stateDir = tempDir('wecom-watch-state-')
  const artifact = tempDir('wecom-watch-artifact-')
  // 预置水位：last_unread 3
  saveWatchState(join(stateDir, 'watch-state.json'), {
    sessions: {
      '陆伟@微信': { last_seen_text_norm: normalizeChatText('在的'), last_unread: 3, updated_at: '2026-08-31T11:00:00Z' },
    },
  })
  let historyCalls = 0
  const { op } = makeWatchOp({
    stateDir,
    artifact,
    unread: UNREAD_LUWEI, // unread_count 仍为 3，不增
    historyImpl: async () => {
      historyCalls++
      return {}
    },
  })
  const r = await op.execute({}, silentCtx())
  assert.equal(r.success, true)
  const events = r.data.events as WatchEvent[]
  assert.equal(events.length, 1)
  assert.equal(events[0]!.type, 'tick')
  if (events[0]!.type === 'tick') assert.equal(events[0]!.unread_total, 3)
  assert.equal(historyCalls, 0)
})

test('watch_poll：unread 增大 → 候选，按水位取增量尾', async () => {
  const stateDir = tempDir('wecom-watch-state-')
  const artifact = tempDir('wecom-watch-artifact-')
  saveWatchState(join(stateDir, 'watch-state.json'), {
    sessions: {
      '陆伟@微信': { last_seen_text_norm: normalizeChatText('在的'), last_unread: 3, updated_at: '2026-08-31T11:00:00Z' },
    },
  })
  const { op } = makeWatchOp({
    stateDir,
    artifact,
    unread: [{ name: '陆伟@微信', preview: 'p', unread_count: 5, x: 259, y: 401 }],
    historyImpl: async () => ({
      title: '陆伟 @微信',
      pages_read: 1,
      messages: [
        { side: 'peer', text: '在吗' },
        { side: 'self', text: '在的' },
        { side: 'peer', text: '新消息1' },
        { side: 'peer', text: '新消息2' },
      ],
    }),
  })
  const r = await op.execute({}, silentCtx())
  const events = r.data.events as WatchEvent[]
  assert.equal(events.length, 1)
  if (events[0]!.type === 'new_messages') {
    assert.deepEqual(events[0]!.messages.map((m) => m.text), ['新消息1', '新消息2'])
  } else {
    assert.fail('应为 new_messages 事件')
  }
  const state = loadWatchState(join(stateDir, 'watch-state.json'))
  // 读取成功后 last_unread 归零（进会话已清角标）
  assert.equal(state.sessions['陆伟@微信']!.last_unread, 0)
  assert.equal(state.sessions['陆伟@微信']!.last_seen_text_norm, normalizeChatText('新消息2'))
})

test('watch_poll：读取成功后角标水位归零——「读取后来 1 条」（角标 1 < 旧水位）仍触发，不漏报', async () => {
  const stateDir = tempDir('wecom-watch-state-')
  const artifact = tempDir('wecom-watch-artifact-')
  // 首轮读 3 条成功 → 水位归零（进会话已清角标）
  const { op } = makeWatchOp({
    stateDir,
    artifact,
    unread: UNREAD_LUWEI,
    historyImpl: async () => ({
      title: '陆伟 @微信',
      pages_read: 1,
      messages: [{ side: 'peer', text: '在吗' }],
    }),
  })
  await op.execute({}, silentCtx())
  assert.equal(loadWatchState(join(stateDir, 'watch-state.json')).sessions['陆伟@微信']!.last_unread, 0)
  // 紧接着来 1 条新消息（角标 1）：若水位记旧值 3，则 1 > 3 不成立会漏报
  const { op: op2 } = makeWatchOp({
    stateDir,
    artifact,
    unread: [{ name: '陆伟@微信', preview: 'p', unread_count: 1, x: 259, y: 401 }],
    historyImpl: async () => ({
      title: '陆伟 @微信',
      pages_read: 1,
      messages: [
        { side: 'peer', text: '在吗' },
        { side: 'peer', text: '又来一条' },
      ],
    }),
  })
  const r2 = await op2.execute({}, silentCtx())
  const ev2 = (r2.data.events as WatchEvent[])[0]!
  assert.equal(ev2.type, 'new_messages')
  if (ev2.type === 'new_messages') assert.deepEqual(ev2.messages.map((m) => m.text), ['又来一条'])
})

test('watch_poll：会话从快照消失 → last_unread 归零（后续低水位重现仍触发）', async () => {
  const stateDir = tempDir('wecom-watch-state-')
  const artifact = tempDir('wecom-watch-artifact-')
  saveWatchState(join(stateDir, 'watch-state.json'), {
    sessions: {
      '陆伟@微信': { last_seen_text_norm: normalizeChatText('在的'), last_unread: 5, updated_at: '2026-08-31T11:00:00Z' },
    },
  })
  // 快照为空（用户已读）：tick + last_unread 归零
  const { op } = makeWatchOp({
    stateDir,
    artifact,
    unread: [],
    historyImpl: async () => ({}),
  })
  const r = await op.execute({}, silentCtx())
  assert.equal((r.data.events as WatchEvent[])[0]!.type, 'tick')
  const state = loadWatchState(join(stateDir, 'watch-state.json'))
  assert.equal(state.sessions['陆伟@微信']!.last_unread, 0)
  // 再来 1 条（1 > 0 → 候选；若未归零则 1 > 5 不成立会漏报）
  const { op: op2 } = makeWatchOp({
    stateDir,
    artifact,
    unread: [{ name: '陆伟@微信', preview: 'p', unread_count: 1, x: 259, y: 401 }],
    historyImpl: async () => ({
      title: 't',
      pages_read: 1,
      messages: [
        { side: 'self', text: '在的' },
        { side: 'peer', text: '又来一条' },
      ],
    }),
  })
  const r2 = await op2.execute({}, silentCtx())
  const ev2 = (r2.data.events as WatchEvent[])[0]!
  assert.equal(ev2.type, 'new_messages')
})

test('watch_poll：直点行 UI_CHANGED → 降级 search 模式重试一次', async () => {
  const stateDir = tempDir('wecom-watch-state-')
  const artifact = tempDir('wecom-watch-artifact-')
  const modes: string[] = []
  const { op } = makeWatchOp({
    stateDir,
    artifact,
    unread: UNREAD_LUWEI,
    historyImpl: async (call) => {
      const mode = call.args![call.args!.indexOf('-OpenMode') + 1]!
      modes.push(mode)
      if (mode === 'row') throw new CodedOperationError('UI_CHANGED', '会话标题「张三」与目标「陆伟@微信」不一致，已中止（防串会话）')
      return { title: '陆伟 @微信', pages_read: 1, messages: [{ side: 'peer', text: '在吗' }] }
    },
  })
  const r = await op.execute({}, silentCtx())
  assert.equal(r.success, true)
  assert.deepEqual(modes, ['row', 'search'])
  assert.equal((r.data.events as WatchEvent[])[0]!.type, 'new_messages')
})

test('watch_poll：读取失败（非 UI_CHANGED）→ 不推进水位、无事件、下轮仍候选', async () => {
  const stateDir = tempDir('wecom-watch-state-')
  const artifact = tempDir('wecom-watch-artifact-')
  let fail = true
  const impl = async (): Promise<Record<string, unknown>> => {
    if (fail) throw new CodedOperationError('INTERNAL_ERROR', 'OCR 失败')
    return { title: 't', pages_read: 1, messages: [{ side: 'peer', text: '在吗' }] }
  }
  const { op } = makeWatchOp({ stateDir, artifact, unread: UNREAD_LUWEI, historyImpl: impl })
  const r1 = await op.execute({}, silentCtx())
  assert.equal(r1.success, true) // 单候选失败不拖垮整轮
  assert.equal((r1.data.events as WatchEvent[])[0]!.type, 'tick')
  assert.equal(loadWatchState(join(stateDir, 'watch-state.json')).sessions['陆伟@微信'], undefined)
  // 下轮恢复：仍候选（首轮未推进水位）
  fail = false
  const r2 = await op.execute({}, silentCtx())
  assert.equal((r2.data.events as WatchEvent[])[0]!.type, 'new_messages')
})

test('watch_poll：状态文件损坏 → 按空状态跑首轮全量，不崩溃', async () => {
  const stateDir = tempDir('wecom-watch-state-')
  const artifact = tempDir('wecom-watch-artifact-')
  writeFileSync(join(stateDir, 'watch-state.json'), '{corrupted json!!!', 'utf8')
  const { op } = makeWatchOp({
    stateDir,
    artifact,
    unread: UNREAD_LUWEI,
    historyImpl: async () => ({ title: 't', pages_read: 1, messages: [{ side: 'peer', text: '在吗' }] }),
  })
  const r = await op.execute({}, silentCtx())
  assert.equal(r.success, true)
  assert.equal((r.data.events as WatchEvent[])[0]!.type, 'new_messages')
  // 损坏文件被新水位覆盖，可正常解析（读取成功 last_unread 归零）
  assert.equal(loadWatchState(join(stateDir, 'watch-state.json')).sessions['陆伟@微信']!.last_unread, 0)
})

test('watch_poll：读取中被取消 → CANCELLED 透传（不被候选容错吞掉）', async () => {
  const stateDir = tempDir('wecom-watch-state-')
  const artifact = tempDir('wecom-watch-artifact-')
  const { op } = makeWatchOp({
    stateDir,
    artifact,
    unread: UNREAD_LUWEI,
    historyImpl: async () => {
      throw new CancelledError()
    },
  })
  const r = await op.execute({}, silentCtx())
  assert.equal(r.code, 'CANCELLED')
  assert.equal(r.effect, 'none')
})

// ---------- watchState ----------

test('watchState：损坏 JSON / 结构不符 / 单条损坏 → 容错为空或丢单条', () => {
  const dir = tempDir('wecom-watchstate-test-')
  const p = join(dir, 'watch-state.json')
  // 不存在 → 空
  assert.deepEqual(loadWatchState(p), { sessions: {} })
  // 非法 JSON → 空
  writeFileSync(p, 'not json', 'utf8')
  assert.deepEqual(loadWatchState(p), { sessions: {} })
  // 结构不符 → 空
  writeFileSync(p, JSON.stringify({ sessions: [1, 2] }), 'utf8')
  assert.deepEqual(loadWatchState(p), { sessions: {} })
  // 单条损坏只丢该条
  writeFileSync(
    p,
    JSON.stringify({
      sessions: {
        good: { last_seen_text_norm: 'a', last_unread: 1, updated_at: 't' },
        bad: { last_seen_text_norm: 1 },
      },
    }),
    'utf8',
  )
  const state = loadWatchState(p)
  assert.equal(Object.keys(state.sessions).length, 1)
  assert.equal(state.sessions.good!.last_unread, 1)
})

test('watchState：roundtrip（保存后可原样读回）', () => {
  const dir = tempDir('wecom-watchstate-test-')
  const p = join(dir, 'sub', 'watch-state.json') // 目录不存在自动创建
  const state = {
    sessions: {
      '陆伟@微信': { last_seen_text_norm: 'zaidi', last_unread: 3, updated_at: '2026-08-31T12:00:00Z' },
    },
  }
  saveWatchState(p, state)
  assert.deepEqual(loadWatchState(p), state)
  // 文件内容是格式化 JSON（人可读，排障友好）
  assert.ok(readFileSync(p, 'utf8').includes('陆伟@微信'))
})
