/**
 * M3 operation 契约测试（mock runDriverFn / verifyRefFn，绝不触达真实企微/驱动）。
 * M9：wecom_history_read 改名 wecom_read_session（驱动 read-session.ps1，智能分发）。
 *
 * wecom_unread_list：成功聚合 / name 过滤 / 单元素解包防御 / 非法参数 / UI_CHANGED 透传。
 * wecom_read_session：成功（target_ref + 驱动参数，无 OpenMode）/ time 字段沿袭 / 过期 /
 *   非法 max_pages、since_days / artifact 目录缺失 / UI_CHANGED 透传 / navigate_required
 *   分发路径编排（read → search → select → read）/ 两轮 navigate_required →
 *   TARGET_NOT_FOUND（未读取消息）/ 候选歧义 TARGET_AMBIGUOUS（文案「已中止读取」）/
 *   阶段超时保持 RESULT_TIMEOUT（readonly 不套
 *   EXECUTION_UNKNOWN）/ 驱动脚本守卫（无 has_draft、无旧搜索链、旧驱动已删除）。
 * wecom_watch_poll：首轮新会话全量事件 + 水位推进 / 同内容不重发（unread 不增 → tick，
 *   不调读取驱动）/ unread 增大取增量尾 / 读取成功 last_unread 归零（读取后来 1 条仍触发）/
 *   会话消失 last_unread 归零 / 直点行未命中 navigate_required 自动分发（search+select +
 *   二次读取不带 row 坐标）/ 读取失败不推进水位 / 状态文件损坏容错。
 * watchState：损坏 JSON / 结构不符 / 单条损坏 / roundtrip。
 */
import assert from 'node:assert/strict'
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test, { after } from 'node:test'
import { createWecomUnreadListOperation } from '../src/operations/unreadList.js'
import { createWecomReadSessionOperation } from '../src/operations/readSession.js'
import {
  computeDelta,
  createWecomWatchPollOperation,
  lastMessageNorm,
  normalizeChatText,
  type WatchEvent,
} from '../src/operations/watchPoll.js'
import { CancelledError, CodedOperationError, type OpContext } from '../src/operations/types.js'
import type { PowerShellScriptOptions, RunPowerShellDriverFn } from '../src/platform/powershell.js'
import {
  createTargetRef,
  verifyTargetRef,
  type CreateTargetRefFn,
  type VerifyTargetRefFn,
} from '../src/platform/targetRef.js'
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

// ---------- wecom_read_session（M9，前身 wecom_history_read） ----------

const VALID_REF = 'valid-ref'
const TARGET = { name: '陆伟', type: 'contact', subtitle: '微信联系人' }

function makeReadOp(
  runDriverFn: RunPowerShellDriverFn,
  extra: { verifyRefFn?: VerifyTargetRefFn; createRefFn?: CreateTargetRefFn; artifactDirFn?: () => string | null } = {},
) {
  const dir = tempDir('wecom-read-session-test-')
  return createWecomReadSessionOperation({
    runDriverFn,
    verifyRefFn: extra.verifyRefFn ?? (() => TARGET),
    createRefFn: extra.createRefFn,
    artifactDirFn: extra.artifactDirFn ?? (() => dir),
    // M10b：显式空 env 锁定 OCR 通道（既有用例全部为 OCR 链路），防宿主机
    // AID_WECOM_SERVER_URL 环境变量把用例意外切进模型通道
    env: {},
  })
}

