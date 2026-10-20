import assert from 'node:assert/strict'
import test from 'node:test'
import { Client } from '@modelcontextprotocol/sdk/client/index.js'
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js'
import { InMemoryTransport } from '@modelcontextprotocol/sdk/inMemory.js'
import { decodeProviderResult } from '../dist/legacy/providerResult.js'
import { ProviderManager } from '../dist/legacy/providerManager.js'
import { configureRuntimeHome } from '../dist/legacy/config.js'
import { mkdtempSync, readFileSync, rmSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'

const receipt = { success: true, code: 'OK', effect: 'none', message: 'done',
  data: { count: 3 }, retryable: false, run_id: 'test-run' }
const text = value => ({ type: 'text', text: JSON.stringify(value) })

test('first-party structured result remains authoritative and preserves all fields', () => {
  const result = { ...receipt, data: { images: [{ base64: 'test-image' }] }, extension: 42 }
  assert.equal(decodeProviderResult({ structuredContent: result, content: [text(result)] }).result, result)
})

test('legacy text fallback skips progress and unrelated JSON before a valid receipt', () => {
  const decoded = decodeProviderResult({ content: [
    { type: 'text', text: 'progress PRIVATE_NAME' }, text({ progress: 100 }),
    { type: 'image', data: 'private-image', mimeType: 'image/png' }, text(receipt),
  ] })
  assert.deepEqual(decoded, { result: receipt })
})

test('domain failures and v2 receipt semantics are preserved', () => {
  const v1 = { ...receipt, success: false, code: 'UI_CHANGED', effect: 'partial' }
  assert.deepEqual(decodeProviderResult({ content: [text(v1)] }).result, v1)
  const { retryable, ...base } = receipt
  const v2 = { ...base, data: null, safe_to_retry: false, phase: 'unknown', effect: 'unknown',
    success: false, code: 'EXECUTION_UNKNOWN', evidence_ref: 'opaque-ref' }
  assert.equal(decodeProviderResult({ structuredContent: v2 }).result, v2)
})

test('malformed or ambiguous results never become success or safe to retry', () => {
  for (const reply of [null, [], { content: [text(null)] }, { content: [text(true)] },
    { structuredContent: [] }, { structuredContent: { success: true } },
    { content: [text({ ...receipt, success: 'true' })] },
    { content: [text({ ...receipt, code: 'WRONG_PAGE' })] },
    { content: [text({ ...receipt, effect: 'bogus' })] },
    { content: [text({ ...receipt, data: [] })] },
    { structuredContent: {}, content: [text(receipt)] },
    { content: [text(receipt), text({ ...receipt, run_id: 'other-run' })] },
    { isError: 'true', structuredContent: receipt },
    { isError: true, structuredContent: receipt, content: [text(receipt)] },
  ]) {
    const decoded = decodeProviderResult(reply)
    assert.equal(decoded.result.success, false)
    assert.equal(decoded.result.effect, 'unknown')
    assert.equal(decoded.result.retryable, false)
    assert.ok(decoded.diagnostic)
  }
})

test('parse diagnostics contain structural facts only, including SDK error payloads', () => {
  const decoded = decodeProviderResult({ isError: true,
    structuredContent: { privateKeyName: 'SECRET' },
    content: [{ type: 'text', text: 'PRIVATE_NAME password=SECRET' }] })
  assert.deepEqual(decoded.diagnostic, { reason: 'mcp_error', structuredPresent: true,
    contentCount: 1, textCount: 1, textChars: 28 })
  assert.doesNotMatch(JSON.stringify(decoded), /PRIVATE_NAME|password|SECRET|privateKeyName/)
})

test('real MCP SDK handler errors stay unknown; later text receipts work without replay', async () => {
  const home = mkdtempSync(path.join(os.tmpdir(), 'aid-provider-receipt-'))
  configureRuntimeHome(home)
  const server = new McpServer({ name: 'receipt-fixture', version: '1' })
  let calls = 0
  server.registerTool('handler_error', { inputSchema: {} }, async () => {
    calls++
    throw new Error('PRIVATE_CANDIDATE password=SECRET')
  })
  server.registerTool('legacy_receipt', { inputSchema: {} }, async () => {
    calls++
    return { content: [{ type: 'text', text: 'finished reading' }, text(receipt)] }
  })
  server.registerTool('structured_receipt', { inputSchema: {} }, async () => {
    calls++
    return { content: [text(receipt)], structuredContent: receipt }
  })
  server.registerTool('error_with_success_payload', { inputSchema: {} }, async () => {
    calls++
    return { isError: true, content: [text(receipt)], structuredContent: receipt }
  })
  const client = new Client({ name: 'runtime-test', version: '1' }, { capabilities: {} })
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair()
  const manager = new ProviderManager('/never-spawn')
  try {
    await server.connect(serverTransport)
    await client.connect(clientTransport)
    manager.client = client
    const failed = await manager.callTool('handler_error', {})
    assert.equal(failed.effect, 'unknown')
    assert.equal(failed.retryable, false)
    assert.match(failed.message, /报告工具执行错误/)
    assert.deepEqual(await manager.callTool('legacy_receipt', {}), receipt)
    assert.deepEqual(await manager.callTool('structured_receipt', {}), receipt)
    assert.equal((await manager.callTool('error_with_success_payload', {})).success, false)
    assert.equal(calls, 4, 'a failed reply must never replay the operation')
    const log = readFileSync(path.join(home, 'logs/runtime.log'), 'utf8')
    assert.match(log, /mcp_error/)
    assert.doesNotMatch(log, /PRIVATE_CANDIDATE|password|SECRET/)
  } finally {
    await manager.shutdown()
    await server.close()
    rmSync(home, { recursive: true, force: true })
    configureRuntimeHome(undefined)
  }
})
