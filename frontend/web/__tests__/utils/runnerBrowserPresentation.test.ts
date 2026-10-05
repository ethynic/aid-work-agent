import { describe, expect, it } from 'vitest'
import { historyMessages, mergeRunnerMessages, runnerMessages } from '@/utils/runnerMessages'
import type { BrowserHumanAssistance } from '@/types'
import { runnerView } from '../mocks/runnerView'

const card = (aid: string, state: BrowserHumanAssistance['state']): BrowserHumanAssistance => ({
  assistance_id: aid, run_id: 'native-run', continuation_id: '', reason_code: 'PAGE_VERIFICATION',
  surface: 'server_web', title: '请完成页面验证', steps: [], completion_mode: 'confirm_only',
  completion_status: 'waiting', expires_at: '2099-01-01T00:00:00Z', state, view_available: false,
})

describe('Runner Browser facts replace persisted display copies', () => {
  it('current second wait and terminal card replace old history/result metadata without appending a turn', () => {
    const old = card('first-wait', 'controlling')
    const history = historyMessages([{ message_id: 'physical-assistant', role: 'assistant', content: 'old output',
      session_id: 'A', created_at: '2026-10-01T12:00:05Z',
      metadata: { runner_id: 'runner-A', runner_queue_order: 1, browserAssistance: old } }])
    const row = runnerView({ status: 'completed', view_revision: 8,
      snapshot: { browserAssistance: card('second-wait', 'cancelled') },
      result: { status: 'completed', output: 'final output', assistant_metadata: { browserAssistance: old } } })
    const once = mergeRunnerMessages(history, [row])
    const twice = mergeRunnerMessages(once, [row, row])
    expect(twice).toHaveLength(2)
    expect(twice[1].messageId).toBe('physical-assistant')
    expect(twice[1].content).toBe('final output')
    expect(twice[1].browserAssistance).toEqual(row.snapshot.browserAssistance)
    expect(twice[1].runnerId).toBe('runner-A')
  })

  it('nested child verification remains visible as an ordinary message without claiming terminal cancellation', () => {
    const row = runnerView({ status: 'waiting', cancel_requested: true, snapshot: {
      browserAssistance: { ...card('owned-wait', 'controlling'), completion_status: 'verification_required' },
      waiting: { kind: 'child_wait', child_wait: { kind: 'child_wait', child_wait: {
        kind: 'verification', question: '原浏览器操作需要核对，请勿重复执行。' } } },
    } })
    const message = runnerMessages(row)[1]
    expect(message.waitingNotice).toBe('原浏览器操作需要核对，请勿重复执行。')
    expect(message.cancelled).toBe(false)
    expect(message.progressMessages?.some(event => event.content.includes('已停止'))).toBe(false)
    expect(message.browserAssistance?.completion_status).toBe('verification_required')
  })
})
