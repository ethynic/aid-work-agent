import type { RunnerView } from '@/api/runner'

export function runnerView(overrides: Partial<RunnerView> = {}): RunnerView {
  const id = overrides.runner_id || 'runner-A'
  return { runner_id: 'runner-A', client_request_id: 'request-A', queue_order: 1,
    session: { kind: 'web', session_id: 'A' }, status: 'running', cancel_requested: false,
    revision: 1, view_revision: 1, control_revision: 0,
    settlement_status: ['completed', 'failed', 'cancelled'].includes(overrides.status || 'running') ? 'settled' : 'pending',
    accepted_at: '2026-10-01T12:00:00Z', updated_at: '2026-10-01T12:00:01Z',
    snapshot: { input: { message_id: id + ':user', text: 'original input', attachments: [] }, output: '' },
    ...overrides }
}
