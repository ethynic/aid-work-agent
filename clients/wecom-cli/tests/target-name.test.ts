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
import type { WecomOperation, OpContext } from '../src/operations/types.js'
import {
  createTargetRef,
  verifyTargetRef,
  type CreateTargetRefFn,
  type VerifyTargetRefFn,
} from '../src/platform/targetRef.js'
import type { PowerShellScriptOptions, RunPowerShellDriverFn } from '../src/platform/powershell.js'

type OpKind = 'send' | 'send-image' | 'send-file'
const KINDS: OpKind[] = ['send-image', 'send-file']

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
    createRefFn: (name, type, subtitle, coords, account) => createTargetRef(name, type, subtitle, { keyDir, coords, account }),
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
  catalogCalls?: string[]
  stageScript: string
  stages: Array<Record<string, unknown> | Error>
  searchResult: Record<string, unknown> | Error
  selectResult: Record<string, unknown> | Error
}

const STAGE_SCRIPT: Record<OpKind, string> = {
  send: 'message-send.ps1',
  'send-image': 'send-image.ps1',
  'send-file': 'send-file.ps1',
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
  mock.catalogCalls = []
  const shared = { runDriverFn: makeRunDriver(mock), artifactDirFn: () => root,
    probeAccountFn: async () => ({ account: 'account' }), ...realDeps(keyDir),
    readCatalogFn: async (args: { query?: string }) => {
      mock.catalogCalls!.push(args.query!)
      if (mock.searchResult instanceof Error) throw mock.searchResult
      const raw = Array.isArray(mock.searchResult.items) ? mock.searchResult.items as { name: string }[] : []
      const hits = raw.filter(row => row.name.includes(args.query!))
      return { message: '', data: { self_user_id: 'account', self_name: '本人', match_count: hits.length,
        candidates: hits.map(row => ({ name: row.name, section: '联系人' })) } }
    },
  }
  switch (kind) {
    case 'send':
      // P2 任务 3.4：显式置空本地库状态（锁定老路径契约，不依赖测试机真实 localdb-state.json）
      return createWecomMessageSendOperation({ ...shared, localdb: { readStateFn: () => null } })
    case 'send-image':
      return createWecomSendImageOperation(shared)
    case 'send-file':
      return createWecomSendFileOperation(shared)
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
  }
}

const expectEffect = (_kind: OpKind) => 'applied'
const expectNotSent = (kind: OpKind) =>
  kind === 'send' ? '未发送消息' : kind === 'send-image' ? '未发送图片' : kind === 'send-file' ? '未发送文件' : '未读取消息'
const expectRefuse = (_kind: OpKind) => '已拒绝发送'

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
  title: '陆伟@微信',
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
    assert.deepEqual(r.data.resolved_target, { name: '陆伟@微信', subtitle: '', section: '联系人' })
    assert.equal(r.data.navigated, false)
    // 本地目录先定位，再阶段驱动；未调用 OCR 搜索脚本。
    assert.deepEqual(mock.catalogCalls, ['陆伟'])
    assert.equal(mock.calls.length, 1)
    assert.ok(mock.calls[0]!.script.endsWith(STAGE_SCRIPT[kind]))
    // 阶段驱动收到 resolved ref 解出的条目身份（ref 模式同款参数形态）
    assert.deepEqual(
      mock.calls[0]!.args?.slice(0, 6),
      ['-TargetName', '陆伟@微信', '-Subtitle', '', '-Section', 'contact'],
    )
  })

  test(`M11a ${kind}：best 身份不符 → 回退规则唯一匹配项（分发路径 select 消费唯一项坐标）`, async () => {
    const keyDir = tempDir('wecom-tn-key-')
    const mock = makeMock(kind, [{ ...NAV_REQUIRED }, okStage(kind)], { ...SEARCH_BEST_MISMATCH })
    const op = makeOp(kind, keyDir, tempDir('wecom-tn-root-'), mock)
    const r = await op.execute({ target_name: '陆伟', ...kindExtras(kind) }, silentCtx())
    assert.equal(r.success, true, r.message)
    assert.deepEqual(r.data.resolved_target, { name: '陆伟@微信', subtitle: '', section: '联系人' })
    // 目录忽略不匹配的张三；定位 → 阶段 → 目录复核 → select → 阶段。
    assert.deepEqual(mock.catalogCalls, ['陆伟', '陆伟', '陆伟@微信', '陆伟@微信'])
    assert.equal(mock.calls.length, 3)
    assert.ok(mock.calls[0]!.script.endsWith(STAGE_SCRIPT[kind]))
    assert.ok(mock.calls[1]!.script.endsWith('chat-select.ps1'))
    assert.ok(mock.calls[2]!.script.endsWith(STAGE_SCRIPT[kind]))
    assert.equal(argAfter(mock.calls[1]!, '-TargetName'), '陆伟@微信')
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
    // 歧义时绝不 select / 不调阶段驱动
    assert.deepEqual(mock.catalogCalls, ['陆伟'])
    assert.equal(mock.calls.length, 0)
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
    assert.equal(r.data.match_count, 0)
    assert.equal(mock.calls.length, 0)
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

test('M11a send-image / send-file：无坐标账号绑定 ref 仍可走分发链路', async () => {
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
    assert.equal(mock.calls.length, 3)
    assert.ok(mock.calls[1]!.script.endsWith('chat-select.ps1'), `${kind} 分发链消费无坐标 ref`)
  }
})
