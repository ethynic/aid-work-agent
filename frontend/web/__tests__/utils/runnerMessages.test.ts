import { describe, expect, it } from 'vitest'
import { historyMessages, mergeRunnerMessages, runnerMessages, visibleFailedMessages } from '@/utils/runnerMessages'
import { runnerView } from '../mocks/runnerView'

describe('Runner display merges durable facts with existing messages', () => {
  it.each(['running', 'failed', 'cancelled'] as const)('retry %s does not remove the failed source before success', status => {
    const source = runnerView({ status: 'failed' })
    const retry = runnerView({ runner_id: 'retry', client_request_id: 'retry-key', status, queue_order: 2 })
    const rows = [source, retry]
    const visible = visibleFailedMessages(mergeRunnerMessages([], rows), rows, { retry_sources: { 'retry-key': source.runner_id } })
    expect(visible.some(message => message.runnerId === source.runner_id && message.runnerFailure)).toBe(true)
  })

  it('successful linked retry cleans failed ancestors, while identical unrelated input and partial output remain', () => {
    const source = runnerView({ status: 'failed', result: { status: 'failed', output: '部分结果' } })
    const retry = runnerView({ runner_id: 'retry', client_request_id: 'retry-key', status: 'failed', queue_order: 2 })
    const success = runnerView({ runner_id: 'success', client_request_id: 'success-key', status: 'completed', queue_order: 3 })
    const unrelated = runnerView({ runner_id: 'unrelated', client_request_id: 'unrelated-key', status: 'failed', queue_order: 4 })
    const rows = [source, retry, success, unrelated]
    const visible = visibleFailedMessages(mergeRunnerMessages([], rows), rows,
      { retry_sources: { 'retry-key': source.runner_id, 'success-key': retry.runner_id } })
    expect(visible.filter(message => message.runnerFailure).map(message => message.runnerId)).toEqual(['unrelated'])
    expect(visible.some(message => message.content === '部分结果')).toBe(true)
  })

  it('manual hide cannot remove normal messages or partial artifacts and does not imply retry success', () => {
    const source = runnerView({ status: 'failed' })
    const retry = runnerView({ runner_id: 'retry', client_request_id: 'retry-key', status: 'failed', queue_order: 2,
      result: { status: 'failed', output: '保留文件', assistant_metadata: { downloadableFiles: [
        { file_id: 'f', file_name: 'f.txt', file_size: 1, download_url: '/f' }] } } })
    const running = runnerView({ runner_id: 'active', client_request_id: 'active-key', queue_order: 3 })
    const rows = [source, retry, running]
    const visible = visibleFailedMessages(mergeRunnerMessages([], rows), rows, {
      hidden_failed: { retry: true, active: true }, retry_sources: { 'retry-key': source.runner_id },
    })
    expect(visible.filter(message => message.runnerFailure).map(message => message.runnerId)).toEqual(['runner-A'])
    expect(visible.some(message => message.runnerId === 'active' && message.role === 'user')).toBe(true)
    expect(visible.find(message => message.content === '保留文件')?.downloadableFiles).toHaveLength(1)
  })
  it('failed model calls show failure on the user input and never produce an empty final assistant', () => {
    const first = runnerView({ status: 'failed', result: { status: 'failed', output: '', error_code: 'EXECUTION_FAILED' } })
    const second = runnerView({ ...first, runner_id: 'runner-B', client_request_id: 'request-B', queue_order: 2,
      snapshot: { input: { message_id: 'runner-B:user', text: '上海的', attachments: [] } } })
    const restored = historyMessages([{ message_id: 'runner-A:assistant', session_id: 'A', role: 'assistant',
      content: '', created_at: first.accepted_at, metadata: { runner_id: 'runner-A' } }])
    const messages = mergeRunnerMessages(restored, [first, second])
    expect(messages.map(message => message.role)).toEqual(['user', 'user'])
    expect(messages.every(message => message.runnerFailure?.message.includes('请稍后重试'))).toBe(true)
    expect(messages.some(message => message.content.includes('EXECUTION_FAILED'))).toBe(false)
  })

  it('failed tasks retain partial replies and downloadable artifacts instead of discarding real results', () => {
    const row = runnerView({ status: 'failed', result: { status: 'failed', output: '已完成部分分析' },
      snapshot: { downloadableFiles: [{ file_id: 'report', file_name: 'report.txt', file_size: 1, download_url: '/report' }] } })
    const messages = runnerMessages(row)
    expect(messages[0].runnerFailure).toBeDefined()
    expect(messages[1].content).toBe('已完成部分分析')
    expect(messages[1].downloadableFiles?.[0].file_id).toBe('report')
  })

  it('failure belongs to the latest supplemental input while clarification history remains visible', () => {
    const messages = runnerMessages(runnerView({ status: 'failed', snapshot: {
      input: { message_id: 'runner-A:user', text: '安排出行', attachments: [] },
      clarificationQuestions: [{ message_id: 'wait-1:assistant', wait_id: 'wait-1', text: '去哪里？', created_at: '2026-10-01T12:00:00Z' }],
      supplementalInputs: [{ control_id: 'c1', client_request_id: 'answer-1', message_id: 'answer-1:user', text: '上海',
        wait_id: 'wait-1', accepted_at: '2026-10-01T12:00:01Z' }],
    } }))
    expect(messages[0].runnerFailure).toBeUndefined()
    expect(messages[1].content).toBe('去哪里？')
    expect(messages[2].runnerFailure).toBeDefined()
    expect(messages).toHaveLength(3)
  })
  it('interrupted resume screening shows an explanation and preserves the last execution time after rejected resume', () => {
    const row = runnerView({ status: 'interrupted', updated_at: '2026-10-14T06:21:18Z',
      snapshot: { output: '', progressMessages: [{ type: 'progress', data: '本地读取已完成', timestamp: Date.parse('2026-10-14T06:21:17Z') }] } })
    const before = runnerMessages(row).slice(-1)[0]!
    const after = runnerMessages({ ...row, updated_at: '2026-10-14T06:26:17Z',
      snapshot: { ...row.snapshot, supplementalInputs: [{ control_id: 'ctrl', client_request_id: 'retry',
        message_id: 'retry:user', text: '继续筛选', accepted_at: '2026-10-14T06:26:17Z' }] } }).slice(-1)[0]!
    expect(before.waitingNotice).toContain('任务已中断')
    expect(before.waitingNotice).toContain('不能作为最终结果')
    expect(after.timestamp).toBe(before.timestamp)
    expect(after.content).toBe('')
    expect(after.progressMessages?.some(item => item.type === 'complete')).toBe(false)
  })

  it('old verification waits with no question still explain that uncertain effects are not replayed', () => {
    const row = runnerView({ status: 'waiting', snapshot: { waiting: { kind: 'verification' }, output: '' } })
    expect(runnerMessages(row).slice(-1)[0]?.waitingNotice).toContain('不会自动重复操作')
    expect(runnerMessages({ ...row, updated_at: '2026-10-14T06:26:17Z' }).slice(-1)[0]?.timestamp)
      .toBe(Date.parse(row.accepted_at))
    expect(runnerMessages({ ...row, snapshot: { waiting: { kind: 'verification', question: '核对原页面' } } }).slice(-1)[0]?.waitingNotice)
      .toBe('核对原页面')
    expect(runnerMessages({ ...row, status: 'completed', finished_at: '2026-10-14T06:27:17Z' }).slice(-1)[0]?.waitingNotice).toBeUndefined()
    expect(runnerMessages({ ...row, status: 'running', snapshot: { output: '' } }).slice(-1)[0]?.waitingNotice).toBeUndefined()
  })

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
