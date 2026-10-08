"""Short HTTP operations. Connections are observers, never runner task owners."""

import asyncio
import traceback
import re
from typing import Annotated, Literal
from fastapi import FastAPI, Request, Query, Path
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware

from .contracts import RunnerError, RunnerSubmit, SessionRef, SessionQuery
from .control_contracts import RunnerControl
from .event_stream import ObserverAdmission, open_runner_events, parse_cursor


class EventOperationMiddleware(BaseHTTPMiddleware):
    """Direct ASGI supervision for the event observer only."""
    async def __call__(self, scope, receive, send):
        # This exact observer route must see real socket backpressure. Ordinary
        # requests keep BaseHTTP, and both paths share the original dispatch.
        if (scope['type'] != 'http' or scope.get('method') != 'GET'
                or not re.fullmatch(r'/v1/runners/[^/]+/events', scope.get('path', ''))):
            return await super().__call__(scope, receive, send)
        forwarded = object()
        started = False

        async def direct_send(message):
            nonlocal started
            if message['type'] == 'http.response.start':
                started = True
            await send(message)

        async def direct_next(_request):
            await self.app(scope, receive, direct_send)
            return forwarded

        response = await self.dispatch_func(Request(scope, receive), direct_next)
        if response is not forwarded and not started:
            await response(scope, receive, send)


def credentials(request):
    authorization = request.headers.get("Authorization", "")
    return {"service_id": request.headers.get("X-AgentRunner-Service"),
            "service_token": request.headers.get("X-AgentRunner-Service-Token"),
            "user_token": authorization[7:] if authorization.startswith("Bearer ") else None,
            "target_tenant": request.headers.get("X-Tenant-Id") or None,
            "actor_user": request.headers.get("X-AgentRunner-Channel-User") or None,
            "actor_chat": request.headers.get("X-AgentRunner-Channel-Chat") or None,
            "actor_source": request.headers.get("X-AgentRunner-Source") or None}


def create_app(*, config=None, manager=None):
    from src.config.settings import settings
    config = config or settings.agent_runner
    if manager is None:
        from .authorization import RunnerAuthorizer
        from .manager import RunnerManager
        from .profiles import MainProfileCatalog
        from .repository import RunnerRepository
        from .application_sources import build_source_capabilities
        repository=RunnerRepository()
        sources=build_source_capabilities(config,repository.connection_factory)
        manager = RunnerManager(repository, RunnerAuthorizer(config,source_port=sources), MainProfileCatalog())

    app = FastAPI(title="AgentRunner", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.runner_manager = manager
    from .control_application import ControlApplication
    controls = ControlApplication(manager)
    event_admission = ObserverAdmission()

    def enabled():
        if not config.enabled:
            raise RunnerError("AGENT_RUNNER_DISABLED", 503)

    @app.exception_handler(RunnerError)
    async def runner_error(_request, error):
        return JSONResponse(status_code=error.status,
                            content={"success": False, "error": error.code, "debug": error.code})

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request, _error):
        # Pydantic's default errors echo input values, including extra headers or
        # accidentally submitted credentials. Return only a stable safe code.
        return JSONResponse(status_code=422, content={"success": False, "error": "INVALID_REQUEST", "debug": "INVALID_REQUEST"})

    async def safe_operation_errors(request, call_next):
        try:
            return await call_next(request)
        except Exception as error:
            # Do not rethrow to Uvicorn's exception logger or diagnose request
            # locals. Frame locations retain diagnostics without credentials.
            frames = [f"{frame.filename}:{frame.lineno}:{frame.name}" for frame in traceback.extract_tb(error.__traceback__)]
            logger.error("AgentRunner HTTP operation failed: {kind}; frames={frames}",
                         kind=type(error).__name__, frames=frames)
            return JSONResponse(status_code=500, content={"success": False, "error": "RUNNER_STORAGE_UNAVAILABLE", "debug": "RUNNER_STORAGE_UNAVAILABLE"})

    app.add_middleware(EventOperationMiddleware, dispatch=safe_operation_errors)

    @app.get("/health")
    async def health():
        return {"service": "AgentRunner", "enabled": config.enabled}

    @app.post("/v1/runners", status_code=202)
    async def submit(body: RunnerSubmit, request: Request):
        enabled()
        accept_new = request.headers.get('X-AgentRunner-Accept-New', 'true')
        if accept_new not in ('true', 'false'):
            raise RunnerError('INVALID_ACCEPT_POLICY', 422)
        value, created = await asyncio.to_thread(manager.submit, body, credentials(request), accept_new=accept_new=='true')
        return {"success": True, "created": created, "runner": value}


    @app.get("/v1/runners/{runner_id}")
    async def get(runner_id: str, request: Request):
        enabled()
        return {"success": True, "runner": await asyncio.to_thread(manager.get, runner_id, credentials(request))}

    @app.get('/v1/runners/{runner_id}/events')
    async def events(runner_id: str, request: Request,
                     after_seq: int | None = Query(default=None, ge=0, le=2**63 - 1)):
        enabled()
        cursor = parse_cursor(after_seq, request.headers.get('Last-Event-ID'))
        return await open_runner_events(manager, runner_id, credentials(request), cursor, event_admission)

    @app.get('/v1/runners/{runner_id}/channel-result')
    async def channel_result(runner_id: str, request: Request):
        enabled()
        return await asyncio.to_thread(manager.channel_result, runner_id, credentials(request))

    @app.post("/v1/runners/{runner_id}/cancel")
    async def cancel(runner_id: str, request: Request):
        enabled()
        return {"success": True, "runner": await asyncio.to_thread(manager.cancel, runner_id, credentials(request))}

    @app.post('/v1/runners/{runner_id}/controls',status_code=202)
    async def submit_control(runner_id: str,body: RunnerControl,request: Request):
        enabled()
        control, created, runner = await controls.submit_async(runner_id,body,credentials(request))
        return {'success':True,'control':control,'created':created,'runner':runner}

    @app.get('/v1/runners/{runner_id}/controls/{control_id}')
    async def get_control(runner_id: str,control_id: str,request: Request):
        enabled()
        return {'success':True,'control':await asyncio.to_thread(controls.get,runner_id,control_id,credentials(request))}

    @app.get("/v1/sessions/{kind}/{session_id}/runners")
    async def list_session(kind: Literal["web", "channel"],
                           session_id: Annotated[str, Path(min_length=1, max_length=256)], request: Request,
                           source: Literal["chat", "wecom_kf", "feishu", "dingtalk"] = "chat",
                           profile_id: str = Query(default="main", min_length=1, max_length=128),
                           channel_user_id: str | None = Query(default=None, max_length=256),
                           channel_chat_id: str | None = Query(default=None, max_length=256),
                           before_runner_id: str | None = Query(default=None, max_length=128),
                           limit: int = 100):
        enabled()
        if not 1 <= limit <= 100:
            raise RunnerError("INVALID_LIMIT", 400)
        query = SessionQuery(session=SessionRef(kind=kind, session_id=session_id),
            source=source, profile_id=profile_id, channel_user_id=channel_user_id, channel_chat_id=channel_chat_id)
        page = await asyncio.to_thread(manager.list_session, query, credentials(request), limit, before_runner_id)
        return {"success": True, **page}

    return app
