/**
 * PowerShell 驱动输出解析（parseDriverOutcome）契约：
 * - 最后一行 DRIVER_JSON: {...} 为准；ok=true → data；ok=false → CodedOperationError
 * - 驱动 code 走白名单，未知 code 归并 INTERNAL_ERROR
 * - 非零退出且无 DRIVER_JSON → INTERNAL_ERROR（带 stderr 摘要）；零退出无 JSON 同样违约
 * 纯函数单测，不启动真实 PowerShell。
 */
import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import test from 'node:test'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
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
  assert.deepEqual(parseDriverOutcome(r('DRIVER_JSON: {"ok":true,"data":{"wechat_name":"WayneLu"}}')), {
    wechat_name: 'WayneLu',
  })
  assert.deepEqual(parseDriverOutcome(r('DRIVER_JSON: {"ok":true}')), {})
})

test('取最后一行 DRIVER_JSON；容忍日志行 / CRLF / BOM', () => {
  const stdout = '[1] log line\r\nDRIVER_JSON: {"ok":false,"code":"UI_CHANGED","message":"旧"}\r\nmore log\r\nDRIVER_JSON: {"ok":true,"data":{"a":1}}\r\n'
  assert.deepEqual(parseDriverOutcome(r(stdout)), { a: 1 })
  assert.deepEqual(parseDriverOutcome(r('﻿DRIVER_JSON: {"ok":true,"data":{"b":2}}')), { b: 2 })
})

test('ok=false：白名单 code 透传，message 保留', () => {
  const e = assertCoded(
    () => parseDriverOutcome(r('DRIVER_JSON: {"ok":false,"code":"UI_CHANGED","message":"未等到弹窗"}')),
    'UI_CHANGED',
  )
  assert.equal(e.message, '未等到弹窗')
})

test('ok=false：M1 关键错误码在白名单内（WECOM_NOT_FOUND/NOT_LOGGED_IN/CUSTOMER_NOT_FOUND/EXECUTION_UNKNOWN/CONFIG_MISSING）', () => {
  for (const code of ['WECOM_NOT_FOUND', 'NOT_LOGGED_IN', 'CUSTOMER_NOT_FOUND', 'EXECUTION_UNKNOWN', 'CONFIG_MISSING', 'FOREGROUND_LOST', 'WINDOW_AMBIGUOUS']) {
    assertCoded(() => parseDriverOutcome(r(`DRIVER_JSON: {"ok":false,"code":"${code}","message":"x"}`)), code)
  }
})

test('ok=false：未知 code 归并 INTERNAL_ERROR（防止驱动自造不稳定 code）', () => {
  assertCoded(() => parseDriverOutcome(r('DRIVER_JSON: {"ok":false,"code":"WEIRD_CODE","message":"x"}')), 'INTERNAL_ERROR')
  assertCoded(() => parseDriverOutcome(r('DRIVER_JSON: {"ok":false}')), 'INTERNAL_ERROR')
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

test('所有 drivers/ps1 脚本必须以 UTF-8 BOM 开头（PS 5.1 无 BOM 会按 GBK 误解析中文）', () => {
  // dist/tests → 包根需要上两级（编译后测试在 dist/tests/ 下运行）
  const dir = fileURLToPath(new URL('../../drivers/ps1/', import.meta.url))
  const scripts = readdirSync(dir).filter((f) => f.endsWith('.ps1'))
  assert.ok(scripts.length >= 4, `驱动脚本数量异常：${scripts.length}`)
  for (const f of scripts) {
    const head = readFileSync(join(dir, f)).subarray(0, 3)
    assert.deepEqual([...head], [0xef, 0xbb, 0xbf], `${f} 缺少 UTF-8 BOM`)
  }
})
