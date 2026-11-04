"""企微会话旁路日志与日报 API（设计 §5/§6，`/api/wecom-conversations` 前缀）。

复用现有认证/租户上下文与 `_ok/_err` 响应风格；同租户当前创建用户可见。
没有人工记录开关、持续采集管理、处理状态编辑或附件原件入口（设计 §7）；
页面查询只读已保存数据，进入页面不启动任务。
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import json
import uuid
from datetime import date as date_type
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query, Request

from src.api.agent_runner_web import error_response as _runner_error_response
from src.session_tasks.api import _current_user_and_tenant, _err, _from_service_error, _ok
from src.session_tasks.constants import SessionTaskError
from src.services.agent_runner.contracts import RunnerError

from . import plans, service

router = APIRouter(prefix="/api/wecom-conversations", tags=["wecom-conversations"])


def _service_error(exc: service.ConversationServiceError):
    return _err(exc.status, str(exc), exc.code)


async def _directory_device(tenant_id, user_id):
    from src.local_tools.proxy_tool import WecomSessionDirectoryTool
    tool = WecomSessionDirectoryTool()
    device, error = await tool._find_ready_device(tenant_id, user_id)
    if device is None:
        if error and '不支持企业微信操作' in error:
            return tool, None, _err(409, '当前选定设备缺少会话目录能力，请安装最新版 Runtime 和 CLI', 'TOOL_NOT_SUPPORTED')
        return tool, None, _err(409, error or '当前选定设备不可用', 'DEVICE_UNAVAILABLE')
    capabilities = (device.get('capabilities_json') or {}).get('capabilities') or []
    if tool.name not in capabilities:
        return tool, None, _err(409, '当前选定设备缺少会话目录能力，请安装最新版 Runtime 和 CLI', 'TOOL_NOT_SUPPORTED')
    return tool, device, None


async def _directory_call(tool, tenant_id, user_id, device, arguments):
    result = await tool._dispatch_and_wait(tenant_id, user_id, None, device, arguments, None)
    if not result.get('success'):
        code = result.get('code') or 'DIRECTORY_UNAVAILABLE'
        message = ('当前设备缺少会话目录能力，请安装最新版 Runtime 和 CLI' if code in
                   ('TOOL_NOT_SUPPORTED', 'TOOL_NOT_ALLOWED', 'LOCAL_DEVICE_UNSUPPORTED') else
                   result.get('message') or '本地会话目录读取失败')
        return None, _err(409, message, code)
    data = result.get('data')
    if not isinstance(data, dict) or data.get('schema_version') != 1 or not data.get('self_user_id'):
        return None, _err(409, '本地会话目录结果不完整，请升级企业微信插件', 'DIRECTORY_SCHEMA_INVALID')
    return data, None


@router.get('/directory')
async def list_local_directory(request: Request, query: str = Query('', max_length=100),
                               cursor: str | None = Query(None, max_length=4096),
                               limit: int = Query(20, ge=1, le=50)):
    try:
        tenant_id, user_id = await _current_user_and_tenant(request)
        tool, device, error = await _directory_device(tenant_id, user_id)
        if error:
            return error
        data, error = await _directory_call(tool, tenant_id, user_id, device,
                                             {'mode': 'list', 'query': query, 'limit': limit,
                                              **({'cursor': cursor} if cursor else {})})
        if error:
            return error
        conversations = data.get('conversations')
        if not isinstance(conversations, list):
            return _err(409, '本地会话目录结果不完整，请升级企业微信插件', 'DIRECTORY_SCHEMA_INVALID')
        known = await asyncio.to_thread(service.conversations_for_locators, tenant_id, user_id,
                                        [row['session'] for row in conversations])
        by_locator = {row['session_locator']: row for row in known}
        for row in conversations:
            existing = by_locator.get(row['session'])
            row['added'] = existing is not None
            if existing is not None:
                row['conversation_id'] = existing['id']
        return _ok({'device_id': str(device['id']), **data})
    except SessionTaskError as exc:
        return _from_service_error(exc)


@router.get('/directory/preview')
async def preview_local_conversation(request: Request, session: str = Query(..., max_length=2048)):
    try:
        tenant_id, user_id = await _current_user_and_tenant(request)
        tool, device, error = await _directory_device(tenant_id, user_id)
        if error:
            return error
        data, error = await _directory_call(tool, tenant_id, user_id, device,
                                             {'mode': 'preview', 'session': session, 'limit': 3})
        if error:
            return error
        return _ok({'device_id': str(device['id']), **data})
    except SessionTaskError as exc:
        return _from_service_error(exc)


@router.post('/conversations/add')
async def add_local_conversations(request: Request):
    try:
        tenant_id, user_id = await _current_user_and_tenant(request)
        body = await request.json()
        sessions = body.get('sessions') if isinstance(body, dict) else None
        if not isinstance(sessions, list) or not sessions or any(
                not isinstance(value, str) or not value.startswith('ws1.') or len(value) > 2048 for value in sessions):
            return _err(400, '请选择本地目录返回的稳定会话标识', 'VALIDATION_FAILED')
        expected_device = body.get('expected_device_id')
        expected_generation = body.get('expected_source_generation')
        if any(value is not None and (not isinstance(value, str) or not value or len(value) > 256)
               for value in (expected_device, expected_generation)):
            return _err(400, '目录来源预期值无效', 'VALIDATION_FAILED')
        sessions = list(dict.fromkeys(sessions))
        tool, device, error = await _directory_device(tenant_id, user_id)
        if error:
            return error
        if expected_device is not None and expected_device != str(device['id']):
            return _err(409, '选定设备已改变，请重新打开添加会话', 'DEVICE_MISMATCH')
        account = generation = None
        targets = []
        for start in range(0, len(sessions), 50):
            current, ready_error = await tool._find_ready_device(tenant_id, user_id)
            if current is None or str(current['id']) != str(device['id']):
                return _err(409, '选定设备已改变，请重新打开添加会话', 'DEVICE_MISMATCH')
            chunk = sessions[start:start + 50]
            data, error = await _directory_call(tool, tenant_id, user_id, device,
                                                 {'mode': 'resolve', 'sessions': chunk})
            if error:
                return error
            if account is None:
                account, generation = data['self_user_id'], data.get('source_generation')
                if expected_generation is not None and expected_generation != generation:
                    return _err(409, '企微本地库已变化，请重新搜索会话', 'IDENTITY_UNCONFIRMED')
            if account != data['self_user_id'] or generation != data.get('source_generation') or not generation:
                return _err(409, '企微账号或本地库在添加期间改变，请重试', 'IDENTITY_UNCONFIRMED')
            resolved = data.get('resolved_targets')
            if not isinstance(resolved, list) or [row.get('session') for row in resolved if isinstance(row, dict)] != chunk:
                return _err(409, '本地会话核验结果不完整，未添加任何会话', 'DIRECTORY_SCHEMA_INVALID')
            targets.extend(resolved)
        current, ready_error = await tool._find_ready_device(tenant_id, user_id)
        if current is None or str(current['id']) != str(device['id']):
            return _err(409, '选定设备已改变，请重新打开添加会话', 'DEVICE_MISMATCH')
        rows = await asyncio.to_thread(service.add_verified_conversations, tenant_id, user_id,
                                       targets, account=account, device_id=str(device['id']))
        return _ok({'conversations': [item['conversation'] for item in rows],
                    'added_count': sum(item['created'] for item in rows)})
    except service.ConversationServiceError as exc:
        return _service_error(exc)
    except SessionTaskError as exc:
        return _from_service_error(exc)


@router.get("/conversations")
async def list_conversations(request: Request,
                             limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0)):
    """记录范围会话列表（自动纳入；含来源账号/设备与最近日志状态）。"""
    try:
        tenant_id, user_id = await _current_user_and_tenant(request)
        rows = await asyncio.to_thread(service.list_conversations, tenant_id, user_id,
                                       limit=limit, offset=offset)
        return _ok({'conversations': rows})
    except SessionTaskError as exc:
        return _from_service_error(exc)


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: int, request: Request):
    try:
        tenant_id, user_id = await _current_user_and_tenant(request)
        row = await asyncio.to_thread(service.get_conversation, tenant_id, user_id, conversation_id)
        if row is None:
            return _err(404, '会话不存在或无权访问', 'CONVERSATION_NOT_FOUND')
        return _ok({'conversation': row})
    except SessionTaskError as exc:
        return _from_service_error(exc)


@router.get("/conversations/{conversation_id}/messages")
async def list_messages(conversation_id: int, request: Request,
                        business_date: date_type | None = None,
                        before_id: int | None = Query(None, ge=0),
                        before_sent: str | None = Query(None, max_length=40),
                        after_id: int | None = Query(None, ge=0),
                        after_sent: str | None = Query(None, max_length=40),
                        undated: bool = False,
                        order: str = Query('asc', pattern='^(asc|desc)$'),
                        limit: int = Query(50, ge=1, le=200)):
    """已保存聊天时间线（不要求 Runtime 在线；分页与排序同键 keyset）。"""
    try:
        tenant_id, user_id = await _current_user_and_tenant(request)
        rows = await asyncio.to_thread(service.list_messages, tenant_id, user_id, conversation_id,
                                       business_date=business_date, before_id=before_id,
                                       before_sent_at=_parse_sent_key(before_sent),
                                       after_id=after_id, after_sent_at=_parse_sent_key(after_sent),
                                       limit=limit, undated=undated, order=order)
        has_more = len(rows) >= min(limit, 200)
        return _ok({'messages': [_public_message(row) for row in rows], 'has_more': has_more})
    except service.ConversationServiceError as exc:
        return _service_error(exc)
    except SessionTaskError as exc:
        return _from_service_error(exc)


def _parse_sent_key(value):
    """复合 keyset 的时间键：ISO 时间或 'infinity'（无日期尾部）。"""
    if value is None or value == '':
        return None
    if value == 'infinity':
        return 'infinity'
    try:
        from datetime import datetime
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise service.ConversationServiceError('KEYSET_INVALID', '分页键 sent 格式非法') from None


def _public_message(row):
    row = dict(row)
    for key in ('source_fields', 'attachment_ref', 'origin_evidence'):
        row[key] = service._json(row.get(key))
    if row.get('sent_at') is not None:
        row['sent_at'] = row['sent_at'].isoformat()
    return row


@router.get("/messages/{message_id}")
async def get_message(message_id: int, request: Request):
    try:
        tenant_id, user_id = await _current_user_and_tenant(request)
        row = await asyncio.to_thread(service.get_message, tenant_id, user_id, message_id)
        return _ok({'message': _public_message(row)})
    except service.ConversationServiceError as exc:
        return _service_error(exc)
    except SessionTaskError as exc:
        return _from_service_error(exc)


# ==================== 日报计划与工作台（设计 §5.1/§6.2） ====================

@router.get("/daily-plans")
async def list_daily_plans(request: Request):
    """Compatibility projection for the daily summary page: zero or one plan."""
    try:
        tenant_id, user_id = await _current_user_and_tenant(request)
        row = await asyncio.to_thread(plans.get_daily_plan, tenant_id, user_id)
        return _ok({'plans': [_public_plan(row)] if row else []})
    except service.ConversationServiceError as exc:
        return _service_error(exc)
    except SessionTaskError as exc:
        return _from_service_error(exc)


def _public_plan(row):
    row = dict(row)
    config = plans.parse_plan_prompt(row.pop('task_prompt', '') or '') or {}
    row['config'] = {key: value for key, value in config.items() if key != 'kind'}
    row.pop('pending_snapshot', None)
    return row


@router.post("/daily-plans")
async def create_daily_plan(request: Request):
    """Create the user's one page-owned daily summary plan."""
    return await _write_daily_plan(request, create=True)


