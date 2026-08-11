/**
 * 用例 8：DPAPI 加解密 round-trip（Windows 真实跑；非 Windows skip）。
 */
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { dpapiProtect, dpapiUnprotect } from '../src/dpapi.js'

test('DPAPI round-trip：密文不含明文，解密回原文', { skip: process.platform !== 'win32' }, async () => {
  const token = 'dpt_9f8e7d6c5b4a39281706f5e4d3c2b1a09f8e7d6c5b4a39281706f5e4d3c2b1a0'
  const cipher = await dpapiProtect(token)
  assert.ok(cipher.length > 0)
  assert.ok(!cipher.includes(token), '密文不得含明文')
  assert.match(cipher, /^[A-Za-z0-9+/=]+$/, '密文应为 base64')
  const back = await dpapiUnprotect(cipher)
  assert.equal(back, token)
})

test('DPAPI 损坏密文：fail-loud 抛错', { skip: process.platform !== 'win32' }, async () => {
  await assert.rejects(() => dpapiUnprotect('AAAA'), /DPAPI|失败|无输出/)
})
