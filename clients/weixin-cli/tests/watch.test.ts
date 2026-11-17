import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import { watchCurrentConversation } from '../src/cli/commands/watch.js'
import { createNameOperations } from '../src/operations/nameOperations.js'
import { failResult, okResult } from '../src/operations/types.js'
import type { SessionObserverMessage, SessionObserverRequest } from '../src/platform/sessionObserver.js'

test('watch keeps its old watermark through a gap and emits only the count of newly confirmed messages', async () => {
  const controller = new AbortController()
  const privateBody = 'private chat text'
  const chatName = 'private chat title'
  const message = (): SessionObserverMessage => ({
    sender: 'peer',
    text: privateBody,
    local_message_id: 'm-' + randomUUID(),
    source_evidence_ref: 'private capture reference',
  })
  const historical = message()
  const newMessages = [message(), message()]
  const observed: SessionObserverRequest['watermark'][] = []
  const renderedEvents: string[] = []
  const logs: string[] = []
  let binding: string | undefined
  let acquireCount = 0
  let held = false

  await watchCurrentConversation(chatName, 10_000, controller.signal, {
    mutex: {
      async acquire() {
        assert.equal(held, false)
        if (++acquireCount === 2) return false
        held = true
        return true
      },
      async release() {
        assert.equal(held, true)
        held = false
      },
    },
    report: (line) => logs.push(line),
    emit: (event) => renderedEvents.push(JSON.stringify(event)),
    async wait(ms, signal) {
      assert.equal(ms, 10_000)
      assert.equal(signal, controller.signal)
      assert.equal(held, false, 'the watch must let other CLI operations run between polls')
    },
    observe: {
      async execute(args, ctx) {
        assert.equal(held, true)
        assert.equal(ctx.signal, controller.signal)
        assert.equal(args.target_name, chatName)
        binding ??= args.conversation_binding_id
        assert.equal(args.conversation_binding_id, binding)
        observed.push(args.watermark)
        ctx.progress({ stage: 'driver', message: privateBody })
        const round = observed.length
        if (round === 3) return failResult('round', 'UI_CHANGED', privateBody, 'none')
        if (round === 6) controller.abort()
        assert.ok(round <= 6, 'an aborted watch must never start another poll')
        const messages = round === 1 ? [historical] : round === 4 ? newMessages : round === 6 ? [message()] : []
        return okResult('round', privateBody, 'none', {
          observation_id: randomUUID(),
          conversation_binding_id: binding,
          binding_version: args.binding_version,
          account_identity_version: args.account_identity_version,
          observed_at: new Date().toISOString(),
          coverage: round === 2 ? 'gap' : 'complete_window',
          gap_reason: round === 2 ? 'alignment_broken' : null,
          window_fingerprint: round === 2 ? null : 'fp_window00' + round,
          ordered_messages: messages,
          screenshot: 'private screenshot',
        })
      },
    },
  })

  const baseline = { last_local_message_id: historical.local_message_id, window_fingerprint: 'fp_window001' }
  const newWatermark = { last_local_message_id: newMessages[1]!.local_message_id, window_fingerprint: 'fp_window004' }
  assert.deepEqual(observed, [null, baseline, baseline, baseline, newWatermark, {
    ...newWatermark, window_fingerprint: 'fp_window005',
  }])
  assert.deepEqual(renderedEvents, ['{"event":"new_messages","count":2}'])
  assert.equal(held, false)
  assert.ok(logs.some((line) => line.includes('存量消息不回放')))
  assert.ok(logs.some((line) => line.includes('alignment_broken')))
  assert.ok(logs.some((line) => line.includes('BUSY')))
  assert.ok(logs.some((line) => line.includes('UI_CHANGED')))
  assert.ok(!logs.join(' ').includes('private'), 'the terminal must not print observer diagnostics or message content')
})

test('watch uses the current-only observer even for the first baseline and releases the lock on stop', async () => {
  const controller = new AbortController()
  const requests: Record<string, unknown>[] = []
  const { observe } = createNameOperations({
    observeCurrentOnly: true,
    driver: async (request) => {
      requests.push(request)
      return {
        unchanged: false, title: 'exact name', native_title: 'exact name', exact_name: true, unique_match: true, complete: true,
        evidence_ref: 'dpapi:fixture', frame: 'a'.repeat(64), input_empty: true, objects: [],
      }
    },
  })
  let releaseCount = 0
  await watchCurrentConversation('exact name', 1000, controller.signal, {
    observe,
    mutex: {
      acquire: async () => true,
      release: async () => { releaseCount++ },
    },
    emit: () => assert.fail('empty baseline has no new messages'),
    report: (line) => {
      assert.match(line, /已建立会话水位/)
      controller.abort()
    },
    wait: async () => assert.fail('stop must cancel the interval between polls'),
  })
  assert.deepEqual(requests, [{ action: 'read', target_name: 'exact name', previous_frame: undefined, open: false }])
  assert.equal(releaseCount, 1)
})

test('watch rejects missing, duplicate, and overflowing options without starting a driver', () => {
  const cli = fileURLToPath(new URL('../src/cli/index.js', import.meta.url))
  for (const flags of [
    ['--target-name'],
    ['--target-name', 'first', '--target-name', 'second'],
    ['--target-name', 'chat', '--interval'],
    ['--target-name', 'chat', '--interval', '2147484'],
  ]) {
    const result = spawnSync(process.execPath, [cli, 'watch', ...flags], {
      encoding: 'utf8', windowsHide: true, timeout: 5000,
    })
    assert.equal(result.status, 2, result.stderr)
    assert.equal(result.stdout, '')
    assert.match(result.stderr, /用法.*watch --target-name/)
  }
})
