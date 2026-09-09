/**
 * v2 写路径 E2E（宪章 P1-C 交付物 5）：许可 → journal → 执行 → operation-result → outbox。
 *
 * 测试栈注入 v2 契约样例 manifest（weixin protocol_version=2 + shared_lock_capable=true），
 * 生产 TRUSTED_MANIFESTS 不受影响（真 v2 未交付前仍是 1/false，v2Gate.test.ts 守门）。
 */
import assert from 'node:assert/strict'
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { randomUUID } from 'node:crypto'
import { spawn } from 'node:child_process'
import { test } from 'node:test'
import type { CallLogEntry } from './helpers/fakeCloud.js'
import type { TestStack } from './helpers/runtimeStack.js'
import { fakeProviderEntry, startTestStack } from './helpers/runtimeStack.js'
import { FakeCloud } from './helpers/fakeCloud.js'
import { ApiClient, ApiError } from '../src/apiClient.js'
import { desktopLockName, deriveResourceKey } from '../src/desktopLock.js'
import type { ProviderManifest } from '../src/providers.js'

/** v2 契约样例 manifest（仅测试注入；宪章 P1-D 前生产注册表不变） */
const V2_WEIXIN_MANIFEST: ProviderManifest = {
  provider_key: 'weixin',
  provider_id: 'ai.aidwork.weixin',
  tools: ['weixin_probe_v2', 'weixin_message_send_v2'],
  execution_target: 'local_required',
  protocol_version: 2,
  shared_lock_capable: true,
  write_tools: new Set(['weixin_message_send_v2']),
}

interface V2StackOpts {
  runtimeDataDir?: string
  device?: { device_id: string; token: string }
  cloud?: TestStack['cloud']
  keepCloudOnStop?: boolean
  /** 桌面锁 resource_key（默认每栈独立 session 派生；锁相关测试显式传入） */
  desktopResourceKey?: string
  /** 本地许可有效期上限 ms（默认 90s；防御检查类测试注入 0） */
  permitLocalCapMs?: number
}

async function startV2Stack(opts: V2StackOpts = {}): Promise<TestStack> {
  return startTestStack({
    providerEntries: { weixin: fakeProviderEntry() },
    manifests: { weixin: V2_WEIXIN_MANIFEST },
    runtimeDataDir: opts.runtimeDataDir,
    device: opts.device,
    cloud: opts.cloud,
    keepCloudOnStop: opts.keepCloudOnStop,
    desktopResourceKey: opts.desktopResourceKey,
    permitLocalCapMs: opts.permitLocalCapMs,
  })
}

/** R15 形态的 v2 操作描述（正文不进 arguments，payload_ref/hash 引用） */
function v2Args(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    protocol_version: 2,
    operation: 'weixin.message.send',
    provider_key: 'weixin',
    target_ref: 'target-1',
    target_handle: 'handle:target-1',
    target_version: 'tv-1',
    payload_ref: 'payload:1',
    payload_hash: 'a'.repeat(64),
    request_id: `req-${randomUUID()}`,
    delivery_id: `del-${randomUUID()}`,
    authorization_revision: 'rev-1',
    authorization_epoch: 1,
    resource_key: 'rk-1',
    deadline_at: new Date(Date.now() + 600_000).toISOString(),
    ...overrides,
  }
}

function clicksPath(dir: string): string {
  return path.join(dir, 'clicks.jsonl')
}

async function waitForOperationResult(stack: TestStack, id: string, label: string): Promise<CallLogEntry> {
  await stack.cloud.waitFor(
    () => stack.cloud.callsFor(id).some((c) => c.type === 'operation_result'),
    15_000,
    label,
  )
  return stack.cloud.callsFor(id).find((c) => c.type === 'operation_result')!
}

/** 回执日志出现早于 Runtime 收 2xx 执行 remove()——outbox 清空必须等待而非即时断言 */
async function waitForOutboxDrained(stack: TestStack, label = 'outbox drained'): Promise<void> {
  await stack.cloud.waitFor(() => stack.outbox.loadAll().length === 0, 10_000, label)
}

function clickCount(file: string): number {
  if (!existsSync(file)) return 0
  const text = readFileSync(file, 'utf8').trim()
  return text ? text.split('\n').length : 0
}

