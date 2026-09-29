/**
 * M11a --target-name 直达模式契约测试（send / send-image / send-file / read-session
 * 四命令；全 mock，绝不触达真实企微窗口/驱动脚本——runDriverFn 注入按脚本名分发的
 * mock，verifyRefFn/createRefFn 注入临时 keyDir 的真实签发/验证）。
 *
 * 覆盖（四命令各一遍）：
 * - target_name 成功（快路径）：内部 chatSearch（剥 @微信 的 query）→ Jev best 身份
 *   校验采纳 → 用命中条目的 target_ref 走既有链路（阶段驱动收到条目身份），data 附
 *   resolved_target {name, subtitle, section}；effect 与 ref 模式一致（写 applied /
 *   read-session none）
 * - best 身份不符 → 回退规则唯一匹配项（分发路径 select 消费唯一项坐标）
 * - 同名多候选无法消歧 → TARGET_AMBIGUOUS（fail-closed 不猜，不调阶段驱动）
 * - 搜索无结果 → TARGET_NOT_FOUND（内部 search 的诊断 data 透传）
 * - target_ref + target_name 双传 / 都不传 → INVALID_ARGUMENT，不调驱动
 * 另覆盖 send 专属 --subtitle 消歧收紧（best 身份符但 subtitle 消歧键不符 → 不信 best）、
 * 前缀陷阱（仅返回更长同首名「陆伟民」→ 零身份命中 TARGET_NOT_FOUND）、target_name
 * 带 @微信 后缀（query 剥后缀 + 两侧归一化比对）。
 */
import assert from 'node:assert/strict'
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test, { after } from 'node:test'
import { createWecomMessageSendOperation, type WecomMessageSendArgs } from '../src/operations/messageSend.js'
import { createWecomSendImageOperation, type WecomSendImageArgs } from '../src/operations/sendImage.js'
import { createWecomSendFileOperation, type WecomSendFileArgs } from '../src/operations/sendFile.js'
import { createWecomReadSessionOperation, type WecomReadSessionArgs } from '../src/operations/readSession.js'
import type { WecomOperation, OpContext } from '../src/operations/types.js'
import {
  createTargetRef,
  verifyTargetRef,
  type CreateTargetRefFn,
  type VerifyTargetRefFn,
} from '../src/platform/targetRef.js'
import type { PowerShellScriptOptions, RunPowerShellDriverFn } from '../src/platform/powershell.js'

type OpKind = 'send' | 'send-image' | 'send-file' | 'read-session'
const KINDS: OpKind[] = ['send', 'send-image', 'send-file', 'read-session']

function silentCtx(): OpContext {
  return { signal: new AbortController().signal, progress: () => {} }
}

const tempDirs: string[] = []
after(() => {
  for (const d of tempDirs) rmSync(d, { recursive: true, force: true })
})

