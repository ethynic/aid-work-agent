import { RunnerEventDecoder, RunnerEventProtocolError, RUNNER_EVENT_FRAME_BYTES, type RunnerEventFrame, type RunnerEventStatus } from './runnerEventStream'
import type { UploadedFile } from './agent'
import type { BrowserHumanAssistance, DownloadableFile, ImageRef, QuickOption, VerboseMessage } from '@/types'

export type RunnerStatus = RunnerEventStatus
export interface RunnerWait { kind: string; wait_id?: string; target_execution_id?: string; question?: string; child_wait?: RunnerWait }
export interface RunnerSupplementalInput { control_id: string; client_request_id: string; message_id: string; text: string; wait_id?: string | null; accepted_at: string; attachments?: UploadedFile[] }
export interface RunnerControlIntent { client_request_id: string; action: 'pause' | 'resume' | 'reply'; target_execution_id?: string; wait_id?: string; answer?: string; attachments?: UploadedFile[] }
export interface RunnerControlView { control_id: string; runner_id: string; client_request_id: string;
  action: 'pause' | 'resume' | 'reply'; status: 'accepted' | 'claimed' | 'consumed' | 'rejected'; error_code?: string | null }
export interface RunnerPresentation {
  progressMessages?: Record<string, unknown>[]
  downloadableFiles?: DownloadableFile[]
  verboseMessages?: VerboseMessage[]
  quickOptions?: QuickOption[]
  browserAssistance?: BrowserHumanAssistance
  imagesPlacement?: ImageRef['placement']
}
export interface RunnerView {
  runner_id: string
  client_request_id: string
  queue_order: number
  session: { kind: string; session_id: string }
  status: RunnerStatus
  cancel_requested: boolean
  pause_requested?: boolean
  resume_requested?: boolean
  revision: number
  control_revision: number
  settlement_status: 'pending' | 'settled'
  view_revision: number
  accepted_at: string
  updated_at: string
  finished_at?: string | null
  snapshot: RunnerPresentation & { output?: string; images?: ImageRef[]; waiting?: RunnerWait;
    supplementalInputs?: RunnerSupplementalInput[];
    clarificationQuestions?: { message_id: string; wait_id: string; text: string; created_at: string }[];
    input?: { message_id: string; text: string; attachments: UploadedFile[] } }
  result?: { status: string; output?: string; images?: ImageRef[]; error_code?: string; assistant_metadata?: RunnerPresentation } | null
}
export interface RunnerSubmission {
  client_request_id: string
  message: string
  session_id: string
  files?: UploadedFile[]
  subagent?: string | null
  instance_id?: string | null
  video_params?: Record<string, unknown> | null
}
export interface RunnerCapabilities { web_enabled: boolean; observe_existing?: boolean; contract_version: number; transport: string; events_supported?: boolean }
export interface RunnerPage { runners: RunnerView[]; active_runners: RunnerView[]; has_more: boolean; next_cursor: string | null }
export class RunnerRequestError extends Error {
  constructor(public code: string, public status: number, message = code) { super(message) }
}

const apiBase = () => `${import.meta.env.VITE_API_BASE_URL || '/api'}/chat`
export const runnerObservationComplete = (row: RunnerView) => runnerTerminal(row.status) && row.settlement_status === 'settled'
export const runnerTerminal = (status: RunnerStatus) => ['completed', 'failed', 'cancelled'].includes(status)

/** Requests are observers. Aborting them never sends a control request. */
export class RunnerClient {
  private controllers = new Set<AbortController>()
  private eventsSupported = false
  private observers = new Map<string, { stopped: boolean; timer?: ReturnType<typeof setTimeout>;
    stop?: () => void; transportChanged?: () => void }>()