/** 外部进程占住指定锁名（不经被测代码，真实跨进程互斥；与 desktopArbitration.test.ts 同模式） */
async function holdLockExternally(name: string, holdMs: number): Promise<{ exited: Promise<void> }> {
  const holder = spawn(process.execPath, ['--input-type=module', '-e', `
    import net from 'node:net'
    const server = net.createServer((s) => s.destroy())
    server.listen(${JSON.stringify(name)}, () => {
      process.stdout.write('HELD\\n')
      setTimeout(() => process.exit(0), ${holdMs})
    })
    server.on('error', (err) => { process.stderr.write(String(err)); process.exit(1) })
  `], { stdio: ['ignore', 'pipe', 'ignore'] })
  const exited = new Promise<void>((resolve) => holder.on('exit', () => resolve()))
  let out = ''
  holder.stdout.on('data', (c: Buffer) => { out += c.toString() })
  const deadline = Date.now() + 5_000
  while (!out.includes('HELD') && Date.now() < deadline) await new Promise((r) => setTimeout(r, 20))
  assert.ok(out.includes('HELD'), '外部持锁进程应已监听管道')
  return { exited }
}

test('快乐路径：permit → journal → 执行 → verified → operation-result ACK → outbox 清空', async () => {
  const dataDir = mkdtempSync(path.join(os.tmpdir(), 'aidwork-v2e2e-'))
  const stack = await startV2Stack({ runtimeDataDir: dataDir })
  try {
    const clicks = clicksPath(dataDir)
    const args = v2Args({ clicks_file: clicks, durationMs: 60 })
    const id = stack.cloud.enqueueInvocation('weixin_message_send_v2', args, { provider: 'weixin' })
    const result = await waitForOperationResult(stack, id, 'v2 happy path')
    const payload = result.payload

    // 回执字段（claim/request_id/permit handle/effect/phase/safe_to_retry/success/evidence）
    const inv = stack.cloud.getInvocation(id)!
    assert.equal(payload['claim_token'], inv.claim_token)
    assert.equal(payload['request_id'], args['request_id'])
    assert.equal(payload['permit_id'], inv.permit?.permit_id)
    assert.equal(payload['permit_token'], inv.permit?.permit_token)
    assert.equal(payload['effect'], 'applied')
    assert.equal(payload['phase'], 'verified')
    assert.equal(payload['safe_to_retry'], false)
    assert.equal(payload['success'], true)
    assert.equal(payload['code'], 'OK')
    assert.ok(typeof payload['evidence_ref'] === 'string' && payload['evidence_ref'])

    // 许可申请恰好一次，字段绑定（claim/request_id/target_version/payload_hash）
    const authCalls = stack.cloud.callsFor(id).filter((c) => c.type === 'write_authorize')
    assert.equal(authCalls.length, 1)
    assert.equal(authCalls[0]!.payload['request_id'], args['request_id'])
    assert.equal(authCalls[0]!.payload['target_version'], 'tv-1')
    assert.equal(authCalls[0]!.payload['payload_hash'], args['payload_hash'])

    // journal：may_have_started 一行（permit_id/payload_hash/operation），不含 permit_token/claim_token
    const journalFile = path.join(dataDir, 'journal', `${id}.jsonl`)
    assert.ok(existsSync(journalFile), 'journal 必须在输入开始前落盘')
    const lines = readFileSync(journalFile, 'utf8').trim().split('\n')
    assert.equal(lines.length, 1)
    const journal = JSON.parse(lines[0]!) as Record<string, unknown>
    assert.equal(journal['invocation_id'], id)
    assert.equal(journal['permit_id'], inv.permit?.permit_id)
    assert.equal(journal['payload_hash'], args['payload_hash'])
    assert.equal(journal['operation'], 'weixin.message.send')
    assert.equal(journal['phase'], 'may_have_started')
    assert.ok(!('permit_token' in journal) && !('claim_token' in journal))

    // outbox：2xx ACK 后清空（等待式——回执日志先于本地 remove()）；旧 /result 端点未被使用；副作用恰好一次
    await waitForOutboxDrained(stack, 'happy path outbox drained')
    assert.ok(!stack.cloud.callsFor(id).some((c) => c.type === 'result'), 'v2 不得走旧 /result 端点')
    assert.equal(clickCount(clicks), 1)
    assert.equal(inv.state, 'succeeded')
  } finally {
    await stack.stop()
    rmSync(dataDir, { recursive: true, force: true })
  }
})

