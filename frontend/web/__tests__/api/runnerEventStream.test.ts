/** Prepared pure byte-decoder contracts. No mutable product import/run yet. */
import { describe, expect, it, vi } from 'vitest'
import { RunnerEventDecoder, RunnerEventProtocolError } from '@/api/runnerEventStream'

const encode = (value: string) => new TextEncoder().encode(value)
const state = () => ({ runner_id: 'runner-通知', version: 1, attempt: 2,
  view_revision: 3, control_revision: 1, status: 'running', settlement_status: 'pending' })
const event = (overrides: Record<string, unknown> = {}) => ({
  ...state(), seq: 1, kind: 'revision_changed', invalidate: true, ...overrides })
const wire = (value: unknown, id = '1', kind = 'revision_changed') =>
  'id: ' + id + '\nevent: ' + kind + '\ndata: ' + JSON.stringify(value) + '\n\n'

describe('Runner notification decoder retains finite metadata, never message body', () => {
  it('decodes actual UTF8 byte splits, CRLF split, BOM, comments and multi-data once in order', () => {
    const received = vi.fn()
    const decoder = new RunnerEventDecoder('runner-通知', received)
    const text = '\uFEFF: heartbeat\r\n\r\nid: 1\r\nevent: revision_changed\r\n'
      + 'data: {\r\ndata: ' + JSON.stringify(event()).slice(1, -1) + '\r\ndata: }\r\n\r\n'
    for (const byte of encode(text)) decoder.push(new Uint8Array([byte]))
    decoder.finish()
    expect(received).toHaveBeenCalledOnce()
    expect(received).toHaveBeenCalledWith({ type: 'event', seq: 1, kind: 'revision_changed', state: state() })
    expect(Object.keys(received.mock.calls[0][0]).sort()).toEqual(['kind', 'seq', 'state', 'type'])
  })

  it('accepts historical head0 reset as a typed query hint without synthesized output', () => {
    const received = vi.fn()
    const decoder = new RunnerEventDecoder('runner-通知', received)
    decoder.push(encode(wire({ reset: true, query_required: true, head: 0, floor: 0,
      last_seq: 0, state: { ...state(), status: 'completed', settlement_status: 'settled' } }, '0', 'reset')))
    decoder.finish()
    expect(received).toHaveBeenCalledWith({ type: 'reset', seq: 0, floor: 0,
      state: { ...state(), status: 'completed', settlement_status: 'settled' } })
  })

  it.each([
    { label: 'foreign owner', value: event({ runner_id: 'foreign' }) },
    { label: 'boolean count', value: event({ attempt: true }) },
    { label: 'unsafe integer', value: event({ view_revision: Number.MAX_SAFE_INTEGER + 1 }) },
    { label: 'numeric invalidate', value: event({ invalidate: 1 }) },
    { label: 'private cumulative body', value: event({ output: 'fixture cumulative text' }) },
    { label: 'mismatched event sequence', value: event({ seq: 2 }) },
    { label: 'invalid settlement', value: event({ settlement_status: 'unknown' }) },
  ])('rejects $label before any metadata callback', ({ value }) => {
    const received = vi.fn()
    expect(() => new RunnerEventDecoder('runner-通知', received).push(encode(wire(value))))
      .toThrow(RunnerEventProtocolError)
    expect(received).not.toHaveBeenCalled()
  })

  it('rejects reset floor beyond head and unsupported fields or event names', () => {
    for (const input of [
      wire({ reset: true, query_required: true, head: 1, floor: 2, last_seq: 1, state: state() }, '1', 'reset'),
      wire(event(), '1', 'unknown-event'),
      'retry: 1000\n\n',
      'id: 1\nid: 1\nevent: revision_changed\ndata: ' + JSON.stringify(event()) + '\n\n',
    ]) {
      const received = vi.fn()
      expect(() => new RunnerEventDecoder('runner-通知', received).push(encode(input))).toThrow(RunnerEventProtocolError)
      expect(received).not.toHaveBeenCalled()
    }
  })

  it('rejects invalid UTF8, oversized comment frame and unfinished EOF, without a partial callback', () => {
    const received = vi.fn()
    const invalid = new RunnerEventDecoder('runner-通知', received)
    expect(() => invalid.push(new Uint8Array([0xff, 10]))).toThrow(RunnerEventProtocolError)
    expect(() => new RunnerEventDecoder('runner-通知', received).push(encode(':' + 'x'.repeat(65536))))
      .toThrow(RunnerEventProtocolError)
    const partial = new RunnerEventDecoder('runner-通知', received)
    partial.push(encode(wire(event()).slice(0, -1)))
    expect(() => partial.finish()).toThrow(RunnerEventProtocolError)
    expect(received).not.toHaveBeenCalled()
  })

  it('rejects one over64KiB raw chunk even when it contains many individually small valid frames', () => {
    const received = vi.fn()
    const tiny = wire(event())
    const oversized = encode(tiny.repeat(Math.ceil(65537 / encode(tiny).byteLength)))
    expect(oversized.byteLength).toBeGreaterThan(65536)
    expect(() => new RunnerEventDecoder('runner-通知', received).push(oversized)).toThrow(RunnerEventProtocolError)
    expect(received).not.toHaveBeenCalled()
  })
})
