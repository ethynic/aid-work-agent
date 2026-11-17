"""Tenant and owner scoped management for WeCom scheduled text sends."""

import asyncio
from typing import Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from src.local_tools.repository import list_devices
from src.session_tasks.api import _current_user_and_tenant, _err, _from_service_error, _ok
from src.session_tasks.constants import SessionTaskError

from . import proxy_tasks, scheduled_sends as schedules, service
from .api import _resolve_directory_targets, _service_error

router = APIRouter(prefix='/api/wecom-conversations/scheduled-sends', tags=['wecom-scheduled-sends'])


class PlanWrite(BaseModel):
    model_config = ConfigDict(extra='forbid')

    name: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=2000)
    sessions: list[str] = Field(min_length=1, max_length=proxy_tasks.SESSIONS_MAX)
    schedule_type: Literal['once', 'daily', 'weekly']
    time_config: dict
    expected_device_id: str | None = Field(default=None, min_length=1, max_length=256)
    expected_source_generation: str | None = Field(default=None, min_length=1, max_length=256)

    def plan_fields(self):
        values = self.model_dump(exclude={'expected_device_id', 'expected_source_generation'})
        values['name'] = proxy_tasks._validate_title(self.name)
        proxy_tasks.validate_opening_message(self.text, field_name='定时发送消息')
        schedules._time_fields(self.schedule_type, self.time_config)
        values['sessions'] = proxy_tasks._validate_sessions(self.sessions)
        return values


def _display_plans(tenant_id, user_id, plans):
    if not plans:
        return []
    locators = list({locator for plan in plans for locator in plan['sessions']})
    conversations = service.conversations_for_locators(tenant_id, user_id, locators)
    names = {(str(row['device_id']), row['session_locator']): row['name'] for row in conversations}
    devices = {str(row['id']): row['name'] for row in list_devices(tenant_id, user_id)}
    return [{**plan, 'device_name': devices.get(plan['device_id']),
             'session_names': {locator: names.get((plan['device_id'], locator)) for locator in plan['sessions']}}
            for plan in plans]


async def _plan_response(tenant_id, user_id, plan, *, status_code=200):
    displayed = await asyncio.to_thread(_display_plans, tenant_id, user_id, [plan])
    return _ok({'plan': displayed[0]}, status_code=status_code)


def _write_error(exc):
    if isinstance(exc, SessionTaskError):
        return _from_service_error(exc)
    return _service_error(exc)


@router.get('')
async def list_plans(request: Request, limit: int = Query(20, ge=1, le=50), offset: int = Query(0, ge=0)):
    try:
        tenant_id, user_id = await _current_user_and_tenant(request)
        plans = await asyncio.to_thread(schedules.list_plans, tenant_id, user_id, limit=limit + 1, offset=offset)
        rows = await asyncio.to_thread(_display_plans, tenant_id, user_id, plans[:limit])
        return _ok({'plans': rows, 'has_more': len(plans) > limit})
    except SessionTaskError as exc:
        return _from_service_error(exc)


_REASONS = {
    'NO_CREDIT': '租户积分不足', 'SEND_WINDOW_EXPIRED': '超过计划时间 10 分钟',
    'SEND_NOT_STARTED': '设备未开始发送', 'PLAN_PAUSED': '配置已暂停',
    'PLAN_DELETED': '配置已删除', 'PLAN_INACTIVE': '配置未启用',
    'TENANT_UNAVAILABLE': '租户已停用', 'EXECUTION_UNKNOWN': '缺少可确认的发送回执',
    'DESKTOP_RESOURCE_BUSY': '设备桌面正忙',
    'WECOM_NOT_FOUND': '设备未运行企业微信', 'NOT_LOGGED_IN': '企业微信未登录',
    'WINDOW_AMBIGUOUS': '无法确定企业微信主窗口',
    'TARGET_NOT_FOUND': '企业微信找不到目标会话', 'TARGET_REF_STALE': '目标会话或登录账号已变化',
    'TARGET_AMBIGUOUS': '匹配到多个会话，无法确认目标',
    'IDENTITY_UNCONFIRMED': '无法确认企业微信账号', 'CONFIG_MISSING': '设备上的企业微信配置不完整',
    'LOCALDB_UNAVAILABLE': '企业微信本地会话库不可用', 'KEY_SCAN_FAILED': '无法解锁企业微信本地会话库',
    'CONTENT_UNAVAILABLE': '无法读取发送所需的会话或窗口信息',
    'VERSION_UNSUPPORTED': '企业微信版本不支持本次操作', 'SNAPSHOT_UNSTABLE': '企业微信本地会话库暂不可用',
    'TOOL_NOT_SUPPORTED': '设备上的企业微信插件缺少发送能力',
}