test('许可拒绝（服务端 4xx）→ PERMIT_DENIED，effect none，不执行不写 journal', async () => {
  const dataDir = mkdtempSync(path.join(os.tmpdir(), 'aidwork-v2e2e-'))
  const stack = await startV2Stack({ runtimeDataDir: dataDir })
  try {
    stack.cloud.writeAuthorizeMode = 'deny'
    stack.cloud.writeAuthorizeDenyStatus = 403
    stack.cloud.writeAuthorizeDenyCode = 'ADAPTER_DENIED'
    const clicks = clicksPath(dataDir)
    const id = stack.cloud.enqueueInvocation('weixin_message_send_v2', v2Args({ clicks_file: clicks }), { provider: 'weixin' })
    const result = await waitForOperationResult(stack, id, 'permit denied')
    assert.equal(result.payload['code'], 'PERMIT_DENIED')
    assert.equal(result.payload['effect'], 'none')
    assert.equal(result.payload['phase'], 'prepared')
    assert.equal(result.payload['safe_to_retry'], false)
    assert.equal(clickCount(clicks), 0, '许可被拒绝不执行')
    assert.ok(!existsSync(path.join(dataDir, 'journal', `${id}.jsonl`)), '未执行不得写 journal')
    await waitForOutboxDrained(stack, 'denied outbox drained')
  } finally {
    await stack.stop()
    rmSync(dataDir, { recursive: true, force: true })
  }
})

test('许可网络失败 → PERMIT_UNAVAILABLE，可重试，不执行', async () => {
  const dataDir = mkdtempSync(path.join(os.tmpdir(), 'aidwork-v2e2e-'))
  const stack = await startV2Stack({ runtimeDataDir: dataDir })
  try {
    stack.cloud.writeAuthorizeMode = 'network'
    const clicks = clicksPath(dataDir)
    const id = stack.cloud.enqueueInvocation('weixin_message_send_v2', v2Args({ clicks_file: clicks }), { provider: 'weixin' })
    const result = await waitForOperationResult(stack, id, 'permit network fail')
    assert.equal(result.payload['code'], 'PERMIT_UNAVAILABLE')
    assert.equal(result.payload['effect'], 'none')
    assert.equal(result.payload['phase'], 'prepared')
    assert.equal(result.payload['safe_to_retry'], true)
    assert.equal(clickCount(clicks), 0)
  } finally {
    await stack.stop()
    rmSync(dataDir, { recursive: true, force: true })
  }
})

test('许可到达即过期（服务端 deadline 已过）→ PERMIT_UNAVAILABLE，不执行', async () => {
  const dataDir = mkdtempSync(path.join(os.tmpdir(), 'aidwork-v2e2e-'))
  const stack = await startV2Stack({ runtimeDataDir: dataDir })
  try {
    stack.cloud.writeAuthorizeDeadlineAt = new Date(Date.now() - 1_000).toISOString()
    const clicks = clicksPath(dataDir)
    const id = stack.cloud.enqueueInvocation('weixin_message_send_v2', v2Args({ clicks_file: clicks }), { provider: 'weixin' })
    const result = await waitForOperationResult(stack, id, 'permit expired on arrival')
    assert.equal(result.payload['code'], 'PERMIT_UNAVAILABLE')
    assert.equal(result.payload['effect'], 'none')
    assert.equal(result.payload['safe_to_retry'], true)
    assert.equal(clickCount(clicks), 0)
    // 许可已签发（服务端 delivery 已 may_have_started）但本地判过期，不消费
    assert.ok(stack.cloud.getInvocation(id)!.permit)
  } finally {
    await stack.stop()
    rmSync(dataDir, { recursive: true, force: true })
  }
})

test('journal 写入失败（数据根目录不可写）→ JOURNAL_WRITE_FAILED，effect none，不执行', async () => {
  const dataDir = mkdtempSync(path.join(os.tmpdir(), 'aidwork-v2e2e-'))
  const stack = await startV2Stack({ runtimeDataDir: dataDir })
  try {
    // 许可正常签发，但 journal 根是一个普通文件 → mkdir/open 失败
    const blocker = path.join(dataDir, 'blocker')
    writeFileSync(blocker, 'x')
    const blockedDataDir = path.join(blocker, 'sub')
    const clicks = clicksPath(dataDir)
    // 该栈 dataDir 已固定，改用一个直接构造 runner 的方式成本高——用第二栈指向 blocked 目录
    const stack2 = await startV2Stack({ runtimeDataDir: blockedDataDir })
    try {
      const id = stack2.cloud.enqueueInvocation('weixin_message_send_v2', v2Args({ clicks_file: clicks }), { provider: 'weixin' })
      const result = await waitForOperationResult(stack2, id, 'journal fsync fail')
      assert.equal(result.payload['code'], 'JOURNAL_WRITE_FAILED')
      assert.equal(result.payload['effect'], 'none')
      assert.equal(result.payload['phase'], 'prepared')
      assert.equal(clickCount(clicks), 0, 'journal 失败禁止执行')
      assert.ok(stack2.cloud.getInvocation(id)!.permit, '许可已签发（服务端 may_have_started），本地拒绝执行')
      await waitForOutboxDrained(stack2, 'journal-fail outbox drained')
    } finally {
      await stack2.stop()
    }
  } finally {
    await stack.stop()
    rmSync(dataDir, { recursive: true, force: true })
  }
})