@router.put('/daily-plan')
async def update_daily_plan(request: Request):
    return await _write_daily_plan(request, create=False)


@router.get('/daily-plan')
async def get_daily_plan(request: Request):
    try:
        tenant_id, user_id = await _current_user_and_tenant(request)
        row = await asyncio.to_thread(plans.get_daily_plan, tenant_id, user_id)
        return _ok({'plan': _public_plan(row) if row else None})
    except service.ConversationServiceError as exc:
        return _service_error(exc)
    except SessionTaskError as exc:
        return _from_service_error(exc)


async def _write_daily_plan(request: Request, *, create: bool):
    try:
        tenant_id, user_id = await _current_user_and_tenant(request)
        body = await request.json()
        if not isinstance(body, dict) or set(body) - {
                'target_day', 'scope_mode', 'conversation_ids', 'hour', 'minute',
                'interval_hours', 'notification_rule_id'}:
            return _err(400, '计划配置字段无效', 'VALIDATION_FAILED')
        tool, device, error = await _directory_device(tenant_id, user_id)
        if error:
            return error
        identity, error = await _directory_call(
            tool, tenant_id, user_id, device, {'mode': 'list', 'limit': 1})
        if error:
            return error
        current, _ = await tool._find_ready_device(tenant_id, user_id)
        if current is None or str(current['id']) != str(device['id']):
            return _err(409, '选定设备已改变，请重试', 'DEVICE_MISMATCH')
        row = await asyncio.to_thread(
            plans.save_daily_plan, tenant_id, user_id, create=create,
            account=identity['self_user_id'], device=str(device['id']),
            target_day=body.get('target_day', 'yesterday'),
            scope_mode=body.get('scope_mode'),
            conversation_ids=body.get('conversation_ids') or [],
            hour=body.get('hour'), minute=body.get('minute'),
            interval_hours=body.get('interval_hours'),
            notification_rule_id=body.get('notification_rule_id'),
            notification_rule_supplied='notification_rule_id' in body)
        return _ok({'plan': _public_plan(row)}, status_code=201 if create else 200)
    except service.ConversationServiceError as exc:
        return _service_error(exc)
    except SessionTaskError as exc:
        return _from_service_error(exc)


