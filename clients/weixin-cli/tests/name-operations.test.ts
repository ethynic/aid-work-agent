import test from 'node:test'
import assert from 'node:assert/strict'
import { randomUUID } from 'node:crypto'
import { createNameOperations } from '../src/operations/nameOperations.js'
import type { NameWindow } from '../src/platform/nameSession.js'

test('名称解析仅返回定位证据，会话观察继续使用已定位的窗口', async () => {
  const title = 'Team Chat'
  const nativeTitle = 'Team·Chat'
  const window: NameWindow = {
    unchanged: false, title, native_title: nativeTitle, exact_name: true, unique_match: true,
    complete: true, frame: 'a'.repeat(64), evidence_ref: 'dpapi:test_evidence', input_empty: true,
    objects: [{
      type: 'text', sender: 'peer', text: 'private history', bounds: { x0: 10, y0: 20, x1: 100, y1: 40 },
      clipped: false, evidence_hash: 'b'.repeat(64),
    }],
  }
  const { resolve, observe } = createNameOperations({
    driver: async (request) => request.open ? window : { unchanged: true, frame: window.frame },
  })
  const ctx = { signal: new AbortController().signal, progress() {} }

  const resolved = await resolve.execute({ target_name: title }, ctx)
  assert.equal(resolved.success, true)
  assert.equal(resolved.data.evidence_ref, window.evidence_ref)
  assert.ok(!JSON.stringify(resolved.data).includes('private history'))
  assert.equal('native_title' in resolved.data, false)

  const observed = await observe.execute({
    target_name: title, conversation_binding_id: randomUUID(), binding_version: 1, account_identity_version: 0, watermark: null,
  }, ctx)
  assert.equal(observed.success, true)
  assert.equal(observed.data.coverage, 'complete_window')
  assert.equal(observed.data.frame, window.frame)
  assert.equal('native_title' in observed.data, false)
})
