"""Authenticated notification capabilities, bindings, preferences and resource reads."""
import re

from fastapi import APIRouter, Depends, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from loguru import logger

from .contracts import (NotificationError, PreferenceRequest, RegisterTarget, Subject,
                        TenantRegisterTarget, TestRequest)
from .service import notification_repository


class NotificationRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def safe(request):
            try:
                return await handler(request)
            except RequestValidationError:
                # Validation errors echo the subscription keys in their input.
                return JSONResponse({'error':'NOTIFICATION_INVALID_REQUEST'}, status_code=422)
            except NotificationError as error:
                return JSONResponse({'error':error.code}, status_code=error.status)
            except Exception as error:
                logger.error('notification api failure class={}', type(error).__name__)
                return JSONResponse({'error':'NOTIFICATION_UNAVAILABLE'}, status_code=503)
        return safe


def create_router(config=None, repository=None, user_loader=None):
    if config is None:
        from src.config.settings import settings
        config = settings.notifications
    repository = repository or notification_repository(config)
    if user_loader is None:
        from src.services.auth_service import fresh_web_subject
        user_loader = fresh_web_subject
    router = APIRouter(prefix='/api/notifications',tags=['Notifications'],route_class=NotificationRoute)

    def subject(request: Request):
        return _resolve_subject(request)

    def admin_subject(request: Request):
        return _resolve_subject(request, admin_only=True)

    def _resolve_subject(request: Request, admin_only: bool = False):
        from src.services.auth_service import AuthSubjectError
        try:
            user = user_loader(request.headers.get('Authorization',''))
        except AuthSubjectError:
            raise NotificationError('NOTIFICATION_UNAUTHORIZED', 401) from None
        requested = request.headers.get('X-Tenant-Id') or None
        platform = user['role'] == 'platform_admin'
        tenant = requested if platform else user['tenant_id']
        if (requested and not platform and requested != tenant) or (tenant is None and not platform):
            raise NotificationError('NOTIFICATION_FORBIDDEN', 403)
        if admin_only and user['role'] not in ('tenant_admin', 'platform_admin'):
            raise NotificationError('NOTIFICATION_FORBIDDEN', 403)
        return Subject(user['user_id'],tenant)

    def enabled():
        repository.assert_schema()

    @router.get('/capabilities')
    def capabilities(current: Subject = Depends(subject)):
        with repository.connection_factory() as connection:
            repository.guard(connection.cursor(),current)
        repository.assert_schema()
        adapter = repository.registry.get('web_push','web_push')
        available = bool(adapter and adapter.available())
        return {'schema_version':1,'enabled':True,
                'web_push':{'enabled':available,'public_key':config.web_push.public_key if available else None,
                            'key_id':config.web_push.key_id if available else None}}

    @router.get('/targets')
    def targets(current: Subject = Depends(subject)):
        enabled()
        return {'targets':repository.targets(current)}

    @router.post('/targets')
    def register(body: RegisterTarget, current: Subject = Depends(subject)):
        enabled()
        return repository.register_target(current, body)

    @router.delete('/targets/{target_id}')
    def revoke(target_id: str, request: Request, current: Subject = Depends(subject)):
        enabled()
        value = request.headers.get('X-Notification-Binding-Version','')
        if not re.fullmatch(r'[1-9][0-9]{0,17}',value):
            raise NotificationError('NOTIFICATION_INVALID_BINDING_VERSION')
        target = repository.revoke_target(current,target_id,int(value))
        return {'success':True,'target':target}

    @router.get('/preferences')
    def preferences(current: Subject = Depends(subject)):
        enabled()
        return {'preferences':repository.preferences(current)}

    @router.put('/preferences')
    def preference(body: PreferenceRequest, current: Subject = Depends(subject)):
        enabled()
        return repository.set_preference(current,body)

    @router.post('/test',status_code=202)
    def test(body: TestRequest, current: Subject = Depends(subject)):
        enabled()
        return {'notification_ref':repository.test(current,body)}

    @router.get('/tenant-targets')
    def tenant_targets(current: Subject = Depends(admin_subject)):
        enabled()
        return {'targets':repository.tenant_targets(current)}

    @router.post('/tenant-targets')
    def register_tenant_target(body: TenantRegisterTarget, current: Subject = Depends(admin_subject)):
        enabled()
        return repository.register_tenant_target(current, body)

    @router.delete('/tenant-targets/{target_id}')
    def revoke_tenant_target(target_id: str, request: Request, current: Subject = Depends(admin_subject)):
        enabled()
        value = request.headers.get('X-Notification-Binding-Version','')
        if not re.fullmatch(r'[1-9][0-9]{0,17}',value):
            raise NotificationError('NOTIFICATION_INVALID_BINDING_VERSION')
        target = repository.revoke_tenant_target(current,target_id,int(value))
        return {'success':True,'target':target}

    @router.post('/tenant-test',status_code=202)
    def tenant_test(body: TestRequest, current: Subject = Depends(admin_subject)):
        enabled()
        return {'notification_ref':repository.tenant_test(current,body)}

    @router.get('/inbox')
    def inbox(request: Request, current: Subject = Depends(subject)):
        enabled()
        unread_only = request.query_params.get('unread_only','') in ('1','true')
        try:
            limit = int(request.query_params.get('limit','20'))
        except ValueError:
            limit = 20
        return repository.inbox(current,unread_only=unread_only,limit=max(1,min(limit,100)))

    @router.post('/inbox/{notification_ref}/read')
    def inbox_read(notification_ref: str, current: Subject = Depends(subject)):
        enabled()
        if not re.fullmatch(r'[a-f0-9]{32}', notification_ref):
            raise NotificationError('NOTIFICATION_NOT_FOUND',404)
        return repository.mark_inbox_read(current,notification_ref)

    @router.get('/messages/{notification_ref}')
    def resolve(notification_ref: str, current: Subject = Depends(subject)):
        enabled()
        if not re.fullmatch(r'[a-f0-9]{32}', notification_ref):
            raise NotificationError('NOTIFICATION_NOT_FOUND',404)
        return repository.resolve(current,notification_ref)

    return router


router = create_router()
