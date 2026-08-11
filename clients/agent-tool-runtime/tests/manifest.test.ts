/**
 * 用例 6：manifest 校验——非法 tool_name → TOOL_NOT_ALLOWED（不 started、不调 Provider）。
 */
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { startTestStack } from './helpers/runtimeStack.js'

test('manifest：非法 tool_name 直接回 TOOL_NOT_ALLOWED，不 started 不执行', async () => {
  const stack = await startTestStack()
  try {
    const id = stack.cloud.enqueueInvocation('evil_tool', {})
    await stack.cloud.waitFor(
      () => stack.cloud.callsFor(id).some((c) => c.type === 'result'),
      10_000,
      'TOOL_NOT_ALLOWED result',
    )
    const calls = stack.cloud.callsFor(id)
    assert.ok(!calls.some((c) => c.type === 'started'), '非法工具不得标记 started')
    assert.ok(!calls.some((c) => c.type === 'progress'), '非法工具不得有进度')
    const result = calls.find((c) => c.type === 'result')!
    assert.equal(result.payload['success'], false)
    assert.equal(result.payload['code'], 'TOOL_NOT_ALLOWED')
    assert.equal(result.payload['retryable'], false)
    assert.equal(stack.cloud.getInvocation(id)!.state, 'failed')
  } finally {
    await stack.stop()
  }
})

test('manifest：受信清单内全部 7 个工具名都放行（单测层面）', async () => {
  const { isToolAllowed, TRUSTED_MANIFEST, manifestDigest } = await import('../src/manifestVerifier.js')
  for (const tool of TRUSTED_MANIFEST.tools) {
    assert.ok(isToolAllowed(tool), `${tool} 应被放行`)
  }
  assert.ok(!isToolAllowed('boss_delete_everything'))
  assert.match(manifestDigest(), /^[0-9a-f]{64}$/)
})
