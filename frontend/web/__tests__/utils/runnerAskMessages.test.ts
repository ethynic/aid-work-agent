import { describe, expect, it } from 'vitest'
import { historyMessages, mergeRunnerMessages, runnerMessages } from '@/utils/runnerMessages'
import { runnerView } from '../mocks/runnerView'
import type { AskForm } from '@/types'

const form: AskForm = {
  form_id: 'frm_1', wait_id: 'wait-1', title: '归档范围', intro: '说明',
  allow_skip: true, expires_at: null,
  questions: [
    { id: 'q1', tag: '归档范围', prompt: '哪些任务归档？', type: 'multi', required: true,
      options: [{ key: 'q1o1', label: '任务一' }, { key: 'q1o2', label: '任务二' }] },
  ],
}

const waitingForm = {
  kind: 'clarification', wait_id: 'wait-1', target_execution_id: 'exec-1',
  question: '需要澄清', form,
}
const questions = [{ message_id: 'wait-1:assistant', wait_id: 'wait-1', text: '需要澄清', created_at: '2026-10-14T10:00:00Z' }]

const jobOptions = [{ key: 'job-a', label: '岗位甲' }, { key: 'job-b', label: '岗位乙' }]
const legacyOptions = { quickOptions: jobOptions }
const jobProgress = [
  { type: 'tool_start', toolName: 'boss_jobs_list', toolCallId: 'jobs-1' },
  { type: 'tool_result', toolName: 'boss_jobs_list', toolCallId: 'jobs-1', success: true,
    result: { success: true, data: { options: jobOptions } } },
]