@router.post('/daily-plan/pause')
async def pause_daily_plan(request: Request):
    from src.scheduler.db import ScheduledTaskDB
    try:
        tenant_id, user_id = await _current_user_and_tenant(request)
        plan = await asyncio.to_thread(plans.get_daily_plan, tenant_id, user_id)
        if plan is None:
            return _err(404, '每日总结计划不存在', 'PLAN_NOT_FOUND')
        changed = await asyncio.to_thread(ScheduledTaskDB.pause, plan['task_id'],
                                          tenant_id=tenant_id, user_id=user_id,
                                          allow_special=True)
        if not changed:
            return _err(409, '计划当前状态不可暂停', 'PLAN_STATE_CONFLICT')
        return _ok({'status': 'paused'})
    except service.ConversationServiceError as exc:
        return _service_error(exc)
    except SessionTaskError as exc:
        return _from_service_error(exc)


@router.post('/daily-plan/resume')
async def resume_daily_plan(request: Request):
    from src.scheduler.db import ScheduledTaskDB
    try:
        tenant_id, user_id = await _current_user_and_tenant(request)
        plan = await asyncio.to_thread(plans.get_daily_plan, tenant_id, user_id)
        if plan is None:
            return _err(404, '每日总结计划不存在', 'PLAN_NOT_FOUND')
        config = plans.parse_plan_prompt(plan['task_prompt']) or {}
        tool, device, error = await _directory_device(tenant_id, user_id)
        if error:
            return error
        identity, error = await _directory_call(
            tool, tenant_id, user_id, device, {'mode': 'list', 'limit': 1})
        if error:
            return error
        if (config.get('account') != identity['self_user_id']
                or str(config.get('device')) != str(device['id'])):
            return _err(409, '计划绑定的企微账号或设备与当前选定来源不符', 'PLAN_SOURCE_MISMATCH')
        updated = await asyncio.to_thread(ScheduledTaskDB.resume_recompute,
                                          plan['task_id'], tenant_id=tenant_id,
                                          user_id=user_id, allow_special=True)
        if not updated:
            return _err(409, '计划当前状态不可恢复', 'PLAN_STATE_CONFLICT')
        return _ok({'plan': _public_plan(updated)})
    except service.ConversationServiceError as exc:
        return _service_error(exc)
    except SessionTaskError as exc:
        return _from_service_error(exc)


