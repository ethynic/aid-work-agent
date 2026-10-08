"""Transparent legacy Web SSE bridge over durable Runner acceptance.

Old already-opened pages still POST ``/api/chat/stream`` and Stop via
``/api/chat/{sid}/cancel``. When the caller is a fresh web principal and the
session is an actually owned ``chat_sessions`` row (the same predicate family
as the Web gateway), the request is bridged through the trusted Runner service:

* every bridged submit goes through durable acceptance, so the persistent
  session claim serializes execution and the legacy loop can no longer run in
  parallel beside an accepted Runner;
* the bridge is a strict read-only proxy of the public projection: it never
  writes chat history, records or fees, and a client disconnect only detaches
  observation (the worker keeps its accepted task);
* callers without a fresh login or without an owned web session keep the exact
  legacy authorization scope (anonymous memory sessions, D1/Runtime/CLI).

No ``src.api`` import lives in this module; the neutral subject primitives of
the auth service are the only identity dependency.
"""

import asyncio
import json
import time
import uuid
from urllib.parse import quote

from src.db.database import get_db_connection
from src.services.auth_service import AuthSubjectError, fresh_web_subject

from .contracts import AttachmentRef, RunnerError, RunnerSubmit, SessionRef, TERMINAL_STATUSES
from .web_client import RunnerServiceClient, close_event_transport

VIDEO_PARAM_KEYS = ('mode', 'duration_sec', 'ratio', 'resolution', 'card_count', 'prompt_model')
_LEGACY_RETRY_SECONDS = 0.5


def _fresh_login(authorization):
    """Fresh token row re-verification without any session ownership demand."""
    from src.config.settings import settings

    if not settings.agent_runner.web_enabled:
        return None
    try:
        return fresh_web_subject(authorization)
    except AuthSubjectError:
        return None


def bridged_web_subject(authorization, target_tenant, session_id, *, config=None):
    """Return the bridging subject, or None to keep the request's legacy scope.

    The decision is server-side only: a fresh token row re-verification plus an
    actually owned ``chat_sessions`` row. User-Agent or client self-reported
    headers are never consulted, and a disabled web switch keeps behaviour
    identical to today (no bridge, no interlock side effects).
    """
    from src.config.settings import settings

    config = config or settings.agent_runner
    if not config.web_enabled:
        return None
    try:
        user = fresh_web_subject(authorization)
    except AuthSubjectError:
        return None
    platform = user['role'] == 'platform_admin'
    tenant_id = (target_tenant or None) if platform else user['tenant_id']
    if (not platform and target_tenant and target_tenant != tenant_id) or (tenant_id is None and not platform):
        # Foreign tenant targeting keeps its original legacy scope; the bridge
        # never widens or rewrites it.
        return None
    with get_db_connection() as connection:
        cursor = connection.cursor()
        cursor.execute('''SELECT session_id FROM chat_sessions WHERE session_id=%s AND user_id=%s
                          AND tenant_id IS NOT DISTINCT FROM %s''',
                       (session_id, user['user_id'], tenant_id))
        if cursor.fetchone() is None:
            return None
    return {'user_id': user['user_id'], 'tenant_id': tenant_id}


def legacy_submit_intent(message, session_id, *, files=None, subagent=None,
                         instance_id=None, video_params=None):
    """One immutable Runner intent per legacy HTTP submit.

    Legacy payloads carry no stable request key, so each HTTP request mints a
    fresh ``legacy:<uuid4>`` submit key; identical retries are never merged by
    body hash and never silently dropped.
    """
    attachments = []
    for item in files or []:
        if not isinstance(item, dict):
            continue
        attachments.append(AttachmentRef(
            file_id=item.get('file_id') if isinstance(item.get('file_id'), str) else None,
            name=item.get('name') if isinstance(item.get('name'), str) else 'unknown',
            type=item.get('type') if item.get('type') in ('file', 'image') else 'file',
            mime_type=item.get('mime_type') if isinstance(item.get('mime_type'), str) else '',
            content=item.get('content') if isinstance(item.get('content'), str) else None,
            size=item.get('size') if type(item.get('size')) is int else None))
    request_data = {}
    if isinstance(video_params, dict):
        request_data['video_params'] = {key: video_params[key] for key in VIDEO_PARAM_KEYS
                                        if key in video_params}
    return RunnerSubmit(client_request_id='legacy:' + uuid.uuid4().hex, text=message,
                        session=SessionRef(kind='web', session_id=session_id),
                        attachments=attachments, profile_id=subagent or 'main',
                        routing_policy='explicit' if subagent else 'default_single',
                        instance_id=instance_id, request_data=request_data)