describe('structured ask projections (WP4 runnerMessages)', () => {
  it.each([true, false])('job progress cannot recreate shortcuts alongside a separate question message (old snapshot: %s)', old => {
    const row = runnerView({ status: 'waiting', snapshot: {
      waiting: waitingForm, clarificationQuestions: questions, progressMessages: jobProgress,
      ...(old ? legacyOptions : {}),
    } })
    const before = JSON.stringify(row)
    const projected = mergeRunnerMessages([], [row])
    const question = projected.find(item => item.questionCard)!
    const assistant = projected.find(item => item.messageId === 'runner-A:assistant')!
    expect(question.messageId).not.toBe(assistant.messageId)
    expect(question.questionCard?.form).toEqual(form)
    expect(assistant.progressMessages?.filter(item => item.type === 'tool_result')).toHaveLength(1)
    expect(projected.every(item => !('quickOptions' in item))).toBe(true)
    expect(mergeRunnerMessages(projected, [row]).every(item => !('quickOptions' in item))).toBe(true)
    expect(JSON.stringify(row)).toBe(before)
  })

  it('completed reload keeps the answered card and removes old job shortcuts from finalized tool facts', () => {
    const row = runnerView({ status: 'completed', result: { status: 'completed', output: '已选择岗位',
      assistant_metadata: { progressMessages: jobProgress, ...legacyOptions } }, snapshot: {
      clarificationQuestions: questions,
      supplementalInputs: [{ control_id: 'answer', client_request_id: 'key', message_id: 'answer:user',
        wait_id: 'wait-1', text: '选择岗位甲', accepted_at: '2026-10-14T10:01:00Z', form,
        answers: [{ question_id: 'q1', selected_keys: ['q1o1'] }] }],
    } })
    const projected = runnerMessages(row)
    expect(projected.find(item => item.answeredForm)?.answeredForm?.terminal).toBe(true)
    expect(projected.every(item => !('quickOptions' in item))).toBe(true)
  })

  it('history-only loading ignores retired options even when tool source is absent', () => {
    const progress = jobProgress.map(event => event.type === 'tool_result' ? { ...event, toolName: undefined } : event)
    const projected = historyMessages([{ message_id: 'persisted', session_id: 'A', role: 'assistant',
      content: '历史回复', created_at: '2026-10-14T10:00:00Z', metadata: {
        runner_id: 'runner-A', quickOptions: jobOptions, progressMessages: progress,
      } }])
    expect(projected[0]).not.toHaveProperty('quickOptions')
    expect(projected[0].progressMessages).toHaveLength(2)
  })

  it.each(['present_options', 'future_tool', undefined])('no tool can restore the retired buttons (%s)', toolName => {
    const explicit = { type: 'tool_result', toolName, toolCallId: 'choices-1', success: true,
      result: { success: true, question: '请选择', data: { options: jobOptions } } }
    const row = runnerView({ status: 'waiting', snapshot: { waiting: waitingForm, clarificationQuestions: questions,
      ...legacyOptions, progressMessages: [...jobProgress, explicit] } })
    expect(runnerMessages(row).every(item => !('quickOptions' in item))).toBe(true)
  })

  it('snapshot choices with no tool facts are also retired', () => {
    const row = runnerView({ status: 'waiting', snapshot: { waiting: waitingForm, clarificationQuestions: questions,
      ...legacyOptions } })
    expect(runnerMessages(row).every(item => !('quickOptions' in item))).toBe(true)
  })

  it('binds the question message to its own runner/wait/execution identities with the live form', () => {
    const row = runnerView({ status: 'waiting', snapshot: { waiting: waitingForm, clarificationQuestions: questions } })
    const projected = runnerMessages(row)
    const question = projected.find(item => item.messageId === 'wait-1:assistant')
    expect(question?.questionCard).toEqual({
      runnerId: 'runner-A', waitId: 'wait-1', targetExecutionId: 'exec-1', form, terminal: false,
    })
  })

  it('unwraps nested child clarification waits before binding the card', () => {
    const row = runnerView({ status: 'waiting', snapshot: {
      waiting: { kind: 'child_wait', child_wait: waitingForm }, clarificationQuestions: questions } })
    expect(runnerMessages(row).find(item => item.messageId === 'wait-1:assistant')?.questionCard?.waitId).toBe('wait-1')
  })

  it('attaches the read-only answered card from supplementalInputs form/answers projection', () => {
    const answers = [{ question_id: 'q1', selected_keys: ['q1o1'], text: '自定义说明' }]
    const row = runnerView({ status: 'running', view_revision: 2, snapshot: {
      waiting: waitingForm, clarificationQuestions: questions,
      supplementalInputs: [{ control_id: 'control-1', client_request_id: 'key-1', message_id: 'control-1:user',
        text: 'Q1【归档范围】任务一；自定义：自定义说明', wait_id: 'wait-1', accepted_at: '2026-10-14T10:01:00Z',
        form, answers }] } })
    const answered = runnerMessages(row).find(item => item.messageId === 'control-1:user')
    expect(answered?.answeredForm).toEqual({
      controlId: 'control-1', clientRequestId: 'key-1', acceptedAt: '2026-10-14T10:01:00Z',
      form, answers, terminal: false,
    })
    expect(answered?.content).toBe('Q1【归档范围】任务一；自定义：自定义说明')
  })

  it('plain supplemental inputs and formless waits keep the legacy projection shape', () => {
    const row = runnerView({ status: 'waiting', snapshot: {
      waiting: { kind: 'clarification', wait_id: 'wait-legacy', target_execution_id: 'exec-1', question: '旧 clarify' },
      clarificationQuestions: [{ message_id: 'wait-legacy:assistant', wait_id: 'wait-legacy', text: '旧 clarify', created_at: '2026-10-14T10:00:00Z' }],
      supplementalInputs: [{ control_id: 'control-2', client_request_id: 'key-2', message_id: 'control-2:user',
        text: '文本回答', wait_id: 'wait-legacy', accepted_at: '2026-10-14T10:01:00Z' }] } })
    const projected = runnerMessages(row)
    expect(projected.find(item => item.messageId === 'wait-legacy:assistant')?.questionCard).toBeUndefined()
    expect(projected.find(item => item.messageId === 'control-2:user')?.answeredForm).toBeUndefined()
    expect(projected.find(item => item.messageId === 'control-2:user')?.content).toBe('文本回答')
  })

  it('terminal runners drop the live card; their answers project as applied read-only cards', () => {
    const waiting = runnerView({ status: 'cancelled', snapshot: { waiting: waitingForm, clarificationQuestions: questions } })
    expect(runnerMessages(waiting).find(item => item.messageId === 'wait-1:assistant')?.questionCard).toBeUndefined()

    const answered = runnerView({ status: 'completed', finished_at: '2026-10-14T11:00:00Z',
      result: { status: 'completed', output: 'done' }, snapshot: {
        clarificationQuestions: questions,
        supplementalInputs: [{ control_id: 'control-1', client_request_id: 'key-1', message_id: 'control-1:user',
          text: 'Q1【归档范围】任务一', wait_id: 'wait-1', accepted_at: '2026-10-14T10:01:00Z', form,
          answers: [{ question_id: 'q1', selected_keys: ['q1o1'], text: null }] }] } })
    expect(runnerMessages(answered).find(item => item.messageId === 'control-1:user')?.answeredForm?.terminal).toBe(true)
  })

  it('unanswered cancelled tasks keep the question text projection without any form replay', () => {
    const row = runnerView({ status: 'cancelled', snapshot: { clarificationQuestions: questions } })
    const projected = runnerMessages(row)
    expect(projected.map(item => item.messageId)).toContain('wait-1:assistant')
    expect(projected.some(item => item.questionCard || item.answeredForm)).toBe(false)
  })
})
