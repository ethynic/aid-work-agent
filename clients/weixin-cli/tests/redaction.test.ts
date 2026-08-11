/**
 * stderr 日志脱敏（设计 §8 / 开发任务 §9）：手机号正则不得命中日志行。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { redactSensitive } from '../src/security/redaction.js'

test('手机号被脱敏（1[3-9]xxxxxxxxx → 掩码）', () => {
  assert.equal(redactSensitive('拨打 13812345678 或 19900001111'), '拨打 1********** 或 1**********')
  assert.ok(!/1[3-9]\d{9}/.test(redactSensitive('联系人手机号 13700001111 已记录')))
})

test('非手机号数字串不受影响（位数不足/号段不符）', () => {
  assert.equal(redactSensitive('耗时 12345ms，端口 9222'), '耗时 12345ms，端口 9222')
  assert.equal(redactSensitive('编号 12345678901'), '编号 12345678901') // 12 开头非手机号段
  assert.equal(redactSensitive('run_id=abc-123'), 'run_id=abc-123')
})

test('普通中文/英文日志行原样通过', () => {
  const line = 'tool=weixin_probe run_id=uuid success=true code=OK effect=none elapsed_ms=42'
  assert.equal(redactSensitive(line), line)
})
