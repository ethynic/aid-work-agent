"""Fresh native view-ticket consumption and fixed-path sidecar observation."""

import asyncio
import os

import aiohttp
from fastapi import WebSocket

from .browser_endpoint import browser_view_endpoint
from .browser_view_tickets import consume_view_ticket
from .browser_web_auth import BrowserWebAuth
from .contracts import RunnerError


async def observe_native_browser(websocket: WebSocket, run_id, ticket, config):
    auth = BrowserWebAuth(config)
    tasks = []
    try:
        if not config.view_enabled:
            raise RunnerError('BROWSER_VIEW_NOT_AVAILABLE',503)
        assertion = await asyncio.to_thread(consume_view_ticket,run_id,ticket)
        await asyncio.wait_for(asyncio.to_thread(auth.authorize_view,assertion),timeout=2)
        service_id = config.gateway_service_id
        token = os.getenv('AGENT_RUNNER_BROWSER_GATEWAY_TOKEN','')
        if not service_id or not token:
            raise RunnerError('BROWSER_GATEWAY_UNAUTHORIZED',503)
        endpoint = browser_view_endpoint(assertion.owner_endpoint,config.allowed_endpoints)
        headers = {'X-Browser-Gateway-Service':service_id,'X-Browser-Gateway-Token':token,
                   'X-Browser-View-Assertion':assertion.model_dump_json()}
        trace = aiohttp.TraceConfig()
        async def no_redirect(session,context,params):
            raise RunnerError('BROWSER_OWNER_REDIRECT_FORBIDDEN',403)
        trace.on_request_redirect.append(no_redirect)
        # aiohttp's WS handshake can otherwise follow redirects. The trace
        # aborts before any redirected request, including a same-origin path.
        async with aiohttp.ClientSession(trace_configs=[trace],trust_env=False,
                timeout=aiohttp.ClientTimeout(total=None,connect=5,sock_connect=5)) as session:
            timeout_type = getattr(aiohttp,'ClientWSTimeout',None)
            close_timeout = timeout_type(ws_close=1) if timeout_type is not None else 1
            upstream = await asyncio.wait_for(session.ws_connect(endpoint,headers=headers,
                max_msg_size=1024*1024,heartbeat=20,
                timeout=close_timeout),timeout=5)
            try:
                await websocket.accept()
                send_lock = asyncio.Lock()

                async def frames():
                    while True:
                        message = await upstream.receive()
                        if message.type in {aiohttp.WSMsgType.CLOSE,aiohttp.WSMsgType.CLOSED,aiohttp.WSMsgType.ERROR}:
                            return
                        if message.type != aiohttp.WSMsgType.TEXT:
                            raise RunnerError('BROWSER_FRAME_PROTOCOL_INVALID',502)
                        metadata = message.json()
                        if metadata.get('type') == 'frame':
                            binary = await upstream.receive()
                            if binary.type != aiohttp.WSMsgType.BINARY or not 0 < len(binary.data) <= 1024*1024:
                                raise RunnerError('BROWSER_FRAME_PROTOCOL_INVALID',502)
                            async with send_lock:
                                await websocket.send_json(metadata)
                                await websocket.send_bytes(binary.data)
                        elif metadata.get('type') == 'heartbeat':
                            async with send_lock:
                                await websocket.send_json({'type':'heartbeat'})
                        elif metadata.get('type')=='input_rejected':
                            async with send_lock:
                                await websocket.send_json({'type':'input_rejected',
                                    'error_code':metadata.get('error_code','HUMAN_ACTION_REJECTED')})
                        else:
                            raise RunnerError('BROWSER_FRAME_PROTOCOL_INVALID',502)

                async def fresh_authorization():
                    while True:
                        await asyncio.sleep(1)
                        await asyncio.wait_for(asyncio.to_thread(auth.authorize_view,assertion),timeout=2)

                async def relay_input():
                    from src.tools.browser.executor.models import InputMessage
                    while True:
                        message = await websocket.receive()
                        if message['type'] == 'websocket.disconnect':
                            return
                        parsed=InputMessage.model_validate_json(message.get('text',''))
                        await asyncio.wait_for(asyncio.to_thread(auth.authorize_view,assertion),timeout=2)
                        await upstream.send_json(parsed.model_dump())

                tasks = [asyncio.create_task(frames()),asyncio.create_task(fresh_authorization()),
                         asyncio.create_task(relay_input())]
                try:
                    done,_ = await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
                    for task in done:
                        task.result()
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks,return_exceptions=True)
                    # Revoke the external observer before waiting for an owner
                    # close handshake. This never cancels the Browser runtime.
                    await close_observer(websocket)
            finally:
                try:
                    await asyncio.wait_for(upstream.close(),timeout=1)
                except (Exception,asyncio.CancelledError):
                    # Session exit releases the transport even if the owner
                    # does not acknowledge a WS detach.
                    pass
    except Exception:
        # A failed native authorization or sidecar never falls back to a local
        # Hub/input handler. The original Browser and Runner remain untouched.
        pass
    finally:
        await close_observer(websocket)


async def close_observer(websocket):
    try:
        await asyncio.wait_for(websocket.close(code=4403),timeout=1)
    except (RuntimeError,asyncio.TimeoutError):
        pass


async def perform_native_action(assertion, action, config):
    """One fixed, authenticated owner endpoint; no caller-selected method/URL."""
    from .browser_endpoint import trusted_browser_endpoint
    if action not in {'take','extend','complete'}:
        raise RunnerError('HUMAN_ACTION_INVALID',400)
    endpoint=trusted_browser_endpoint(assertion.owner_endpoint,config.allowed_endpoints)+'/action'
    service_id=config.gateway_service_id
    token=os.getenv('AGENT_RUNNER_BROWSER_GATEWAY_TOKEN','')
    if not service_id or not token:
        raise RunnerError('BROWSER_GATEWAY_UNAUTHORIZED',503)
    headers={'X-Browser-Gateway-Service':service_id,'X-Browser-Gateway-Token':token,
             'X-Browser-View-Assertion':assertion.model_dump_json()}
    async with aiohttp.ClientSession(trust_env=False,timeout=aiohttp.ClientTimeout(total=40,connect=5)) as session:
        async with session.post(endpoint,headers=headers,json={'action':action},allow_redirects=False) as response:
            if response.status>=300 and response.status<400:
                raise RunnerError('BROWSER_OWNER_REDIRECT_FORBIDDEN',403)
            result=await response.json()
            if response.status>=400:
                raise RunnerError(result.get('error_code','HUMAN_ACTION_REJECTED'),response.status)
            return result
