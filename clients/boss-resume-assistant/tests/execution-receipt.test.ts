/**
 * 执行回执标准 1.1（Phase 5）：BOSS CLI 侧回执字段、协商/未协商两态、截图边界。
 *
 * - 协商（ctx.executionReceipt='1.1'，来自 MCP extra._meta["aidwork/receipt_version"]）：
 *   失败/部分完成/效果未知终态 data 附加 receipt_version/step/reason/known_effect
 *   （partial 附 partial_results），并尽力截一张当前受权页面；成功不附加、不截图。
 * - 未协商（CLI 直调/旧 Runtime）：data 无 1.1 字段、不截图——旧回执逐字节兼容。
 * - 截图失败/超限（5 MiB 原始字节，P10 同值）/无页面 → 省略，业务结果保留。
 * - 协议锁定：真实 MCP SDK 注册入口经 InMemoryTransport 的 extra._meta 透传
 *   （协议探针已实证 stdio 同路径），协商与未协商两态行为。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js'
import { InMemoryTransport } from '@modelcontextprotocol/sdk/inMemory.js'
import { Client } from '@modelcontextprotocol/sdk/client/index.js'
import { buildReceipt1_1, attachReceipt1_1, MAX_SCREENSHOT_BYTES } from '../src/main/operations/receipt.js'
import { runBossOperation } from '../src/main/operations/bossContext.js'
import { createBossFilterOperation, createBossClearFilterOperation } from '../src/main/operations/bossFilter.js'
import { CodedOperationError, type OperationResult, type OpContext } from '../src/main/operations/types.js'
import { encodeOperationResult, negotiatedReceiptVersion, registerBossTools } from '../src/mcp/server.js'
import type { BossSession } from '../src/main/operations/bossContext.js'
import type { DomSnapshot } from '../src/main/boss/domSnapshot.js'
import { OPERATIONS } from '../src/main/operations/index.js'

process.env.AID_BOSS_AUTO_CHROME = '0'

function ctx(extra: Partial<OpContext> = {}): OpContext {
  return { signal: new AbortController().signal, progress: () => {}, ...extra }
}

function fakeSession(opts: { capture?: () => Promise<Buffer>; url?: string } = {}): BossSession {
  return {
    snapshot: async () => ({ nodes: [] }) as unknown as DomSnapshot,
    click: async () => {},
    clickBrowse: async () => {},
    mouseWheel: async () => {},
    pressEscape: async () => {},
    clickAndType: async () => {},
    captureFullpage: opts.capture ?? (async () => Buffer.alloc(0)),
    getUrl: async () => opts.url ?? 'https://www.zhipin.com/web/chat/recommend',
    close: async () => {},
  }
}

function factoryOf(session: BossSession, opts: { throwOnCreate?: unknown } = {}) {
  return async () => {
    if (opts.throwOnCreate) throw opts.throwOnCreate
    return session
  }
}

test('buildReceipt1_1：固定字段形状与 known_effect 一致性断言', () => {
  const receipt = buildReceipt1_1({
    effect: 'none', step: 'validate_input',
    reason: { code: 'INVALID_ARGUMENT', message: '没有可应用的筛选条件' },
  })
  assert.deepEqual(receipt, {
    receipt_version: '1.1', step: 'validate_input',
    reason: { code: 'INVALID_ARGUMENT', message: '没有可应用的筛选条件' },
    known_effect: 'none',
  })
  // effect 非 1.1 合法枚举 → 编程错误抛出（调用方兜底保留原结果）
  assert.throws(() => buildReceipt1_1({ effect: 'applied!' as never, step: 'x' }), /known_effect/)
  assert.throws(() => buildReceipt1_1({ effect: 'none', step: '' }), /step/)
})

test('runBossOperation 协商失败：data 附加 1.1 字段并尽力截图；未协商同路径零附加', async () => {
  // 借 boss_clear_filter 产出页面阶段失败（WRONG_PAGE：非推荐页，连接成功后 body 抛出）
  const wrongUrl = factoryOf(fakeSession({ capture: async () => Buffer.from('png-bytes'), url: 'https://www.zhipin.com/web/chat/index' }))
  const negotiated = await createBossClearFilterOperation(wrongUrl).execute({}, ctx({ executionReceipt: '1.1' }))
  assert.equal(negotiated.success, false)
  assert.equal(negotiated.data['receipt_version'], '1.1')
  assert.equal(negotiated.data['known_effect'], negotiated.effect)
  assert.equal(negotiated.data['step'], 'execute')
  assert.deepEqual(negotiated.data['reason'], { code: negotiated.code, message: negotiated.message })
  const screenshot = negotiated.data['screenshot'] as { type: string; mimeType: string; data: string }
  assert.equal(screenshot.type, 'image')
  assert.equal(screenshot.mimeType, 'image/png')
  assert.equal(Buffer.from(screenshot.data, 'base64').toString(), 'png-bytes')
  // 业务结果保留：外层业务字段不受回执附加影响（失败 message 原样）
  assert.ok(negotiated.message.includes('推荐牛人页面'))

  // 未协商：同一条失败路径 data 无 1.1 字段、无截图（旧回执兼容）
  const legacy = await createBossClearFilterOperation(wrongUrl).execute({}, ctx())
  assert.equal(legacy.success, false)
  assert.equal(legacy.data['receipt_version'], undefined)
  assert.equal(legacy.data['screenshot'], undefined)
  assert.equal(legacy.data['known_effect'], undefined)
})

test('runBossOperation 成功不附加回执、不截图；调用内截图失败/超限省略且业务结果保留', async () => {
  // 成功路径（清除筛选成功需要面板动作，改用 validate 通过后 body 抛错之外的场景：
  // 用只读 boss_list_jobs 不可行——改验证 attachReceipt1_1 对成功结果直通）
  const ok = { success: true, code: 'OK', message: 'ok', effect: 'applied', data: { applied: true }, retryable: false, run_id: 'r' } as OperationResult
  const passthrough = await attachReceipt1_1(ok, { step: 'postcheck', capture: async () => Buffer.from('x') })
  assert.deepEqual(passthrough, ok)

  // 截图 throw → 省略
  const failed = await attachReceipt1_1(
    { success: false, code: 'UI_CHANGED', message: 'm', effect: 'none', data: { keep: 1 }, retryable: false, run_id: 'r' },
    { step: 'input', capture: async () => { throw new Error('cdp gone') } },
  )
  assert.equal(failed.data['receipt_version'], '1.1')
  assert.equal(failed.data['screenshot'], undefined)
  assert.equal(failed.data['keep'], 1)

  // 超限（>5MiB 原始字节）→ 省略
  const oversized = await attachReceipt1_1(
    { success: false, code: 'UI_CHANGED', message: 'm', effect: 'unknown', data: {}, retryable: false, run_id: 'r' },
    { step: 'input', capture: async () => Buffer.alloc(MAX_SCREENSHOT_BYTES + 1) },
  )
  assert.equal(oversized.data['screenshot'], undefined)
  assert.equal(oversized.data['receipt_version'], '1.1')
})

test('连接失败（无页面）协商回执 step=connect 无截图；参数校验失败 step=validate_input', async () => {
  const result = await runBossOperation(
    { kind: 'write', name: 'boss_greet' },
    ctx({ executionReceipt: '1.1' }),
    async () => { throw new Error('ECONNREFUSED') },
    () => null,
    async () => ({ message: 'unreachable' }),
  )
  assert.equal(result.code, 'CHROME_UNAVAILABLE')
  assert.equal(result.data['receipt_version'], '1.1')
  assert.equal(result.data['step'], 'connect')
  assert.equal(result.data['screenshot'], undefined)

  const invalid = await runBossOperation(
    { kind: 'write', name: 'boss_greet' },
    ctx({ executionReceipt: '1.1' }),
    factoryOf(fakeSession()),
    () => 'names 必须是至少含 1 个姓名的数组',
    async () => ({ message: 'unreachable' }),
  )
  assert.equal(invalid.code, 'INVALID_ARGUMENT')
  assert.equal(invalid.data['receipt_version'], '1.1')
  assert.equal(invalid.data['step'], 'validate_input')
  assert.deepEqual(invalid.data['reason'], { code: 'INVALID_ARGUMENT', message: 'names 必须是至少含 1 个姓名的数组' })
  // 工厂未被调用（fail fast），无截图
  assert.equal(invalid.data['screenshot'], undefined)
})

test('部分完成：保留业务证据，不用聚合完成数量捏造逐项引用', async () => {
  const result = await runBossOperation(
    { kind: 'write', name: 'boss_greet' },
    ctx({ executionReceipt: '1.1' }),
    factoryOf(fakeSession()),
    () => null,
    async () => {
      throw new CodedOperationError('UI_CHANGED', '第3人触发付费墙，已停止', {
        operation_recovery: {
          schema_version: 1, operation_class: 'external_effect',
          business_effect: { status: 'confirmed', completed_count: 2 },
          operation: { name: 'boss_greet', goal: 'g', expected: 'e', result_kind: 'business_result' },
          execution: { stage: 'input', input_status: 'attempted', attempts_used: 1, ordinary_retries_used: 0 },
          verification: { status: 'inconclusive', evidence: [] },
        },
        confirmed_items: [{ item_ref: 'a' }, { item_ref: 'b' }],
      })
    },
  )
  assert.equal(result.effect, 'partial')
  assert.equal(result.data['receipt_version'], '1.1')
  assert.equal(result.data['known_effect'], 'partial')
  assert.equal(result.data['step'], 'input')
  assert.equal(result.data['partial_results'], undefined)
  // 已知业务证据保留
  assert.deepEqual(result.data['confirmed_items'], [{ item_ref: 'a' }, { item_ref: 'b' }])
})

test('boss_filter 零输入回执与 1.1 共存：INVALID_ARGUMENT/none 语义不变，协商补 step/reason', async () => {
  const op = createBossFilterOperation(factoryOf(fakeSession()))
  const negotiated = await op.execute({ experience: '  ', educations: [' '] }, ctx({ executionReceipt: '1.1' }))
  assert.equal(negotiated.success, false)
  assert.equal(negotiated.code, 'INVALID_ARGUMENT')
  assert.equal(negotiated.effect, 'none')
  assert.equal(negotiated.data['receipt_version'], '1.1')
  assert.equal(negotiated.data['known_effect'], 'none')
  assert.equal(negotiated.data['step'], 'validate_input')
  assert.ok(negotiated.data['filter_evidence'])
  assert.equal(negotiated.data['screenshot'], undefined)

  // 未协商：与 Phase 2 基线逐字段一致（无 1.1 字段）
  const legacy = await op.execute({ experience: '  ' }, ctx())
  assert.equal(legacy.data['receipt_version'], undefined)
  assert.equal(legacy.data['step'], undefined)
})

test('登录失效：既有 NOT_LOGGED_IN 映射经 reason.code 透出，known_effect=none', async () => {
  const result = await runBossOperation(
    { kind: 'readonly', name: 'boss_read_chat' },
    ctx({ executionReceipt: '1.1' }),
    factoryOf(fakeSession({ capture: async () => Buffer.from('login-page') })),
    () => null,
    async () => { throw new Error('打开页面失败：请确认已登录 BOSS 直聘') },
  )
  assert.equal(result.code, 'NOT_LOGGED_IN')
  assert.equal(result.data['receipt_version'], '1.1')
  assert.equal(result.data['known_effect'], 'none')
  assert.equal((result.data['reason'] as { code: string }).code, 'NOT_LOGGED_IN')
})

test('MCP 协议两态：extra._meta 协商 → 回执字段；无 _meta → 旧回执（真实 SDK 注册入口）', async () => {
  // handler 每次调用时解析 OPERATIONS[def.name]，可安全替换/还原为注入 fake 工厂的操作
  const original = OPERATIONS['boss_clear_filter']!
  const swapped = createBossClearFilterOperation(factoryOf(fakeSession({ url: 'https://www.zhipin.com/web/chat/index' })))
  OPERATIONS['boss_clear_filter'] = { operation: swapped, cli: original.cli }
  try {
    const server = new McpServer({ name: 'boss-recruiting', version: 'test' }, {})
    registerBossTools(server, { cdpPort: 1 }, { current: null })
    const client = new Client({ name: 'receipt-protocol-test', version: '0.1.0' }, { capabilities: {} })
    const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair()
    await Promise.all([server.connect(serverTransport), client.connect(clientTransport)])

    const negotiated = await client.callTool({
      name: 'boss_clear_filter', arguments: {},
      _meta: { 'aidwork/receipt_version': '1.1' },
    })
    const negotiatedResult = (negotiated as { structuredContent?: Record<string, unknown> }).structuredContent as { data: Record<string, unknown> }
    assert.equal(negotiatedResult.data['receipt_version'], '1.1')
    assert.equal(negotiatedResult.data['known_effect'], 'none')
    assert.ok(negotiatedResult.data['step'])

    const legacyCall = await client.callTool({ name: 'boss_clear_filter', arguments: {} })
    const legacyResult = (legacyCall as { structuredContent?: Record<string, unknown> }).structuredContent as { data: Record<string, unknown> }
    assert.equal(legacyResult.data['receipt_version'], undefined)
    assert.equal(legacyResult.data['known_effect'], undefined)

    // 无关节注 _meta（非 1.1 值）不协商
    const otherMeta = await client.callTool({ name: 'boss_clear_filter', arguments: {}, _meta: { 'aidwork/receipt_version': '9.9' } })
    const otherResult = (otherMeta as { structuredContent?: Record<string, unknown> }).structuredContent as { data: Record<string, unknown> }
    assert.equal(otherResult.data['receipt_version'], undefined)
  } finally {
    OPERATIONS['boss_clear_filter'] = original
  }
})

test('MCP stdio 整包放不下故障截图时保留双份等价业务回执', () => {
  const id = 'request-"回执"'
  const business = { success: false, code: 'UI_CHANGED', message: '已保存 "简历" 附件',
    effect: 'none', retryable: false, run_id: 'receipt-budget',
    data: { receipt_version: '1.1', known_effect: 'none',
      resumes: [{ image: '业务附件', text: '内容 with "quotes" and \\n' }] } } satisfies OperationResult
  const screenshot = { type: 'image', mimeType: 'image/png', data: 'QQ=='.repeat(20) }
  const withScreenshot = { ...business, data: { ...business.data, screenshot } }
  const wireBytes = (response: ReturnType<typeof encodeOperationResult>) =>
    Buffer.byteLength(JSON.stringify({ jsonrpc: '2.0', id, result: response }) + '\n')
  const businessResponse = encodeOperationResult(business, id)
  const withScreenshotResponse = encodeOperationResult(withScreenshot, id)
  const budget = wireBytes(businessResponse)
  assert.ok(wireBytes(withScreenshotResponse) > budget)
  assert.deepEqual(encodeOperationResult(withScreenshot, id, wireBytes(withScreenshotResponse)), withScreenshotResponse)

  const justOverBudget = encodeOperationResult(withScreenshot, id, wireBytes(withScreenshotResponse) - 1)
  assert.deepEqual(justOverBudget, businessResponse)

  const legacy = { ...business, data: { resumes: business.data.resumes, screenshot } }
  assert.deepEqual(encodeOperationResult(legacy, id, budget), encodeOperationResult(legacy, id))

  const response = encodeOperationResult(withScreenshot, id, budget)
  assert.deepEqual(response.structuredContent, business)
  assert.deepEqual(JSON.parse(response.content[0]!.text), business)
  assert.equal(wireBytes(response), budget)
  assert.deepEqual(withScreenshot.data.screenshot, screenshot, '原操作回执未被修改')
})

test('negotiatedReceiptVersion：仅精确字符串 1.1 协商', () => {
  assert.equal(negotiatedReceiptVersion({ 'aidwork/receipt_version': '1.1' }), '1.1')
  assert.equal(negotiatedReceiptVersion({ 'aidwork/receipt_version': '1.0' }), undefined)
  assert.equal(negotiatedReceiptVersion({ progressToken: 1 }), undefined)
  assert.equal(negotiatedReceiptVersion(undefined), undefined)
})