test('Provider 崩溃（v2 写动作）→ unknown/unknown 不可重试，回执附 permit', async () => {
  const dataDir = mkdtempSync(path.join(os.tmpdir(), 'aidwork-v2e2e-'))
  const stack = await startV2Stack({ runtimeDataDir: dataDir })
  try {
    const clicks = clicksPath(dataDir)
    const id = stack.cloud.enqueueInvocation('weixin_message_send_v2', v2Args({ clicks_file: clicks, crash: true }), { provider: 'weixin' })
    const result = await waitForOperationResult(stack, id, 'v2 crash unknown')
    assert.equal(result.payload['code'], 'EXECUTION_UNKNOWN')
    assert.equal(result.payload['effect'], 'unknown')
    assert.equal(result.payload['phase'], 'unknown')
    assert.equal(result.payload['safe_to_retry'], false)
    assert.equal(result.payload['permit_id'], stack.cloud.getInvocation(id)!.permit?.permit_id)
    assert.ok(existsSync(path.join(dataDir, 'journal', `${id}.jsonl`)), '崩溃前 journal 已落盘')
    assert.equal(stack.cloud.getInvocation(id)!.state, 'unknown')
    await waitForOutboxDrained(stack, 'crash outbox drained')
  } finally {
    await stack.stop()
    rmSync(dataDir, { recursive: true, force: true })
  }
})

test('断网回执：执行完成后回执网络断 → 重启（同设备/同云端）outbox 恰好重投一次，无重复副作用', async () => {
  const dataDir = mkdtempSync(path.join(os.tmpdir(), 'aidwork-v2e2e-'))
  // 断连开关：栈1 期间 operation-result 一律断连；恢复后正常 ACK
  let resultOffline = true
  const stack1 = await startV2Stack({ runtimeDataDir: dataDir, keepCloudOnStop: true })
  try {
    stack1.cloud.operationResultInterceptor = () => (resultOffline ? 'destroy' : undefined)
    const clicks = clicksPath(dataDir)
    const id = stack1.cloud.enqueueInvocation('weixin_message_send_v2', v2Args({ clicks_file: clicks, durationMs: 60 }), { provider: 'weixin' })
    // 等执行完成 + 至少 2 次失败重试（outbox attempts 可观测）
    await stack1.cloud.waitFor(
      () => (stack1.outbox.load(id)?.attempts ?? 0) >= 2,
      15_000,
      'offline retries',
    )
    assert.equal(clickCount(clicks), 1, '执行恰好一次')
    // 关停（shutdown 中止退避，条目必须保留在磁盘）
    await stack1.stop()
    const kept = stack1.outbox.load(id)
    assert.ok(kept, '关停后 outbox 条目保留')
    assert.ok(kept!.attempts >= 2)
    assert.equal(kept!.payload['effect'], 'applied')
    assert.equal(kept!.payload['claim_token'], stack1.cloud.getInvocation(id)!.claim_token)

    // 恢复网络，重启 Runtime（同设备 token + 同云端 + 同数据目录）→ 启动重投恰好一次
    resultOffline = false
    const stack2 = await startV2Stack({
      runtimeDataDir: dataDir,
      cloud: stack1.cloud,
      device: { device_id: stack1.deviceId, token: stack1.token },
    })
    try {
      await stack2.cloud.waitFor(
        () => stack2.cloud.getInvocation(id)!.state === 'succeeded',
        15_000,
        'replay acked',
      )
      assert.equal(stack2.outbox.load(id), null, '重投 ACK 后条目删除')
      const acked = stack2.cloud.callsFor(id).filter((c) => c.type === 'operation_result')
      assert.equal(acked.length, 1, '成功回执恰好一次（断连尝试不计）')
      assert.equal(clickCount(clicks), 1, '重投不产生重复副作用（Provider 不再执行）')
      const journalLines = readFileSync(path.join(dataDir, 'journal', `${id}.jsonl`), 'utf8').trim().split('\n')
      assert.equal(journalLines.length, 1, '重启后不重复写 journal')
    } finally {
      await stack2.stop()
    }
  } finally {
    // stack1/cloud2 共享同一 cloud（keepCloudOnStop），此处统一关闭
    await stack1.cloud.stop()
    rmSync(dataDir, { recursive: true, force: true })
  }
})

