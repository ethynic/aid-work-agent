"""主 API 的 Web/渠道网关传输，不在调用进程执行任务。"""

import os
import asyncio
import json

import httpx

from .contracts import RunnerError


EVENT_ERROR_CODES = frozenset({'USER_UNAUTHORIZED', 'SERVICE_UNAUTHORIZED',
    'SERVICE_SOURCE_FORBIDDEN', 'SESSION_NOT_FOUND', 'TENANT_FORBIDDEN',
    'CHANNEL_ACTOR_REQUIRED', 'CHANNEL_ACTOR_FORBIDDEN', 'CHANNEL_USER_FORBIDDEN',
    'RUNNER_NOT_FOUND', 'RUNNER_STORAGE_UNAVAILABLE', 'INVALID_REQUEST',
    'INVALID_EVENT_CURSOR', 'AGENT_RUNNER_DISABLED', 'RUNNER_EVENT_OBSERVER_LIMIT',
    # scheduled 来源（任务视图读取/事件流经网关透传，设计 §5.2/§5.5）
    'SCHEDULED_TASK_NOT_ACCEPTABLE', 'SCHEDULED_SUBMIT_FORBIDDEN',
    'SCHEDULED_TASK_NOT_FOUND', 'TASK_SESSION_CHAT_FORBIDDEN'})


async def close_event_transport(response, client):
    """Try both owned HTTP handles, even across repeated caller cancellation."""
    async def cleanup():
        for handle in (response, client):
            if handle is not None:
                try:
                    await asyncio.wait_for(handle.aclose(), 1)
                except Exception:
                    pass

    closing = asyncio.create_task(cleanup())
    cancelled = False
    while not closing.done():
        try:
            await asyncio.shield(closing)
        except asyncio.CancelledError:
            cancelled = True
    closing.result()
    if cancelled:
        raise asyncio.CancelledError


async def event_error(response):
    if 300 <= response.status_code < 400:
        return RunnerError('RUNNER_SERVICE_REDIRECT_FORBIDDEN', 502)
    content = bytearray()
    async for chunk in response.aiter_raw():
        if len(content) + len(chunk) > 4096:
            return RunnerError('RUNNER_SERVICE_INVALID_RESPONSE', 502)
        content.extend(chunk)
    try:
        value = json.loads(content)
        code = value.get('error') if isinstance(value, dict) else None
    except (ValueError, UnicodeError):
        code = None
    code = code if isinstance(code, str) and code in EVENT_ERROR_CODES else 'RUNNER_SERVICE_ERROR'
    return RunnerError(code, response.status_code if response.status_code >= 400 else 502)


def valid_runner(value):
    if not isinstance(value, dict):
        return False
    session = value.get('session')
    return (isinstance(value.get('runner_id'), str) and bool(value['runner_id'])
            and isinstance(value.get('client_request_id'),str) and bool(value['client_request_id'])
            and isinstance(value.get('profile_id'),str) and bool(value['profile_id'])
            and type(value.get('queue_order')) is int and value['queue_order'] > 0
            and isinstance(session, dict) and session.get('kind') in ('web', 'channel')
            and isinstance(session.get('session_id'), str) and bool(session['session_id'])
            and value.get('status') in ('queued', 'running', 'waiting', 'paused', 'interrupted',
                                       'finalizing', 'completed', 'failed', 'cancelled')
            and isinstance(value.get('snapshot'), dict)
            and all(type(value.get(key)) is int and value[key] >= 0
                    for key in ('revision', 'view_revision', 'control_revision')))


def validate_response(value, path):
    valid = value.get('success') is True
    if '/controls' in path:
        control = value.get('control')
        valid = valid and isinstance(control,dict) and all(
            isinstance(control.get(key),str) and bool(control[key])
            for key in ('control_id','runner_id','client_request_id')) and control.get('status') in (
                'accepted','claimed','consumed','rejected')
        if path.endswith('/controls'):
            valid = valid and valid_runner(value.get('runner'))
    elif path.startswith('/v1/sessions/'):
        valid = valid and all(isinstance(value.get(key), list) and all(valid_runner(row) for row in value[key])
                              for key in ('runners', 'active_runners'))
        valid = valid and type(value.get('has_more')) is bool and (value.get('next_cursor') is None
                         or isinstance(value['next_cursor'], str)) and 'next_cursor' in value
    else:
        valid = valid and valid_runner(value.get('runner'))
    if not valid:
        raise RunnerError('RUNNER_SERVICE_INVALID_RESPONSE', 502)


