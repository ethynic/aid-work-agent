"""Platform-only Feishu destination for Runner long-running alerts."""

import re

from fastapi import APIRouter, Depends, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from loguru import logger

from src.notifications.contracts import (NotificationError, PlatformRunnerTargetRequest,
                                         Subject)
from src.notifications.service import notification_repository


class PlatformAlertRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def safe(request):
            try:
                return await handler(request)
            except RequestValidationError:
                return JSONResponse({'error': 'NOTIFICATION_INVALID_REQUEST',
                                     'debug': 'NOTIFICATION_INVALID_REQUEST'}, status_code=422)
            except NotificationError as error:
                return JSONResponse({'error': error.code, 'debug': error.code},
                                    status_code=error.status)
            except Exception as error:
                logger.opt(exception=True).error('runner alert target api failure kind={}',
                                                 type(error).__name__)
                return JSONResponse({'error': 'NOTIFICATION_UNAVAILABLE',
                                     'debug': 'NOTIFICATION_UNAVAILABLE'}, status_code=503)

        return safe


def create_router(repository=None, user_loader=None):
    repository = repository or notification_repository()
    if user_loader is None:
        from src.services.auth_service import fresh_web_subject
        user_loader = fresh_web_subject
    router = APIRouter(prefix='/api/platform/runner-alert-target',
                       tags=['Runner alerts'], route_class=PlatformAlertRoute)

    def platform_subject(request: Request):
        from src.services.auth_service import AuthSubjectError
        if request.headers.get('X-Tenant-Id'):
            raise NotificationError('NOTIFICATION_FORBIDDEN', 403)
        try:
            user = user_loader(request.headers.get('Authorization', ''))
        except AuthSubjectError:
            raise NotificationError('NOTIFICATION_UNAUTHORIZED', 401) from None
        if user['role'] != 'platform_admin' or user['tenant_id'] is not None:
            raise NotificationError('NOTIFICATION_FORBIDDEN', 403)
        return Subject(user['user_id'], None)

    @router.get('')
    def get_target(subject: Subject = Depends(platform_subject)):
        return {'success': True, 'target': repository.get_platform_runner_target(subject)}

    @router.put('')
    def put_target(body: PlatformRunnerTargetRequest,
                   subject: Subject = Depends(platform_subject)):
        return {'success': True, 'target': repository.register_platform_runner_target(subject, body)}

    @router.delete('')
    def delete_target(request: Request, subject: Subject = Depends(platform_subject)):
        value = request.headers.get('X-Notification-Binding-Version', '')
        if not re.fullmatch(r'[1-9][0-9]{0,17}', value):
            raise NotificationError('NOTIFICATION_INVALID_BINDING_VERSION')
        return {'success': True, 'target': repository.revoke_platform_runner_target(subject, int(value))}

    return router


router = create_router()
