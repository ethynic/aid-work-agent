/** Fifty empty monitoring targets must still fit in one accepted result. */
import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { groupMonitorFixture } from './helpers/groupMonitorFixture.js'
import { createWecomReadSessionsOperation } from '../src/operations/readSessions.js'
import { createWecomSessionDirectoryOperation } from '../src/operations/sessionDirectory.js'
import { sessionLocator, type SessionReadArgs } from '../src/platform/localdb/sessions.js'
import type { LocaldbReadDeps } from '../src/platform/localdb/readSession.js'
import type { OpContext } from '../src/operations/types.js'

const context = (): OpContext => ({ signal: new AbortController().signal, progress() {} })

test('fifty explicit empty ws1 targets keep every locator within the existing 16 KiB read budget',
  { skip: process.platform !== 'win32' }, async () => {
    const base = mkdtempSync(join(tmpdir(), 'wecom-empty-targets-'))
    try {
      const fixture = await groupMonitorFixture(base)
      const ids = Array.from({ length: 50 }, (_, index) => `R:empty-${index.toString().padStart(2, '0')}`)
      const sessions = ids.map(id => sessionLocator(fixture.current.account, id))
      const deps: LocaldbReadDeps = { ...fixture.deps,
        workDirFn: () => ({ dir: base, run_id: 'synthetic', cleanup: () => true }),
        captureFn: async () => ({ ok: true, dbSnapshot: base, walSnapshot: null, capture: {} as never }),
        runPyFn: async (params, options) => {
          if (params[0] === 'decrypt') return { ok: true, data: { ok: true }, raw_last_line: null }
          if (params[0] === 'query') return { ok: true, data: { ok: true, self_user_id: fixture.current.account, schema_version: 1 }, raw_last_line: null }
          if (params[0] === 'directory') {
            const request = JSON.parse(options?.stdin ?? '{}') as { mode: string; limit?: number; conversation_ids?: string[] }
            if (request.mode === 'list') {
              assert.equal(request.limit, 30, 'a large requested page is bounded before local query')
              return { ok: true, data: { ok: true, self_user_id: fixture.current.account,
                conversations: ids.slice(0, 30).map(conversation_id => ({ conversation_id,
                  name: '同名'.repeat(64), kind: 'group', peer_platform: 'unknown', same_name: true })), next_after: ids[29] }, raw_last_line: null }
            }
            return { ok: true, data: { ok: true, self_user_id: fixture.current.account,
              resolved_targets: request.conversation_ids!.map(conversation_id => ({ conversation_id,
                name: '同名'.repeat(64), kind: 'group', peer_platform: 'unknown' })) }, raw_last_line: null }
          }
          assert.equal(params[0], 'sessions')
          return { ok: true, data: { ok: true, selected_sessions: ids,
            session_details: ids.map(conversation_id => ({ conversation_id, name: '空目标', kind: 'group', peer_platform: 'unknown' })),
            sessions: [], pending_sessions: [], failed: [], baseline: true, history_truncated: false, has_more: false,
            state: { after_id: 0, anchor: '', retry: {}, unresolved: {}, context_after: null, start_fence: 0 } }, raw_last_line: null }
        },
      }
      const op = createWecomReadSessionsOperation(deps)
      const first = await op.execute({ sessions, limit: 50 } satisfies SessionReadArgs, context())
      assert.equal(first.success, true, first.message)
      assert.deepEqual((first.data.resolved_targets as { session: string }[]).map(row => row.session), sessions)
      assert.ok(Buffer.byteLength(JSON.stringify(first.data), 'utf8') <= 16 * 1024 - 512)
      const cursor = JSON.parse(Buffer.from((first.data.cursor as string).slice(4), 'base64url').toString('utf8'))
      assert.equal(cursor.targets, null, 'stable IDs are reconstructed from cursor.scope')
      assert.equal(cursor.requested, null, 'cursor must not repeat all 50 original locators')
      const next = await op.execute({ cursor: first.data.cursor as string, limit: 50 }, context())
      assert.equal(next.success, true, next.message)
      assert.equal((next.data.resolved_targets as unknown[]).length, 50)
      const directory = createWecomSessionDirectoryOperation(deps)
      const page = await directory.execute({ mode: 'list', limit: 50 }, context())
      assert.equal(page.success, true, page.message)
      assert.equal((page.data.conversations as unknown[]).length, 30)
      assert.ok(Buffer.byteLength(JSON.stringify(page.data), 'utf8') <= 64 * 1024)
      const resolved = await directory.execute({ mode: 'resolve', sessions }, context())
      assert.equal(resolved.success, true, resolved.message)
      assert.equal((resolved.data.resolved_targets as unknown[]).length, 50)
      assert.ok(Buffer.byteLength(JSON.stringify(resolved.data), 'utf8') <= 64 * 1024)
    } finally { rmSync(base, { recursive: true, force: true }) }
  })
