"""Short Web operations over the independent AgentRunner HTTP service."""

import asyncio
from contextlib import asynccontextmanager
from urllib.parse import quote

from fastapi import APIRouter, FastAPI, Request, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field

from src.config.settings import settings
from src.services.agent_runner.contracts import AttachmentRef, RunnerError, RunnerSubmit, SessionRef
from src.services.agent_runner.web_client import RunnerServiceClient, close_event_transport
from src.services.agent_runner.control_contracts import RunnerControl
from src.services.agent_runner.event_stream import ObserverAdmission, parse_cursor
from src.services.agent_runner.web_event_stream import WebEventResponse


class WebSubmission(BaseModel):
    client_request_id: str = Field(min_length=1, max_length=128)
    message: str = Field(max_length=1_000_000)
    session_id: str = Field(min_length=1, max_length=256)
    files: list[AttachmentRef] = Field(default_factory=list, max_length=100)
    subagent: str | None = Field(default=None, max_length=128)
    instance_id: str | None = Field(default=None, max_length=256)
    video_params: dict | None = None

    class Config:
        extra = 'forbid'

    def runner_request(self):
        video_keys = ('mode','duration_sec','ratio','resolution','card_count','prompt_model')
        data = {'video_params':{key:self.video_params[key] for key in video_keys if key in self.video_params}} if self.video_params else {}
        return RunnerSubmit(client_request_id=self.client_request_id, text=self.message,
            session=SessionRef(kind='web', session_id=self.session_id), attachments=self.files,
            profile_id=self.subagent or 'main', routing_policy='explicit' if self.subagent else 'default_single',
            instance_id=self.instance_id, request_data=data)


def error_response(error):
    status, code = error.status, error.code
    if code == 'CREDIT_BLOCKED':
        return JSONResponse(status_code=403, content={'success':False, 'code':'NO_CREDIT',
            'error':'积分余额已耗尽，数字员工无法工作', 'debug':code})
    return JSONResponse(status_code=status, content={'success':False, 'code':code, 'error':code, 'debug':code})


def authorize_capabilities(authorization):
    from src.api.web_subject import fresh_web_user
    fresh_web_user(authorization)


class RunnerWebRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def safe_handler(request):
            try:
                return await handler(request)
            except RequestValidationError:
                return JSONResponse(status_code=422, content={'success':False, 'error':'INVALID_REQUEST', 'debug':'INVALID_REQUEST'})
            except RunnerError as error:
                return error_response(error)
        return safe_handler


