/**
 * wecom_message_send M6 契约测试（智能分发：当前会话直发 / search+select 分发再发；
 * 全 mock，绝不触达真实企微窗口/驱动脚本——runDriverFn 注入按脚本名分发的 mock，
 * verifyRefFn/createRefFn 注入临时 keyDir 的真实签发/验证）。
 *
 * 覆盖：快路径成功透传（sent_verification/input_point/timing）/ 分发路径（navigate_required
 * → 内部 chatSearch → chatSelect → 二次 send，断言编排顺序与传参）/ best 身份校验
 * （subtitle 消歧键不一致 → 不信 best 走规则唯一匹配；一致 → 直接用 best；身份不符 → 规则）/
 * 两轮 navigate_required → TARGET_NOT_FOUND / 编排中 search/select 失败与取消 →
 * 透传错误码（未发送消息，effect=none）/ 候选歧义 → TARGET_AMBIGUOUS / 二次发送驱动
 * EXECUTION_UNKNOWN → effect=unknown / ref 过期与篡改 / 换行放行（多行走驱动侧
 * 剪贴板粘贴通道，驱动参数须收到原文）、纯空白与超长 → INVALID_ARGUMENT。
 */
import assert from 'node:assert/strict'
import { existsSync, mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test, { after } from 'node:test'
import { createWecomMessageSendOperation } from '../src/operations/messageSend.js'
import {
  createTargetRef,
  verifyTargetRef,
  type CreateTargetRefFn,
  type VerifyTargetRefFn,
} from '../src/platform/targetRef.js'
import { CancelledError, CodedOperationError, type OpContext } from '../src/operations/types.js'
import type { PowerShellScriptOptions, RunPowerShellDriverFn } from '../src/platform/powershell.js'

function silentCtx(): OpContext {
  return { signal: new AbortController().signal, progress: () => {} }
}

const tempDirs: string[] = []
after(() => {
  for (const d of tempDirs) rmSync(d, { recursive: true, force: true })
})

function makeKeyDir(): string {
  const dir = mkdtempSync(join(tmpdir(), 'wecom-message-send-v2-key-'))
  tempDirs.push(dir)
  return dir
}

function makeArtifactRoot(): string {
  const dir = mkdtempSync(join(tmpdir(), 'wecom-message-send-v2-test-'))
  tempDirs.push(dir)
  return dir
}

/** 真实签发 + 真实验证（临时 keyDir）：内部 chatSearch 签发的 ref 由同一 key 验证 */
function realDeps(keyDir: string): { verifyRefFn: VerifyTargetRefFn; createRefFn: CreateTargetRefFn } {
  return {
    verifyRefFn: (ref) => verifyTargetRef(ref, { keyDir }),
    createRefFn: (name, type, subtitle, coords) => createTargetRef(name, type, subtitle, { keyDir, coords }),
  }
}

/** 按脚本名分发的驱动 mock：message-send.ps1 消费 sendStages 队列（逐次出队） */
interface DriverMock {
  calls: PowerShellScriptOptions[]
  sendStages: Array<Record<string, unknown> | Error>
  searchResult: Record<string, unknown> | Error
  selectResult: Record<string, unknown> | Error
}

function makeRunDriver(mock: DriverMock): RunPowerShellDriverFn {
  return (async (opts: PowerShellScriptOptions) => {
    mock.calls.push(opts)
    const base = opts.script.replace(/^.*[\\/]/, '')
    if (base === 'message-send.ps1') {
      const stage = mock.sendStages.shift()
      if (stage === undefined) throw new Error('message-send.ps1 出现未预期的额外调用')
      if (stage instanceof Error) throw stage
      return stage
    }
    if (base === 'chat-search.ps1') {
      if (mock.searchResult instanceof Error) throw mock.searchResult
      return mock.searchResult
    }
    if (base === 'chat-select.ps1') {
      if (mock.selectResult instanceof Error) throw mock.selectResult
      return mock.selectResult
    }
    throw new Error(`未预期的驱动脚本：${base}`)
  }) as RunPowerShellDriverFn
}

function makeOp(keyDir: string, root: string, mock: DriverMock) {
  const op = createWecomMessageSendOperation({
    runDriverFn: makeRunDriver(mock),
    artifactDirFn: () => root,
    ...realDeps(keyDir),
  })
  return op
}

const NAV_REQUIRED = { navigate_required: true, reason: '标题归一化规则未命中（当前标题「张三」≠ 目标「陆伟」）', title: '张三', timing_ms: { precheck: 900 }, screenshot_paths: [] }

const SENT_OK_JEV = {
  navigate_required: false,
  title: '陆伟 @微信',
  sent_verification: { method: 'jev', result: 'sent' },
  input_point: { x: 640, y: 648, source: 'jev' },
  timing_ms: { precheck: 900, jev1: 812, typing: 1600, title_recheck: 800, send_wait: 1300, jev2: 750, total: 6800 },
  screenshot_paths: ['step1-precheck.png', 'step2-typed.png', 'step3-after.png'],
}

const SEARCH_OK = {
  query: '陆伟',
  items: [
    { name: '陆伟', subtitle: '微信联系人', section: '联系人', x: 200, y: 60, probability: 0.29 },
    { name: '陆伟@微信', subtitle: '', section: '联系人', x: 200, y: 120, probability: 0.71 },
  ],
  best_index: 1,
  overlay: { x: 100, y: 50, w: 400, h: 542 },
  jev: { used: true, latency_ms: 812, is_ambiguous: false, best_confidence: 0.71 },
  timing_ms: { focus: 250, total: 5300 },
}

const SELECT_OK = {
  target: { name: '陆伟@微信', subtitle: '', section: 'contact' },
  title: '陆伟',
  clicked: { x: 300, y: 170 },
  timing_ms: { overlay_check: 30, total: 4600 },
  screenshot_paths: ['overlay-preclick.png', 'main-opened.png'],
}

function sendArtifactDir(call: PowerShellScriptOptions): string {
  const i = call.args?.indexOf('-ArtifactDir') ?? -1
  assert.ok(i !== -1 && typeof call.args?.[i + 1] === 'string', 'send 驱动参数应含 -ArtifactDir')
  return call.args![i + 1]! as string
}

test('v2 快路径：navigate_required=false → 成功透传，不触发 search/select（Jev 判定在驱动内，TS 只透传）', async () => {
  const keyDir = makeKeyDir()
  const root = makeArtifactRoot()
  const mock: DriverMock = { calls: [], sendStages: [{ ...SENT_OK_JEV }], searchResult: {}, selectResult: {} }
  const ref = createTargetRef('陆伟@微信', 'contact', '微信联系人', { keyDir, coords: { x: 1, y: 2 } })
  const op = makeOp(keyDir, root, mock)
  const r = await op.execute({ target_ref: ref, text: '你好，这是测试消息' }, silentCtx())
  assert.equal(r.success, true)
  assert.equal(r.code, 'OK')
  assert.equal(r.effect, 'applied')
  assert.equal(r.retryable, false)
  assert.equal(r.data.target, '陆伟@微信')
  assert.equal(r.data.title, '陆伟 @微信')
  assert.equal(r.data.navigated, false)
  assert.deepEqual(r.data.sent_verification, { method: 'jev', result: 'sent' })
  assert.deepEqual(r.data.input_point, { x: 640, y: 648 })
  assert.equal((r.data.timing_ms as Record<string, number>).total, 6800)
  assert.equal((r.data.screenshot_paths as string[]).length, 3)
  assert.match(r.message, /已向「陆伟@微信」发送消息/)
  assert.match(r.message, /当前会话直发/)
  // 只调一次 send 驱动；参数含身份 + text + send-<ts> artifact 目录（先建后用）
  assert.equal(mock.calls.length, 1)
  assert.ok(mock.calls[0]!.script.endsWith('message-send.ps1'))
  assert.deepEqual(
    mock.calls[0]!.args?.slice(0, 8),
    ['-TargetName', '陆伟@微信', '-Subtitle', '微信联系人', '-Section', 'contact', '-Text', '你好，这是测试消息'],
  )
  const dir = sendArtifactDir(mock.calls[0]!)
  assert.ok(dir.startsWith(root) && /send-\d{4}-\d{2}-\d{2}T/.test(dir), dir)
  assert.ok(existsSync(dir), 'artifact 目录先建后用')
})

test('v2 分发路径：第一轮 navigate_required=true → chatSearch → chatSelect（subtitle 收紧后规则唯一匹配项）→ 二次 send 成功', async () => {
  const keyDir = makeKeyDir()
  const root = makeArtifactRoot()
  const mock: DriverMock = {
    calls: [],
    sendStages: [{ ...NAV_REQUIRED }, { navigate_required: false, title: '陆伟', sent_verification: { method: 'rule_2of3', result: 'sent', rule_checks_passed: 3 }, input_point: { x: 640, y: 648, source: 'ratio' }, timing_ms: { total: 12000 }, screenshot_paths: ['a.png'] }],
    searchResult: { ...SEARCH_OK },
    selectResult: { ...SELECT_OK },
  }
  const ref = createTargetRef('陆伟@微信', 'contact', '微信联系人', { keyDir, coords: { x: 1, y: 2 } })
  const op = makeOp(keyDir, root, mock)
  const r = await op.execute({ target_ref: ref, text: '第二条' }, silentCtx())
  assert.equal(r.success, true)
  assert.equal(r.code, 'OK')
  assert.equal(r.effect, 'applied')
  assert.equal(r.data.navigated, true)
  assert.equal((r.data.sent_verification as Record<string, unknown>).method, 'rule_2of3')
  assert.match(r.message, /已自动搜索并切换会话/)
  // 编排顺序：send → search → select → send；search 用剥 @微信 的 query + type 收紧
  assert.equal(mock.calls.length, 4)
  assert.ok(mock.calls[0]!.script.endsWith('message-send.ps1'))
  assert.ok(mock.calls[1]!.script.endsWith('chat-search.ps1'))
  assert.ok(mock.calls[2]!.script.endsWith('chat-select.ps1'))
  assert.ok(mock.calls[3]!.script.endsWith('message-send.ps1'))
  assert.deepEqual(mock.calls[1]!.args?.slice(0, 2), ['-Query', '陆伟'])
  const searchDirIdx = mock.calls[1]!.args!.indexOf('-ArtifactDir')
  const searchDir = mock.calls[1]!.args![searchDirIdx + 1]! as string
  assert.ok(searchDir.startsWith(root) && /search-\d{4}-\d{2}-\d{2}T/.test(searchDir), searchDir)
  assert.ok(existsSync(searchDir), 'search artifact 目录先建后用')
  // select 消费 subtitle 收紧后的规则唯一匹配项：目标 subtitle=微信联系人 → items[0] 陆伟
  // （subtitle 微信联系人, x=200 y=60）；Jev best（items[1] subtitle=''）不落在收紧候选集内，
  // 不得信任（同名同分区多条时 Jev 看不到 target_ref 的 subtitle，直接信任会发错人）
  assert.deepEqual(
    mock.calls[2]!.args?.slice(0, 10),
    ['-TargetName', '陆伟', '-Subtitle', '微信联系人', '-Section', 'contact', '-X', '200', '-Y', '60'],
  )
  const selectDirIdx = mock.calls[2]!.args!.indexOf('-ArtifactDir')
  assert.ok(/select-\d{4}-\d{2}-\d{2}T/.test(mock.calls[2]!.args![selectDirIdx + 1]! as string))
  // 两次 send 驱动参数身份一致、artifact 目录相互独立
  assert.deepEqual(mock.calls[0]!.args?.slice(0, 8), mock.calls[3]!.args?.slice(0, 8))
  const dir1 = sendArtifactDir(mock.calls[0]!)
  const dir2 = sendArtifactDir(mock.calls[3]!)
  assert.notEqual(dir1, dir2, '两轮 send 的 artifact 目录必须独立')
  assert.ok(existsSync(dir1) && existsSync(dir2))
})

test('v2 best 身份不符 → 从 items 按 name+section 规则取唯一匹配项（不用错误的 best）', async () => {
  const keyDir = makeKeyDir()
  const root = makeArtifactRoot()
  const mock: DriverMock = {
    calls: [],
    sendStages: [{ ...NAV_REQUIRED }, { navigate_required: false, title: '陆伟', sent_verification: { method: 'jev', result: 'sent' }, input_point: { x: 100, y: 200, source: 'jev' }, timing_ms: { total: 7000 }, screenshot_paths: [] }],
    searchResult: {
      // Jev best（R0 张三）与目标身份不符：同分区另有一条唯一匹配的「陆伟@微信」
      items: [
        { name: '张三', subtitle: '', section: '联系人', x: 200, y: 60, probability: 0.8 },
        { name: '陆伟@微信', subtitle: '微信联系人', section: '联系人', x: 200, y: 120, probability: 0.2 },
      ],
      best_index: 0,
      overlay: { x: 100, y: 50, w: 400, h: 542 },
      jev: { used: true, latency_ms: 700, best_confidence: 0.8 },
      timing_ms: { total: 5000 },
    },
    selectResult: { ...SELECT_OK },
  }
  const ref = createTargetRef('陆伟@微信', 'contact', '微信联系人', { keyDir })
  const op = makeOp(keyDir, root, mock)
  const r = await op.execute({ target_ref: ref, text: 'hi' }, silentCtx())
  assert.equal(r.success, true)
  // select 拿到的是规则唯一匹配项 items[1] 的坐标（200,120），不是 best（200,60）
  const selArgs = mock.calls[2]!.args!
  assert.equal(selArgs[selArgs.indexOf('-X') + 1], '200')
  assert.equal(selArgs[selArgs.indexOf('-Y') + 1], '120')
  assert.equal(selArgs[selArgs.indexOf('-TargetName') + 1], '陆伟@微信')
})

test('v2 best 与目标 subtitle 消歧键一致（落在收紧候选集内）→ 直接用 best', async () => {
  const keyDir = makeKeyDir()
  const root = makeArtifactRoot()
  const mock: DriverMock = {
    calls: [],
    sendStages: [{ ...NAV_REQUIRED }, { navigate_required: false, title: '陆伟', sent_verification: { method: 'jev', result: 'sent' }, input_point: { x: 100, y: 200, source: 'jev' }, timing_ms: { total: 7000 }, screenshot_paths: [] }],
    searchResult: { ...SEARCH_OK },
    selectResult: { ...SELECT_OK },
  }
  // 目标 subtitle 为空 → 无收紧 → 候选集含两条同名项；best（items[1]）落在候选集内且
  // 身份相符 → 信任 Jev 结论，select 用 best 坐标（200,120）
  const ref = createTargetRef('陆伟@微信', 'contact', '', { keyDir })
  const op = makeOp(keyDir, root, mock)
  const r = await op.execute({ target_ref: ref, text: 'hi' }, silentCtx())
  assert.equal(r.success, true)
  const selArgs = mock.calls[2]!.args!
  assert.equal(selArgs[selArgs.indexOf('-X') + 1], '200')
  assert.equal(selArgs[selArgs.indexOf('-Y') + 1], '120')
  assert.equal(selArgs[selArgs.indexOf('-TargetName') + 1], '陆伟@微信')
})

test('v2 两轮都 navigate_required → TARGET_NOT_FOUND（message 写明两轮），未发送消息', async () => {
  const keyDir = makeKeyDir()
  const mock: DriverMock = {
    calls: [],
    sendStages: [{ ...NAV_REQUIRED }, { navigate_required: true, reason: 'Jev 判定当前会话非目标（unclear）', title: '张三', timing_ms: {}, screenshot_paths: [] }],
    searchResult: { ...SEARCH_OK },
    selectResult: { ...SELECT_OK },
  }
  const ref = createTargetRef('陆伟@微信', 'contact', '微信联系人', { keyDir })
  const op = makeOp(keyDir, makeArtifactRoot(), mock)
  const r = await op.execute({ target_ref: ref, text: 'hi' }, silentCtx())
  assert.equal(r.success, false)
  assert.equal(r.code, 'TARGET_NOT_FOUND')
  assert.equal(r.effect, 'none')
  assert.equal(r.retryable, false)
  assert.match(r.message, /两轮/)
  assert.match(r.message, /未发送消息/)
  assert.equal(mock.calls.length, 4)
})

test('v2 编排中 select 失败（UI_CHANGED）→ 透传错误码，effect=none，未发送消息', async () => {
  const keyDir = makeKeyDir()
  const mock: DriverMock = {
    calls: [],
    sendStages: [{ ...NAV_REQUIRED }],
    searchResult: { ...SEARCH_OK },
    selectResult: new CodedOperationError('UI_CHANGED', '会话标题「陆伟民」与目标「陆伟」不一致，进入的会话与 target_ref 不符，已判失败'),
  }
  const ref = createTargetRef('陆伟@微信', 'contact', '微信联系人', { keyDir })
  const op = makeOp(keyDir, makeArtifactRoot(), mock)
  const r = await op.execute({ target_ref: ref, text: 'hi' }, silentCtx())
  assert.equal(r.success, false)
  assert.equal(r.code, 'UI_CHANGED')
  assert.equal(r.effect, 'none')
  assert.match(r.message, /进入会话阶段/)
  assert.match(r.message, /未发送消息/)
  assert.match(r.message, /陆伟民/)
  assert.equal(mock.calls.length, 3, '失败后不再二次调用 send 驱动')
})

test('v2 编排中 search 失败（TARGET_NOT_FOUND）→ 透传错误码与结构化 data（query 等）', async () => {
  const keyDir = makeKeyDir()
  const mock: DriverMock = {
    calls: [],
    sendStages: [{ ...NAV_REQUIRED }],
    // 面板无结果：chatSearch 走自身 TARGET_NOT_FOUND 分支（带 query/jev/timing 诊断 data）
    searchResult: { query: '陆伟', items: [], best_index: null, overlay: { x: 100, y: 50, w: 400, h: 84 }, jev: { used: false, latency_ms: 0, reason: 'no_items' }, timing_ms: { total: 4200 } },
    selectResult: {},
  }
  const ref = createTargetRef('陆伟@微信', 'contact', '微信联系人', { keyDir })
  const op = makeOp(keyDir, makeArtifactRoot(), mock)
  const r = await op.execute({ target_ref: ref, text: 'hi' }, silentCtx())
  assert.equal(r.success, false)
  assert.equal(r.code, 'TARGET_NOT_FOUND')
  assert.equal(r.effect, 'none')
  assert.match(r.message, /搜索阶段/)
  assert.equal(r.data.query, '陆伟', '内部 search 的诊断 data 应透传')
  assert.equal(mock.calls.length, 2)
})

test('v2 候选歧义（best 不符 + 两条 name+section 匹配且 subtitle 均不符）→ TARGET_AMBIGUOUS', async () => {
  const keyDir = makeKeyDir()
  const mock: DriverMock = {
    calls: [],
    sendStages: [{ ...NAV_REQUIRED }],
    searchResult: {
      items: [
        { name: '张三', subtitle: '', section: '联系人', x: 200, y: 60, probability: 0.6 },
        { name: '陆伟@微信', subtitle: '其他（待设置部门）', section: '联系人', x: 200, y: 120, probability: 0.2 },
        { name: '陆伟', subtitle: '其他（待设置部门）', section: '联系人', x: 200, y: 180, probability: 0.2 },
      ],
      best_index: 0,
      overlay: { x: 100, y: 50, w: 400, h: 542 },
      jev: { used: true, latency_ms: 700, best_confidence: 0.6 },
      timing_ms: { total: 5000 },
    },
    selectResult: {},
  }
  const ref = createTargetRef('陆伟@微信', 'contact', '微信联系人', { keyDir })
  const op = makeOp(keyDir, makeArtifactRoot(), mock)
  const r = await op.execute({ target_ref: ref, text: 'hi' }, silentCtx())
  assert.equal(r.success, false)
  assert.equal(r.code, 'TARGET_AMBIGUOUS')
  assert.equal(r.effect, 'none')
  assert.match(r.message, /未发送消息/)
  assert.equal(mock.calls.length, 2, '歧义时不调 select')
})

test('v2 编排中 select 被取消 → CANCELLED + effect=none（定位阶段未输入任何文字）', async () => {
  const keyDir = makeKeyDir()
  const mock: DriverMock = {
    calls: [],
    sendStages: [{ ...NAV_REQUIRED }],
    searchResult: { ...SEARCH_OK },
    selectResult: new CancelledError(),
  }
  const ref = createTargetRef('陆伟@微信', 'contact', '微信联系人', { keyDir })
  const op = makeOp(keyDir, makeArtifactRoot(), mock)
  const r = await op.execute({ target_ref: ref, text: 'hi' }, silentCtx())
  assert.equal(r.success, false)
  assert.equal(r.code, 'CANCELLED')
  assert.equal(r.effect, 'none', '定位阶段取消：未发送消息，不可误报 unknown')
  assert.match(r.message, /未发送消息/)
})

test('v2 二次 send 驱动 EXECUTION_UNKNOWN → 透传 + effect=unknown，绝不重试', async () => {
  const keyDir = makeKeyDir()
  const mock: DriverMock = {
    calls: [],
    sendStages: [
      { ...NAV_REQUIRED },
      new CodedOperationError('EXECUTION_UNKNOWN', '发送后校验失败（三选二仅过 1/3 项）：消息可能已发出，不会自动重试，请人工核对'),
    ],
    searchResult: { ...SEARCH_OK },
    selectResult: { ...SELECT_OK },
  }
  const ref = createTargetRef('陆伟@微信', 'contact', '微信联系人', { keyDir })
  const op = makeOp(keyDir, makeArtifactRoot(), mock)
  const r = await op.execute({ target_ref: ref, text: 'hi' }, silentCtx())
  assert.equal(r.success, false)
  assert.equal(r.code, 'EXECUTION_UNKNOWN')
  assert.equal(r.effect, 'unknown')
  assert.equal(r.retryable, false)
  assert.equal(mock.calls.length, 4, 'EXECUTION_UNKNOWN 不触发第三次 send')
})

test('v2 target_ref 过期 → TARGET_REF_STALE；篡改 → INVALID_ARGUMENT；均不调驱动', async () => {
  const keyDir = makeKeyDir()
  const now = Math.floor(Date.now() / 1000)
  const staleRef = createTargetRef('陆伟@微信', 'contact', '微信联系人', { keyDir, now: now - 400 })
  const validRef = createTargetRef('陆伟@微信', 'contact', '微信联系人', { keyDir })
  const mock: DriverMock = { calls: [], sendStages: [], searchResult: {}, selectResult: {} }
  const op = makeOp(keyDir, makeArtifactRoot(), mock)
  const r1 = await op.execute({ target_ref: staleRef, text: 'hi' }, silentCtx())
  assert.equal(r1.code, 'TARGET_REF_STALE')
  assert.equal(r1.effect, 'none')
  const r2 = await op.execute({ target_ref: validRef + 'x', text: 'hi' }, silentCtx())
  assert.equal(r2.code, 'INVALID_ARGUMENT')
  assert.equal(r2.effect, 'none')
  assert.equal(mock.calls.length, 0)
})

test('v2 text 含换行 → 通过校验且驱动参数收到原文（多行走驱动侧剪贴板粘贴通道，副作用：覆盖用户剪贴板）', async () => {
  const keyDir = makeKeyDir()
  const root = makeArtifactRoot()
  const mock: DriverMock = { calls: [], sendStages: [{ ...SENT_OK_JEV }], searchResult: {}, selectResult: {} }
  const ref = createTargetRef('陆伟@微信', 'contact', '微信联系人', { keyDir, coords: { x: 1, y: 2 } })
  const op = makeOp(keyDir, root, mock)
  const multiline = '第一行\n第二行\r\n第三行'
  const r = await op.execute({ target_ref: ref, text: multiline }, silentCtx())
  assert.equal(r.success, true)
  assert.equal(r.code, 'OK')
  assert.equal(r.effect, 'applied')
  // 单次 send 驱动调用；-Text 参数必须是原文（含 LF/CRLF），换行语义由驱动侧剪贴板通道承载
  assert.equal(mock.calls.length, 1)
  assert.ok(mock.calls[0]!.script.endsWith('message-send.ps1'))
  const textIdx = mock.calls[0]!.args!.indexOf('-Text')
  assert.notEqual(textIdx, -1)
  assert.equal(mock.calls[0]!.args![textIdx + 1], multiline)
})

test('v2 text 超长 / 空 / 纯空白 → INVALID_ARGUMENT，不调驱动', async () => {
  const keyDir = makeKeyDir()
  const ref = createTargetRef('陆伟@微信', 'contact', '微信联系人', { keyDir })
  const mock: DriverMock = { calls: [], sendStages: [], searchResult: {}, selectResult: {} }
  const op = makeOp(keyDir, makeArtifactRoot(), mock)
  const r2 = await op.execute({ target_ref: ref, text: 'x'.repeat(2001) }, silentCtx())
  assert.equal(r2.code, 'INVALID_ARGUMENT')
  const r3 = await op.execute({ target_ref: ref, text: '' }, silentCtx())
  assert.equal(r3.code, 'INVALID_ARGUMENT')
  // 纯空白（半角/全角/NEL）：归一化后前缀为空会使驱动三选二 Contains('') 恒真，必须入口拒绝
  // （多行放行后此校验仍是硬门槛——纯空白即便含换行也拒）
  const r4 = await op.execute({ target_ref: ref, text: ' \u3000\u0085' }, silentCtx())
  assert.equal(r4.code, 'INVALID_ARGUMENT')
  assert.match(r4.message, /空白/)
  const r5 = await op.execute({ target_ref: ref, text: ' \r\n \n' }, silentCtx())
  assert.equal(r5.code, 'INVALID_ARGUMENT')
  assert.match(r5.message, /空白/)
  assert.equal(mock.calls.length, 0)
})
