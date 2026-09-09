/**
 * v2 写路径单元测试：writeAuthorize（单调时钟上限/失败分类）、journal（追加+fsync 失败）、
 * resultOutbox（原子落盘/ACK 删除/重投分类/启动重投）。
 */
import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync, rmSync, writeFileSync, existsSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { test } from 'node:test'
import type { ApiClient, OperationResultPayload, WriteAuthorizeResponse } from '../src/apiClient.js'
import { ApiError, DeviceRevokedError, NetworkError } from '../src/apiClient.js'
import { acquireWritePermit, LOCAL_PERMIT_CAP_MS, PermitAcquireError } from '../src/writeAuthorize.js'
import { appendJournalEntry, journalFilePath, JournalWriteError } from '../src/journal.js'
import { deliverOutboxEntry, replayPendingOutbox, ResultOutbox, resultOutboxDir } from '../src/resultOutbox.js'

function tmpRoot(label: string): string {
  return mkdtempSync(path.join(os.tmpdir(), `aidwork-v2unit-${label}-`))
}

function stubApi(fn: (invocationId: string, payload: OperationResultPayload) => Promise<unknown>): ApiClient {
  return {
    operationResult: async (invocationId: string, payload: OperationResultPayload) => fn(invocationId, payload),
  } as unknown as ApiClient
}

function authorizeApi(respOrFn: WriteAuthorizeResponse | (() => Promise<WriteAuthorizeResponse>)): ApiClient {
  return {
    writeAuthorize: async () => (typeof respOrFn === 'function' ? respOrFn() : respOrFn),
  } as unknown as ApiClient
}

function v2Payload(effect = 'none'): OperationResultPayload {
  return { claim_token: 'ct', request_id: 'req-1', effect, phase: 'prepared', safe_to_retry: false }
}

// ---------- writeAuthorize ----------

test('writeAuthorize：服务端 deadline 未到 + 默认上限 → 许可有效，剩余时间受 90s 封顶', async () => {
  const api = authorizeApi({
    permit_id: 'p1',
    permit_token: 't1',
    deadline_at: new Date(Date.now() + 600_000).toISOString(),
  })
  const permit = await acquireWritePermit(api, 'inv-1', { claim_token: 'ct', request_id: 'r' })
  assert.equal(permit.permitId, 'p1')
  assert.equal(permit.permitToken, 't1')
  assert.ok(permit.isValid())
  assert.ok(permit.msRemaining() <= LOCAL_PERMIT_CAP_MS + 50, '本地有效期必须被 90s 上限封顶')
  assert.ok(permit.msRemaining() > 0)
})

test('writeAuthorize：单调时钟上限生效（deadline 很远但 cap=500ms → 800ms 后失效）', async () => {
  const api = authorizeApi({
    permit_id: 'p1',
    permit_token: 't1',
    deadline_at: new Date(Date.now() + 3_600_000).toISOString(),
  })
  const permit = await acquireWritePermit(api, 'inv-1', { claim_token: 'ct', request_id: 'r' }, { localCapMs: 500 })
  assert.ok(permit.isValid())
  await new Promise((r) => setTimeout(r, 800))
  assert.ok(!permit.isValid(), '超过本地单调上限必须失效')
})

test('writeAuthorize：服务端 deadline 已过（到达即过期）→ PERMIT_UNAVAILABLE 可重试', async () => {
  const api = authorizeApi({
    permit_id: 'p1',
    permit_token: 't1',
    deadline_at: new Date(Date.now() - 1_000).toISOString(),
  })
  await assert.rejects(
    acquireWritePermit(api, 'inv-1', { claim_token: 'ct', request_id: 'r' }),
    (err: unknown) => {
      assert.ok(err instanceof PermitAcquireError)
      assert.equal(err.code, 'PERMIT_UNAVAILABLE')
      assert.equal(err.retryable, true)
      return true
    },
  )
})

test('writeAuthorize：deadline_at 非法/缺失 → 按到达即过期处理，不误发许可', async () => {
  const api = authorizeApi({ permit_id: 'p1', permit_token: 't1', deadline_at: '' })
  await assert.rejects(
    acquireWritePermit(api, 'inv-1', { claim_token: 'ct', request_id: 'r' }),
    (err: unknown) => err instanceof PermitAcquireError && err.code === 'PERMIT_UNAVAILABLE',
  )
})