def create_router(config=None, client=None):
    config = config or settings.agent_runner
    client = client or RunnerServiceClient(config)
    router = APIRouter(prefix='/api/chat', tags=['AgentRunner Web'], route_class=RunnerWebRoute)
    event_admission = ObserverAdmission()

    async def forward(request, method, path, **arguments):
        # Incoming service/actor headers are never forwarded. End-user credentials
        # remain request-local, and the service performs fresh subject/owner auth.
        try:
            return await client.request(method, path, authorization=request.headers.get('Authorization',''),
                tenant=request.headers.get('X-Tenant-Id'), **arguments)
        except RunnerError as error:
            return error_response(error)

    @router.get('/runners/capabilities')
    async def capabilities(request: Request):
        # Configuration, not a reachability probe. Never silently choose a legacy
        # loop after an accepted task or because the service is temporarily down.
        await asyncio.to_thread(authorize_capabilities, request.headers.get('Authorization',''))
        return {'web_enabled':config.web_enabled,
                'observe_existing':bool(config.api_url and client.token),
                'events_supported':bool(config.api_url and client.token),
                'contract_version':1, 'transport':'runner_poll'}

    @router.post('/runners', status_code=202)
    async def submit(body: WebSubmission, request: Request):
        intent = body.runner_request()
        # A disabled submit switch still looks up a matching accepted key; it can
        # never create a runner. Retries keep the original immutable payload.
        return await forward(request, 'POST', '/v1/runners',
            body=intent.model_dump(mode='json'), accept_new=config.web_enabled)

    @router.post('/runners/sync')
    async def sync_reply(body: WebSubmission, request: Request):
        from src.services.agent_runner.web_sync import synchronous_web_reply
        return await synchronous_web_reply(request, body, config=config, client=client)

    @router.get('/runners/{runner_id}')
    async def get_runner(runner_id: str, request: Request):
        return await forward(request, 'GET', '/v1/runners/'+quote(runner_id,safe=''))

    @router.get('/runners/{runner_id}/events')
    async def events(runner_id: str, request: Request,
                     after_seq: int | None = Query(default=None, ge=0, le=2**63 - 1)):
        cursor = parse_cursor(after_seq, request.headers.get('Last-Event-ID'))
        event_admission.acquire()
        transferred = False
        response = upstream_client = None
        try:
            response, upstream_client = await client.open_events(
                '/v1/runners/' + quote(runner_id, safe='') + '/events',
                authorization=request.headers.get('Authorization', ''),
                tenant=request.headers.get('X-Tenant-Id') or None, after_seq=cursor)
            observer = WebEventResponse(response, upstream_client, event_admission)
            transferred = True
            return observer
        finally:
            if not transferred:
                try:
                    await close_event_transport(response, upstream_client)
                finally:
                    event_admission.release()

    @router.post('/runners/{runner_id}/cancel')
    async def cancel_runner(runner_id: str, request: Request):
        return await forward(request, 'POST', '/v1/runners/'+quote(runner_id,safe='')+'/cancel')

    @router.post('/runners/{runner_id}/controls',status_code=202)
    async def runner_control(runner_id: str,body: RunnerControl,request: Request):
        if body.action not in {'pause','resume','reply'}:
            raise RunnerError('CONTROL_COMPLETION_FORBIDDEN',403)
        return await forward(request,'POST','/v1/runners/'+quote(runner_id,safe='')+'/controls',
                             body=body.model_dump(mode='json'))

    @router.get('/runners/{runner_id}/controls/{control_id}')
    async def get_control(runner_id: str,control_id: str,request: Request):
        return await forward(request,'GET','/v1/runners/'+quote(runner_id,safe='')+'/controls/'+quote(control_id,safe=''))

    @router.get('/sessions/{session_id}/runners')
    async def session_runners(session_id: str, request: Request,
                              before_runner_id: str | None = Query(default=None,max_length=128),
                              limit: int = Query(default=100,ge=1,le=100)):
        params = {'limit':limit}
        if before_runner_id is not None:
            params['before_runner_id'] = before_runner_id
        return await forward(request, 'GET', '/v1/sessions/web/'+quote(session_id,safe='')+'/runners', params=params)

    return router


router = create_router()


def create_app():
    """Standalone Web gateway composition, sharing the real session API routes.

    The existing main app mounts the same router and owns its own infrastructure.
    This factory also permits a separate Web process without a legacy Agent or
    scheduler, with explicit owned PG lifecycle and no automatic migrations.
    """
    @asynccontextmanager
    async def infrastructure(_app):
        from src.db.database import get_postgres_pool, init_postgres_pool, close_postgres_pool
        owns_pool = get_postgres_pool() is None
        try:
            if owns_pool:
                await asyncio.to_thread(init_postgres_pool)
            yield
        finally:
            if owns_pool:
                await asyncio.to_thread(close_postgres_pool)

    app = FastAPI(lifespan=infrastructure)
    app.include_router(create_router())
    from src.api.session import router as sessions
    from src.saas.middleware import TenantContextMiddleware
    app.include_router(sessions)
    app.add_middleware(TenantContextMiddleware)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request, _error):
        return JSONResponse(status_code=422, content={'success':False, 'error':'INVALID_REQUEST', 'debug':'INVALID_REQUEST'})

    return app
