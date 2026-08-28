/**
 * PowerShell 驱动输出解析（parseDriverOutcome）契约：
 * - 最后一行 DRIVER_JSON: {...} 为准；ok=true → data；ok=false → CodedOperationError
 * - 驱动 code 走白名单，未知 code 归并 INTERNAL_ERROR
 * - 非零退出且无 DRIVER_JSON → INTERNAL_ERROR（带 stderr 摘要）；零退出无 JSON 同样违约
 * 纯函数单测，不启动真实 PowerShell。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { parseDriverOutcome, type PowerShellScriptResult } from '../src/platform/powershell.js'
import { CodedOperationError } from '../src/operations/types.js'

function r(stdout: string, exitCode = 0, stderr = ''): PowerShellScriptResult {
  return { stdout, stderr, exitCode }
}

function assertCoded(fn: () => unknown, code: string): CodedOperationError {
  try {
    fn()
  } catch (e) {
    assert.ok(e instanceof CodedOperationError, `应抛 CodedOperationError，实际：${String(e)}`)
    assert.equal(e.code, code)
    return e
  }
  assert.fail('应抛出 CodedOperationError')
}

test('ok=true：返回 data；缺省 data → {}', () => {
  assert.deepEqual(parseDriverOutcome(r('DRIVER_JSON: {"ok":true,"data":{"items":[1]}}')), { items: [1] })
  assert.deepEqual(parseDriverOutcome(r('DRIVER_JSON: {"ok":true}')), {})
})

test('取最后一行 DRIVER_JSON；容忍日志行 / CRLF / BOM', () => {
  const stdout = '[1] log line\r\nDRIVER_JSON: {"ok":false,"code":"UI_CHANGED","message":"旧"}\r\nmore log\r\nDRIVER_JSON: {"ok":true,"data":{"a":1}}\r\n'
  assert.deepEqual(parseDriverOutcome(r(stdout)), { a: 1 })
  assert.deepEqual(parseDriverOutcome(r('﻿DRIVER_JSON: {"ok":true,"data":{"b":2}}')), { b: 2 })
})

test('ok=false：白名单 code 透传，message 保留', () => {
  const e = assertCoded(
    () => parseDriverOutcome(r('DRIVER_JSON: {"ok":false,"code":"UI_CHANGED","message":"单栏模式"}')),
    'UI_CHANGED',
  )
  assert.equal(e.message, '单栏模式')
})

test('ok=false：未知 code 归并 INTERNAL_ERROR（防止驱动自造不稳定 code）', () => {
  assertCoded(() => parseDriverOutcome(r('DRIVER_JSON: {"ok":false,"code":"WEIRD_CODE","message":"x"}')), 'INTERNAL_ERROR')
  assertCoded(() => parseDriverOutcome(r('DRIVER_JSON: {"ok":false}')), 'INTERNAL_ERROR')
})

test('EXECUTION_UNKNOWN 在白名单内（写动作 unknown 语义透传）', () => {
  assertCoded(
    () => parseDriverOutcome(r('DRIVER_JSON: {"ok":false,"code":"EXECUTION_UNKNOWN","message":"发送后校验失败"}')),
    'EXECUTION_UNKNOWN',
  )
})

test('INSUFFICIENT_CREDIT / CONFIG_MISSING 在白名单内（服务端代理 402/凭据缺失透传）', () => {
  const e1 = assertCoded(
    () => parseDriverOutcome(r('DRIVER_JSON: {"ok":false,"code":"INSUFFICIENT_CREDIT","message":"积分余额不足"}')),
    'INSUFFICIENT_CREDIT',
  )
  assert.equal(e1.message, '积分余额不足')
  assertCoded(
    () => parseDriverOutcome(r('DRIVER_JSON: {"ok":false,"code":"CONFIG_MISSING","message":"未配置激活码"}')),
    'CONFIG_MISSING',
  )
})

test('非零退出且无 DRIVER_JSON → INTERNAL_ERROR（带 stderr 摘要）', () => {
  const e = assertCoded(() => parseDriverOutcome(r('some progress logs', 1, 'boom happened')), 'INTERNAL_ERROR')
  assert.ok(e.message.includes('boom happened'))
  assert.ok(e.message.includes('exit=1'))
})

test('零退出但无 DRIVER_JSON → INTERNAL_ERROR（驱动契约违约）', () => {
  assertCoded(() => parseDriverOutcome(r('only logs', 0)), 'INTERNAL_ERROR')
})

test('DRIVER_JSON 非法 JSON → INTERNAL_ERROR', () => {
  assertCoded(() => parseDriverOutcome(r('DRIVER_JSON: {not json')), 'INTERNAL_ERROR')
})