@router.get("/daily-reports")
async def daily_reports(request: Request, business_date: date_type,
                        conversation_ids: str | None = None,
                        task_id: str | None = Query(None, max_length=64),
                        cursor: int | None = Query(None, ge=0),
                        limit: int = Query(50, ge=1, le=200)):
    """日报工作台按日期聚合：记录范围会话在指定业务日的报告（含缺失会话），
    附本次执行逐会话事实（带 task_id 时按该计划范围取最新事实；评审三 P1-4）。"""
    try:
        tenant_id, user_id = await _current_user_and_tenant(request)
        wanted = None
        if conversation_ids:
            try:
                wanted = [int(item) for item in conversation_ids.split(',') if item]
            except ValueError:
                return _err(400, 'conversation_ids 取值非法', 'VALIDATION_FAILED')

        def _load():
            page = service.daily_summaries_for_date(tenant_id, user_id, business_date,
                                                    conversation_ids=wanted,
                                                    after_id=cursor, limit=limit)
            latest = service.list_daily_executions(tenant_id, user_id, business_date,
                                                   limit=1, task_id=task_id or None)
            generation = (service.get_daily_execution(tenant_id, user_id,
                          latest['executions'][0]['runner_id']) if latest['executions'] else None)
            return page, generation

        page, generation = await asyncio.to_thread(_load)
        return _ok({'business_date': str(business_date), **page,
                    'last_generation': generation})
    except SessionTaskError as exc:
        return _from_service_error(exc)


