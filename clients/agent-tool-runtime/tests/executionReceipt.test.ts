/**
 * 执行回执标准 1.1（Phase 5）：Runtime 侧协商注入、扩展验证与回传。
 *
 * - 协商（claim 响应附加 receipt_version）→ provider.callTool 收到 opts.requestMeta
 *   {"aidwork/receipt_version":"1.1"}（请求级 _meta，不进业务 arguments）；未协商不注入。
 * - 扩展验证：未协商携带 receipt_version → 剥离（防伪造）；未知版本/形状坏 → 剥离按旧
 *   回执语义 + 脱敏诊断；known_effect 与外层 effect 矛盾（两个合法值）→ 保留原结果原扩展
 *   + 诊断（自动恢复按 unknown 是消费者职责）；合法扩展随既有 /result 透传。
 * - 能力宣告：回执标准与已安装 Provider 无关，原 BOSS 停稳证据能力仍按原规则。
 * - E2E（fakeCloud + runtimeStack + fakeProvider 真 stdio）：协商调用 _meta 经真实
 *   stdio 透传（插件回显 receipt_meta）、合法回执经 /result 到云端、_runtime_completion 共存。
 */
import assert from 'node:assert/strict'
import { mkdtempSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { randomUUID } from 'node:crypto'
import { test } from 'node:test'
import { runInvocation } from '../src/invocationRunner.js'
import type { RunnerDeps } from '../src/invocationRunner.js'
import type { ProviderManager, ProviderSet } from '../src/providerManager.js'
import type { ApiClient, ClaimedInvocation, InvocationResultPayload } from '../src/apiClient.js'
import { deriveResourceKey } from '../src/desktopLock.js'
import { deviceCapabilities, type RuntimeConfig } from '../src/config.js'
import { RECEIPT_DATA_KEYS, receiptEffectContradiction, validateExecutionReceipt } from '../src/executionReceipt.js'
import { fakeProviderEntry, startTestStack } from './helpers/runtimeStack.js'

const existingEntry = fileURLToPath(new URL('./helpers/fakeProvider.js', import.meta.url))

/** 协商 1.1 的合法失败回执（真实 CLI 形状） */
const RECEIPT_FAILURE = {
  receipt_version: '1.1', step: 'input',
  reason: { code: 'UI_CHANGED', message: '页面弹层遮挡' },
  known_effect: 'none',
  partial_results: [{ item_ref: 'attempt:1', status: 'completed', known_effect: 'applied' }],
  screenshot: { type: 'image', mimeType: 'image/png', data: Buffer.from('png').toString('base64') },
}

interface Harness {
  deps: RunnerDeps
  results: Array<{ id: string; payload: InvocationResultPayload }>
  callOptions: Array<Record<string, unknown> | undefined>
}

function fakeProviders(key: string, manager: Pick<ProviderManager, 'callTool'>): ProviderSet {
  return { has: (k: string) => k === key, get: (k: string) => (k === key ? manager : undefined) } as unknown as ProviderSet
}

function makeHarness(opts: {
  providerKey?: string
  /** 受控 provider 结果（缺省成功回执）；opts 每次 callTool 捕获供 requestMeta 断言 */
  result?: Record<string, unknown>
} = {}): Harness {
  const results: Harness['results'] = []
  const callOptions: Harness['callOptions'] = []
  const api = {
    started: async () => ({}),
    progress: async () => ({ seq: 1, cancel: false }),
    result: async (id: string, payload: InvocationResultPayload) => {
      results.push({ id, payload })
    },
    operationResult: async () => ({}),
  } as unknown as ApiClient
  const providerKey = opts.providerKey ?? 'boss-recruiting'
  const manager = {
    callTool: async (_name: string, _args: Record<string, unknown>, providerOpts?: Record<string, unknown>) => {
      callOptions.push(providerOpts)
      return opts.result ?? { success: true, code: 'OK', effect: 'none', data: { echo: 1 }, retryable: false, run_id: 'r-1' }
    },
  }
  const deps: RunnerDeps = {
    api,
    providers: fakeProviders(providerKey, manager as unknown as Pick<ProviderManager, 'callTool'>),
    desktopCheck: async () => true,
    desktopResourceKey: deriveResourceKey(randomUUID()),
    retryBaseMs: 10,
    retryMaxMs: 50,
    runtimeDataDir: mkdtempSync(path.join(os.tmpdir(), 'aidwork-receipt-')),
  }
  return { deps, results, callOptions }
}

function invocation(receiptVersion?: string): ClaimedInvocation {
  return {
    invocation_id: `inv-receipt-${randomUUID().slice(0, 8)}`,
    tool_name: 'boss_filter',
    arguments: {},
    claim_token: 'tok-receipt',
    provider: 'boss-recruiting',
    ...(receiptVersion !== undefined ? { receipt_version: receiptVersion } : {}),
  }
}

test('协商调用注入 opts.requestMeta；未协商不注入；_meta 不进业务 arguments', async () => {
  let capturedArgs: Record<string, unknown> | undefined
  const { deps, callOptions } = makeHarness({
    result: { success: false, code: 'UI_CHANGED', message: 'm', effect: 'none', data: {}, retryable: false },
  })
  // args 捕获：经 harness 的 manager 闭包拿不到业务参数，改由 fakeProviders 断言——
  // 这里借助 requestMeta 断言 + arguments 检查（callOptions 捕获 ProviderCallOptions，
  // 业务 arguments 检查放 E2E receipt_meta 复核）
  const manager = deps.providers.get('boss-recruiting') as unknown as { callTool: (n: string, a: Record<string, unknown>, o?: Record<string, unknown>) => Promise<unknown> }
  const originalCallTool = manager.callTool.bind(manager)
  manager.callTool = async (name, args, providerOpts) => {
    capturedArgs = args
    return await originalCallTool(name, args, providerOpts)
  }
  await runInvocation(invocation('1.1'), deps)
  assert.deepEqual(callOptions[0]?.['requestMeta'], { 'aidwork/receipt_version': '1.1' })
  assert.equal(capturedArgs?.['_meta'], undefined, '_meta 不进业务 arguments')

  const { deps: legacyDeps, callOptions: legacyOptions } = makeHarness()
  await runInvocation(invocation(), legacyDeps)
  assert.equal(legacyOptions[0]?.['requestMeta'], undefined, '未协商调用请求与现状逐字节一致')
})

test('validateExecutionReceipt：协商合法回执透传；矛盾/形状坏/未协商判定', () => {
  // 合法回执不剥离
  const ok = validateExecutionReceipt({ ...RECEIPT_FAILURE }, true)
  assert.equal(ok.strip, false)
  assert.deepEqual(ok.diagnostics, [])
  // 未协商携带 → 剥离
  assert.equal(validateExecutionReceipt({ ...RECEIPT_FAILURE }, false).strip, true)
  // 未携带 → 不动（协商与未协商一致）
  assert.equal(validateExecutionReceipt({ echo: 1 }, true).strip, false)
  assert.equal(validateExecutionReceipt({ echo: 1 }, false).strip, false)
  // 未知版本 → 剥离
  assert.equal(validateExecutionReceipt({ ...RECEIPT_FAILURE, receipt_version: '2.0' }, true).strip, true)
  // known_effect 非法 → 剥离
  assert.equal(validateExecutionReceipt({ ...RECEIPT_FAILURE, known_effect: 'big' }, true).strip, true)
  assert.equal(validateExecutionReceipt({ receipt_version: '1.1', step: 's' }, true).strip, true, 'known_effect 缺失')
  // step 缺失 → 剥离
  assert.equal(validateExecutionReceipt({ ...RECEIPT_FAILURE, step: 1 as unknown as string }, true).strip, true)
  // partial_results 形状坏 → 剥离
  assert.equal(validateExecutionReceipt({ ...RECEIPT_FAILURE, partial_results: [{ item_ref: 'a', status: 'nope', known_effect: 'none' }] }, true).strip, true)
  // screenshot 形状坏 → 剥离
  assert.equal(validateExecutionReceipt({ ...RECEIPT_FAILURE, screenshot: { type: 'image', mimeType: 'text/plain', data: 'x' } }, true).strip, true)
  const legacyBusiness = { screenshot: 'business-image', step: 'done', reason: 'business-code' }
  assert.equal(validateExecutionReceipt(legacyBusiness, true).strip, false)
  assert.equal(validateExecutionReceipt(legacyBusiness, false).strip, false)

  // 矛盾判定：两个合法效果值不同 → true；缺外层/任一非法 → false
  assert.equal(receiptEffectContradiction('applied', 'none'), true)
  assert.equal(receiptEffectContradiction('applied', 'applied'), false)
  assert.equal(receiptEffectContradiction(undefined, 'none'), false)
  assert.equal(receiptEffectContradiction('applied', 'bogus'), false)
})

test('扩展剥离集合只覆盖 1.1 键：业务字段永不被触碰', () => {
  assert.deepEqual([...RECEIPT_DATA_KEYS].sort(), ['known_effect', 'partial_results', 'reason', 'receipt_version', 'screenshot', 'step'])
})

// ---------- runner 级矩阵：data 进出 /result payload ----------

test('协商+合法失败回执：随 /result 透传，业务字段保留', async () => {
  const { deps, results } = makeHarness({
    result: { success: false, code: 'UI_CHANGED', message: 'm', effect: 'none', data: { keep: 1, ...structuredClone(RECEIPT_FAILURE) }, retryable: false },
  })
  await runInvocation(invocation('1.1'), deps)
  const data = results[0]!.payload['data'] as Record<string, unknown>
  assert.equal(data['receipt_version'], '1.1')
  assert.equal(data['keep'], 1)
  assert.equal(data['known_effect'], 'none')
})

test('协商+形状坏回执：整块剥离按旧回执语义，业务字段保留', async () => {
  const { deps, results } = makeHarness({
    result: { success: false, code: 'UI_CHANGED', message: 'm', effect: 'none', data: { keep: 1, receipt_version: '9.9', known_effect: 'bogus' }, retryable: false },
  })
  await runInvocation(invocation('1.1'), deps)
  const data = results[0]!.payload['data'] as Record<string, unknown>
  assert.equal(data['receipt_version'], undefined)
  assert.equal(data['known_effect'], undefined)
  assert.equal(data['keep'], 1)
})

test('未协商携带 receipt_version：剥离（防伪造），业务字段保留', async () => {
  const { deps, results } = makeHarness({
    result: { success: false, code: 'UI_CHANGED', message: 'm', effect: 'none', data: { keep: 1, ...structuredClone(RECEIPT_FAILURE) }, retryable: false },
  })
  await runInvocation(invocation(), deps)
  const data = results[0]!.payload['data'] as Record<string, unknown>
  assert.equal(data['receipt_version'], undefined)
  assert.equal(data['screenshot'], undefined)
  assert.equal(data['keep'], 1)
})

test('协商效果矛盾：保留原结果原扩展（不改写、不剥离）', async () => {
  const { deps, results } = makeHarness({
    result: { success: true, code: 'OK', message: 'm', effect: 'applied', data: { ...structuredClone(RECEIPT_FAILURE), known_effect: 'none' }, retryable: false },
  })
  await runInvocation(invocation('1.1'), deps)
  const data = results[0]!.payload['data'] as Record<string, unknown>
  assert.equal(data['receipt_version'], '1.1', '矛盾回执保留原扩展')
  assert.equal(data['known_effect'], 'none')
  assert.equal(results[0]!.payload['effect'], 'applied', '外层 effect 原样（自动恢复按 unknown 判断是消费者职责）')
})

test('_runtime_completion 与 1.1 回执共存于同一 data', async () => {
  const { deps, results } = makeHarness({
    result: { success: true, code: 'OK', message: 'm', effect: 'applied', data: { echo: 1, ...structuredClone(RECEIPT_FAILURE), known_effect: 'applied' }, retryable: false },
  })
  await runInvocation(invocation('1.1'), deps)
  const data = results[0]!.payload['data'] as Record<string, unknown>
  assert.deepEqual(data['_runtime_completion'], { version: 1 })
  assert.equal(data['receipt_version'], '1.1')
  assert.equal(data['known_effect'], 'applied')
})

// ---------- 能力宣告与 E2E ----------

test('deviceCapabilities：回执协议由宿主宣告，旧 BOSS 结束证据单独协商', () => {
  const config: RuntimeConfig = { server: 'http://127.0.0.1:1', device_id: 'dev-1', bossCliEntry: existingEntry }
  const caps = deviceCapabilities(config)
  assert.ok((caps['capabilities'] as string[]).includes('runtime_execution_receipt_v1_1'))
  assert.ok((caps['capabilities'] as string[]).includes('runtime_boss_completion_v1'))
  // 没有 BOSS 仍支持通用回执，原 BOSS 专用结束事实不声明。
  const noBoss = deviceCapabilities({ server: 'http://127.0.0.1:1', device_id: 'dev-1', providers: { wecom: { entry: existingEntry } } })
  assert.ok((noBoss['capabilities'] as string[]).includes('runtime_execution_receipt_v1_1'))
  assert.ok(!(noBoss['capabilities'] as string[]).includes('runtime_boss_completion_v1'))
})

test('E2E（fakeCloud + runtimeStack 真 stdio）：协商 _meta 透传、合法回执与截图到云端、伪造剥离', async () => {
  const stack = await startTestStack({ providerEntries: { 'resume-tool': fakeProviderEntry() },
    manifests: { 'resume-tool': { provider_key: 'resume-tool', provider_id: 'ai.example.resume-tool',
      tools: ['resume_parse'], execution_target: 'local_required', protocol_version: 1,
      shared_lock_capable: false, write_tools: new Set() } },
  })
  try {
    // 协商调用：_meta 经真实 stdio 到 fakeProvider（回显 receipt_meta），失败回执含截图到 /result
    const id = stack.cloud.enqueueInvocation('resume_parse',
      { fail: true, receipt_data: true, receipt_echo: true, durationMs: 20 },
      { provider: 'resume-tool', receipt_version: '1.1' })
    await stack.cloud.waitFor(() => stack.cloud.callsFor(id).some((c) => c.type === 'result'), 15_000, 'negotiated result')
    const result = stack.cloud.callsFor(id).find((c) => c.type === 'result')!
    const data = result.payload['data'] as Record<string, unknown>
    assert.equal(data['receipt_version'], '1.1', '合法协商回执经 /result 到云端')
    assert.equal(data['known_effect'], 'none')
    assert.deepEqual(data['partial_results'], [{ item_ref: 'attempt:1', status: 'completed', known_effect: 'applied' }])
    const screenshot = data['screenshot'] as { type: string; mimeType: string; data: string }
    assert.equal(screenshot.type, 'image')
    assert.equal(Buffer.from(screenshot.data, 'base64').toString(), 'fake-png', '截图块原样透传（云端消费由服务端测试覆盖）')

    // 未协商调用：插件伪造回执被 Runtime 剥离（防伪造）
    const forgedId = stack.cloud.enqueueInvocation('boss_filter',
      { fail: true, receipt_data: true, durationMs: 20 },
      { provider: 'boss-recruiting' })
    await stack.cloud.waitFor(() => stack.cloud.callsFor(forgedId).some((c) => c.type === 'result'), 15_000, 'forged result')
    const forged = stack.cloud.callsFor(forgedId).find((c) => c.type === 'result')!
    const forgedData = forged.payload['data'] as Record<string, unknown>
    assert.equal(forgedData['receipt_version'], undefined, '未协商回执扩展被剥离')
    assert.equal(forgedData['screenshot'], undefined)
    assert.equal(forged.payload['code'], 'FAKE_FAIL', '业务失败结果原样保留')

    // 协商成功调用：_meta 确实经 stdio 传入插件（receipt_meta 回显），data 无 1.1 字段
    const echoId = stack.cloud.enqueueInvocation('boss_filter',
      { receipt_echo: true, durationMs: 20 },
      { provider: 'boss-recruiting', receipt_version: '1.1' })
    await stack.cloud.waitFor(() => stack.cloud.callsFor(echoId).some((c) => c.type === 'result'), 15_000, 'echo result')
    const echo = stack.cloud.callsFor(echoId).find((c) => c.type === 'result')!
    const echoData = echo.payload['data'] as Record<string, unknown>
    const meta = echoData['receipt_meta'] as Record<string, unknown>
    assert.equal(meta['aidwork/receipt_version'], '1.1', '请求级 _meta 经真实 stdio 透传')
    assert.ok(meta['progressToken'], 'SDK 自动进度订阅元数据与回执协商共存')
    assert.equal(echoData['receipt_version'], undefined, '成功结果不携带回执字段')
  } finally {
    await stack.stop()
  }
})