test('writeAuthorize：4xx 分类——QUOTA_EXCEEDED 可重试 DENIED / ADAPTER_DENIED 不可重试 DENIED', async () => {
  const quota = authorizeApi(async () => {
    throw new ApiError(409, '云端返回 HTTP 409: QUOTA_EXCEEDED', 'QUOTA_EXCEEDED')
  })
  await assert.rejects(
    acquireWritePermit(quota, 'inv-1', { claim_token: 'ct', request_id: 'r' }),
    (err: unknown) => {
      assert.ok(err instanceof PermitAcquireError)
      assert.equal(err.code, 'PERMIT_DENIED')
      assert.equal(err.serverCode, 'QUOTA_EXCEEDED')
      assert.equal(err.retryable, true)
      return true
    },
  )
  const denied = authorizeApi(async () => {
    throw new ApiError(403, '云端返回 HTTP 403: ADAPTER_DENIED', 'ADAPTER_DENIED')
  })
  await assert.rejects(
    acquireWritePermit(denied, 'inv-1', { claim_token: 'ct', request_id: 'r' }),
    (err: unknown) => {
      assert.ok(err instanceof PermitAcquireError)
      assert.equal(err.code, 'PERMIT_DENIED')
      assert.equal(err.retryable, false)
      return true
    },
  )
})

test('writeAuthorize：5xx/网络失败 → PERMIT_UNAVAILABLE 可重试；设备撤销 → 不可重试', async () => {
  const down = authorizeApi(async () => {
    throw new ApiError(500, '云端返回 HTTP 500', 'PERMIT_SERVICE_UNAVAILABLE')
  })
  await assert.rejects(
    acquireWritePermit(down, 'inv-1', { claim_token: 'ct', request_id: 'r' }),
    (err: unknown) => err instanceof PermitAcquireError && err.code === 'PERMIT_UNAVAILABLE' && err.retryable === true,
  )
  const net = authorizeApi(async () => {
    throw new NetworkError('网络请求失败: ECONNREFUSED')
  })
  await assert.rejects(
    acquireWritePermit(net, 'inv-1', { claim_token: 'ct', request_id: 'r' }),
    (err: unknown) => err instanceof PermitAcquireError && err.code === 'PERMIT_UNAVAILABLE' && err.retryable === true,
  )
  const revoked = authorizeApi(async () => {
    throw new DeviceRevokedError('设备 token 无效或已撤销')
  })
  await assert.rejects(
    acquireWritePermit(revoked, 'inv-1', { claim_token: 'ct', request_id: 'r' }),
    (err: unknown) => err instanceof PermitAcquireError && err.code === 'PERMIT_UNAVAILABLE' && err.retryable === false,
  )
})

// ---------- journal ----------

