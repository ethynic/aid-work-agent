import test from 'node:test'
import assert from 'node:assert/strict'
import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import fs from 'node:fs'
import { syncBuiltinESMExports } from 'node:module'
import { loadSendReceipt, saveSendReceipt, validReceiptId } from '../src/platform/sendReceipt.js'
import type { SendReceipt } from '../src/platform/localdb/sendVerification.js'
import { CodedOperationError } from '../src/operations/types.js'

// Framing fixture only; Windows integration tests exercise real DPAPI, not this substitute.
const crypto = { protect: async (s: string) => Buffer.from(s).toString('base64'),
  unprotect: async (s: string) => Buffer.from(s, 'base64').toString('utf8') }
const id = 'send-2026-10-26T00-00-00-000Z'
const receipt: SendReceipt = { version: 1, account: 'self', conversationId: 'R:anonymous',
  baseline: 1, anchor: 'a'.repeat(64), sourceId: 'synthetic-file', schema: 1, text: 'anonymous', since: 10, until: 314 }

test('receipt不可覆盖，路径/密文帧错误不能生成可派发的checkpoint', async () => {
  const root = mkdtempSync(join(tmpdir(), 'wecom-receipt-'))
  const dir = join(root, id)
  mkdirSync(dir)
  try {
    assert.equal(await saveSendReceipt(dir, receipt, crypto), id)
    const old = readFileSync(join(dir, 'send-receipt.dpapi'), 'utf8')
    await assert.rejects(saveSendReceipt(dir, { ...receipt, text: 'replacement' }, crypto))
    assert.equal(readFileSync(join(dir, 'send-receipt.dpapi'), 'utf8'), old)
    assert.equal((await loadSendReceipt(root, id, crypto)).text, receipt.text)
    assert.equal(validReceiptId('../outside'), false)
    await assert.rejects(saveSendReceipt(join(root, 'wrong'), receipt, crypto))
    await assert.rejects(saveSendReceipt(join(root, id + '-2'), receipt, { protect: async () => '' }))
  } finally { rmSync(root, { recursive: true, force: true }) }
})

test('receipt绑定目录ID，版本/基线锚/窗口无效及过大文件均拒绝', async () => {
  const root = mkdtempSync(join(tmpdir(), 'wecom-receipt-'))
  const dir = join(root, id)
  mkdirSync(dir)
  try {
    await saveSendReceipt(dir, receipt, crypto)
    mkdirSync(join(root, id + '-2'))
    copyFileSync(join(dir, 'send-receipt.dpapi'), join(root, id + '-2', 'send-receipt.dpapi'))
    await assert.rejects(loadSendReceipt(root, id + '-2', crypto))
    for (const patch of [{ version: 2 }, { anchor: '' }, { baseline: -1 }, { until: 10000 }, { text: '' }]) {
      writeFileSync(join(dir, 'send-receipt.dpapi'), await crypto.protect(JSON.stringify({ receipt_id: id, ...receipt, ...patch })))
      await assert.rejects(loadSendReceipt(root, id, crypto))
    }
    writeFileSync(join(dir, 'send-receipt.dpapi'), 'a'.repeat(65537))
    await assert.rejects(loadSendReceipt(root, id, crypto))
  } finally { rmSync(root, { recursive: true, force: true }) }
})

test('write完成后flush或close失败只清自建文件；自清失败保留unknown和ID', async () => {
  const original = { fsyncSync: fs.fsyncSync, closeSync: fs.closeSync, unlinkSync: fs.unlinkSync }
  for (const fault of ['flush', 'close', 'cleanup'] as const) {
    const root = mkdtempSync(join(tmpdir(), 'wecom-receipt-owned-'))
    const dir = join(root, id)
    mkdirSync(dir)
    try {
      if (fault === 'close') fs.closeSync = fd => { original.closeSync(fd); throw new Error('synthetic close error') }
      else fs.fsyncSync = () => { throw new Error('synthetic flush error') }
      if (fault === 'cleanup') fs.unlinkSync = () => { throw new Error('synthetic locked file') }
      syncBuiltinESMExports()
      await assert.rejects(saveSendReceipt(dir, receipt, crypto), err => {
        if (fault === 'cleanup') {
          assert.ok(err instanceof CodedOperationError)
          assert.equal(err.effectOverride, 'unknown')
          assert.equal(err.data?.receipt_id, id)
          assert.equal(err.data?.receipt_invalidation_failed, true)
          assert.ok(fs.existsSync(join(dir, 'send-receipt.dpapi')), '不能谎称失败的自清已完成')
        } else assert.equal(fs.existsSync(join(dir, 'send-receipt.dpapi')), false, '已捕获的owned文件失败不得残留可确认的完整正文')
        return true
      })
    } finally {
      Object.assign(fs, original)
      syncBuiltinESMExports()
      rmSync(root, { recursive: true, force: true })
    }
  }
})
