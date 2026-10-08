import { describe, expect, it } from 'vitest'
import { historyMessages, mergeRunnerMessages, runnerMessages } from '@/utils/runnerMessages'
import { runnerView } from '../mocks/runnerView'

describe('Runner display merges durable facts with existing messages', () => {
  it('queue order preserves complete turns even when second accepted time is earlier than first assistant finish', () => {
    const first = runnerView({ status: 'completed', finished_at: '2026-10-01T12:00:10Z', result: { status: 'completed', output: 'first result' } })
    const second = runnerView({ runner_id: 'runner-B', client_request_id: 'request-B', queue_order: 2,
      accepted_at: '2026-10-01T12:00:02Z', snapshot: { input: { message_id: 'runner-B:user', text: 'second input', attachments: [] } } })
    const messages = mergeRunnerMessages([], [second, first])
    expect(messages.map(item => [item.runnerId, item.role])).toEqual([
      ['runner-A', 'user'], ['runner-A', 'assistant'], ['runner-B', 'user'], ['runner-B', 'assistant']])
    expect(messages[2].timestamp).toBe(Date.parse(second.accepted_at))
    expect(messages[2].timestamp).toBeLessThan(messages[1].timestamp!)
  })

  it('history plus recent/active copies replace optimistic and committed copies exactly once without losing physical message IDs', () => {
    const history = historyMessages([
      { message_id: 'runner-A:user', session_id: 'A', role: 'user', content: 'original input', created_at: '2026-10-01T12:00:05Z',
        metadata: { runner_id: 'runner-A', runner_queue_order: 1, accepted_at: '2026-10-01T12:00:00Z' } },
      { message_id: 'runner-A:message:1', session_id: 'A', role: 'assistant', content: 'persisted result', created_at: '2026-10-01T12:00:05Z', metadata: { runner_id: 'runner-A', runner_queue_order: 1 } },
    ])
    const row = runnerView({ status: 'completed', result: { status: 'completed', output: 'cumulative complete result' } })
    const merged = mergeRunnerMessages([...history, { role: 'user', content: 'optimistic', timestamp: 1, clientRequestId: 'request-B' }],
      [row, row, runnerView({ runner_id: 'runner-B', client_request_id: 'request-B', queue_order: 2 })])
    expect(merged).toHaveLength(4)
    expect(merged[0].timestamp).toBe(Date.parse(row.accepted_at))
    expect(merged[1].messageId).toBe('runner-A:message:1')
    expect(merged[1].content).toBe('cumulative complete result')
  })

  it('queued cancellation keeps submitted input and a visible stopped assistant without committed history', () => {
    const projected = runnerMessages(runnerView({ status: 'cancelled', result: { status: 'cancelled', output: '' } }))
    expect(projected[0].content).toBe('original input')
    expect(projected[0].messageId).toBe('runner-A:user')
    expect(projected[1].cancelled).toBe(true)
    expect(projected[1].progressMessages?.some(item => item.content.includes('已停止'))).toBe(true)
  })

  it('cumulative tool facts keep tool-call identity, masking, downloads, options, images and verbose details', () => {
    const row = runnerView({ snapshot: { output: 'answer', images: [{ file_id: 'img', download_url: '/api/files/img/download', display_name: 'Fixture image', mime_type: 'image/png', size_bytes: 42, source: 'tool_generated', usage: 'inline', placement: 'before_text' }], imagesPlacement: 'before_text',
      verboseMessages: [{ eventId: 'verbose-1', data: 'working', source: 'policy', timestamp: 1 }],
      progressMessages: [{ type: 'tool_start', toolName: 'future_tool', displayName: 'Future tool', toolCallId: 'call-1', toolArgs: { password: '***' } },
        { type: 'tool_result', toolName: 'future_tool', toolCallId: 'call-1', success: true, result: { success: true, file_id: 'file', file_name: 'report.txt',
          data: { options: [{ key: 'one', label: 'One' }, { key: 'two', label: 'Two' }] } } }] } })
    const projected = runnerMessages(row)[1]
    expect(projected.progressMessages?.map(item => item.toolCallId)).toEqual(['call-1', 'call-1'])
    expect(projected.progressMessages?.[0].toolArgs).toEqual({ password: '***' })
    expect(projected.downloadableFiles?.map(item => item.file_id)).toEqual(['file'])
    expect(projected.quickOptions?.map(item => item.key)).toEqual(['one', 'two'])
    expect(projected.images?.[0].placement).toBe('before_text')
    expect(projected.images?.[0].download_url).toBe('/api/files/img/download')
    expect(projected.images?.[0].display_name).toBe('Fixture image')
    expect(projected.verboseMessages?.[0].eventId).toBe('verbose-1')
    expect(mergeRunnerMessages(mergeRunnerMessages([], [row]), [row])[1].progressMessages).toHaveLength(2)
  })
})
