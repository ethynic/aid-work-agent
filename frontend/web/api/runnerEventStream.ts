/** Pure, bounded decoding of the Runner notification wire, never message text. */
export const RUNNER_EVENT_FRAME_BYTES = 65536
export const runnerEventStatuses = ['queued', 'running', 'waiting', 'paused', 'interrupted',
  'finalizing', 'completed', 'failed', 'cancelled'] as const
export type RunnerEventStatus = typeof runnerEventStatuses[number]
export interface RunnerEventState {
  runner_id: string
  version: 1
  attempt: number
  view_revision: number
  control_revision: number
  status: RunnerEventStatus
  settlement_status: 'pending' | 'settled'
}
export type RunnerEventKind = 'created' | 'revision_changed' | 'terminal' | 'settlement_changed'
export type RunnerEventFrame =
  | { type: 'event'; seq: number; kind: RunnerEventKind; state: RunnerEventState }
  | { type: 'reset'; seq: number; floor: number; state: RunnerEventState }
export class RunnerEventProtocolError extends Error {
  constructor() { super('RUNNER_EVENT_PROTOCOL_INVALID') }
}

const stateKeys = ['runner_id', 'version', 'attempt', 'view_revision', 'control_revision', 'status', 'settlement_status']
const kinds = ['created', 'revision_changed', 'terminal', 'settlement_changed']
const scalar = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0
const object = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value)
const keys = (value: Record<string, unknown>, expected: string[]) =>
  Object.keys(value).length === expected.length && expected.every(key => Object.prototype.hasOwnProperty.call(value, key))

function state(value: Record<string, unknown>, runnerId: string): RunnerEventState {
  if (value.runner_id !== runnerId || value.version !== 1 || !scalar(value.attempt)
      || !scalar(value.view_revision) || !scalar(value.control_revision)
      || !runnerEventStatuses.includes(value.status as RunnerEventStatus)
      || !['pending', 'settled'].includes(value.settlement_status as string)) throw new RunnerEventProtocolError()
  return { runner_id: runnerId, version: 1, attempt: value.attempt,
    view_revision: value.view_revision, control_revision: value.control_revision,
    status: value.status as RunnerEventStatus, settlement_status: value.settlement_status as 'pending' | 'settled' }
}

/** Retains at most one 64KiB wire frame, including split UTF8 and line endings. */
export class RunnerEventDecoder {
  private line: number[] = []
  private bytes = 0
  private afterCR = false
  private firstLine = true
  private kind?: string
  private id?: string
  private data: string[] = []
  constructor(private runnerId: string, private onFrame: (frame: RunnerEventFrame) => void) {}

  push(chunk: Uint8Array) {
    if (chunk.byteLength > RUNNER_EVENT_FRAME_BYTES) throw new RunnerEventProtocolError()
    for (const byte of chunk) {
      if (this.afterCR) {
        this.afterCR = false
        if (byte === 10) {
          if (++this.bytes > RUNNER_EVENT_FRAME_BYTES) throw new RunnerEventProtocolError()
          this.completeLine()
          continue
        }
        this.completeLine()
      }
      if (++this.bytes > RUNNER_EVENT_FRAME_BYTES) throw new RunnerEventProtocolError()
      if (byte === 13) this.afterCR = true
      else if (byte === 10) this.completeLine()
      else this.line.push(byte)
    }
  }

  finish() {
    if (this.afterCR) { this.afterCR = false; this.completeLine() }
    if (this.line.length || this.bytes || this.kind !== undefined || this.id !== undefined || this.data.length)
      throw new RunnerEventProtocolError()
  }

  private completeLine() {
    let text: string
    try { text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(new Uint8Array(this.line)) }
    catch { throw new RunnerEventProtocolError() }
    this.line = []
    if (this.firstLine) { this.firstLine = false; if (text.startsWith('\uFEFF')) text = text.slice(1) }
    if (!text) {
      if (this.data.length) this.dispatch()
      else if (this.kind !== undefined || this.id !== undefined) throw new RunnerEventProtocolError()
      this.kind = this.id = undefined
      this.data = []
      this.bytes = 0
      return
    }
    if (text.startsWith(':')) return
    const colon = text.indexOf(':')
    const field = colon < 0 ? text : text.slice(0, colon)
    let value = colon < 0 ? '' : text.slice(colon + 1)
    if (value.startsWith(' ')) value = value.slice(1)
    if (field === 'data') this.data.push(value)
    else if (field === 'event' && this.kind === undefined) this.kind = value
    else if (field === 'id' && this.id === undefined) this.id = value
    else throw new RunnerEventProtocolError()
  }

  private dispatch() {
    if (!this.id || !/^(0|[1-9][0-9]*)$/.test(this.id) || !scalar(Number(this.id))) throw new RunnerEventProtocolError()
    const seq = Number(this.id)
    let value: unknown
    try { value = JSON.parse(this.data.join('\n')) }
    catch { throw new RunnerEventProtocolError() }
    if (!object(value)) throw new RunnerEventProtocolError()
    if (this.kind === 'reset') {
      if (!keys(value, ['reset', 'query_required', 'head', 'floor', 'last_seq', 'state'])
          || value.reset !== true || value.query_required !== true || !scalar(value.head)
          || !scalar(value.floor) || value.floor > value.head || value.head !== seq
          || value.last_seq !== seq || !object(value.state) || !keys(value.state, stateKeys)) throw new RunnerEventProtocolError()
      this.onFrame({ type: 'reset', seq, floor: value.floor, state: state(value.state, this.runnerId) })
    } else {
      if (!kinds.includes(this.kind || '') || !keys(value, [...stateKeys, 'seq', 'kind', 'invalidate'])
          || seq === 0 || value.seq !== seq || value.kind !== this.kind || value.invalidate !== true) throw new RunnerEventProtocolError()
      this.onFrame({ type: 'event', seq, kind: this.kind as RunnerEventKind, state: state(value, this.runnerId) })
    }
  }
}
