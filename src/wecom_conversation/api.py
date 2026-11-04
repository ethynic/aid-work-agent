"""企微会话旁路日志与日报 API（设计 §5/§6，`/api/wecom-conversations` 前缀）。

复用现有认证/租户上下文与 `_ok/_err` 响应风格；同租户当前创建用户可见。
没有人工记录开关、持续采集管理、处理状态编辑或附件原件入口（设计 §7）；
页面查询只读已保存数据，进入页面不启动任务。
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import date as date_type

from fastapi import APIRouter, Query, Request

from src.api.agent_runner_web import error_response as _runner_error_response
from src.session_tasks.api import _current_user_and_tenant, _err, _from_service_error, _ok
from src.session_tasks.constants import SessionTaskError
from src.services.agent_runner.contracts import RunnerError

from . import plans, service

router = APIRouter(prefix="/api/wecom-conversations", tags=["wecom-conversations"])


def _service_error(exc: service.ConversationServiceError):
    return _err(exc.status, str(exc), exc.code)


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
    """当前用户的企微日报计划（scheduled_tasks 行 + 会话数）。"""
    try:
        tenant_id, user_id = await _current_user_and_tenant(request)

        def _load():
            from src.db.database import get_db_connection
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    '''SELECT t.*, (SELECT count(*) FROM bs_wecom_conversations c
                                    WHERE c.daily_plan_task_id = t.task_id
                                      AND c.tenant_id = t.tenant_id AND c.user_id = t.user_id) AS conversation_count
                       FROM scheduled_tasks t
                       WHERE t.tenant_id=%s AND t.user_id=%s AND t.status != 'deleted'
                         AND t.task_prompt LIKE %s
                       ORDER BY t.created_at DESC LIMIT 100''',
                    (tenant_id, user_id, plans.DAILY_MARKER + '%'))
                return [dict(row) for row in cursor.fetchall()]

        rows = await asyncio.to_thread(_load)
        return _ok({'plans': [_public_plan(row) for row in rows]})
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
    """建立/扩展日报计划：同来源复用合适计划，一个计划管理多个会话。

    会话以稳定 ws1 标识登记（Agent 引导或用户选择均可）；明确拒绝日报则不
    安排（前端不调用本接口）。
    """
    try:
        tenant_id, user_id = await _current_user_and_tenant(request)
        body = await request.json()
        locators = body.get('session_locators')
        if not isinstance(locators, list) or not locators or len(locators) > 50:
            return _err(400, 'session_locators 必须为 1–50 个稳定会话标识（ws1.）', 'VALIDATION_FAILED')
        schedule = body.get('schedule')
        if not isinstance(schedule, dict) or not schedule.get('schedule_type'):
            return _err(400, 'schedule 缺少 schedule_type', 'VALIDATION_FAILED')
        profile_id = body.get('profile_id')
        if profile_id is not None and (not isinstance(profile_id, str) or not (1 <= len(profile_id) <= 128)):
            return _err(400, 'profile_id 取值非法', 'VALIDATION_FAILED')
        result = await asyncio.to_thread(
            plans.ensure_daily_plan, tenant_id, user_id, locators=locators,
            schedule=schedule, profile_id=profile_id,
            timezone_name=body.get('timezone'), device_id=body.get('device_id'),
            requirements=body.get('requirements') or '',
            instance_id=body.get('instance_id'),
            notification_rule_id=body.get('notification_rule_id'))
        return _ok({'plan': _public_plan(result['plan']), 'created': result['created'],
                    'conversations': result['conversations']}, status_code=201)
    except service.ConversationServiceError as exc:
        return _service_error(exc)
    except SessionTaskError as exc:
        return _from_service_error(exc)
    except ValueError as exc:
        return _err(400, str(exc), 'SCHEDULE_INVALID')


@router.get("/daily-reports")
async def daily_reports(request: Request, business_date: date_type,
                        conversation_ids: str | None = Query(None, max_length=500),
                        task_id: str | None = Query(None, max_length=64)):
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
            rows = service.daily_summaries_for_date(tenant_id, user_id, business_date,
                                                    conversation_ids=wanted)
            generation = service.last_daily_generation(tenant_id, user_id, business_date,
                                                       task_id=task_id or None)
            return rows, generation

        rows, generation = await asyncio.to_thread(_load)
        return _ok({'business_date': str(business_date), 'reports': rows,
                    'last_generation': generation})
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
    """手动生成/重算：一个计划或一组会话在指定业务日的日报（沿现有 Runner）。"""
    try:
        tenant_id, user_id = await _current_user_and_tenant(request)
        body = await request.json()
        business_date = body.get('business_date')
        if not isinstance(business_date, str) or len(business_date) != 10:
            return _err(400, 'business_date 须为 YYYY-MM-DD', 'VALIDATION_FAILED')
        try:
            date_type.fromisoformat(business_date)
        except ValueError:
            return _err(400, 'business_date 须为 YYYY-MM-DD', 'VALIDATION_FAILED')
        conversation_ids = body.get('conversation_ids')
        if conversation_ids is not None and (not isinstance(conversation_ids, list)
                                             or not conversation_ids or len(conversation_ids) > 50):
            return _err(400, 'conversation_ids 必须为 1–50 个业务会话ID', 'VALIDATION_FAILED')
        client_request_id = body.get('client_request_id')
        if client_request_id is not None and (not isinstance(client_request_id, str)
                                              or not (1 <= len(client_request_id) <= 128)):
            return _err(400, 'client_request_id 须为 1–128 字符', 'VALIDATION_FAILED')
        try:
            submitted = await plans.submit_manual_report(
                request.headers.get('Authorization', ''), tenant_id, user_id,
                conversation_ids=conversation_ids,
                plan_task_id=body.get('plan_task_id'), business_date=business_date,
                client_request_id=client_request_id or None,
                timezone_name=body.get('timezone'), device_id=body.get('device_id'),
                profile_id=body.get('profile_id'))
        except RunnerError as exc:
            return _runner_error_response(exc)
        return _ok({'business_date': business_date, **submitted})
    except service.ConversationServiceError as exc:
        return _service_error(exc)
    except SessionTaskError as exc:
        return _from_service_error(exc)