def form_error_details(value):
    """结构合法的逐题错误明细才跨边界（§5.7），整体不合法时丢弃且绝不降级为字符串。

    只保留 question_id/code/message 三个字段，与 ask-form-errors.schema.json 对齐。
    """
    if not isinstance(value, list) or not value:
        return None
    details = []
    for item in value:
        if (not isinstance(item, dict) or not all(
                isinstance(item.get(key), str) and item.get(key)
                for key in ('question_id', 'code', 'message'))):
            return None
        details.append({key: item[key] for key in ('question_id', 'code', 'message')})
    return details


class RunnerServiceClient:
    def __init__(self, config, *, token=None):
        self.config = config
        self.token = token if token is not None else os.environ.get('AGENT_RUNNER_WEB_SERVICE_TOKEN', '')

    async def request(self, method, path, *, authorization, tenant=None, body=None, params=None, accept_new=None,
                      channel_source=None, channel_user=None, channel_chat=None):
        if not self.token:
            raise RunnerError('RUNNER_GATEWAY_NOT_CONFIGURED', 503)
        headers = {'X-AgentRunner-Service':self.config.web_service_id,
                   'X-AgentRunner-Service-Token':self.token}
        if authorization:
            headers['Authorization'] = authorization
        if channel_source:
            headers.update({'X-AgentRunner-Source':channel_source, 'X-AgentRunner-Channel-User':channel_user})
            if channel_chat:
                headers['X-AgentRunner-Channel-Chat'] = channel_chat
        if tenant:
            headers['X-Tenant-Id'] = tenant
        if accept_new is not None:
            headers['X-AgentRunner-Accept-New'] = 'true' if accept_new else 'false'
        try:
            async with httpx.AsyncClient(base_url=self.config.api_url.rstrip('/'), timeout=15, follow_redirects=False, trust_env=False) as client:
                response = await client.request(method, path, headers=headers, json=body, params=params)
        except httpx.HTTPError:
            raise RunnerError('RUNNER_SERVICE_UNAVAILABLE', 503) from None
        try:
            value = response.json()
        except ValueError:
            raise RunnerError('RUNNER_SERVICE_INVALID_RESPONSE', 502) from None
        if not isinstance(value, dict):
            raise RunnerError('RUNNER_SERVICE_INVALID_RESPONSE', 502)
        if not response.is_success:
            code = value.get('error')
            # Only stable error codes from the trusted service cross this boundary.
            code = code if isinstance(code, str) and code.replace('_','').isalnum() and len(code)<=128 else 'RUNNER_SERVICE_ERROR'
            # 受理前表单校验的逐题明细原样透传（§5.7），不降级为通用错误字符串。
            raise RunnerError(code, response.status_code, errors=form_error_details(value.get('errors')))
        validate_response(value, path)
        return value

    async def open_events(self, path, *, authorization, tenant=None, after_seq=0):
        """Return a live response/client only after a bounded successful handshake.

        Caller owns closing both resources. This is not the short JSON request
        method, and never forwards incoming service/channel credentials.
        """
        if not self.token:
            raise RunnerError('RUNNER_GATEWAY_NOT_CONFIGURED', 503)
        headers = {'X-AgentRunner-Service': self.config.web_service_id,
                   'X-AgentRunner-Service-Token': self.token, 'Authorization': authorization}
        if tenant:
            headers['X-Tenant-Id'] = tenant
        client = httpx.AsyncClient(base_url=self.config.api_url.rstrip('/'),
            timeout=httpx.Timeout(connect=5, pool=5, write=5, read=20),
            follow_redirects=False, trust_env=False, http2=False)
        response = None
        transferred = False
        try:
            request = client.build_request('GET', path, headers=headers, params={'after_seq': after_seq})
            response = await asyncio.wait_for(client.send(request, stream=True), 5)
            if not response.is_success:
                raise await asyncio.wait_for(event_error(response), 5)
            if (response.status_code != 200 or
                    response.headers.get('Content-Type', '').split(';', 1)[0].strip().lower() != 'text/event-stream'):
                raise RunnerError('RUNNER_SERVICE_INVALID_RESPONSE', 502)
            transferred = True
            return response, client
        except (httpx.HTTPError, asyncio.TimeoutError):
            raise RunnerError('RUNNER_SERVICE_UNAVAILABLE', 503) from None
        finally:
            if not transferred:
                await close_event_transport(response, client)