  private async request<T>(path: string, headers: Record<string, string>, body?: unknown): Promise<T> {
    const controller = new AbortController()
    this.controllers.add(controller)
    const timeout = setTimeout(() => controller.abort(), 15000)
    try {
      const response = await fetch(apiBase() + path, {
        method: body === undefined ? 'GET' : 'POST', signal: controller.signal,
        headers: { ...headers, ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
        body: body === undefined ? undefined : JSON.stringify(body),
      })
      const value = await response.json().catch(() => null)
      if (!response.ok) throw new RunnerRequestError(value?.code || 'RUNNER_REQUEST_FAILED', response.status, value?.error)
      if (!value || typeof value !== 'object') throw new RunnerRequestError('RUNNER_SERVICE_INVALID_RESPONSE', 502)
      return value as T
    } finally { clearTimeout(timeout); this.controllers.delete(controller) }
  }
  async capabilities(headers: Record<string, string>) {
    const value = await this.request<RunnerCapabilities>('/runners/capabilities', headers)
    if (typeof value.web_enabled !== 'boolean' || typeof value.observe_existing !== 'boolean'
        || value.contract_version !== 1 || value.transport !== 'runner_poll'
        || (value.events_supported !== undefined && typeof value.events_supported !== 'boolean')) {
      throw new RunnerRequestError('RUNNER_SERVICE_INVALID_RESPONSE', 502)
    }
    return value
  }
  installCapabilities(value: RunnerCapabilities) {
    if (value.contract_version !== 1 || value.transport !== 'runner_poll'
        || (value.events_supported !== undefined && typeof value.events_supported !== 'boolean'))
      throw new RunnerRequestError('RUNNER_SERVICE_INVALID_RESPONSE', 502)
    const enabled = value.events_supported === true
    if (enabled === this.eventsSupported) return
    this.eventsSupported = enabled
    for (const observer of this.observers.values()) observer.transportChanged?.()
  }
  async submit(body: RunnerSubmission, headers: Record<string, string>) {
    return (await this.request<{ success: boolean; runner: RunnerView }>('/runners', headers, body)).runner
  }
  async get(id: string, headers: Record<string, string>) {
    return (await this.request<{ success: boolean; runner: RunnerView }>('/runners/' + encodeURIComponent(id), headers)).runner
  }
  async cancel(id: string, headers: Record<string, string>) {
    return (await this.request<{ success: boolean; runner: RunnerView }>('/runners/' + encodeURIComponent(id) + '/cancel', headers, {})).runner
  }
  async control(id: string, body: RunnerControlIntent, headers: Record<string, string>) {
    const result = await this.request<{ success: boolean; runner: RunnerView; control: RunnerControlView }>(
      '/runners/' + encodeURIComponent(id) + '/controls',headers,body)
    this.validateControl(result, id)
    if (result.control.client_request_id !== body.client_request_id || result.control.action !== body.action
        || result.runner?.runner_id !== id) throw new RunnerRequestError('RUNNER_SERVICE_INVALID_RESPONSE',502)
    return result
  }
  private validateControl(result: { success: boolean; control: RunnerControlView }, runnerId: string) {
    const control = result.control
    if (result.success !== true || !control || control.runner_id !== runnerId
        || typeof control.control_id !== 'string' || !control.control_id
        || typeof control.client_request_id !== 'string' || !control.client_request_id
        || !['pause','resume','reply'].includes(control.action)
        || !['accepted','claimed','consumed','rejected'].includes(control.status)
        || (control.error_code != null && typeof control.error_code !== 'string')) {
      throw new RunnerRequestError('RUNNER_SERVICE_INVALID_RESPONSE',502)
    }
  }
  async getControl(runnerId: string, controlId: string, headers: Record<string,string>) {
    const result = await this.request<{ success: boolean; control: RunnerControlView }>(
      '/runners/' + encodeURIComponent(runnerId) + '/controls/' + encodeURIComponent(controlId),headers)
    this.validateControl(result,runnerId)
    if (result.control.control_id !== controlId) throw new RunnerRequestError('RUNNER_SERVICE_INVALID_RESPONSE',502)
    return result.control
  }
  observeControl(runnerId: string, controlId: string, headers: Record<string,string>,
                 onView: (control: RunnerControlView) => void, onError: (error: Error) => void) {
    const id = 'control:'+controlId
    if (this.observers.has(id)) return
    const observer: { stopped: boolean; timer?: ReturnType<typeof setTimeout> } = { stopped:false }
    this.observers.set(id,observer)
    let failures = 0
    const poll = async () => {
      try {
        const control = await this.getControl(runnerId,controlId,headers)
        if (observer.stopped) return
        failures = 0
        onView(control)
        if (['consumed','rejected'].includes(control.status)) { this.observers.delete(id); return }
      } catch (issue) {
        if (observer.stopped) return
        failures++
        onError(issue as Error)
        if (issue instanceof RunnerRequestError && issue.status===401) { this.observers.delete(id); return }
      }
      const interval = failures ? Math.min(30000,1000*2**Math.min(failures,5)) : document.hidden ? 5000 : 1000
      if (!observer.stopped) observer.timer = setTimeout(poll,interval)
    }
    void poll()
  }
  list(sid: string, headers: Record<string, string>, before?: string) {
    return this.request<RunnerPage>('/sessions/' + encodeURIComponent(sid) + '/runners' + (before ? '?before_runner_id=' + encodeURIComponent(before) : ''), headers)
  }
  observe(id: string, headers: Record<string, string>, onView: (view: RunnerView) => void, onError: (error: Error) => void) {
    if (this.observers.has(id)) return
    const observer: { stopped: boolean; timer?: ReturnType<typeof setTimeout>;
      stop?: () => void; transportChanged?: () => void } = { stopped: false }
    this.observers.set(id, observer)
    let cursor = 0
    let wireCursor = 0
    let hint: RunnerEventFrame | undefined
    let lastView: RunnerView | undefined
    let inFlight = false
    let dirty = false
    let failures = 0
    let streamFailures = 0
    let healthy = false
    let readerController: AbortController | undefined
    let retryTimer: ReturnType<typeof setTimeout> | undefined
    const current = () => !observer.stopped && this.observers.get(id) === observer
    const stop = () => {
      observer.stopped = true
      clearTimeout(observer.timer)
      clearTimeout(retryTimer)
      readerController?.abort()
      if (this.observers.get(id) === observer) this.observers.delete(id)
    }
    observer.stop = stop

    const scheduleQuery = () => {
      clearTimeout(observer.timer)
      if (!current()) return
      const interval = failures ? Math.min(30000, 1000 * 2 ** Math.min(failures, 5))
        : hint ? 1000 : healthy ? (document.hidden ? 15000 : 5000) : (document.hidden ? 5000 : 1000)
      observer.timer = setTimeout(() => void refresh(), interval)
    }
    const refresh = async () => {
      if (!current()) return
      if (inFlight) { dirty = true; return }
      clearTimeout(observer.timer)
      inFlight = true
      try {
        do {
          dirty = false
          try {
            const row = await this.get(id, headers)
            if (!current()) return
            if (row.runner_id !== id || !Number.isSafeInteger(row.view_revision) || row.view_revision < 0
                || !Number.isSafeInteger(row.control_revision) || row.control_revision < 0
                || !['pending', 'settled'].includes(row.settlement_status))
              throw new RunnerRequestError('RUNNER_SERVICE_INVALID_RESPONSE', 502)
            failures = 0
            const target = hint
            const newer = !lastView || row.view_revision >= lastView.view_revision
              && row.control_revision >= lastView.control_revision
            const confirmed = !target || row.view_revision >= target.state.view_revision
              && row.control_revision >= target.state.control_revision
            if (newer && confirmed) {
              // Only an authoritative GET can acknowledge a notification/reset.
              // A reset may correct a future cursor; it never regresses the view.
              if (target) { cursor = target.seq; if (hint === target) hint = undefined }
              lastView = row
              onView(row)
              if (runnerObservationComplete(row)) { stop(); return }
            }
          } catch (issue) {
            if (!current()) return
            failures++
            onError(issue as Error)
            if (issue instanceof RunnerRequestError && issue.status === 401) { stop(); return }
            // Query errors use the original finite poll backoff, not an SSE
            // failure UI or a new submission/legacy transport.
            dirty = false
          }
        } while (dirty && current())
      } finally { inFlight = false; scheduleQuery() }
    }

    const connect = async () => {
      clearTimeout(retryTimer)
      if (!current() || !this.eventsSupported || readerController) return
      const controller = new AbortController()
      readerController = controller
      wireCursor = cursor
      const handshake = setTimeout(() => controller.abort(), 10000)
      let reader: ReadableStreamDefaultReader<Uint8Array> | undefined
      try {
        const response = await fetch(apiBase() + '/runners/' + encodeURIComponent(id) + '/events?after_seq=' + cursor, {
          headers: { ...headers }, signal: controller.signal, redirect: 'error',
        })
        clearTimeout(handshake)
        if (!current() || !this.eventsSupported || controller.signal.aborted) {
          // Even a late handshake must not retain a detached body reader.
          void response.body?.cancel().catch(() => {})
          return
        }
        if (!response.ok) throw new RunnerRequestError('RUNNER_REQUEST_FAILED', response.status)
        if (response.status !== 200 || response.headers.get('Content-Type')?.split(';', 1)[0].trim().toLowerCase() !== 'text/event-stream'
            || !response.body) throw new RunnerEventProtocolError()
        healthy = true
        scheduleQuery()
        const decoder = new RunnerEventDecoder(id, frame => {
          if (!current()) return
          if (frame.type === 'event') {
            // A valid replay is harmless; an unaccounted gap needs a query.
            if (frame.seq <= wireCursor) return
            if (frame.seq !== wireCursor + 1) throw new RunnerEventProtocolError()
          }
          wireCursor = frame.seq
          streamFailures = 0
          hint = { ...frame, state: { ...frame.state,
            view_revision: Math.max(frame.state.view_revision, hint?.state.view_revision || 0, lastView?.view_revision || 0),
            control_revision: Math.max(frame.state.control_revision, hint?.state.control_revision || 0, lastView?.control_revision || 0) } }
          void refresh()
        })
        reader = response.body.getReader()
        while (current() && this.eventsSupported) {
          let deadline: ReturnType<typeof setTimeout> | undefined
          let rejectRead: () => void = () => {}
          const interrupted = new Promise<never>((_resolve, reject) => {
            rejectRead = () => reject(new RunnerRequestError('RUNNER_STREAM_INTERRUPTED', 503))
            deadline = setTimeout(() => { rejectRead(); controller.abort() }, 30000)
          })
          controller.signal.addEventListener('abort', rejectRead, { once: true })
          try {
            if (controller.signal.aborted) rejectRead()
            const part = await Promise.race([reader.read(), interrupted])
            if (!current()) return
            if (part.done) { decoder.finish(); break }
            if (part.value.byteLength > RUNNER_EVENT_FRAME_BYTES) throw new RunnerEventProtocolError()
            decoder.push(part.value)
          } finally {
            clearTimeout(deadline)
            controller.signal.removeEventListener('abort', rejectRead)
          }
        }
      } catch (issue) {
        if (!current()) return
        if (issue instanceof RunnerEventProtocolError) hint = undefined
        if (issue instanceof RunnerRequestError && issue.status === 401) {
          onError(issue); stop(); return
        }
        // Notifications are an enhancement. A healthy authoritative query
        // makes transport EOF/errors invisible to the product UI.
      } finally {
        clearTimeout(handshake)
        healthy = false
        controller.abort()
        if (reader) {
          void reader.cancel().catch(() => {})
          try { reader.releaseLock() } catch { /* Pending reads are cancelled by the original reader. */ }
        }
        if (readerController === controller) readerController = undefined
        if (current()) {
          void refresh()
          if (this.eventsSupported) {
            streamFailures++
            retryTimer = setTimeout(() => void connect(), Math.min(30000, 1000 * 2 ** Math.min(streamFailures - 1, 5)))
          }
        }
      }
    }
    observer.transportChanged = () => {
      readerController?.abort()
      healthy = false
      clearTimeout(retryTimer)
      void refresh()
      if (this.eventsSupported) void connect()
    }
    void refresh()
    if (this.eventsSupported) void connect()
  }
  detach() {
    for (const observer of this.observers.values()) { observer.stop?.(); observer.stopped = true; clearTimeout(observer.timer) }
    this.observers.clear()
    for (const controller of this.controllers) controller.abort()
    this.controllers.clear()
  }
}