function tempDir(prefix: string): string {
  const dir = mkdtempSync(join(tmpdir(), prefix))
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

/** 建真实小文件（send-image / send-file 的文件契约校验需要 statSync/readFileSync 通过） */
function makeRealFile(name: string, size = 64): string {
  const dir = tempDir('wecom-target-name-f-')
  const p = join(dir, name)
  writeFileSync(p, Buffer.alloc(size, 0x41))
  return p
}

/** 按脚本名分发的驱动 mock：各命令的阶段驱动消费 stages 队列（逐次出队） */
interface DriverMock {
  calls: PowerShellScriptOptions[]
  stageScript: string
  stages: Array<Record<string, unknown> | Error>
  searchResult: Record<string, unknown> | Error
  selectResult: Record<string, unknown> | Error
}

const STAGE_SCRIPT: Record<OpKind, string> = {
  send: 'message-send.ps1',
  'send-image': 'send-image.ps1',
  'send-file': 'send-file.ps1',
  'read-session': 'read-session.ps1',
}

function makeRunDriver(mock: DriverMock): RunPowerShellDriverFn {
  return (async (opts: PowerShellScriptOptions) => {
    mock.calls.push(opts)
    const base = opts.script.replace(/^.*[\\/]/, '')
    if (base === mock.stageScript) {
      const stage = mock.stages.shift()
      if (stage === undefined) throw new Error(`${base} 出现未预期的额外调用`)
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

/** 四命令 operation 的公共调用形态（args 各异，测试按需拼装；registry 同款 any 泛型） */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyOp = WecomOperation<any>

function makeOp(kind: OpKind, keyDir: string, root: string, mock: DriverMock): AnyOp {
  const shared = { runDriverFn: makeRunDriver(mock), artifactDirFn: () => root, ...realDeps(keyDir) }
  switch (kind) {
    case 'send':
      return createWecomMessageSendOperation(shared)
    case 'send-image':
      return createWecomSendImageOperation(shared)
    case 'send-file':
      return createWecomSendFileOperation(shared)
    case 'read-session':
      // env 不配置 SERVER_URL → OCR 通道直连（不触网），聚焦 target-name 定位语义
      return createWecomReadSessionOperation({ ...shared, env: {} as NodeJS.ProcessEnv })
  }
}

function makeMock(kind: OpKind, stages: Array<Record<string, unknown> | Error>, searchResult: DriverMock['searchResult']): DriverMock {
  return { calls: [], stageScript: STAGE_SCRIPT[kind], stages, searchResult, selectResult: { ...SELECT_OK } }
}

/** 各命令除目标选择器外的必填参数（send-image/send-file 建真实文件过契约校验） */
function kindExtras(kind: OpKind): Record<string, unknown> {
  switch (kind) {
    case 'send':
      return { text: '直达模式测试' }
    case 'send-image':
      return { image_path: makeRealFile('target-name.png') }
    case 'send-file':
      return { file_path: makeRealFile('target-name-report.txt') }
    case 'read-session':
      return {}
  }
}

const expectEffect = (kind: OpKind) => (kind === 'read-session' ? 'none' : 'applied')
const expectNotSent = (kind: OpKind) =>
  kind === 'send' ? '未发送消息' : kind === 'send-image' ? '未发送图片' : kind === 'send-file' ? '未发送文件' : '未读取消息'
const expectRefuse = (kind: OpKind) => (kind === 'read-session' ? '已中止读取' : '已拒绝发送')

const NAV_REQUIRED = {
  navigate_required: true,
  reason: '标题归一化规则未命中（当前标题「张三」≠ 目标「陆伟@微信」）',
  title: '张三',
  timing_ms: {},
  screenshot_paths: [],
}

/** 各命令快路径成功的阶段驱动返回 */
function okStage(kind: OpKind): Record<string, unknown> {
  const base = { navigate_required: false, title: '陆伟', timing_ms: { total: 7000 }, screenshot_paths: [] }
  switch (kind) {
    case 'send':
      return { ...base, sent_verification: { method: 'jev', result: 'sent' }, input_point: { x: 640, y: 648 } }
    case 'send-image':
      return { ...base, sent_verification: { method: 'jev', result: 'sent' }, input_stddev: { before: 9.8, paste: 36.5, after: 9.9 } }
    case 'send-file':
      return { ...base, sent_verification: { method: 'jev', result: 'sent' }, paste_check: { stem: 'target-name', paste_hit: true, input_gone: true, list_hit: true } }
    case 'read-session':
      return {
        navigate_required: false,
        title: '陆伟 @微信',
        parse_mode: 'ocr',
        messages: [{ side: 'peer', text: '在吗' }],
        page_paths: [],
        pages_read: 1,
        timing_ms: { total: 9000 },
        screenshot_paths: [],
      }
  }
}

/** 唯一命中条目：名称带 @微信 后缀（target_name 传裸名 → 归一化剥后缀匹配） */
const SEARCH_OK = {
  query: '陆伟',
  items: [{ name: '陆伟@微信', subtitle: '微信联系人', section: '联系人', x: 200, y: 120, probability: 0.71 }],
  best_index: 0,
  overlay: { x: 100, y: 50, w: 400, h: 300 },
  jev: { used: true, latency_ms: 700, is_ambiguous: false, best_confidence: 0.71 },
  timing_ms: { total: 5000 },
}

/** Jev best（张三）身份不符：同名目标在 items 里唯一匹配（回退规则） */
const SEARCH_BEST_MISMATCH = {
  query: '陆伟',
  items: [
    { name: '张三', subtitle: '', section: '联系人', x: 200, y: 60, probability: 0.8 },
    { name: '陆伟@微信', subtitle: '微信联系人', section: '联系人', x: 200, y: 120, probability: 0.2 },
  ],
  best_index: 0,
  overlay: { x: 100, y: 50, w: 400, h: 542 },
  jev: { used: true, latency_ms: 700, best_confidence: 0.8 },
  timing_ms: { total: 5000 },
}

/** 同名（归一化后）两条且无消歧键 → 歧义拒绝 */
const SEARCH_AMBIGUOUS = {
  query: '陆伟',
  items: [
    { name: '张三', subtitle: '', section: '联系人', x: 200, y: 60, probability: 0.6 },
    { name: '陆伟@微信', subtitle: '其他（待设置部门）', section: '联系人', x: 200, y: 120, probability: 0.2 },
    { name: '陆伟', subtitle: '其他（待设置部门）', section: '联系人', x: 200, y: 180, probability: 0.2 },
  ],
  best_index: 0,
  overlay: { x: 100, y: 50, w: 400, h: 542 },
  jev: { used: true, latency_ms: 700, best_confidence: 0.6 },
  timing_ms: { total: 5000 },
}

/** 面板无结果（chatSearch operation 自身 TARGET_NOT_FOUND 分支） */
const SEARCH_EMPTY = {
  query: '陆伟',
  items: [],
  best_index: null,
  jev: { used: false, latency_ms: 0, reason: 'no_items' },
  timing_ms: { total: 4200 },
}

const SELECT_OK = {
  target: { name: '陆伟@微信', subtitle: '微信联系人', section: 'contact' },
  title: '陆伟',
  clicked: { x: 300, y: 170 },
  timing_ms: {},
  screenshot_paths: [],
}

function argAfter(call: PowerShellScriptOptions, flag: string): string {
  const i = call.args?.indexOf(flag) ?? -1
  assert.ok(i !== -1, `驱动参数应含 ${flag}`)
  return call.args![i + 1]! as string
}

// —— 四命令公共场景（数据驱动，每命令独立 test 便于定位） —— //

for (const kind of KINDS) {
  test(`M11a ${kind}：target_name 直达成功（快路径）——内部 search 剥 @微信 query + best 身份校验采纳 + 条目 ref 透传既有链路`, async () => {
    const keyDir = tempDir('wecom-tn-key-')
    const mock = makeMock(kind, [okStage(kind)], { ...SEARCH_OK })
    const op = makeOp(kind, keyDir, tempDir('wecom-tn-root-'), mock)
    // target_name 传裸名「陆伟」：条目「陆伟@微信」归一化后命中（剥 @微信 后比对）
    const r = await op.execute({ target_name: '陆伟', ...kindExtras(kind) }, silentCtx())
    assert.equal(r.success, true, r.message)
    assert.equal(r.code, 'OK')
    assert.equal(r.effect, expectEffect(kind))
    assert.equal(r.retryable, false)
    // resolved_target 三元组透传（命中条目的身份，供调用方观察实际定位到谁）
    assert.deepEqual(r.data.resolved_target, { name: '陆伟@微信', subtitle: '微信联系人', section: '联系人' })
    assert.equal(r.data.navigated, false)
    // 调用顺序：先内部 search（定位），再阶段驱动（快路径直发/直读，无 select）
    assert.equal(mock.calls.length, 2)
    assert.ok(mock.calls[0]!.script.endsWith('chat-search.ps1'))
    assert.ok(mock.calls[1]!.script.endsWith(STAGE_SCRIPT[kind]))
    assert.deepEqual(mock.calls[0]!.args?.slice(0, 2), ['-Query', '陆伟'])
    // 阶段驱动收到 resolved ref 解出的条目身份（ref 模式同款参数形态）
    assert.deepEqual(
      mock.calls[1]!.args?.slice(0, 6),
      ['-TargetName', '陆伟@微信', '-Subtitle', '微信联系人', '-Section', 'contact'],
    )
  })

  test(`M11a ${kind}：best 身份不符 → 回退规则唯一匹配项（分发路径 select 消费唯一项坐标）`, async () => {
    const keyDir = tempDir('wecom-tn-key-')
    const mock = makeMock(kind, [{ ...NAV_REQUIRED }, okStage(kind)], { ...SEARCH_BEST_MISMATCH })
    const op = makeOp(kind, keyDir, tempDir('wecom-tn-root-'), mock)
    const r = await op.execute({ target_name: '陆伟', ...kindExtras(kind) }, silentCtx())
    assert.equal(r.success, true, r.message)
    assert.deepEqual(r.data.resolved_target, { name: '陆伟@微信', subtitle: '微信联系人', section: '联系人' })
    // 定位 search → 阶段驱动 navigate_required → 分发 search（同一 mock 结果）→ select → 二次阶段驱动
    assert.equal(mock.calls.length, 5)
    assert.ok(mock.calls[0]!.script.endsWith('chat-search.ps1'))
    assert.ok(mock.calls[1]!.script.endsWith(STAGE_SCRIPT[kind]))
    assert.ok(mock.calls[2]!.script.endsWith('chat-search.ps1'))
    assert.ok(mock.calls[3]!.script.endsWith('chat-select.ps1'))
    assert.ok(mock.calls[4]!.script.endsWith(STAGE_SCRIPT[kind]))
    // select 拿到的是回退规则唯一匹配项（陆伟@微信, 200/120）的坐标，不是 best（张三, 200/60）
    assert.equal(argAfter(mock.calls[3]!, '-X'), '200')
    assert.equal(argAfter(mock.calls[3]!, '-Y'), '120')
    assert.equal(argAfter(mock.calls[3]!, '-TargetName'), '陆伟@微信')
  })

  test(`M11a ${kind}：同名多候选无法消歧 → TARGET_AMBIGUOUS（fail-closed 不猜，不调阶段驱动）`, async () => {
    const keyDir = tempDir('wecom-tn-key-')
    const mock = makeMock(kind, [], { ...SEARCH_AMBIGUOUS })
    const op = makeOp(kind, keyDir, tempDir('wecom-tn-root-'), mock)
    const r = await op.execute({ target_name: '陆伟', ...kindExtras(kind) }, silentCtx())
    assert.equal(r.success, false)
    assert.equal(r.code, 'TARGET_AMBIGUOUS')
    assert.equal(r.effect, 'none')
    assert.equal(r.retryable, false)
    assert.match(r.message, /陆伟/)
    assert.match(r.message, new RegExp(expectRefuse(kind)))
    assert.match(r.message, new RegExp(expectNotSent(kind)))
    // 只调了一次内部 search；歧义时绝不 select / 不调阶段驱动
    assert.equal(mock.calls.length, 1)
    assert.ok(mock.calls[0]!.script.endsWith('chat-search.ps1'))
  })

  test(`M11a ${kind}：搜索无结果 → TARGET_NOT_FOUND（内部 search 诊断 data 透传）`, async () => {
    const keyDir = tempDir('wecom-tn-key-')
    const mock = makeMock(kind, [], { ...SEARCH_EMPTY })
    const op = makeOp(kind, keyDir, tempDir('wecom-tn-root-'), mock)
    const r = await op.execute({ target_name: '陆伟', ...kindExtras(kind) }, silentCtx())
    assert.equal(r.success, false)
    assert.equal(r.code, 'TARGET_NOT_FOUND')
    assert.equal(r.effect, 'none')
    assert.match(r.message, /搜索阶段/)
    assert.match(r.message, new RegExp(expectNotSent(kind)))
    // chatSearch 的结构化诊断（query/reason）透传给调用方
    assert.equal(r.data.query, '陆伟')
    assert.equal(r.data.reason, 'no_results')
    assert.equal(mock.calls.length, 1)
  })

  test(`M11a ${kind}：target_ref + target_name 双传 / 都不传 → INVALID_ARGUMENT，不调驱动`, async () => {
    const keyDir = tempDir('wecom-tn-key-')
    const ref = createTargetRef('陆伟@微信', 'contact', '微信联系人', { keyDir })
    const mock = makeMock(kind, [], { ...SEARCH_OK })
    const op = makeOp(kind, keyDir, tempDir('wecom-tn-root-'), mock)
    const both = await op.execute({ target_ref: ref, target_name: '陆伟', ...kindExtras(kind) }, silentCtx())
    assert.equal(both.code, 'INVALID_ARGUMENT')
    assert.match(both.message, /互斥/)
    assert.equal(both.effect, 'none')
    const neither = await op.execute({ ...kindExtras(kind) }, silentCtx())
    assert.equal(neither.code, 'INVALID_ARGUMENT')
    assert.match(neither.message, /必须传其一/)
    assert.equal(neither.effect, 'none')
    assert.equal(mock.calls.length, 0, '参数错误绝不调驱动（不触达企微）')
  })
}

// —— send 专属：--subtitle 消歧收紧 —— //

test('M11a send：--subtitle 收紧消歧——best 身份符（同名）但 subtitle 消歧键不符 → 不信 best，回退唯一匹配项', async () => {
  const keyDir = tempDir('wecom-tn-key-')
  const searchResult = {
    query: '陆伟',
    items: [
      // best（Jev 选中的同名条目）subtitle 与调用方消歧键不符
      { name: '陆伟@微信', subtitle: '其他（待设置部门）', section: '联系人', x: 200, y: 60, probability: 0.8 },
      { name: '陆伟', subtitle: '微信联系人', section: '联系人', x: 200, y: 120, probability: 0.2 },
    ],
    best_index: 0,
    overlay: { x: 100, y: 50, w: 400, h: 542 },
    jev: { used: true, latency_ms: 700, best_confidence: 0.8 },
    timing_ms: { total: 5000 },
  }
  const mock = makeMock('send', [{ ...NAV_REQUIRED }, okStage('send')], searchResult)
  const op = makeOp('send', keyDir, tempDir('wecom-tn-root-'), mock)
  const r = await op.execute({ target_name: '陆伟', subtitle: '微信联系人', text: '带副标题消歧' }, silentCtx())
  assert.equal(r.success, true, r.message)
  // resolved 到 subtitle 匹配的唯一项（items[1]「陆伟」），不是 best（items[0]「陆伟@微信」）
  assert.deepEqual(r.data.resolved_target, { name: '陆伟', subtitle: '微信联系人', section: '联系人' })
  assert.equal(mock.calls.length, 5)
  assert.ok(mock.calls[3]!.script.endsWith('chat-select.ps1'))
  assert.equal(argAfter(mock.calls[3]!, '-X'), '200')
  assert.equal(argAfter(mock.calls[3]!, '-Y'), '120')
  assert.equal(argAfter(mock.calls[3]!, '-TargetName'), '陆伟')
})

test('M11a send：前缀陷阱——搜索仅返回更长同首名候选「陆伟民」（best 即它）→ 绝不匹配「陆伟」，TARGET_NOT_FOUND 不调阶段驱动', async () => {
  const keyDir = tempDir('wecom-tn-key-')
  // 归一化严格相等（normNameForMatch）：「陆伟」≠「陆伟民」——best 采纳分支与回退分支都必须拒绝
  const searchResult = {
    query: '陆伟',
    items: [
      { name: '陆伟民', subtitle: '微信联系人', section: '联系人', x: 200, y: 120, probability: 0.9 },
    ],
    best_index: 0,
    overlay: { x: 100, y: 50, w: 400, h: 300 },
    jev: { used: true, latency_ms: 700, best_confidence: 0.9 },
    timing_ms: { total: 5000 },
  }
  const mock = makeMock('send', [], searchResult)
  const op = makeOp('send', keyDir, tempDir('wecom-tn-root-'), mock)
  const r = await (op as WecomOperation<WecomMessageSendArgs>).execute({ target_name: '陆伟', text: 'hi' }, silentCtx())
  assert.equal(r.success, false)
  assert.equal(r.code, 'TARGET_NOT_FOUND')
  assert.equal(r.effect, 'none')
  assert.match(r.message, /没有匹配候选/)
  assert.match(r.message, /未发送消息/)
  // search 成功但身份零命中：只调了一次内部 search，绝不 select / 不调阶段驱动
  assert.equal(mock.calls.length, 1)
  assert.ok(mock.calls[0]!.script.endsWith('chat-search.ps1'))
})

test('M11a send：target_name 带 @微信 后缀——内部 query 剥后缀（-Query 陆伟），身份比对两侧归一化后命中', async () => {
  const keyDir = tempDir('wecom-tn-key-')
  const mock = makeMock('send', [okStage('send')], { ...SEARCH_OK })
  const op = makeOp('send', keyDir, tempDir('wecom-tn-root-'), mock)
  const r = await (op as WecomOperation<WecomMessageSendArgs>).execute({ target_name: '陆伟@微信', text: '带后缀直达' }, silentCtx())
  assert.equal(r.success, true, r.message)
  assert.deepEqual(mock.calls[0]!.args?.slice(0, 2), ['-Query', '陆伟'])
  assert.deepEqual(r.data.resolved_target, { name: '陆伟@微信', subtitle: '微信联系人', section: '联系人' })
  assert.equal(mock.calls.length, 2)
})

test('M11a send：ref 模式不受影响——subtitle 未传 / resolved_target 不出现，行为与 M6 完全一致', async () => {
  const keyDir = tempDir('wecom-tn-key-')
  const mock = makeMock('send', [okStage('send')], { ...SEARCH_OK })
  const op = makeOp('send', keyDir, tempDir('wecom-tn-root-'), mock)
  const ref = createTargetRef('陆伟@微信', 'contact', '微信联系人', { keyDir, coords: { x: 1, y: 2 } })
  const r = await (op as WecomOperation<WecomMessageSendArgs>).execute({ target_ref: ref, text: 'ref 模式回归' }, silentCtx())
  assert.equal(r.success, true, r.message)
  assert.equal(r.effect, 'applied')
  assert.equal(r.data.resolved_target, undefined, 'ref 模式无解析步骤，不附 resolved_target')
  assert.equal(mock.calls.length, 1, 'ref 模式快路径不调内部 search')
})

test('M11a 参数校验矩阵补充：target_name 空/纯空白/超长、subtitle 类型错 → INVALID_ARGUMENT，不调驱动', async () => {
  const keyDir = tempDir('wecom-tn-key-')
  const mock = makeMock('send', [], { ...SEARCH_OK })
  const op = makeOp('send', keyDir, tempDir('wecom-tn-root-'), mock)
  const sendOp = op as WecomOperation<WecomMessageSendArgs>
  const r1 = await sendOp.execute({ target_name: '   ', text: 'hi' }, silentCtx())
  assert.equal(r1.code, 'INVALID_ARGUMENT')
  assert.match(r1.message, /target_name/)
  const r2 = await sendOp.execute({ target_name: 'x'.repeat(101), text: 'hi' }, silentCtx())
  assert.equal(r2.code, 'INVALID_ARGUMENT')
  assert.match(r2.message, /100/)
  const r3 = await sendOp.execute({ target_name: '陆伟', subtitle: 123, text: 'hi' } as unknown as WecomMessageSendArgs, silentCtx())
  assert.equal(r3.code, 'INVALID_ARGUMENT')
  assert.match(r3.message, /subtitle/)
  const r4 = await sendOp.execute({ target_ref: '', text: 'hi' }, silentCtx())
  assert.equal(r4.code, 'INVALID_ARGUMENT')
  assert.equal(mock.calls.length, 0)
})

// —— read-session 直达模式与 MCP 外契约对齐：args 形态直接走 operation（CLI 同款） —— //

test('M11a read-session：target_name + max_pages 透传阶段驱动（-MaxPages），resolved_target 在 OCR 通道 data 中', async () => {
  const keyDir = tempDir('wecom-tn-key-')
  const mock = makeMock('read-session', [okStage('read-session')], { ...SEARCH_OK })
  const op = makeOp('read-session', keyDir, tempDir('wecom-tn-root-'), mock)
  const r = await (op as WecomOperation<WecomReadSessionArgs>).execute({ target_name: '陆伟', max_pages: 3 }, silentCtx())
  assert.equal(r.success, true, r.message)
  assert.equal(r.effect, 'none')
  assert.equal(r.data.channel, 'ocr')
  assert.deepEqual(r.data.resolved_target, { name: '陆伟@微信', subtitle: '微信联系人', section: '联系人' })
  assert.equal(argAfter(mock.calls[1]!, '-MaxPages'), '3')
})

test('M11a send-image / send-file：target_name 模式下内部签发的 ref 含坐标（分发链路可消费，不触发旧版签发拒绝）', async () => {
  for (const kind of ['send-image', 'send-file'] as const) {
    const keyDir = tempDir('wecom-tn-key-')
    const mock = makeMock(kind, [{ ...NAV_REQUIRED }, okStage(kind)], { ...SEARCH_OK })
    const op = makeOp(kind, keyDir, tempDir('wecom-tn-root-'), mock)
    const args =
      kind === 'send-image'
        ? ({ target_name: '陆伟', image_path: makeRealFile('coord.png') } as WecomSendImageArgs)
        : ({ target_name: '陆伟', file_path: makeRealFile('coord-report.txt') } as WecomSendFileArgs)
    const r = await op.execute(args, silentCtx())
    assert.equal(r.success, true, `${kind}: ${r.message}`)
    // 走到分发路径（search → 阶段 → search → select → 阶段）说明 resolved ref 通过了坐标校验
    assert.equal(mock.calls.length, 5)
    assert.ok(mock.calls[3]!.script.endsWith('chat-select.ps1'), `${kind} 分发链消费内部签发的带坐标 ref`)
  }
})