def bridge_error_payload(error):
    """Mirror the Web gateway error mapping the legacy page already renders."""
    if error.code == 'CREDIT_BLOCKED':
        return 403, {'success': False, 'code': 'NO_CREDIT', 'error_code': 'NO_CREDIT',
                     'error': '积分余额已耗尽，数字员工无法工作',
                     'details': '积分余额已耗尽，数字员工无法工作'}
    return error.status, {'success': False, 'code': error.code, 'error_code': error.code,
                          'error': error.code}


def _now_ms():
    return int(time.time() * 1000)


def _frame(event):
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _leaf_waiting(snapshot):
    waiting = snapshot.get('waiting') or {}
    while isinstance(waiting.get('child_wait'), dict):
        waiting = waiting['child_wait']
    return waiting if isinstance(waiting, dict) else {}


class _LegacyFrameState:
    """Monotone public-snapshot deltas rendered as legacy SSE events.

    The runner ledger only carries invalidate cursors, so every notification is
    followed by an authoritative GET and a state diff. The diff itself is the
    idempotency boundary: an unchanged or replayed projection emits nothing.
    """

    def __init__(self):
        self.output = ''
        self.progress = 0
        self.verbose = 0
        self.images = 0
        self.assistance = None
        self.clarification = None

    def project(self, row):
        frames = []
        snapshot = row.get('snapshot') or {}
        output = snapshot.get('output') if isinstance(snapshot.get('output'), str) else ''
        if output.startswith(self.output) and len(output) > len(self.output):
            frames.append({'type': 'response', 'data': output[len(self.output):]})
            self.output = output
        elif output and not output.startswith(self.output):
            # Output shrank or was replaced. Already-delivered text cannot be
            # retracted from the legacy append consumer, so the delivered prefix
            # stays and the base silently moves to the new projection; the turn
            # still closes with its real terminal frame below.
            self.output = output
        items = snapshot.get('progressMessages') or []
        for item in items[max(self.progress, 0):]:
            if isinstance(item, dict) and item.get('type'):
                frames.append({key: value for key, value in item.items() if key != 'content'})
        self.progress = max(self.progress, len(items))
        messages = snapshot.get('verboseMessages') or []
        for item in messages[max(self.verbose, 0):]:
            if isinstance(item, dict) and item.get('eventId'):
                frames.append({'type': 'verbose', **{key: item[key] for key in
                    ('eventId', 'data', 'source', 'timestamp') if key in item}})
        self.verbose = max(self.verbose, len(messages))
        images = snapshot.get('images') or []
        fresh = [item for item in images[max(self.images, 0):] if isinstance(item, dict)]
        if fresh:
            frames.append({'type': 'images', 'images': fresh,
                           'placement': snapshot.get('imagesPlacement') or 'after_text'})
        self.images = max(self.images, len(images))
        assistance = snapshot.get('browserAssistance')
        if (isinstance(assistance, dict) and assistance.get('assistance_id')
                and assistance.get('assistance_id') != self.assistance
                and assistance.get('completion_status') == 'pending'
                and assistance.get('state') in ('pending', 'controlling')):
            self.assistance = assistance['assistance_id']
            # Legacy semantics: the SSE turn ends once the human card is live;
            # the old card then follows its continuation stream on its own.
            frames.append({'type': 'browser_human_required', 'surface': 'server_web', **assistance})
            frames.append({'type': 'complete', 'timestamp': _now_ms()})
            return frames, True
        waiting = _leaf_waiting(snapshot)
        if waiting.get('kind') == 'clarification':
            marker = waiting.get('wait_id') or waiting.get('tool_call_id')
            if marker and marker != self.clarification:
                self.clarification = marker
                frames.append({'type': 'clarification', 'subagentName': '',
                               'question': waiting.get('question') or ''})
                # The legacy turn ends with the question; the old page sends the
                # answer as its next message. The Runner stays parked on its own
                # claim and any follow-up submit is serialized by that claim.
                frames.append({'type': 'complete', 'timestamp': _now_ms()})
                return frames, True
        if row['status'] in TERMINAL_STATUSES and row.get('settlement_status') == 'settled':
            result = row.get('result') or {}
            final_output = result.get('output') if isinstance(result.get('output'), str) else ''
            if final_output.startswith(self.output) and len(final_output) > len(self.output):
                frames.append({'type': 'response', 'data': final_output[len(self.output):]})
            if row['status'] == 'completed':
                frames.append({'type': 'complete', 'timestamp': _now_ms()})
            elif row['status'] == 'cancelled':
                frames.append({'type': 'cancelled', 'timestamp': _now_ms()})
            else:
                frames.append({'type': 'error', 'data': result.get('error_code') or 'RUNNER_EXECUTION_FAILED',
                               'timestamp': _now_ms()})
            return frames, True
        return frames, False


