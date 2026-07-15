import assert from 'node:assert/strict'
import test from 'node:test'
import { assertDownloadSize, MAX_DOWNLOAD_BYTES, normalizeDownloadUrl, normalizeExternalUrl, readDownloadBody, safeSuggestedName } from '../electron/systemCapabilities.js'
import { parseAgentDeepLink } from '../electron/security.js'

test('系统浏览器仅接受无凭据 HTTPS URL', () => {
  assert.equal(normalizeExternalUrl('https://docs.example.com/a'), 'https://docs.example.com/a')
  for (const value of ['http://example.com', 'file:///secret', 'javascript:alert(1)', 'https://u:p@example.com']) {
    assert.throws(() => normalizeExternalUrl(value), value)
  }
})

test('download size accepts exactly 100 MiB and rejects invalid declarations or larger payloads', () => {
  assert.doesNotThrow(() => assertDownloadSize(String(MAX_DOWNLOAD_BYTES)))
  assert.doesNotThrow(() => assertDownloadSize(MAX_DOWNLOAD_BYTES))
  for (const value of [String(MAX_DOWNLOAD_BYTES + 1), MAX_DOWNLOAD_BYTES + 1, '-1', 'invalid']) {
    assert.throws(() => assertDownloadSize(value))
  }
})

test('下载只能来自配置 API origin，文件名不能注入目录', () => {
  assert.equal(normalizeDownloadUrl('https://api.example.com/api/files/1', 'https://api.example.com'), 'https://api.example.com/api/files/1')
  assert.throws(() => normalizeDownloadUrl('https://evil.example/file', 'https://api.example.com'))
  assert.equal(safeSuggestedName('../report?.pdf'), 'report_.pdf')
})

test('deep link 仅接受 Agent/tenant route，拒绝 Portal 与 traversal', () => {
  assert.equal(parseAgentDeepLink(['app.exe', 'aidagent://app/t/acme/chat']), 'aidagent://app/t/acme/chat')
  for (const value of ['aidagent://app/portal', 'aidagent://app/portal/login', 'aidagent://app/%2e%2e/secret']) {
    assert.equal(parseAgentDeepLink(['app.exe', value]), null, value)
  }
})

test('chunked downloads enforce the limit before buffering an oversized response', async () => {
  const oversized = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new Uint8Array(MAX_DOWNLOAD_BYTES))
      controller.enqueue(new Uint8Array(1))
      controller.close()
    },
  })
  await assert.rejects(() => readDownloadBody(new Response(oversized)), /size limit/)
})
