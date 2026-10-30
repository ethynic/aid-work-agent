/**
 * 日志脱敏：手机号永远不以明文进入 stderr 日志（安全原则：敏感信息不明文返回）。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { redactSensitive } from '../src/security/redaction.js'

test('redactSensitive：11 位手机号（1[3-9] 开头）脱敏', () => {
  assert.equal(redactSensitive('phone=13800138000 done'), 'phone=1********** done')
  assert.equal(redactSensitive('13671705875'), '1**********')
})

test('redactSensitive：非手机号不动（10/12 位、10/12 开头、普通文本）', () => {
  assert.equal(redactSensitive('1380013800'), '1380013800')
  assert.equal(redactSensitive('23800138000'), '23800138000')
  assert.equal(redactSensitive('run_id=abc code=OK'), 'run_id=abc code=OK')
})

test('redactSensitive：32-hex 密钥样式串兜底脱敏（设计 §8）', () => {
  const hex = 'a1b2c3d4e5f60718293a4b5c6d7e8f90'
  assert.equal(redactSensitive(`key=${hex}`), 'key=[REDACTED-HEX]')
  // 64-hex（如 sha256 hex）整段遮蔽；hex 内数字段不被手机号规则破坏
  const hex64 = hex + '0123456789abcdef0123456789abcdef'
  assert.equal(redactSensitive(hex64), '[REDACTED-HEX][REDACTED-HEX]')
  assert.equal(redactSensitive(`m=${hex} phone=13800138000`), 'm=[REDACTED-HEX] phone=1**********')
  // 短于 32 位或含非 hex 字符的串不动
  assert.equal(redactSensitive('deadbeefdeadbeefdeadbeefdeadbeef'), '[REDACTED-HEX]')
  assert.equal(redactSensitive('deadbeefdeadbeefdeadbeefdeadbeeg'), 'deadbeefdeadbeefdeadbeefdeadbeeg')
})
