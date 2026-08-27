/**
 * target_ref 签发/验证契约（设计 §5.3：本机 HMAC 密钥、5 分钟 TTL）。
 *
 * 覆盖：往返 / 过期 TARGET_REF_STALE / payload 或签名篡改 INVALID_ARGUMENT /
 * 格式非法 INVALID_ARGUMENT / 跨密钥目录（跨机）不可验证。
 * 密钥目录用临时目录注入，不触碰真实 %LOCALAPPDATA%。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import {
  createTargetRef,
  TARGET_REF_TTL_SECONDS,
  verifyTargetRef,
} from '../src/platform/targetRef.js'
import { CodedOperationError } from '../src/operations/types.js'

function tmpKeyDir(): string {
  return mkdtempSync(join(tmpdir(), 'weixin-targetref-'))
}

function assertCoded(fn: () => unknown, code: string): void {
  assert.throws(fn, (e) => e instanceof CodedOperationError && e.code === code)
}

test('签发/验证往返：解出 name 与 type', () => {
  const keyDir = tmpKeyDir()
  const ref = createTargetRef('张三', 'friend', { keyDir })
  const [body, sig] = ref.split('.')
  assert.ok(body && sig, '格式应为 <payload>.<signature>')
  const id = verifyTargetRef(ref, { keyDir })
  assert.deepEqual(id, { name: '张三', type: 'friend' })
})

test('过期 → TARGET_REF_STALE', () => {
  const keyDir = tmpKeyDir()
  const now = 1_800_000_000
  const ref = createTargetRef('张三', 'friend', { keyDir, now })
  // 边界：exp 时刻仍可用（exp > now），过期 1 秒后拒绝
  assert.doesNotThrow(() => verifyTargetRef(ref, { keyDir, now: now + TARGET_REF_TTL_SECONDS - 1 }))
  assertCoded(() => verifyTargetRef(ref, { keyDir, now: now + TARGET_REF_TTL_SECONDS }), 'TARGET_REF_STALE')
})

test('篡改 payload → INVALID_ARGUMENT（签名校验失败）', () => {
  const keyDir = tmpKeyDir()
  const [body, sig] = createTargetRef('张三', 'friend', { keyDir }).split('.')
  const tamperedBody = (body![0] === 'A' ? 'B' : 'A') + body!.slice(1)
  assertCoded(() => verifyTargetRef(`${tamperedBody}.${sig}`, { keyDir }), 'INVALID_ARGUMENT')
})

test('篡改签名 → INVALID_ARGUMENT', () => {
  const keyDir = tmpKeyDir()
  const [body, sig] = createTargetRef('张三', 'friend', { keyDir }).split('.')
  const tamperedSig = (sig![0] === 'A' ? 'B' : 'A') + sig!.slice(1)
  assertCoded(() => verifyTargetRef(`${body}.${tamperedSig}`, { keyDir }), 'INVALID_ARGUMENT')
})

test('格式非法（无分隔点 / 三段 / 空段 / 非字符串）→ INVALID_ARGUMENT', () => {
  const keyDir = tmpKeyDir()
  for (const bad of ['no-dot', 'a.b.c', '.sig', 'body.', '']) {
    assertCoded(() => verifyTargetRef(bad, { keyDir }), 'INVALID_ARGUMENT')
  }
  // @ts-expect-error 故意传非法类型模拟 Host 侧绕过 schema
  assertCoded(() => verifyTargetRef(123, { keyDir }), 'INVALID_ARGUMENT')
})

test('跨密钥目录（跨机）不可验证 → INVALID_ARGUMENT', () => {
  const ref = createTargetRef('张三', 'friend', { keyDir: tmpKeyDir() })
  assertCoded(() => verifyTargetRef(ref, { keyDir: tmpKeyDir() }), 'INVALID_ARGUMENT')
})

test('密钥复用：同目录两次签发可互相验证', () => {
  const keyDir = tmpKeyDir()
  const r1 = createTargetRef('甲', 'friend', { keyDir })
  const r2 = createTargetRef('乙', 'group', { keyDir })
  assert.equal(verifyTargetRef(r1, { keyDir }).name, '甲')
  assert.equal(verifyTargetRef(r2, { keyDir }).type, 'group')
})