test('read_session：成功（快路径）→ {target,title,navigated,messages,pages_read,timing_ms}，effect=none，驱动参数无 OpenMode', async () => {
  const op = makeReadOp(async (opts) => {
    assert.ok(opts.script.endsWith('read-session.ps1'))
    assert.deepEqual(opts.args?.slice(0, 6), ['-TargetName', '陆伟', '-Subtitle', '微信联系人', '-Section', 'contact'])
    // M9：旧 -OpenMode search/row 参数退役（导航交 TS 智能分发，row 快路径由 watchPoll 专用传参）
    assert.ok(!opts.args?.includes('-OpenMode'))
    assert.ok(opts.args?.includes('-MaxPages'))
    assert.ok(opts.args?.includes('3'))
    assert.ok(opts.args?.includes('-SinceDays'))
    assert.ok(opts.args?.includes('7'))
    assert.ok(opts.args?.includes('-ArtifactDir'))
    return {
      navigate_required: false,
      title: '陆伟 @微信',
      pages_read: 3,
      timing_ms: { precheck: 900, jev1: 800, scroll: 12000, merge: 30, total: 14000 },
      messages: [
        { side: 'timeline', text: '7月16日 09:01' },
        { side: 'peer', text: '在吗', time: '7月16日 09:01' },
        { side: 'self', text: '在的', time: '7月16日 09:01' },
        { side: 'timeline', text: '08:23' },
        { side: 'peer', text: '新一条', time: '08:23' },
        { side: 'bogus', text: '非法 side 归并 peer' },
        { side: 'peer', text: '' },
      ],
      screenshot_paths: ['p1.png'],
    }
  })
  const r = await op.execute({ target_ref: VALID_REF, max_pages: 3, since_days: 7 }, silentCtx())
  assert.equal(r.success, true)
  assert.equal(r.effect, 'none')
  assert.equal(r.data.target, '陆伟')
  assert.equal(r.data.title, '陆伟 @微信')
  assert.equal(r.data.navigated, false)
  assert.equal(r.data.pages_read, 3)
  assert.deepEqual(r.data.timing_ms, { precheck: 900, jev1: 800, scroll: 12000, merge: 30, total: 14000 })
  const messages = r.data.messages as Array<{ side: string; text: string; time?: string }>
  // 空文本行被过滤；非法 side 归并 peer
  assert.equal(messages.length, 6)
  assert.equal(messages[5]!.side, 'peer')
  assert.deepEqual(r.data.screenshot_paths, ['p1.png'])
})

test('read_session：time 字段沿袭——timeline 后的 self/peer 带 time（最近分割线原文），timeline 前的无 time', async () => {
  const op = makeReadOp(async () => ({
    navigate_required: false,
    title: '陆伟 @微信',
    pages_read: 1,
    messages: [
      { side: 'peer', text: '分割线之前的消息' },
      { side: 'timeline', text: '7月16日 09:01' },
      { side: 'peer', text: '在吗', time: '7月16日 09:01' },
      { side: 'timeline', text: '08:23' },
      { side: 'self', text: '在的', time: '08:23' },
    ],
  }))
  const r = await op.execute({ target_ref: VALID_REF }, silentCtx())
  const messages = r.data.messages as Array<{ side: string; text: string; time?: string }>
  assert.equal(messages.length, 5)
  assert.equal(messages[0]!.time, undefined, '首条分割线之前的消息无 time 字段')
  assert.equal(messages[0]!.side, 'peer')
  assert.equal(messages[2]!.time, '7月16日 09:01')
  assert.equal(messages[4]!.time, '08:23', '第二条分割线后的消息带最近分割线文本')
  assert.equal(messages[4]!.side, 'self')
})