test('R20 锁内许可：外部持锁期间无 writeAuthorize 调用且 provider 零调用，锁释放后正常执行', async () => {
  const dataDir = mkdtempSync(path.join(os.tmpdir(), 'aidwork-v2e2e-'))
  const resourceKey = deriveResourceKey(randomUUID())
  const stack = await startV2Stack({ runtimeDataDir: dataDir, desktopResourceKey: resourceKey })
  try {
    const clicks = clicksPath(dataDir)
    const { exited } = await holdLockExternally(desktopLockName(resourceKey), 1_500)
    const id = stack.cloud.enqueueInvocation('weixin_message_send_v2', v2Args({ clicks_file: clicks, durationMs: 60 }), { provider: 'weixin' })
    // 锁等待发生在 started 之后（租约由 forwarder 续期）
    await stack.cloud.waitFor(() => stack.cloud.callsFor(id).some((c) => c.type === 'started'), 15_000, 'started during external hold')
    // 外部持锁窗口内：无 writeAuthorize、无终态、provider 零调用（许可不被锁等待耗尽）
    await new Promise((r) => setTimeout(r, 300))
    assert.ok(!stack.cloud.callsFor(id).some((c) => c.type === 'write_authorize'), '锁等待期间不得发起 writeAuthorize')
    assert.ok(!stack.cloud.callsFor(id).some((c) => c.type === 'operation_result'), '锁等待期间不得回传终态')
    assert.equal(clickCount(clicks), 0, '锁等待期间 provider 零调用')
    await exited
    const exitAt = Date.now()
    // 锁释放后正常执行：许可申请必须发生在外部持锁进程退出之后
    const result = await waitForOperationResult(stack, id, 'lock released then execute')
    const authCalls = stack.cloud.callsFor(id).filter((c) => c.type === 'write_authorize')
    assert.equal(authCalls.length, 1)
    assert.ok(authCalls[0]!.at >= exitAt - 100, `writeAuthorize 必须发生在外部持锁退出后（auth@${authCalls[0]!.at} exit@${exitAt}）`)
    assert.equal(result.payload['effect'], 'applied')
    assert.equal(clickCount(clicks), 1)
    await waitForOutboxDrained(stack, 'R20 lock outbox drained')
  } finally {
    await stack.stop()
    rmSync(dataDir, { recursive: true, force: true })
  }
})

test('R20 防御检查：许可签发后即失效（本地单调时钟上限 0）→ PERMIT_UNAVAILABLE 中止，provider 零调用、不写 journal', async () => {
  const dataDir = mkdtempSync(path.join(os.tmpdir(), 'aidwork-v2e2e-'))
  // 本地上限 0ms：acquire 成功（服务端 deadline 未到）但持锁后防御检查必然判过期 → 中止
  const stack = await startV2Stack({ runtimeDataDir: dataDir, permitLocalCapMs: 0 })
  try {
    const clicks = clicksPath(dataDir)
    const id = stack.cloud.enqueueInvocation('weixin_message_send_v2', v2Args({ clicks_file: clicks }), { provider: 'weixin' })
    const result = await waitForOperationResult(stack, id, 'defensive permit expiry abort')
    assert.equal(result.payload['code'], 'PERMIT_UNAVAILABLE')
    assert.equal(result.payload['effect'], 'none')
    assert.equal(result.payload['phase'], 'prepared')
    assert.equal(result.payload['safe_to_retry'], true)
    assert.equal(clickCount(clicks), 0, '防御检查中止：provider 零调用')
    assert.ok(!existsSync(path.join(dataDir, 'journal', `${id}.jsonl`)), '防御检查在 journal 之前，不得写 journal')
    // 服务端已签发许可；回执附 permit 身份（R22 ①：effect none + prepared → 服务端释放额度）
    assert.ok(stack.cloud.getInvocation(id)!.permit)
    assert.equal(result.payload['permit_id'], stack.cloud.getInvocation(id)!.permit?.permit_id)
    await waitForOutboxDrained(stack, 'defensive expiry outbox drained')
  } finally {
    await stack.stop()
    rmSync(dataDir, { recursive: true, force: true })
  }
})