def _block_cursor(block):
    """Highest SSE id in one upstream notification block, if any."""
    cursor = None
    for line in block.split(b'\n'):
        if line.startswith(b'id:'):
            value = line[3:].strip()
            if value.isdigit():
                cursor = int(value)
    return cursor


async def _fetch_runner(client, authorization, tenant, runner_id):
    value = await client.request('GET', '/v1/runners/' + quote(runner_id, safe=''),
                                 authorization=authorization, tenant=tenant)
    return value['runner']


async def stream_legacy_frames(client, *, authorization, tenant, accepted, session_id):
    """Yield legacy SSE frames as a read-only projection of one accepted runner."""
    row = accepted['runner']
    runner_id = row['runner_id']
    state = _LegacyFrameState()
    yield _frame({'type': 'connected', 'session_id': session_id,
                  'agent_type': 'master' if row['profile_id'] == 'main' else 'standalone'})
    cursor = 0
    while True:
        try:
            row = await _fetch_runner(client, authorization, tenant, runner_id)
        except RunnerError as error:
            # The stream is already committed; close it with an explicit legacy
            # error frame instead of an unexplained transport end. The accepted
            # task itself keeps running under its worker.
            yield _frame({'type': 'error', 'data': error.code, 'timestamp': _now_ms()})
            return
        frames, ended = state.project(row)
        for event in frames:
            yield _frame(event)
        if ended:
            return
        try:
            response, upstream = await client.open_events(
                '/v1/runners/' + quote(runner_id, safe='') + '/events',
                authorization=authorization, tenant=tenant, after_seq=cursor)
        except RunnerError as error:
            # A transport failure never reselects a legacy loop. Report through
            # the legacy error frame; the worker still owns the accepted task.
            try:
                row = await _fetch_runner(client, authorization, tenant, runner_id)
                frames, ended = state.project(row)
                for event in frames:
                    yield _frame(event)
                if ended:
                    return
            except RunnerError:
                pass
            yield _frame({'type': 'error', 'data': error.code, 'timestamp': _now_ms()})
            return
        try:
            buffer = b''
            notified = False
            async for chunk in response.aiter_raw():
                buffer += chunk
                while b'\n\n' in buffer:
                    block, buffer = buffer.split(b'\n\n', 1)
                    seq = _block_cursor(block)
                    if seq is not None and seq > cursor:
                        cursor = seq
                        notified = True
                if notified:
                    # Drain only what already arrived, then re-read the
                    # authoritative projection once per batch of notices.
                    try:
                        row = await _fetch_runner(client, authorization, tenant, runner_id)
                    except RunnerError as error:
                        yield _frame({'type': 'error', 'data': error.code, 'timestamp': _now_ms()})
                        return
                    frames, ended = state.project(row)
                    for event in frames:
                        yield _frame(event)
                    if ended:
                        return
                    notified = False
            # Clean upstream end (bounded observer lifetime or terminal stream):
            # re-read once and resubscribe unless the runner is finished.
            await asyncio.sleep(_LEGACY_RETRY_SECONDS)
        finally:
            await close_event_transport(response, upstream)


async def bridged_stream_response(request, *, message, session_id, files=None,
                                  subagent=None, instance_id=None, video_params=None,
                                  config=None, client=None):
    """Submit through the trusted service and stream legacy frames.

    The trusted service re-verifies the fresh subject and the owned session on
    acceptance; this proxy forwards the end-user credential only. Returns a
    JSON error response when acceptance fails; a disabled or unreachable
    service is never silently replaced by a legacy loop.
    """
    from fastapi.responses import JSONResponse, StreamingResponse

    from src.config.settings import settings
    from src.core.session_queue import session_queue

    config = config or settings.agent_runner
    client = client or RunnerServiceClient(config)
    authorization = request.headers.get('Authorization', '')
    tenant = request.headers.get('X-Tenant-Id') or None
    # Pre-switch legacy owners still mid-turn hold the responding marker; a new
    # runner accepted beside them would execute in parallel with that loop.
    if await asyncio.to_thread(session_queue.is_responding, session_id):
        return JSONResponse(status_code=409, content={
            'success': False, 'error_code': 'SESSION_RESPONDING',
            'error': '当前会话正在回复中，请稍候再试'})
    intent = legacy_submit_intent(message, session_id, files=files, subagent=subagent,
                                  instance_id=instance_id, video_params=video_params)
    try:
        accepted = await client.request('POST', '/v1/runners', authorization=authorization,
                                        tenant=tenant, body=intent.model_dump(mode='json'),
                                        accept_new=config.web_enabled)
    except RunnerError as error:
        status, payload = bridge_error_payload(error)
        return JSONResponse(content=payload, status_code=status)
    row = accepted['runner']

    async def frames():
        async for frame in stream_legacy_frames(client, authorization=authorization, tenant=tenant,
                                                accepted=accepted, session_id=session_id):
            yield frame

    return StreamingResponse(frames(), media_type='text/event-stream', headers={
        'Cache-Control': 'no-cache', 'Connection': 'keep-alive', 'X-Accel-Buffering': 'no'})