def _display_execution(row):
    code = row['error_code']
    reason = _REASONS.get(code) or ({'failed': '本次发送失败', 'expired': '超过计划时间 10 分钟',
                                   'unknown': '缺少可确认的发送回执'}.get(row['status']))
    diagnostic_id = str(row['invocation_id'] or row['id']) if row['status'] in ('failed', 'expired', 'unknown') else None
    return {**row, 'reason': reason, 'diagnostic_id': diagnostic_id}


@router.get('/executions')
async def list_executions(request: Request, task_id: str | None = Query(None, max_length=150),
                          limit: int = Query(20, ge=1, le=50), offset: int = Query(0, ge=0)):
    try:
        tenant_id, user_id = await _current_user_and_tenant(request)
        rows = await asyncio.to_thread(schedules.list_executions, tenant_id, user_id, task_id=task_id,
                                       limit=limit + 1, offset=offset)
        return _ok({'executions': [_display_execution(row) for row in rows[:limit]], 'has_more': len(rows) > limit})
    except SessionTaskError as exc:
        return _from_service_error(exc)


@router.post('')
async def create_plan(request: Request, body: PlanWrite):
    try:
        tenant_id, user_id = await _current_user_and_tenant(request)
        fields = body.plan_fields()
        if not body.expected_device_id or not body.expected_source_generation:
            return _err(400, '请从当前设备的企微目录选择会话', 'DIRECTORY_SOURCE_REQUIRED')
        binding, error = await _resolve_directory_targets(tenant_id, user_id, fields['sessions'],
            expected_device=body.expected_device_id, expected_generation=body.expected_source_generation)
        if error:
            return error
        plan = await asyncio.to_thread(schedules.create_plan, tenant_id, user_id, **fields,
            device_id=binding['device_id'], account=binding['account'], verified_targets=binding['targets'])
        return await _plan_response(tenant_id, user_id, plan, status_code=201)
    except (SessionTaskError, service.ConversationServiceError) as exc:
        return _write_error(exc)


@router.get('/{task_id}')
async def get_plan(task_id: str, request: Request):
    try:
        tenant_id, user_id = await _current_user_and_tenant(request)
        plan = await asyncio.to_thread(schedules.get_plan, tenant_id, user_id, task_id)
        if plan is None:
            return _err(404, '企微定时发送配置不存在或无权访问', 'PLAN_NOT_FOUND')
        return await _plan_response(tenant_id, user_id, plan)
    except SessionTaskError as exc:
        return _from_service_error(exc)


@router.patch('/{task_id}')
async def edit_plan(task_id: str, request: Request, body: PlanWrite):
    try:
        tenant_id, user_id = await _current_user_and_tenant(request)
        existing = await asyncio.to_thread(schedules.get_plan, tenant_id, user_id, task_id)
        if existing is None:
            return _err(404, '企微定时发送配置不存在或无权访问', 'PLAN_NOT_FOUND')
        if existing['status'] != 'paused':
            return _err(409, '请先暂停计划后再编辑', 'PLAN_STATE_CONFLICT')
        fields = body.plan_fields()
        added = [locator for locator in fields['sessions'] if locator not in existing['sessions']]
        verified = None
        if added:
            if body.expected_device_id != existing['device_id'] or not body.expected_source_generation:
                return _err(409, '请在原设备的企微目录中选择新增会话', 'DEVICE_MISMATCH')
            binding, error = await _resolve_directory_targets(tenant_id, user_id, added,
                expected_device=existing['device_id'], expected_generation=body.expected_source_generation,
                expected_account=existing['account'])
            if error:
                return error
            verified = binding['targets']
        plan = await asyncio.to_thread(schedules.edit_plan, tenant_id, user_id, task_id, **fields,
            expected_sessions=existing['sessions'], verified_targets=verified)
        return await _plan_response(tenant_id, user_id, plan)
    except (SessionTaskError, service.ConversationServiceError) as exc:
        return _write_error(exc)


@router.post('/{task_id}/{action}')
async def change_plan(task_id: str, action: Literal['enable', 'pause'], request: Request):
    try:
        tenant_id, user_id = await _current_user_and_tenant(request)
        operation = schedules.enable_plan if action == 'enable' else schedules.pause_plan
        plan = await asyncio.to_thread(operation, tenant_id, user_id, task_id)
        return await _plan_response(tenant_id, user_id, plan)
    except (SessionTaskError, service.ConversationServiceError) as exc:
        return _write_error(exc)


@router.delete('/{task_id}')
async def delete_plan(task_id: str, request: Request):
    try:
        tenant_id, user_id = await _current_user_and_tenant(request)
        await asyncio.to_thread(schedules.delete_plan, tenant_id, user_id, task_id)
        return _ok({'task_id': task_id})
    except (SessionTaskError, service.ConversationServiceError) as exc:
        return _write_error(exc)