test('R21 断网/重启 E2E：发送成功→断网→服务端清扫置 permit expired→重启重投→迟到证据被接纳（2xx）→outbox 清空', async () => {
  const dataDir = mkdtempSync(path.join(os.tmpdir(), 'aidwork-v2e2e-'))
  // 断连开关：栈1 期间 operation-result 一律断连；恢复后正常处理（含过期许可迟到接纳）
  let resultOffline = true
  const stack1 = await startV2Stack({ runtimeDataDir: dataDir, keepCloudOnStop: true })
  try {
    stack1.cloud.operationResultInterceptor = () => (resultOffline ? 'destroy' : undefined)
    const clicks = clicksPath(dataDir)
    const id = stack1.cloud.enqueueInvocation('weixin_message_send_v2', v2Args({ clicks_file: clicks, durationMs: 60 }), { provider: 'weixin' })
    await stack1.cloud.waitFor(
      () => (stack1.outbox.load(id)?.attempts ?? 0) >= 2,
      15_000,
      'offline retries',
    )
    assert.equal(clickCount(clicks), 1, '执行恰好一次')
    // 服务端清扫：许可已过期（R22 过期不释放预留；R21 绑定通过即接纳迟到证据）
    stack1.cloud.expirePermit(id)
    assert.equal(stack1.cloud.getInvocation(id)!.permit?.state, 'expired')
    await stack1.stop()
    assert.ok(stack1.outbox.load(id), '关停后 outbox 条目保留')

    // 恢复网络 + 重启栈（同设备 token/同云端/同数据目录）→ 启动重投 → 迟到证据（含过期许可）被接纳
    resultOffline = false
    const stack2 = await startV2Stack({
      runtimeDataDir: dataDir,
      cloud: stack1.cloud,
      device: { device_id: stack1.deviceId, token: stack1.token },
    })
    try {
      await stack2.cloud.waitFor(
        () => stack2.cloud.getInvocation(id)!.state === 'succeeded',
        15_000,
        'late evidence accepted after replay',
      )
      assert.equal(stack2.cloud.getInvocation(id)!.permit_expired_late, true, '过期许可的迟到回执照常落账（audit 镜像标记）')
      assert.equal(stack2.outbox.load(id), null, '2xx ACK 后条目删除（唯一删除路径）')
      const acked = stack2.cloud.callsFor(id).filter((c) => c.type === 'operation_result')
      assert.equal(acked.length, 1, '成功回执恰好一次（断连尝试不计）')
      assert.equal(clickCount(clicks), 1, '重投不产生重复副作用')
    } finally {
      await stack2.stop()
    }
  } finally {
    await stack1.cloud.stop()
    rmSync(dataDir, { recursive: true, force: true })
  }
})

test('R21 确定性 4xx：条目保留并标记 gave_up（停止主动重试），重启启动重投跳过、永不删除', async () => {
  const dataDir = mkdtempSync(path.join(os.tmpdir(), 'aidwork-v2e2e-'))
  const stack1 = await startV2Stack({ runtimeDataDir: dataDir, keepCloudOnStop: true })
  try {
    // 服务端对回执恒定 422（确定性 4xx：payload 不被接受，重试大概率不变）
    stack1.cloud.operationResultInterceptor = () => 422
    const clicks = clicksPath(dataDir)
    const id = stack1.cloud.enqueueInvocation('weixin_message_send_v2', v2Args({ clicks_file: clicks, durationMs: 60 }), { provider: 'weixin' })
    await stack1.cloud.waitFor(() => stack1.outbox.load(id)?.gave_up === true, 15_000, 'gave_up marked')
    const entry = stack1.outbox.load(id)!
    assert.ok(entry, '确定性 4xx 后条目保留（R21：仅 2xx 删除）')
    assert.equal(entry.gave_up, true)
    assert.ok(entry.gave_up_reason?.includes('422'), `gave_up 原因含 HTTP 状态: ${entry.gave_up_reason}`)
    assert.equal(entry.payload['effect'], 'applied', '回执 payload 完整保留（供对账）')
    assert.equal(clickCount(clicks), 1, '副作用已发生——证据不可丢，留待人工对账')
    const attemptsAtGiveUp = entry.attempts
    assert.equal(attemptsAtGiveUp, 1, '确定性 4xx 首次即收敛（无有界重投）')
    await new Promise((r) => setTimeout(r, 400))
    assert.equal(stack1.outbox.load(id)!.attempts, attemptsAtGiveUp, 'gave_up 后停止主动重试')
    await stack1.stop()

    // 重启（同设备/同云端/同数据目录）：启动重投必须跳过 gave_up 条目，条目永不删除
    const stack2 = await startV2Stack({
      runtimeDataDir: dataDir,
      cloud: stack1.cloud,
      device: { device_id: stack1.deviceId, token: stack1.token },
    })
    try {
      await new Promise((r) => setTimeout(r, 1_200)) // 给启动重投窗口（若未跳过会发起请求）
      assert.equal(
        stack2.cloud.callsFor(id).filter((c) => c.type === 'operation_result').length,
        0,
        'gave_up 条目启动重投跳过（无任何回执调用）',
      )
      const kept = stack2.outbox.load(id)
      assert.ok(kept, 'gave_up 条目永不删除（供对账）')
      assert.equal(kept!.gave_up, true)
      assert.equal(kept!.attempts, attemptsAtGiveUp, '跳过不产生新尝试计数')
    } finally {
      await stack2.stop()
    }
  } finally {
    await stack1.cloud.stop()
    rmSync(dataDir, { recursive: true, force: true })
  }
})

