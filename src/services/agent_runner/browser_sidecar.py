"""Same-loop, read-only view of the original worker-owned Browser runtime."""

import asyncio
from contextlib import contextmanager
import socket
import time

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import JSONResponse
import uvicorn

from src.db.models import verify_password
from src.tools.browser.human_control import get_owned_runtime
from src.tools.browser.resume_store import ResumeStore
from src.tools.browser.view_hub import browser_view_hub
from .browser_endpoint import BROWSER_OWNER_PATH, trusted_browser_endpoint
from .browser_web_auth import BrowserWebAuth, BrowserViewAssertion
from .contracts import RunnerError


class _WorkerLoopServer(uvicorn.Server):
    @contextmanager
    def capture_signals(self):
        # The Runner already owns SIGTERM/SIGINT. A same-loop observer must
        # neither replace those handlers nor stop the entire worker on detach.
        yield

    def install_signal_handlers(self):
        pass


class BrowserViewSidecar:
    def __init__(self, process):
        self.process, self.config = process, process['config']
        self.auth = BrowserWebAuth(self.config)
        self.server = self.task = self.socket = None
        self.accepting = False
        self.views = set()
        self.app = FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
        self.app.websocket(BROWSER_OWNER_PATH+'/view')(self.observe)
        self.app.post(BROWSER_OWNER_PATH+'/action')(self.action)

    def _assertion(self, websocket):
        service_id = websocket.headers.get('X-Browser-Gateway-Service','')
        token = websocket.headers.get('X-Browser-Gateway-Token','')
        peer = self.config.gateway_peers.get(service_id)
        if (not peer or not token or not peer.token_hash
                or not verify_password(token,peer.token_hash)):
            raise RunnerError('BROWSER_GATEWAY_UNAUTHORIZED',401)
        return BrowserViewAssertion.model_validate_json(websocket.headers.get('X-Browser-View-Assertion',''))

    async def _authorize(self, assertion, *, projection_repair=False):
        await asyncio.to_thread(self.auth.authorize_view,assertion)
        if (assertion.owner_boot_id != self.process['boot']
                or assertion.owner_endpoint != self.config.endpoint):
            raise RunnerError('BROWSER_VIEW_OWNER_CHANGED',403)
        runtime = await get_owned_runtime(assertion.tenant_id,assertion.run_id)
        manager = runtime.manager if runtime else None
        owner = getattr(manager,'execution_owner',None)
        executor = runtime.executor if runtime else None
        if (not owner or owner.worker_boot != assertion.owner_boot_id
                or owner.browser_epoch != assertion.browser_epoch
                or owner.root.attempt.worker_id != assertion.owner_worker_id
                or owner.runner_id != assertion.runner_id
                or owner.state.execution_id != assertion.runner_execution_id
                or owner.call_id != assertion.runner_tool_call_id
                or manager not in self.process.get('retained_managers',set())
                or executor is not manager.human_executor(assertion.tenant_id,assertion.run_id)):
            raise RunnerError('BROWSER_RUNTIME_NOT_AVAILABLE',409)
        assistance = await ResumeStore().get_assistance(assertion.tenant_id,assertion.assistance_id)
        if (not assistance or assistance.runner_id != assertion.runner_id
                or assistance.run_id != assertion.run_id or assistance.user_id != assertion.user_id
                or assistance.session_id != assertion.session_id
                or assistance.agent_execution_id != assertion.runner_execution_id
                or assistance.tool_call_id != assertion.runner_tool_call_id
                or (not projection_repair and (assistance.state not in {'pending','controlling'}
                    or assistance.expires_at <= time.time()))):
            raise RunnerError('BROWSER_WAIT_NOT_AVAILABLE',409)
        owner_token = manager._owner_tokens.get((assertion.tenant_id,assertion.run_id))
        if not owner_token or await manager.store.fence_owner(
                assertion.tenant_id,assertion.run_id,owner_token,time.time()) is not None:
            raise RunnerError('BROWSER_RUNTIME_NOT_AVAILABLE',409)
        return runtime

    async def action(self,request: Request):
        from .browser_human_actions import BrowserHumanActions
        from src.tools.browser.owner_port import HumanActionRejected
        current=asyncio.current_task()
        try:
            if not self.accepting:
                raise RunnerError('BROWSER_VIEW_NOT_AVAILABLE',503)
            self.views.add(current)
            assertion=await asyncio.to_thread(self._assertion,request)
            body=await request.json()
            if not isinstance(body,dict) or set(body)!={'action'} or body['action'] not in {'take','extend','complete'}:
                raise RunnerError('HUMAN_ACTION_INVALID',400)
            if body['action']=='complete':
                from .browser_human_actions import accepted_completion_response
                known=await asyncio.to_thread(self.auth.completion_read_assertion,assertion)
                if known is not None:
                    return await accepted_completion_response(known)
            runtime=await asyncio.wait_for(self._authorize(assertion,projection_repair=True),timeout=3)
            if not self.accepting:
                raise RunnerError('BROWSER_VIEW_NOT_AVAILABLE',503)
            actions=BrowserHumanActions(runtime.manager.execution_owner)
            result=await getattr(actions,body['action'])(assertion)
            return result
        except (RunnerError,HumanActionRejected) as error:
            code=getattr(error,'code','HUMAN_ACTION_REJECTED')
            return JSONResponse(status_code=getattr(error,'status',409),content={'error_code':code,'debug':code})
        finally:
            self.views.discard(current)

    async def observe(self, websocket: WebSocket):
        subscription = None
        tasks = []
        current = asyncio.current_task()
        try:
            if not self.accepting:
                raise RunnerError('BROWSER_VIEW_NOT_AVAILABLE',503)
            # Shutdown must also drain handshakes still awaiting authorization.
            self.views.add(current)
            assertion = await asyncio.to_thread(self._assertion,websocket)
            await asyncio.wait_for(self._authorize(assertion),timeout=3)
            if not self.accepting:
                raise RunnerError('BROWSER_VIEW_NOT_AVAILABLE',503)
            await websocket.accept()
            subscription = await browser_view_hub.subscribe(assertion.tenant_id,assertion.run_id)
            send_lock = asyncio.Lock()

            async def frames():
                while True:
                    frame = await subscription.next_frame()
                    if subscription.closed:
                        return
                    async with send_lock:
                        if frame is None:
                            await websocket.send_json({'type':'heartbeat'})
                        else:
                            await websocket.send_json(dict(type='frame',seq=frame.seq,width=frame.width,
                                height=frame.height,captured_at=frame.captured_at))
                            await websocket.send_bytes(frame.jpeg)

            async def fresh_authorization():
                # Independent of frame availability and send_lock: even a slow
                # observer cannot defer revocation by blocking JPEG delivery.
                while True:
                    await asyncio.sleep(1)
                    await asyncio.wait_for(self._authorize(assertion),timeout=3)

            async def receive_input():
                from src.tools.browser.executor.models import InputMessage
                from .browser_human_actions import BrowserHumanActions
                from src.tools.browser.owner_port import HumanActionRejected
                actions=None
                input_tokens=120.0
                last_refill=asyncio.get_running_loop().time()
                while True:
                    message = await websocket.receive()
                    if message['type'] == 'websocket.disconnect':
                        return
                    now=asyncio.get_running_loop().time()
                    input_tokens=min(120.0,input_tokens+(now-last_refill)*60.0)
                    last_refill=now
                    try:
                        if input_tokens<1.0:
                            raise HumanActionRejected('INPUT_RATE_LIMITED')
                        input_tokens-=1.0
                        runtime=await self._authorize(assertion)
                        actions=BrowserHumanActions(runtime.manager.execution_owner)
                        parsed=InputMessage.model_validate_json(message.get('text',''))
                        await actions.input(assertion,parsed,runtime)
                    except (HumanActionRejected,RunnerError) as error:
                        async with send_lock:
                            await websocket.send_json({'type':'input_rejected','error_code':error.code})

            tasks = [asyncio.create_task(frames()),asyncio.create_task(fresh_authorization()),
                     asyncio.create_task(receive_input())]
            done, _ = await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except Exception:
            # Authorization/storage/transport failures close only this observer.
            # No request objects, private headers or frame bytes are logged.
            pass
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
            if subscription is not None:
                await subscription.close()
            self.views.discard(current)
            try:
                await asyncio.wait_for(websocket.close(code=4403),timeout=1)
            except (RuntimeError,asyncio.TimeoutError):
                pass

    async def open(self):
        from urllib.parse import urlsplit
        trusted_browser_endpoint(self.config.endpoint,self.config.allowed_endpoints)
        advertised = urlsplit(self.config.endpoint)
        # This slice is direct HTTP/WS; it has no TLS terminator/proxy contract.
        if advertised.scheme != 'http' or advertised.port != self.config.bind_port:
            raise RuntimeError('BROWSER_VIEW_LISTENER_ENDPOINT_MISMATCH')
        if not self.config.enabled or not self.config.gateway_peers or any(
                not peer.token_hash for peer in self.config.gateway_peers.values()):
            raise RuntimeError('BROWSER_VIEW_SERVICE_AUTH_REQUIRED')
        self.socket = socket.socket(socket.AF_INET,socket.SOCK_STREAM)
        try:
            self.socket.bind((self.config.bind_host,self.config.bind_port))
            self.socket.listen(128)
            self.socket.setblocking(False)
            self.server = _WorkerLoopServer(uvicorn.Config(self.app,log_config=None,access_log=False,
                lifespan='off',ws_max_size=16*1024,timeout_graceful_shutdown=2))
            self.task = asyncio.create_task(self.server.serve(sockets=[self.socket]))
            async def ready():
                while not self.server.started:
                    if self.task.done():
                        await self.task
                        raise RuntimeError('BROWSER_VIEW_LISTENER_NOT_READY')
                    await asyncio.sleep(.01)
            await asyncio.wait_for(ready(),timeout=5)
            self.accepting = True
        except BaseException:
            await self.close()
            raise

    async def stop_observations(self):
        self.accepting = False
        tasks = tuple(self.views)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)

    async def close(self):
        await self.stop_observations()
        if self.server is not None:
            self.server.should_exit = True
        if self.task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self.task),timeout=5)
            except asyncio.TimeoutError:
                self.task.cancel()
                await asyncio.gather(self.task,return_exceptions=True)
        if self.socket is not None:
            self.socket.close()