test('read_session：target_ref 过期 → TARGET_REF_STALE，不调驱动', async () => {
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

test('read_session：非法 max_pages / since_days / 缺 target_ref → INVALID_ARGUMENT，不调驱动', async () => {
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

test('read_session：%LOCALAPPDATA% 缺失（artifactDirFn=null）→ INTERNAL_ERROR，不调驱动', async () => {
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

test('read_session：标题不一致 UI_CHANGED / CONTENT_UNAVAILABLE 透传，effect=none', async () => {
  for (const code of ['UI_CHANGED', 'CONTENT_UNAVAILABLE'] as const) {
    const op = makeReadOp(async () => {
      throw new CodedOperationError(code, 'x')
    })
    const r = await op.execute({ target_ref: VALID_REF }, silentCtx())
    assert.equal(r.code, code)
    assert.equal(r.effect, 'none')
  }
})

// —— 智能分发（navigate.ts 共享编排，readonly 模式）—— //

/** 真实签发 + 真实验证（临时 keyDir）：内部 chatSearch 签发的 ref 由同一 key 验证 */
function realDeps(keyDir: string): { verifyRefFn: VerifyTargetRefFn; createRefFn: CreateTargetRefFn } {
  return {
    verifyRefFn: (ref) => verifyTargetRef(ref, { keyDir }),
    createRefFn: (name, type, subtitle, coords) => createTargetRef(name, type, subtitle, { keyDir, coords }),
  }
}

const READ_NAV_REQUIRED = {
  navigate_required: true,
  reason: '标题归一化规则未命中（当前标题「张三」≠ 目标「陆伟」）',
  title: '张三',
  timing_ms: { precheck: 900 },
  screenshot_paths: [],
}

const READ_OK = {
  navigate_required: false,
  title: '陆伟 @微信',
  pages_read: 1,
  timing_ms: { total: 9000 },
  messages: [
    { side: 'timeline', text: '08:23' },
    { side: 'peer', text: '在吗', time: '08:23' },
  ],
  screenshot_paths: ['step1-precheck.png', 'page-1.png'],
}

const SEARCH_OK_LUWEI = {
  query: '陆伟',
  items: [{ name: '陆伟', subtitle: '微信联系人', section: '联系人', x: 200, y: 60, probability: 0.9 }],
  best_index: 0,
  overlay: { x: 100, y: 50, w: 400, h: 542 },
  jev: { used: true, latency_ms: 700, is_ambiguous: false, best_confidence: 0.9 },
  timing_ms: { focus: 250, total: 5300 },
}

const SELECT_OK_LUWEI = {
  target: { name: '陆伟', subtitle: '微信联系人', section: 'other' },
  title: '陆伟 @微信',
  clicked: { x: 300, y: 110 },
  timing_ms: { overlay_check: 30, total: 4600 },
  screenshot_paths: ['overlay-preclick.png', 'main-opened.png'],
}

test('read_session：分发路径——第一轮 navigate_required=true → chatSearch → chatSelect → 二次 read-session 成功（navigated=true）', async () => {
  const keyDir = tempDir('wecom-read-session-key-')
  const dir = tempDir('wecom-read-session-test-')
  const ref = createTargetRef('陆伟', 'contact', '微信联系人', { keyDir })
  const calls: PowerShellScriptOptions[] = []
  const stages: Array<Record<string, unknown>> = [{ ...READ_NAV_REQUIRED }, { ...READ_OK }]
  const op = makeReadOp(
    async (opts) => {
      calls.push(opts)
      const base = opts.script.replace(/^.*[\\/]/, '')
      if (base === 'read-session.ps1') {
        const stage = stages.shift()
        if (stage === undefined) throw new Error('read-session.ps1 出现未预期的额外调用')
        return stage
      }
      if (base === 'chat-search.ps1') return SEARCH_OK_LUWEI
      if (base === 'chat-select.ps1') return SELECT_OK_LUWEI
      throw new Error(`未预期的驱动脚本：${base}`)
    },
    { ...realDeps(keyDir), artifactDirFn: () => dir },
  )
  const r = await op.execute({ target_ref: ref, max_pages: 2 }, silentCtx())
  assert.equal(r.success, true)
  assert.equal(r.effect, 'none')
  assert.equal(r.data.navigated, true)
  assert.equal(r.data.title, '陆伟 @微信')
  const messages = r.data.messages as Array<{ side: string; text: string; time?: string }>
  assert.deepEqual(messages.map((m) => [m.side, m.text, m.time]), [
    ['timeline', '08:23', undefined],
    ['peer', '在吗', '08:23'],
  ])
  assert.match(r.message, /已自动搜索并切换会话/)
  // 编排顺序：read-session → chat-search → chat-select → read-session
  assert.equal(calls.length, 4)
  assert.ok(calls[0]!.script.endsWith('read-session.ps1'))
  assert.ok(calls[1]!.script.endsWith('chat-search.ps1'))
  assert.ok(calls[2]!.script.endsWith('chat-select.ps1'))
  assert.ok(calls[3]!.script.endsWith('read-session.ps1'))
  assert.deepEqual(calls[1]!.args?.slice(0, 2), ['-Query', '陆伟'])
  // 两轮 read-session 驱动身份/翻页参数一致、artifact 目录相互独立（navigate 编排内建）
  const argAfter = (call: PowerShellScriptOptions, flag: string): string => {
    const i = call.args?.indexOf(flag) ?? -1
    assert.ok(i !== -1, `驱动参数应含 ${flag}`)
    return call.args![i + 1]! as string
  }
  assert.equal(argAfter(calls[0]!, '-MaxPages'), '2')
  assert.equal(argAfter(calls[3]!, '-MaxPages'), '2')
  assert.notEqual(argAfter(calls[0]!, '-ArtifactDir'), argAfter(calls[3]!, '-ArtifactDir'))
})

test('read_session：两轮都 navigate_required → TARGET_NOT_FOUND（message 写明两轮 + 未读取消息）', async () => {
  const keyDir = tempDir('wecom-read-session-key-')
  const ref = createTargetRef('陆伟', 'contact', '微信联系人', { keyDir })
  const op = makeReadOp(
    async (opts) => {
      const base = opts.script.replace(/^.*[\\/]/, '')
      if (base === 'read-session.ps1') {
        return { navigate_required: true, reason: 'Jev 判定当前会话非目标（unclear）', title: '张三', timing_ms: {}, screenshot_paths: [] }
      }
      if (base === 'chat-search.ps1') return SEARCH_OK_LUWEI
      if (base === 'chat-select.ps1') return SELECT_OK_LUWEI
      throw new Error(`未预期的驱动脚本：${base}`)
    },
    { ...realDeps(keyDir), artifactDirFn: () => tempDir('wecom-read-session-test-') },
  )
  const r = await op.execute({ target_ref: ref }, silentCtx())
  assert.equal(r.success, false)
  assert.equal(r.code, 'TARGET_NOT_FOUND')
  assert.equal(r.effect, 'none')
  assert.equal(r.retryable, false)
  assert.match(r.message, /两轮/)
  assert.match(r.message, /未读取消息/)
})

test('read_session：候选歧义 → TARGET_AMBIGUOUS，文案为「已中止读取」（readonly 链路不得说「已拒绝发送」）', async () => {
  const keyDir = tempDir('wecom-read-session-key-')
  const ref = createTargetRef('陆伟', 'contact', '微信联系人', { keyDir })
  const op = makeReadOp(
    async (opts) => {
      const base = opts.script.replace(/^.*[\\/]/, '')
      if (base === 'read-session.ps1') return { ...READ_NAV_REQUIRED }
      if (base === 'chat-search.ps1') {
        // best（张三）身份不符 + 两条「陆伟」name+section 匹配且 subtitle 均与目标
        // 「微信联系人」不符 → 无法消歧 → TARGET_AMBIGUOUS
        return {
          query: '陆伟',
          items: [
            { name: '张三', subtitle: '', section: '联系人', x: 200, y: 60, probability: 0.6 },
            { name: '陆伟', subtitle: '甲部门', section: '联系人', x: 200, y: 120, probability: 0.2 },
            { name: '陆伟', subtitle: '乙部门', section: '联系人', x: 200, y: 180, probability: 0.2 },
          ],
          best_index: 0,
          overlay: { x: 100, y: 50, w: 400, h: 542 },
          jev: { used: true, latency_ms: 700, is_ambiguous: true, best_confidence: 0.6 },
          timing_ms: { total: 5000 },
        }
      }
      if (base === 'chat-select.ps1') throw new Error('歧义时不得调 chat-select')
      throw new Error(`未预期的驱动脚本：${base}`)
    },
    { ...realDeps(keyDir), artifactDirFn: () => tempDir('wecom-read-session-test-') },
  )
  const r = await op.execute({ target_ref: ref }, silentCtx())
  assert.equal(r.success, false)
  assert.equal(r.code, 'TARGET_AMBIGUOUS')
  assert.equal(r.effect, 'none')
  assert.equal(r.retryable, false)
  assert.match(r.message, /已中止读取（未读取消息）/)
  assert.doesNotMatch(r.message, /发送/, '只读链路不得出现「拒绝发送」类写动作文案')
})

test('read_session：阶段超时保持 RESULT_TIMEOUT（readonly 不套写动作 EXECUTION_UNKNOWN），effect=none', async () => {
  const op = makeReadOp(async () => {
    throw new CodedOperationError('RESULT_TIMEOUT', 'PowerShell 驱动执行超时（>600s），已终止子进程')
  })
  const r = await op.execute({ target_ref: VALID_REF }, silentCtx())
  assert.equal(r.success, false)
  assert.equal(r.code, 'RESULT_TIMEOUT', '只读阶段无写副作用可 unknown，超时不得翻译为 EXECUTION_UNKNOWN')
  assert.equal(r.effect, 'none')
  assert.doesNotMatch(r.message, /可能已发出/)
})

test('read_session：驱动脚本守卫——无 has_draft（read 绝不因草稿中止）、无旧 M2 搜索链、旧驱动已删除', () => {
  const ps1 = readFileSync(
    new URL('../../drivers/ps1/read-session.ps1', import.meta.url),
    'utf8',
  )
  // 智能分发契约
  assert.ok(ps1.includes('navigate_required'), 'read-session.ps1 应返回 navigate_required 交 TS 分发')
  assert.ok(ps1.includes('right_conversation'), 'read-session.ps1 Jev #1 应只问 right_conversation')
  // read 是只读动作：不点输入框、不输入文字，has_draft 不适用（绝不因草稿中止）
  assert.ok(!ps1.includes('has_draft'), 'read-session.ps1 不得含 has_draft 判定（读消息不碰输入框）')
  // 旧 M2 搜索链（Open-WeComSearchOverlay / ESC 关闭）自 M9 退役
  assert.ok(!ps1.includes('Open-WeComSearchOverlay'), 'read-session.ps1 不得内置旧搜索链（导航交 TS 编排）')
  assert.ok(!ps1.includes('Send-WeComEscape'), 'read-session.ps1 不得使用 ESC（最小化企微风险）')
  // 时间戳沿袭在驱动侧实现
  assert.ok(ps1.includes('lastTimeline'), 'read-session.ps1 应含时间戳沿袭（timeline → 后续消息 time 字段）')
  // 旧驱动已删除（改名映射：history-read.ps1 → read-session.ps1）
  assert.equal(existsSync(new URL('../../drivers/ps1/history-read.ps1', import.meta.url)), false)
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
  readImpl: (call: MockDriverCall) => Promise<Record<string, unknown>>
}) {
  const calls: MockDriverCall[] = []
  const runDriverFn: RunPowerShellDriverFn = async (driverOpts) => {
    const call: MockDriverCall = { script: driverOpts.script, args: driverOpts.args }
    calls.push(call)
    if (driverOpts.script.endsWith('unread-list.ps1')) return { unread: opts.unread }
    if (driverOpts.script.endsWith('read-session.ps1')) return opts.readImpl(call)
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

test('watch_poll：首轮新会话 → new_messages 全量 + 水位推进落盘；首轮读取带 RowX/RowY（row 快路径）', async () => {
  const stateDir = tempDir('wecom-watch-state-')
  const artifact = tempDir('wecom-watch-artifact-')
  const { op, calls } = makeWatchOp({
    stateDir,
    artifact,
    unread: UNREAD_LUWEI,
    readImpl: async () => ({
      navigate_required: false,
      title: '陆伟 @微信',
      pages_read: 1,
      messages: [
        { side: 'timeline', text: '08:23' },
        { side: 'peer', text: '在吗', time: '08:23' },
        { side: 'self', text: '在的', time: '08:23' },
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
    // 时间线不进事件；增量消息沿袭 time 字段（最近分割线原文）
    assert.deepEqual(ev.messages.map((m) => [m.text, m.time]), [
      ['在吗', '08:23'],
      ['在的', '08:23'],
    ])
  }
  // row 快路径：首轮读取带 RowX/RowY（unread OCR 行坐标）；旧 -OpenMode 参数已退役
  const readCall = calls.find((c) => c.script.endsWith('read-session.ps1'))!
  assert.ok(!readCall.args!.includes('-OpenMode'))
  assert.equal(readCall.args![readCall.args!.indexOf('-RowX') + 1], '259')
  assert.equal(readCall.args![readCall.args!.indexOf('-RowY') + 1], '401')
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
  let readCalls = 0
  const { op } = makeWatchOp({
    stateDir,
    artifact,
    unread: UNREAD_LUWEI, // unread_count 仍为 3，不增
    readImpl: async () => {
      readCalls++
      return {}
    },
  })
  const r = await op.execute({}, silentCtx())
  assert.equal(r.success, true)
  const events = r.data.events as WatchEvent[]
  assert.equal(events.length, 1)
  assert.equal(events[0]!.type, 'tick')
  if (events[0]!.type === 'tick') assert.equal(events[0]!.unread_total, 3)
  assert.equal(readCalls, 0)
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
    readImpl: async () => ({
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
    readImpl: async () => ({
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
    readImpl: async () => ({
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
    readImpl: async () => ({}),
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
    readImpl: async () => ({
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

test('watch_poll：直点行未命中（navigate_required）→ 智能分发 search+select 后二次读取（不带 row 坐标）', async () => {
  const stateDir = tempDir('wecom-watch-state-')
  const artifact = tempDir('wecom-watch-artifact-')
  const keyDir = tempDir('wecom-watch-key-')
  const calls: MockDriverCall[] = []
  let readCalls = 0
  const runDriverFn: RunPowerShellDriverFn = async (driverOpts) => {
    const call: MockDriverCall = { script: driverOpts.script, args: driverOpts.args }
    calls.push(call)
    const base = driverOpts.script.replace(/^.*[\\/]/, '')
    if (base === 'unread-list.ps1') return { unread: UNREAD_LUWEI }
    if (base === 'read-session.ps1') {
      readCalls++
      if (readCalls === 1) {
        // row 快路径点击未命中：驱动会话判定不过 → navigate_required 交 TS 编排
        return {
          navigate_required: true,
          reason: '标题归一化规则未命中（当前标题「张三」≠ 目标「陆伟@微信」）',
          title: '张三',
          timing_ms: {},
          screenshot_paths: [],
        }
      }
      return { navigate_required: false, title: '陆伟 @微信', pages_read: 1, messages: [{ side: 'peer', text: '在吗', time: '08:23' }] }
    }
    if (base === 'chat-search.ps1') {
      return {
        query: '陆伟',
        items: [{ name: '陆伟@微信', subtitle: '', section: '聊天记录', x: 100, y: 60, probability: 0.9 }],
        best_index: 0,
        overlay: { x: 1, y: 2, w: 400, h: 500 },
        jev: { used: false, latency_ms: 0 },
        timing_ms: {},
      }
    }
    if (base === 'chat-select.ps1') {
      return { target: { name: '陆伟@微信' }, title: '陆伟 @微信', clicked: { x: 1, y: 2 }, timing_ms: {}, screenshot_paths: [] }
    }
    throw new Error(`unexpected driver ${base}`)
  }
  const op = createWecomWatchPollOperation({
    runDriverFn,
    statePathFn: () => join(stateDir, 'watch-state.json'),
    artifactDirFn: () => artifact,
    nowFn: () => new Date('2026-08-31T12:00:00Z'),
    verifyRefFn: (ref) => verifyTargetRef(ref, { keyDir }),
    createRefFn: (name, type, subtitle, coords) => createTargetRef(name, type, subtitle, { keyDir, coords }),
  })
  const r = await op.execute({}, silentCtx())
  assert.equal(r.success, true)
  const ev = (r.data.events as WatchEvent[])[0]!
  assert.equal(ev.type, 'new_messages')
  if (ev.type === 'new_messages') {
    assert.deepEqual(ev.messages.map((m) => [m.text, m.time]), [['在吗', '08:23']])
  }
  // 编排顺序：unread → read-session（row）→ chat-search → chat-select → read-session（无 row）
  assert.deepEqual(
    calls.map((c) => c.script.replace(/^.*[\\/]/, '')),
    ['unread-list.ps1', 'read-session.ps1', 'chat-search.ps1', 'chat-select.ps1', 'read-session.ps1'],
  )
  const firstRead = calls[1]!
  const secondRead = calls[4]!
  assert.equal(firstRead.args![firstRead.args!.indexOf('-RowX') + 1], '259', '首轮带 row 坐标（快路径）')
  assert.ok(!secondRead.args!.includes('-RowX'), '分发后的二次读取已在目标会话，不再点行')
  assert.equal(firstRead.args![firstRead.args!.indexOf('-TargetName') + 1], '陆伟@微信')
  assert.equal(secondRead.args![secondRead.args!.indexOf('-TargetName') + 1], '陆伟@微信')
  // 水位仍正常推进（读取成功）
  const state = loadWatchState(join(stateDir, 'watch-state.json'))
  assert.equal(state.sessions['陆伟@微信']!.last_unread, 0)
})

test('watch_poll：读取失败（非 UI_CHANGED）→ 不推进水位、无事件、下轮仍候选', async () => {
  const stateDir = tempDir('wecom-watch-state-')
  const artifact = tempDir('wecom-watch-artifact-')
  let fail = true
  const impl = async (): Promise<Record<string, unknown>> => {
    if (fail) throw new CodedOperationError('INTERNAL_ERROR', 'OCR 失败')
    return { title: 't', pages_read: 1, messages: [{ side: 'peer', text: '在吗' }] }
  }
  const { op } = makeWatchOp({ stateDir, artifact, unread: UNREAD_LUWEI, readImpl: impl })
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
    readImpl: async () => ({ title: 't', pages_read: 1, messages: [{ side: 'peer', text: '在吗' }] }),
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
    readImpl: async () => {
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