test('迟到/重复回执：operation-result 幂等 ACK（late），不改判不重复副作用', async () => {
  const dataDir = mkdtempSync(path.join(os.tmpdir(), 'aidwork-v2e2e-'))
  const stack = await startV2Stack({ runtimeDataDir: dataDir })
  try {
    const clicks = clicksPath(dataDir)
    const id = stack.cloud.enqueueInvocation('weixin_message_send_v2', v2Args({ clicks_file: clicks }), { provider: 'weixin' })
    const first = await waitForOperationResult(stack, id, 'first result')
    assert.equal(stack.cloud.getInvocation(id)!.state, 'succeeded')

    // 重复 POST 同一回执（模拟对账重放）：2xx + late，状态不变
    const ack = await stack.api.operationResult(id, first.payload as unknown as import('../src/apiClient.js').OperationResultPayload)
    assert.equal(ack.late, true)
    assert.equal(ack.state, 'succeeded')
    assert.equal(stack.cloud.callsFor(id).filter((c) => c.type === 'operation_result').length, 2)
    assert.equal(clickCount(clicks), 1)
    assert.equal(stack.cloud.getInvocation(id)!.state, 'succeeded')
  } finally {
    await stack.stop()
    rmSync(dataDir, { recursive: true, force: true })
  }
})

test('v2 只读操作（非写工具）不申请许可、不写 journal，正常回执', async () => {
  const dataDir = mkdtempSync(path.join(os.tmpdir(), 'aidwork-v2e2e-'))
  const stack = await startV2Stack({ runtimeDataDir: dataDir })
  try {
    const id = stack.cloud.enqueueInvocation('weixin_probe_v2', v2Args({ durationMs: 50 }), { provider: 'weixin' })
    const result = await waitForOperationResult(stack, id, 'v2 read-only')
    assert.equal(result.payload['effect'], 'none')
    assert.equal(result.payload['phase'], 'prepared')
    assert.ok(!result.payload['permit_id'], '只读操作不携带 permit')
    assert.ok(!stack.cloud.callsFor(id).some((c) => c.type === 'write_authorize'), '只读操作不申请许可')
    assert.ok(!existsSync(path.join(dataDir, 'journal', `${id}.jsonl`)), '只读操作不写 journal')
    assert.equal(stack.cloud.getInvocation(id)!.state, 'failed', 'none 映射 failed（对齐服务端 delivery 语义）')
  } finally {
    await stack.stop()
    rmSync(dataDir, { recursive: true, force: true })
  }
})

