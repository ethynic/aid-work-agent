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
from pydantic import BaseModel,Field

from .contracts import RunnerError, RunnerSubmit, SessionRef, SessionQuery
from .control_contracts import RunnerControl
from .event_stream import ObserverAdmission, open_runner_events, parse_cursor


class SourceInputRequest(BaseModel):
    source: Literal['wecom_kf','feishu','dingtalk']
    account_id: str=Field(min_length=1,max_length=256)
    namespace: str=Field(min_length=1,max_length=256)
    message_id: str=Field(min_length=1,max_length=256)
    client_request_id: str=Field(min_length=1,max_length=128)

    class Config:
        extra='forbid'


class SourceReceiptRequest(BaseModel):
    source: Literal['wecom_kf', 'feishu', 'dingtalk']
    account_id: str = Field(min_length=1, max_length=256)
    namespace: str = Field(min_length=1, max_length=256)
    message_id: str = Field(min_length=1, max_length=256)

    class Config:
        extra = 'forbid'


class SourceBatchRequest(BaseModel):
    members: list[SourceReceiptRequest] = Field(min_length=1, max_length=32)
    client_request_id: str = Field(min_length=1, max_length=128)

    class Config:
        extra = 'forbid'


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
            "actor_source": request.headers.get("X-AgentRunner-Source") or None,
            "source_input":request.headers.get('X-AgentRunner-Source-Input') or None}


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

    @app.post('/v1/source-inputs',status_code=202)
    async def accept_source(body:SourceInputRequest,request:Request):
        enabled()
        from .source_receipts import SourceLocator,source_offload
        locator=SourceLocator(body.source,body.account_id,body.namespace,body.message_id)
        if body.client_request_id!=locator.stable_key:
            raise RunnerError('INVALID_SOURCE_RECEIPT',422)
        auth=credentials(request)
        existing=await source_offload(manager.find_source,locator,auth)
        if existing is not None: return existing
        prepared=await manager.prepare_source(locator,auth)
        return await source_offload(manager.accept_source,locator,body.client_request_id,auth,prepared)

    @app.get('/v1/source-inputs')
    async def read_source(request:Request,
                          source:str=Query(min_length=1,max_length=32),
                          account_id:str=Query(min_length=1,max_length=256),
                          namespace:str=Query(min_length=1,max_length=256),
                          message_id:str=Query(min_length=1,max_length=256)):
        enabled()
        items=request.query_params.multi_items()
        if (len(items)!=4 or {key for key,_ in items}!=
                {'source','account_id','namespace','message_id'}):
            raise RunnerError('SOURCE_LOCATOR_QUERY_INVALID',422)
        from .source_receipts import SourceLocator,source_offload
        locator=SourceLocator(source,account_id,namespace,message_id)
        return await source_offload(manager.read_source,locator,credentials(request))

    @app.get('/v1/source-presentations')
    async def source_presentation(request:Request, source:str=Query(min_length=1,max_length=32),
            account_id:str=Query(min_length=1,max_length=256),namespace:str=Query(min_length=1,max_length=256),
            message_id:str=Query(min_length=1,max_length=256)):
        enabled()
        if len(request.query_params.multi_items())!=4 or set(request.query_params)!= {'source','account_id','namespace','message_id'}:
            raise RunnerError('SOURCE_LOCATOR_QUERY_INVALID',422)
        from .source_receipts import SourceLocator,source_offload
        return await source_offload(manager.read_source,SourceLocator(source,account_id,namespace,message_id),
                                    credentials(request),presentation=True)

    @app.post('/v1/source-deliveries/{delivery_id}/finish')
    async def finish_source_delivery(delivery_id:str,body:SourceReceiptRequest,request:Request):
        enabled()
        if not re.fullmatch(r'kf_delivery_[a-f0-9]{64}',delivery_id):
            raise RunnerError('SOURCE_DELIVERY_INVALID',422)
        from .source_receipts import SourceLocator,source_offload
        return await source_offload(manager.finish_source_delivery,SourceLocator(**body.model_dump()),delivery_id,credentials(request))

    @app.post('/v1/source-input-batches', status_code=202)
    async def accept_source_batch(body: SourceBatchRequest, request: Request):
        enabled()
        from .source_receipts import SourceBatch, SourceLocator, source_offload
        batch = SourceBatch(tuple(SourceLocator(**member.model_dump()) for member in body.members))
        if body.client_request_id != batch.stable_key:
            raise RunnerError('INVALID_SOURCE_BATCH', 422)
        auth = credentials(request)
        existing = await source_offload(manager.find_source_batch, batch, auth)
        if existing is not None:
            return existing
        prepared = await manager.prepare_source_batch(batch, auth)
        return await source_offload(manager.accept_source_batch, batch, body.client_request_id, auth, prepared)

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
