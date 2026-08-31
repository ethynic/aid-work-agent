/**
 * wecom_add_customer operation 契约测试（mock runDriverFn，绝不触达真实企微/驱动）。
 *
 * 覆盖：参数校验（phone 格式 / confirm 显式 true）→ 驱动成功 / CUSTOMER_NOT_FOUND /
 * UI_CHANGED / 超时→EXECUTION_UNKNOWN / 取消→CANCELLED(unknown) / 缺微信名→EXECUTION_UNKNOWN。
 * 写动作语义：失败不自动重试（retryable=false，BUSY/WECOM_NOT_FOUND/NOT_LOGGED_IN 除外）。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { createWecomAddCustomerOperation } from '../src/operations/addCustomer.js'
import { CancelledError, CodedOperationError, type OpContext } from '../src/operations/types.js'
import type { RunPowerShellDriverFn } from '../src/platform/powershell.js'

function silentCtx(): OpContext {
  return { signal: new AbortController().signal, progress: () => {} }
}

function makeOp(runDriverFn: RunPowerShellDriverFn, artifactDirFn?: () => string | null) {
  const dir = mkdtempSync(join(tmpdir(), 'wecom-add-customer-test-'))
  const op = createWecomAddCustomerOperation({
    runDriverFn,
    artifactDirFn: artifactDirFn ?? (() => dir),
  })
  return { op, dir }
}

const VALID = { phone: '13800138000', confirm: true }

// ---------- 参数校验（不触达驱动） ----------

test('add_customer：缺 confirm/--yes → INVALID_ARGUMENT，退出前不调驱动', async () => {
  let called = 0
  const { op, dir } = makeOp(async () => {
    called++
    return {}
  })
  try {
    const r = await op.execute({ phone: '13800138000', confirm: false }, silentCtx())
    assert.equal(r.success, false)
    assert.equal(r.code, 'INVALID_ARGUMENT')
    assert.equal(r.effect, 'none')
    assert.ok(r.message.includes('confirm'))
    assert.equal(called, 0)
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('add_customer：非法手机号一律 INVALID_ARGUMENT', async () => {
  let called = 0
  const { op, dir } = makeOp(async () => {
    called++
    return {}
  })
  try {
    for (const phone of ['', '123', '23800138000', '1380013800a', '138001380000']) {
      const r = await op.execute({ phone, confirm: true }, silentCtx())
      assert.equal(r.code, 'INVALID_ARGUMENT', `phone=${phone} 应被拒绝`)
    }
    // @ts-expect-error 故意传非法值
    const r2 = await op.execute({ confirm: true }, silentCtx())
    assert.equal(r2.code, 'INVALID_ARGUMENT')
    assert.equal(called, 0)
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

// ---------- 驱动结果归并 ----------

test('add_customer：驱动成功带回微信名 → OK + effect=applied + data 完整', async () => {
  const { op, dir } = makeOp(async (opts) => {
    assert.ok(opts.script.endsWith('add-customer.ps1'), '驱动路径应为 drivers/ps1/add-customer.ps1')
    assert.deepEqual(opts.args?.slice(0, 2), ['-Phone', '13800138000'])
    assert.equal(opts.args?.[2], '-ArtifactDir')
    return { wechat_name: 'WayneLu', screenshot_paths: ['a.png', 'b.png'] }
  })
  try {
    const r = await op.execute(VALID, silentCtx())
    assert.equal(r.success, true)
    assert.equal(r.code, 'OK')
    assert.equal(r.effect, 'applied')
    assert.equal(r.retryable, false)
    assert.equal(r.data.phone, '13800138000')
    assert.equal(r.data.wechat_name, 'WayneLu')
    assert.deepEqual(r.data.screenshot_paths, ['a.png', 'b.png'])
    assert.ok(r.message.includes('WayneLu'))
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('add_customer：CUSTOMER_NOT_FOUND → effect=none，不自动重试', async () => {
  const { op, dir } = makeOp(async () => {
    throw new CodedOperationError('CUSTOMER_NOT_FOUND', '手机号未检索到微信用户')
  })
  try {
    const r = await op.execute(VALID, silentCtx())
    assert.equal(r.success, false)
    assert.equal(r.code, 'CUSTOMER_NOT_FOUND')
    assert.equal(r.effect, 'none')
    assert.equal(r.retryable, false)
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('add_customer：UI_CHANGED → effect=none，不自动重试', async () => {
  const { op, dir } = makeOp(async () => {
    throw new CodedOperationError('UI_CHANGED', '未等到「添加客户」弹窗')
  })
  try {
    const r = await op.execute(VALID, silentCtx())
    assert.equal(r.code, 'UI_CHANGED')
    assert.equal(r.effect, 'none')
    assert.equal(r.retryable, false)
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('add_customer：驱动超时 RESULT_TIMEOUT → 归并 EXECUTION_UNKNOWN + effect=unknown（写可能已落地）', async () => {
  const { op, dir } = makeOp(async () => {
    throw new CodedOperationError('RESULT_TIMEOUT', 'PowerShell 驱动执行超时')
  })
  try {
    const r = await op.execute(VALID, silentCtx())
    assert.equal(r.success, false)
    assert.equal(r.code, 'EXECUTION_UNKNOWN')
    assert.equal(r.effect, 'unknown')
    assert.equal(r.retryable, false)
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('add_customer：驱动执行中被取消 → CANCELLED + effect=unknown（不可误报 none）', async () => {
  const { op, dir } = makeOp(async () => {
    throw new CancelledError()
  })
  try {
    const r = await op.execute(VALID, silentCtx())
    assert.equal(r.code, 'CANCELLED')
    assert.equal(r.effect, 'unknown')
    assert.equal(r.retryable, false)
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('add_customer：驱动成功但缺微信名 → EXECUTION_UNKNOWN + effect=unknown（不可误报成功）', async () => {
  const { op, dir } = makeOp(async () => ({ screenshot_paths: [] }))
  try {
    const r = await op.execute(VALID, silentCtx())
    assert.equal(r.code, 'EXECUTION_UNKNOWN')
    assert.equal(r.effect, 'unknown')
    assert.equal(r.retryable, false)
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('add_customer：%LOCALAPPDATA% 缺失（artifactDirFn=null）→ INTERNAL_ERROR，不调驱动', async () => {
  let called = 0
  const { op, dir } = makeOp(async () => {
    called++
    return {}
  }, () => null)
  try {
    const r = await op.execute(VALID, silentCtx())
    assert.equal(r.code, 'INTERNAL_ERROR')
    assert.equal(r.effect, 'unknown') // INTERNAL_ERROR 兜底语义：不可误报 none
    assert.equal(called, 0)
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})