test('journal：追加 JSONL（含 permit_id/payload_hash/operation/may_have_started），两次追加两行', () => {
  const root = tmpRoot('journal')
  try {
    const entry = {
      ts: new Date().toISOString(),
      invocation_id: 'inv-1',
      request_id: 'req-1',
      permit_id: 'permit-1',
      payload_hash: 'h'.repeat(64),
      operation: 'weixin.message.send',
      phase: 'may_have_started' as const,
    }
    appendJournalEntry(root, entry)
    appendJournalEntry(root, entry)
    const file = journalFilePath(root, 'inv-1')
    assert.ok(existsSync(file))
    const lines = readFileSync(file, 'utf8').trim().split('\n')
    assert.equal(lines.length, 2)
    const parsed = JSON.parse(lines[0]!) as typeof entry
    assert.equal(parsed.invocation_id, 'inv-1')
    assert.equal(parsed.permit_id, 'permit-1')
    assert.equal(parsed.payload_hash, 'h'.repeat(64))
    assert.equal(parsed.operation, 'weixin.message.send')
    assert.equal(parsed.phase, 'may_have_started')
    // 敏感项不落盘：journal 结构里没有 permit_token/claim_token 字段
    assert.ok(!('permit_token' in parsed) && !('claim_token' in parsed))
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('journal：目录不可写（根是普通文件）→ JournalWriteError（禁止执行由调用方保证）', () => {
  const root = tmpRoot('journalfail')
  try {
    const blocker = path.join(root, 'blocker')
    writeFileSync(blocker, 'x')
    assert.throws(
      () =>
        appendJournalEntry(path.join(blocker, 'sub'), {
          ts: new Date().toISOString(),
          invocation_id: 'inv-1',
          request_id: 'r',
          permit_id: 'p',
          phase: 'may_have_started',
        }),
      JournalWriteError,
    )
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

// ---------- resultOutbox ----------

test('resultOutbox：save 原子落盘 + load 往返 + markAttempt 计数 + remove 幂等', () => {
  const root = tmpRoot('outbox')
  try {
    const outbox = new ResultOutbox(resultOutboxDir(root))
    outbox.save('inv-1', v2Payload('applied'))
    const entry = outbox.load('inv-1')
    assert.ok(entry)
    assert.equal(entry!.invocation_id, 'inv-1')
    assert.equal(entry!.endpoint, 'operation-result')
    assert.equal(entry!.payload['effect'], 'applied')
    assert.equal(entry!.payload['claim_token'], 'ct')
    assert.equal(entry!.attempts, 0)
    assert.equal(outbox.loadAll().length, 1)

    outbox.markAttempt('inv-1', 'boom')
    const bumped = outbox.load('inv-1')
    assert.equal(bumped!.attempts, 1)
    assert.equal(bumped!.last_error, 'boom')

    outbox.remove('inv-1')
    outbox.remove('inv-1') // 幂等
    assert.equal(outbox.load('inv-1'), null)
    assert.equal(outbox.loadAll().length, 0)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('resultOutbox：deliverOutboxEntry——2xx ACK 删条目；404/409 确定性拒绝保留条目并标记 gave_up（R21：仅 2xx 删除）', async () => {
  const root = tmpRoot('deliver')
  try {
    const outbox = new ResultOutbox(resultOutboxDir(root))
    outbox.save('inv-ack', v2Payload())
    const ok = await deliverOutboxEntry(stubApi(async () => ({ state: 'failed', effect: 'none' })), outbox, outbox.load('inv-ack')!)
    assert.equal(ok, 'acked')
    assert.equal(outbox.load('inv-ack'), null)

    outbox.save('inv-404', v2Payload())
    const gone404 = await deliverOutboxEntry(
      stubApi(async () => {
        throw new ApiError(404, '云端返回 HTTP 404: CLAIM_MISMATCH', 'CLAIM_MISMATCH')
      }),
      outbox,
      outbox.load('inv-404')!,
    )
    assert.equal(gone404, 'gave-up')
    const kept404 = outbox.load('inv-404')
    assert.ok(kept404, '404 条目保留（R21：永不删除）')
    assert.equal(kept404!.gave_up, true)
    assert.ok(kept404!.gave_up_reason?.includes('404'))

    outbox.save('inv-409', v2Payload())
    const gone409 = await deliverOutboxEntry(
      stubApi(async () => {
        throw new ApiError(409, '云端返回 HTTP 409: REQUEST_ID_MISMATCH', 'REQUEST_ID_MISMATCH')
      }),
      outbox,
      outbox.load('inv-409')!,
    )
    assert.equal(gone409, 'gave-up')
    assert.ok(outbox.load('inv-409'), '409 条目保留')
    assert.equal(outbox.load('inv-409')!.gave_up, true)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('resultOutbox：网络失败退避重试后成功（attempts 递增）；确定性 4xx 立即 gave_up（停止主动重试，不再有界重投）', async () => {
  const root = tmpRoot('deliver2')
  try {
    const outbox = new ResultOutbox(resultOutboxDir(root))
    outbox.save('inv-net', v2Payload())
    let calls = 0
    const outcome = await deliverOutboxEntry(
      stubApi(async () => {
        calls += 1
        if (calls === 1) throw new NetworkError('网络请求失败: ECONNRESET')
        return { state: 'succeeded', effect: 'applied' }
      }),
      outbox,
      outbox.load('inv-net')!,
      { retryBaseMs: 10, retryMaxMs: 20 },
    )
    assert.equal(outcome, 'acked')
    assert.equal(calls, 2)
    assert.equal(outbox.load('inv-net'), null)

    // 403 确定性拒绝：首次即 gave_up（R21 推翻旧「3 次重投后删除」收敛），条目保留
    outbox.save('inv-403', v2Payload())
    let rejects = 0
    const gave = await deliverOutboxEntry(
      stubApi(async () => {
        rejects += 1
        throw new ApiError(403, '云端返回 HTTP 403: PERMIT_BINDING_INVALID', 'PERMIT_BINDING_INVALID')
      }),
      outbox,
      outbox.load('inv-403')!,
      { retryBaseMs: 5, retryMaxMs: 5 },
    )
    assert.equal(gave, 'gave-up')
    assert.equal(rejects, 1, '确定性拒绝首次即停止主动重试（无有界重投）')
    const kept403 = outbox.load('inv-403')
    assert.ok(kept403, '确定性拒绝条目保留（永不删除）')
    assert.equal(kept403!.gave_up, true)
    assert.equal(kept403!.attempts, 1)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('resultOutbox：持续网络失败 + 有界尝试 → retry-exhausted 保留；shutdown 中止保留', async () => {
  const root = tmpRoot('deliver3')
  try {
    const outbox = new ResultOutbox(resultOutboxDir(root))
    outbox.save('inv-x', v2Payload())
    const outcome = await deliverOutboxEntry(
      stubApi(async () => {
        throw new NetworkError('网络请求失败: down')
      }),
      outbox,
      outbox.load('inv-x')!,
      { retryBaseMs: 5, retryMaxMs: 5, maxAttempts: 2 },
    )
    assert.equal(outcome, 'retry-exhausted')
    assert.ok(outbox.load('inv-x'))
    assert.equal(outbox.load('inv-x')!.attempts, 2)

    const controller = new AbortController()
    controller.abort()
    outbox.save('inv-y', v2Payload())
    const aborted = await deliverOutboxEntry(
      stubApi(async () => {
        throw new NetworkError('网络请求失败: down')
      }),
      outbox,
      outbox.load('inv-y')!,
      { retryBaseMs: 5, retryMaxMs: 5, shutdownSignal: controller.signal },
    )
    assert.equal(aborted, 'shutdown')
    assert.ok(outbox.load('inv-y'))
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('resultOutbox：replayPendingOutbox 混合收敛（ACK 删除；4xx 新标记 gave_up 保留；网络失败保留；既有 gave_up 跳过不投）', async () => {
  const root = tmpRoot('replay')
  try {
    const outbox = new ResultOutbox(resultOutboxDir(root))
    outbox.save('inv-ack', v2Payload())
    outbox.save('inv-reject', v2Payload())
    outbox.save('inv-keep', v2Payload())
    outbox.save('inv-dead', v2Payload())
    // 预置一条已 gave_up 的死信（模拟上一轮确定性 4xx 后保留的条目）
    outbox.markGaveUp('inv-dead', 'HTTP 422 INVALID_EFFECT')
    const attempted: string[] = []
    const results = new Map<string, unknown>([
      ['inv-ack', { state: 'succeeded', effect: 'applied' }],
      ['inv-reject', new ApiError(404, '云端返回 HTTP 404', 'CLAIM_MISMATCH')],
      ['inv-keep', new NetworkError('网络请求失败: down')],
    ])
    const counts = await replayPendingOutbox(
      stubApi(async (id) => {
        attempted.push(id)
        const r = results.get(id)
        if (r instanceof Error) throw r
        return r
      }),
      outbox,
      { retryBaseMs: 5, retryMaxMs: 5, maxAttempts: 2 },
    )
    assert.deepEqual(counts, { acked: 1, rejected: 1, kept: 1, skippedGaveUp: 1 })
    assert.ok(!attempted.includes('inv-dead'), 'gave_up 条目启动重投必须跳过')
    assert.equal(attempted.length, 4, '3 条可重投条目共 4 次尝试（inv-keep 因 maxAttempts=2 尝试两次）')
    assert.equal(outbox.load('inv-ack'), null, '仅 2xx 删除')
    assert.ok(outbox.load('inv-reject'), '确定性 4xx 条目保留')
    assert.equal(outbox.load('inv-reject')!.gave_up, true)
    assert.ok(outbox.load('inv-keep'), '网络失败条目保留')
    assert.ok(outbox.load('inv-dead'), '既有 gave_up 条目永不删除')
    assert.equal(outbox.load('inv-dead')!.attempts, 0, '跳过条目不产生新尝试计数')
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('resultOutbox：loadAll 忽略解析失败的残留文件（不抛错、不阻塞其余条目）', () => {
  const root = tmpRoot('loadall')
  try {
    const dir = resultOutboxDir(root)
    const outbox = new ResultOutbox(dir)
    outbox.save('inv-ok', v2Payload())
    writeFileSync(path.join(dir, 'inv-broken.json'), '{not json')
    const all = outbox.loadAll()
    assert.equal(all.length, 1)
    assert.equal(all[0]!.invocation_id, 'inv-ok')
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})
