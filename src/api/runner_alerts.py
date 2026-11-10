"""Platform administrator views and thresholds for Runner duration alerts."""

import asyncio

from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.saas.context import get_current_tenant_id
from src.services.auth_service import AuthSubjectError, fresh_web_subject
from src.services.agent_runner.runner_alerts import DEFAULT_THRESHOLD_MINUTES, RunnerAlertRepository


router = APIRouter(prefix='/api/platform', tags=['Runner 运行告警'])


class ThresholdUpdate(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    tenant_id: str = Field(min_length=1, max_length=128)
    profile_id: str = Field(min_length=1, max_length=128)
    minutes: int = Field(ge=1)


async def _require_platform_context(request):
    try:
        user = await asyncio.to_thread(fresh_web_subject,
                                       request.headers.get('Authorization', ''))
    except AuthSubjectError:
        raise HTTPException(401, detail={'error': '未登录', 'debug': 'AUTH_REQUIRED'}) from None
    if (user.get('role') != 'platform_admin' or user.get('tenant_id') is not None
            or get_current_tenant_id() is not None or request.headers.get('X-Tenant-Id')):
        raise HTTPException(403, detail={'error': '需要平台管理员全局权限',
                                         'debug': 'PLATFORM_CONTEXT_REQUIRED'})


def _invalid_request():
    raise HTTPException(400, detail={'error': '告警请求参数无效', 'debug': 'RUNNER_ALERT_INVALID_REQUEST'})


def _bounded_id(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= 128
            or not value.strip() or value != value.strip()):
        _invalid_request()
    return value


def _page_param(value, *, default, maximum=None):
    if value is None:
        return default
    try:
        number = int(value)
    except ValueError:
        _invalid_request()
    if number < 1 or (maximum is not None and number > maximum):
        _invalid_request()
    return number


async def _read(call, *args, **kwargs):
    try:
        return await asyncio.to_thread(call, *args, **kwargs)
    except HTTPException:
        raise
    except Exception:
        logger.opt(exception=True).error('Runner alert API storage unavailable')
        raise HTTPException(500, detail={'error': '告警数据暂不可用',
                                         'debug': 'RUNNER_ALERT_STORAGE_UNAVAILABLE'}) from None


@router.get('/runner-alerts')
async def list_runner_alerts(request: Request):
    await _require_platform_context(request)
    query = request.query_params
    state = query.get('state', 'all')
    if state not in ('all', 'current', 'history'):
        _invalid_request()
    runner_id = _bounded_id(query['runner_id']) if 'runner_id' in query else None
    page = _page_param(query.get('page'), default=1)
    page_size = _page_param(query.get('page_size'), default=20, maximum=100)
    repository = RunnerAlertRepository()
    result = await _read(repository.list_alerts, state=state, runner_id=runner_id,
                         page=page, page_size=page_size)
    result['monitor'] = await _read(repository.monitor_status)
    return {'success': True, **result}


@router.get('/runner-alerts/{alert_id}')
async def get_runner_alert(request: Request, alert_id: str):
    await _require_platform_context(request)
    result = await _read(RunnerAlertRepository().detail, _bounded_id(alert_id))
    if result is None:
        raise HTTPException(404, detail={'error': '告警不存在', 'debug': 'RUNNER_ALERT_NOT_FOUND'})
    return {'success': True, **result}


@router.get('/runner-alert-thresholds')
async def get_runner_alert_thresholds(request: Request):
    await _require_platform_context(request)
    overrides = await _read(RunnerAlertRepository().thresholds)
    return {'success': True, 'default_minutes': DEFAULT_THRESHOLD_MINUTES,
            'overrides': overrides}


@router.put('/runner-alert-thresholds')
async def put_runner_alert_threshold(request: Request):
    await _require_platform_context(request)
    try:
        body = ThresholdUpdate.model_validate(await request.json())
    except (ValidationError, ValueError):
        _invalid_request()
    _bounded_id(body.tenant_id)
    _bounded_id(body.profile_id)
    await _read(RunnerAlertRepository().set_threshold, body.tenant_id,
                body.profile_id, body.minutes)
    return {'success': True}


@router.delete('/runner-alert-thresholds')
async def delete_runner_alert_threshold(request: Request):
    await _require_platform_context(request)
    tenant_id = _bounded_id(request.query_params.get('tenant_id'))
    profile_id = _bounded_id(request.query_params.get('profile_id'))
    await _read(RunnerAlertRepository().delete_threshold, tenant_id, profile_id)
    return {'success': True}