@router.get('/daily-reports/executions')
async def daily_report_executions(request: Request, business_date: date_type,
                                  cursor: str | None = Query(None, max_length=512),
                                  limit: int = Query(30, ge=1, le=100)):
    try:
        tenant_id, user_id = await _current_user_and_tenant(request)
        before = None
        if cursor:
            try:
                value = json.loads(base64.urlsafe_b64decode(cursor + '=' * (-len(cursor) % 4)))
                before = (datetime.fromisoformat(value['accepted_at']), value['runner_id'])
                if not isinstance(before[1], str) or not 1 <= len(before[1]) <= 128:
                    raise ValueError('invalid runner')
            except (ValueError, KeyError, TypeError, UnicodeDecodeError, binascii.Error):
                return _err(400, '执行记录游标无效', 'VALIDATION_FAILED')
        page = await asyncio.to_thread(service.list_daily_executions, tenant_id, user_id,
                                       business_date, before=before, limit=limit)
        if page['next_cursor']:
            accepted_at, runner_id = page['next_cursor']
            page['next_cursor'] = base64.urlsafe_b64encode(json.dumps({
                'accepted_at': accepted_at, 'runner_id': runner_id}).encode()).decode().rstrip('=')
        return _ok({'business_date': str(business_date), **page})
    except SessionTaskError as exc:
        return _from_service_error(exc)


@router.get('/daily-reports/executions/{runner_id}')
async def daily_report_execution(request: Request, runner_id: str):
    try:
        tenant_id, user_id = await _current_user_and_tenant(request)
        row = await asyncio.to_thread(service.get_daily_execution, tenant_id, user_id, runner_id)
        if row is None:
            return _err(404, '执行记录不存在或无权查看', 'EXECUTION_NOT_FOUND')
        return _ok({'execution': row})
    except SessionTaskError as exc:
        return _from_service_error(exc)


@router.get("/conversations/{conversation_id}/daily-summaries")
async def list_daily_summaries(conversation_id: int, request: Request,
                               before_date: date_type | None = None,
                               limit: int = Query(30, ge=1, le=100)):
    try:
        tenant_id, user_id = await _current_user_and_tenant(request)
        rows = await asyncio.to_thread(service.list_daily_summaries, tenant_id, user_id,
                                       conversation_id, before_date=before_date, limit=limit)
        return _ok({'summaries': rows})
    except SessionTaskError as exc:
        return _from_service_error(exc)


@router.post("/daily-reports/generate")
async def generate_daily_report(request: Request):
    """Update selected conversations for Shanghai today in one Runner."""
    try:
        tenant_id, user_id = await _current_user_and_tenant(request)
        body = await request.json()
        if not isinstance(body, dict) or set(body) - {'conversation_ids', 'client_request_id'}:
            return _err(400, '手动更新仅接受会话 ID 和请求 ID', 'VALIDATION_FAILED')
        conversation_ids = body.get('conversation_ids')
        if (not isinstance(conversation_ids, list) or not conversation_ids
                or any(type(item) is not int or item <= 0 for item in conversation_ids)):
            return _err(400, 'conversation_ids 须为非空业务会话 ID 清单', 'VALIDATION_FAILED')
        conversation_ids = list(dict.fromkeys(conversation_ids))
        client_request_id = body.get('client_request_id')
        if client_request_id is not None and (not isinstance(client_request_id, str)
                                              or not (1 <= len(client_request_id) <= 128)):
            return _err(400, 'client_request_id 须为 1–128 字符', 'VALIDATION_FAILED')
        tool, device, error = await _directory_device(tenant_id, user_id)
        if error:
            return error
        identity, error = await _directory_call(
            tool, tenant_id, user_id, device, {'mode': 'list', 'limit': 1})
        if error:
            return error
        current, _ = await tool._find_ready_device(tenant_id, user_id)
        if current is None or str(current['id']) != str(device['id']):
            return _err(409, '选定设备已改变，请重试', 'DEVICE_MISMATCH')
        await asyncio.to_thread(service.report_conversations, tenant_id, user_id,
                                conversation_ids, account=identity['self_user_id'],
                                device_id=str(device['id']))
        business_date = datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
        try:
            submitted = await plans.submit_manual_report(
                request.headers.get('Authorization', ''), tenant_id, user_id,
                conversation_ids=conversation_ids,
                account=identity['self_user_id'], device_id=str(device['id']),
                business_date=business_date, client_request_id=client_request_id or None)
        except RunnerError as exc:
            return _runner_error_response(exc)
        return _ok({'business_date': business_date, **submitted})
    except service.ConversationServiceError as exc:
        return _service_error(exc)
    except SessionTaskError as exc:
        return _from_service_error(exc)