async def cancel_session_runners(request, session_id, *, config=None, client=None):
    """Legacy Stop for a bridged session: cancel its active runners, honestly.

    Returns None when the caller keeps the legacy scope (the endpoint then
    answers exactly as before). Otherwise the payload reports what was actually
    cancelled; an unavailable service or an empty active set is never reported
    as an unconditional success.
    """
    from src.config.settings import settings

    authorization = request.headers.get('Authorization', '')
    subject = await asyncio.to_thread(bridged_web_subject, authorization,
                                      request.headers.get('X-Tenant-Id') or None, session_id)
    if subject is None:
        return None
    config = config or settings.agent_runner
    client = client or RunnerServiceClient(config)
    tenant = subject['tenant_id']
    try:
        listed = await client.request('GET', '/v1/sessions/web/' + quote(session_id, safe='') + '/runners',
                                      authorization=authorization, tenant=tenant, params={'limit': 100})
    except RunnerError as error:
        if error.status == 404:
            return 200, {'success': False, 'session_id': session_id, 'error_code': 'NO_ACTIVE_RUNNER',
                         'error': '当前会话没有可取消的新任务', 'cancelled_runners': [], 'failed_runners': []}
        status = error.status if error.status >= 500 else 502
        return status, {'success': False, 'session_id': session_id, 'code': error.code,
                        'error_code': error.code, 'error': error.code,
                        'cancelled_runners': [], 'failed_runners': []}
    cancelled, failed = [], []
    for row in listed.get('active_runners') or []:
        try:
            await client.request('POST', '/v1/runners/' + quote(row['runner_id'], safe='') + '/cancel',
                                 authorization=authorization, tenant=tenant)
            cancelled.append(row['runner_id'])
        except RunnerError as error:
            if error.code == 'RUNNER_NOT_FOUND':
                continue
            failed.append({'runner_id': row['runner_id'], 'error_code': error.code})
    content = {'success': bool(cancelled) and not failed, 'session_id': session_id,
               'cancelled_runners': cancelled, 'failed_runners': failed}
    if not content['success']:
        content['error_code'] = 'NO_ACTIVE_RUNNER' if not cancelled and not failed else 'RUNNER_CANCEL_FAILED'
        content['error'] = '当前会话没有可取消的新任务' if not cancelled and not failed else '部分任务取消失败，请稍后重试'
    return 200, content


async def legacy_loop_interlock(request, session_id, *, config=None, client=None):
    """Bidirectional check before a legacy loop may start on a session.

    A fresh-login legacy caller must not start a loop beside an accepted
    Runner (persistent claim) nor beside a live legacy push marker. Returns an
    error payload tuple, or None when the loop may start. Service
    unavailability fails closed for fresh web principals; it never silently
    re-enables a parallel loop. Anonymous or unauthenticated callers keep the
    untouched legacy scope.
    """
    from src.config.settings import settings
    from src.core.session_queue import session_queue

    authorization = request.headers.get('Authorization', '')
    user = await asyncio.to_thread(_fresh_login, authorization)
    if user is None:
        return None
    config = config or settings.agent_runner
    client = client or RunnerServiceClient(config)
    if await asyncio.to_thread(session_queue.is_responding, session_id):
        return 409, {'success': False, 'error_code': 'SESSION_RESPONDING',
                     'error': '当前会话正在回复中，请稍候再试'}
    target_tenant = request.headers.get('X-Tenant-Id') or None
    platform = user['role'] == 'platform_admin'
    tenant = (target_tenant or None) if platform else user['tenant_id']
    try:
        listed = await client.request('GET', '/v1/sessions/web/' + quote(session_id, safe='') + '/runners',
                                      authorization=authorization, tenant=tenant, params={'limit': 100})
    except RunnerError as error:
        if error.status == 404:
            # Not an owned web session for this principal: no Runner of this
            # caller exists on it, so the original legacy scope is preserved.
            return None
        status = error.status if error.status >= 500 else 502
        return status, {'success': False, 'error_code': error.code, 'error': error.code}
    if listed.get('active_runners'):
        return 409, {'success': False, 'error_code': 'RUNNER_ACTIVE',
                     'error': '当前会话已有新任务在执行，请先停止或等待完成'}
    return None