test('v2 写 + shutdown 中止：未拿到可信结果 → unknown 且不可自动重试（safe_to_retry/retryable false）', async () => {
  const dataDir = mkdtempSync(path.join(os.tmpdir(), 'aidwork-v2e2e-'))
  const stack = await startV2Stack({ runtimeDataDir: dataDir, keepCloudOnStop: true })
  try {
    const id = stack.cloud.enqueueInvocation('weixin_message_send_v2', v2Args({ durationMs: 1_500 }), { provider: 'weixin' })
    // 等 journal 落盘（许可已到手、Provider 执行中）再关停——落在「输入可能已开始」窗口
    const journalFile = path.join(dataDir, 'journal', `${id}.jsonl`)
    await stack.cloud.waitFor(() => existsSync(journalFile), 15_000, 'journal before shutdown')
    await stack.stop() // shutdown 协作中止 → 终态经 operation-result 回传（stop 等待完成）
    const result = stack.cloud.callsFor(id).find((c) => c.type === 'operation_result')
    assert.ok(result, 'shutdown 终态必须回传 operation-result')
    assert.equal(result.payload['code'], 'CANCELLED')
    assert.equal(result.payload['effect'], 'unknown', '写动作未拿到可信结果一律 unknown')
    assert.equal(result.payload['safe_to_retry'], false, '§5.3：shutdown 未拿到可信结果不可自动重试')
    assert.equal(result.payload['retryable'], false)
    assert.equal(result.payload['permit_id'], stack.cloud.getInvocation(id)!.permit?.permit_id)
    assert.equal(stack.outbox.loadAll().length, 0)
  } finally {
    await stack.cloud.stop()
    rmSync(dataDir, { recursive: true, force: true })
  }
})

test('fakeCloud 校验对齐服务端：authorize 错 target/hash 409；回执错 request_id 409、无 permit 409、错 permit 403', async () => {
  const cloud = new FakeCloud()
  await cloud.start()
  try {
    const { token } = cloud.createDeviceDirectly()
    const api = new ApiClient(cloud.baseUrl, token)
    const args = v2Args()
    const id = cloud.enqueueInvocation('weixin_message_send_v2', args, { provider: 'weixin' })
    const claim = await api.claim(1)
    assert.equal(claim?.invocation_id, id)
    await api.started(id, claim!.claim_token)

    // write-authorize：target_version / payload_hash 与 invocation 不一致 → 409（对齐 permits.py）
    await assert.rejects(
      api.writeAuthorize(id, { claim_token: claim!.claim_token, request_id: args['request_id'] as string, target_version: 'tampered', payload_hash: args['payload_hash'] as string }),
      (err: unknown) => err instanceof ApiError && err.status === 409 && err.serverMessage === 'TARGET_VERSION_MISMATCH',
    )
    await assert.rejects(
      api.writeAuthorize(id, { claim_token: claim!.claim_token, request_id: args['request_id'] as string, target_version: args['target_version'] as string, payload_hash: 'f'.repeat(64) }),
      (err: unknown) => err instanceof ApiError && err.status === 409 && err.serverMessage === 'PAYLOAD_HASH_MISMATCH',
    )
    // 正确申请 → permit
    const permit = await api.writeAuthorize(id, {
      claim_token: claim!.claim_token,
      request_id: args['request_id'] as string,
      target_version: args['target_version'] as string,
      payload_hash: args['payload_hash'] as string,
    })

    // operation-result：request_id 不一致 → 409（对齐 operation_result.py）
    await assert.rejects(
      api.operationResult(id, { claim_token: claim!.claim_token, request_id: 'wrong', effect: 'applied', phase: 'verified', permit_id: permit.permit_id }),
      (err: unknown) => err instanceof ApiError && err.status === 409 && err.serverMessage === 'REQUEST_ID_MISMATCH',
    )
    // applied+verified 无 permit → 409 PERMIT_REQUIRED
    await assert.rejects(
      api.operationResult(id, { claim_token: claim!.claim_token, request_id: args['request_id'] as string, effect: 'applied', phase: 'verified' }),
      (err: unknown) => err instanceof ApiError && err.status === 409 && err.serverMessage === 'PERMIT_REQUIRED',
    )
    // 错 permit_id → 403 PERMIT_BINDING_INVALID
    await assert.rejects(
      api.operationResult(id, { claim_token: claim!.claim_token, request_id: args['request_id'] as string, effect: 'applied', phase: 'verified', permit_id: '00000000-0000-0000-0000-000000000000', permit_token: permit.permit_token }),
      (err: unknown) => err instanceof ApiError && err.status === 403 && err.serverMessage === 'PERMIT_BINDING_INVALID',
    )
    // 正确回执 → 200
    const ack = await api.operationResult(id, {
      claim_token: claim!.claim_token,
      request_id: args['request_id'] as string,
      effect: 'applied', phase: 'verified',
      permit_id: permit.permit_id, permit_token: permit.permit_token,
    })
    assert.equal(ack.state, 'succeeded')
  } finally {
    await cloud.stop()
  }
})
