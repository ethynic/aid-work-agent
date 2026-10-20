import assert from 'node:assert/strict'
import test from 'node:test'
import { randomUUID, createHash } from 'node:crypto'
import { unlink } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { z } from 'zod'
import { rememberBrowserTarget, resolveBrowserTarget } from '../src/main/boss/browserTargetBinding.js'
import { runBossOperation, type BossSession } from '../src/main/operations/bossContext.js'
import { CodedOperationError } from '../src/main/operations/types.js'
import { TOOL_DEFS } from '../src/mcp/toolDefs.js'

async function removeOwnBinding(ref: string, target: string, port: number) {
  const root = join(tmpdir(), 'aid-boss-targets')
  await Promise.allSettled([unlink(join(root, `${ref}.json`)), unlink(join(root, `${createHash('sha256').update(`${port}:${target}`).digest('hex')}.target`))])
}
const context = { signal: new AbortController().signal, progress: () => {} }
function fakeSession(target: string): BossSession {
  return { getTargetId: async () => target, close: async () => {} } as unknown as BossSession
}

test('opaque local references bind the original target and port, while other references cannot select a target ID', async () => {
  const target = `target-${randomUUID()}`, ref = await rememberBrowserTarget(target, 9333)
  try {
    assert.match(ref, /^[a-f0-9-]{36}$/)
    assert.equal(ref.includes(target), false)
    assert.deepEqual(await resolveBrowserTarget(ref, 9333), { target_id: target, cdp_port: 9333 })
    assert.equal(await rememberBrowserTarget(target, 9333), ref, 'same target reuses its opaque binding for standalone receipt retrieval')
    for (const [value, port] of [[ref, 9222], [randomUUID(), 9333], ['../target', 9333]] as const) {
      await assert.rejects(resolveBrowserTarget(value, port), (error: unknown) => error instanceof CodedOperationError && error.data?.recovery_action_error === 'STALE_OBSERVATION' && error.data.input_status === 'not_started')
    }
  } finally { await removeOwnBinding(ref, target, 9333) }
})

test('all failure facts receive the same binding and a mismatched resumed session is rejected before the executor runs', async () => {
  const target = `target-${randomUUID()}`
  const result = await runBossOperation({ kind: 'readonly', name: 'boss_open_detail' }, context, async () => fakeSession(target), () => null, async () => {
    throw new CodedOperationError('UI_CHANGED', 'blocked', { operation_recovery: { schema_version: 1 }, page_operation: { schema_version: 1 } })
  })
  const ref = String(result.data.browser_target_ref)
  try {
    assert.equal((result.data.operation_recovery as Record<string, unknown>).browser_target_ref, ref)
    assert.equal((result.data.page_operation as Record<string, unknown>).browser_target_ref, ref)
    let executions = 0
    const resumed = await runBossOperation({ kind: 'readonly', name: 'boss_open_detail' }, { ...context, browserTargetRef: ref }, async () => fakeSession('another-business-tab'), () => null, async () => { executions++; return { message: 'wrong' } })
    assert.equal(resumed.success, false); assert.equal(resumed.data.recovery_action_error, 'STALE_OBSERVATION')
    assert.equal(resumed.data.input_status, 'not_started'); assert.equal(executions, 0)
    const same = await runBossOperation({ kind: 'readonly', name: 'boss_open_detail' }, { ...context, browserTargetRef: ref }, async () => fakeSession(target), () => null, async session => {
      executions++; assert.equal(session.browserTargetRef, ref); return { message: 'ok' }
    })
    assert.equal(same.success, true); assert.equal(same.data.browser_target_ref, ref); assert.equal(executions, 1)
    assert.equal(JSON.stringify(same).includes(target), false)
  } finally { await removeOwnBinding(ref, target, 9222) }
})

test('a single optional transport field exists on every MCP command without changing its business parameters', () => {
  const ref = randomUUID()
  for (const definition of TOOL_DEFS) {
    assert.ok(definition.zodShape._boss_target_ref)
    const shape = z.object({ _boss_target_ref: definition.zodShape._boss_target_ref! })
    assert.deepEqual(shape.parse({ _boss_target_ref: ref }), { _boss_target_ref: ref })
    assert.deepEqual(shape.parse({}), {})
    assert.equal(shape.safeParse({ _boss_target_ref: 'raw-target-id' }).success, false)
  }
})
