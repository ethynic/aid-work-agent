"""服务器结果受理后的企微旁路日志回调（设计 §3.1）。

触发点是服务器已接受并持久化的成功结果：Runtime 沿原通路回传，服务器完成
原校验、原计费与原 ACK 之**后**，由本模块尽力异步保存聊天日志。Agent 不调用
保存命令；CLI 不直连业务库。回调失败只记录脱敏错误，不改变原结果（设计 §7）。

只使用服务器已接受的 invocation 行归属（tenant/user/device），不以重新上报
请求里的自由身份覆盖原归属。幂等靠原消息唯一键；重复事件不重复入库。
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from loguru import logger

from . import archive

READ_TOOLS = {'wecom_read_session', 'wecom_read_sessions'}
SEND_TOOL = 'wecom_message_send'
SEND_STATUS_TOOL = 'wecom_send_status'


def _open_connection():
    """真实连接上下文（评审 P1-4）：打开即用连接，供 with 语句使用。"""
    from src.db.database import get_db_connection
    return get_db_connection()


def _factory():
    """连接工厂（传给 archive.store_*，其内部以 factory() 取连接）。"""
    from src.db.database import get_db_connection
    return get_db_connection


def _data_of(row):
    result = row.get('result_json') or {}
    data = result.get('data')
    return data if isinstance(data, dict) else {}


def _arguments_of(row):
    arguments = row.get('arguments_json')
    return arguments if isinstance(arguments, dict) else {}


def _note_scope_error(tenant_id, user_id, locator, error):
    """日志写入失败：把脱敏错误记到会话 coverage，供页面如实展示（设计 §3.1）。"""
    try:
        from psycopg2.extras import Json
        with _open_connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                '''UPDATE bs_wecom_conversations SET
                       coverage = jsonb_set(COALESCE(coverage, '{}'::jsonb),
                           '{last_log_error}', %s, true),
                       updated_at = CURRENT_TIMESTAMP
                   WHERE tenant_id=%s AND user_id=%s AND session_locator=%s''',
                (Json(f'{type(error).__name__} at '
                      f'{datetime.now(timezone.utc).isoformat()[:19]}'),
                 tenant_id, user_id, locator))
            connection.commit()
    except Exception:
        logger.opt(exception=True).warning(
            f'后端日志：企微旁路日志错误登记失败 tenant={tenant_id} user={user_id}')


def _register_in_connection(connection, send_row, *, locator, reason, name=None,
                            kind=None):
    """范围登记+合适计划并入，同一连接事务内完成后由调用方提交（评审 P2-9）。"""
    conversation, newly = archive.register_scope(
        connection, send_row['tenant_id'], send_row['user_id'], locator, name=name,
        kind=kind, device_id=send_row.get('device_id'), reason=reason)
    if conversation is not None and newly:
        profile = _runner_profile(connection, send_row['tenant_id'],
                                  send_row['user_id'], send_row)
        _join_suitable_plan(connection, send_row['tenant_id'], send_row['user_id'],
                            conversation, profile_id=profile)
    return conversation, newly


def _register_from_send(row, *, locator, reason, name=None, kind=None):
    """Agent 已成功回复的会话自动纳入记录范围并并入合适日报计划（含提交）。"""
    with _open_connection() as connection:
        result = _register_in_connection(connection, row, locator=locator,
                                         reason=reason, name=name, kind=kind)
        connection.commit()
    return result


def _runner_profile(connection, tenant_id, user_id, invocation_row):
    """已接受调用所属 Runner 的智能体配置（计划匹配用；不可确定返回 None）。"""
    runner_id = (invocation_row.get('business_ref') or {}).get('runner_id')
    if not runner_id:
        return None
    cursor = connection.cursor()
    cursor.execute(
        '''SELECT profile_id FROM agent_runners
           WHERE runner_id=%s AND tenant_id=%s AND user_id=%s LIMIT 1''',
        (runner_id, tenant_id, user_id))
    row = cursor.fetchone()
    return row.get('profile_id') if row else None


def _join_suitable_plan(connection, tenant_id, user_id, conversation_row, *, profile_id):
    """新增代理会话沿已授权范围并入合适日报计划（设计 §5.1，评审 P1-7）。

    严格匹配（service.find_suitable_plan）：同来源账号、同设备、可确定时同
    智能体；任一不匹配不并入，由用户显式建立/扩展计划时处理。
    """
    from . import service
    if not conversation_row.get('source_account_id'):
        return None
    with_plan = service.find_suitable_plan(
        tenant_id, user_id,
        device_id=conversation_row.get('device_id'),
        account=conversation_row['source_account_id'],
        profile_id=profile_id)
    if with_plan is None:
        return None
    cursor = connection.cursor()
    cursor.execute(
        '''UPDATE bs_wecom_conversations SET daily_plan_task_id=%s,
               updated_at=CURRENT_TIMESTAMP WHERE id=%s''',
        (with_plan['task_id'], conversation_row['id']))
    return with_plan['task_id']


def _schedule_backfill(tenant_id, user_id, conversation_row, *, device_id):
    """首次纳入范围：提交一次普通只读补录任务（设计 §3.1/§3.3）。

    以一次性定时任务沿现有 Scheduler 派发（服务身份受理、幂等键、退避重试
    均复用既有机制）；补录经 RuntimeExecution 局部业务适配执行日期读取，
    不调用总结模型。提交失败只记录日志，留待后续日期读取补充。
    """
    try:
        from .plans import schedule_backfill_task
        schedule_backfill_task(tenant_id, user_id, conversation_row, device_id=device_id)
    except Exception as error:
        logger.warning(
            f'后端日志：企微首录补录任务提交失败 tenant={tenant_id} user={user_id} '
            f'conversation={conversation_row.get("id")} error={type(error).__name__}')


async def _handle_read(row):
    arguments = _arguments_of(row)
    if arguments.get('attachment') is not None or arguments.get('resolve_only') is True:
        return
    data = _data_of(row)
    if data.get('channel') not in (None, 'local-db'):
        return
    stats = await asyncio.to_thread(
        archive.store_read_result, _factory(), row['tenant_id'], row['user_id'],
        data, arguments, device_id=row.get('device_id'),
        invocation_id=str(row['id']))
    reconciled = data.get('reconciled_sends')
    if isinstance(reconciled, list) and reconciled:
        await asyncio.to_thread(archive.store_reconciled_sends, _factory(),
                                row['tenant_id'], row['user_id'], reconciled)
    if stats.get('messages'):
        logger.debug(f'后端日志：企微旁路日志保存 invocation={row["id"]} stats={stats}')


async def _handle_send(row):
    data = _data_of(row)
    locator = data.get('session')
    conversation = None
    if isinstance(locator, str) and locator.startswith('ws1.'):
        try:
            conversation, newly = await asyncio.to_thread(
                _register_from_send, row, locator=locator, reason='agent_replied',
                name=data.get('title') if isinstance(data.get('title'), str) else None,
                kind=data.get('kind') if isinstance(data.get('kind'), str) else None)
        except Exception as error:
            await asyncio.to_thread(_note_scope_error, row['tenant_id'], row['user_id'],
                                    locator, error)
            conversation, newly = None, False
        if newly and conversation is not None:
            await asyncio.to_thread(_schedule_backfill, row['tenant_id'], row['user_id'],
                                    conversation, device_id=row.get('device_id'))
    fragment = data.get('observed_chat')
    if isinstance(fragment, dict):
        try:
            await asyncio.to_thread(
                archive.store_send_fragment, _factory(), row['tenant_id'],
                row['user_id'], fragment, invocation_id=str(row['id']))
        except Exception as error:
            await asyncio.to_thread(_note_scope_error, row['tenant_id'], row['user_id'],
                                    fragment.get('session'), error)
    reconciled = data.get('reconciled_sends')
    if isinstance(reconciled, list) and reconciled:
        await _store_reconciled(row, reconciled)


async def _store_reconciled(row, reconciled):
    try:
        await asyncio.to_thread(archive.store_reconciled_sends, _factory(),
                                row['tenant_id'], row['user_id'], reconciled)
    except Exception as error:
        logger.opt(exception=True).warning(
            f'后端日志：企微对账归因保存失败 tenant={row["tenant_id"]} '
            f'error={type(error).__name__}')


async def _handle_send_status(row):
    """原 unknown 发送后来经只读对账确认：同一回调补归因并纳入记录范围。"""
    data = _data_of(row)
    if data.get('send_status') != 'confirmed':
        return
    ref = row.get('business_ref') or {}
    original_id = ref.get('send_invocation_id')
    if not original_id:
        return

    def _load_and_register():
        from src.local_tools.repository import observed_invocation
        with _open_connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                '''SELECT id::text, tenant_id, user_id, device_id, state, result_json
                   FROM local_tool_invocations
                   WHERE id=%s AND tenant_id=%s AND user_id=%s
                     AND tool_name='wecom_message_send' AND provider_key='wecom' LIMIT 1''',
                (original_id, row['tenant_id'], row['user_id']))
            original = cursor.fetchone()
            if original is None:
                return None, False, None
            # 迟到成功视图（评审二 P1-7）：原发送可能仍为 unknown 但 _late_result
            # 已含有效 session/observed_chat；保持持久事实，仅读可信完成视图。
            original = observed_invocation(dict(original))
            original_data = (original.get('result_json') or {}).get('data') or {}
            locator = original_data.get('session')
            if not isinstance(locator, str) or not locator.startswith('ws1.'):
                return None, False, None
            send_row = {**row, 'device_id': original.get('device_id')}
            result = _register_in_connection(
                connection, send_row, locator=locator, reason='agent_replied',
                name=original_data.get('title') if isinstance(original_data.get('title'), str) else None)
            connection.commit()
            return *result, original_data.get('observed_chat')

    try:
        conversation, newly, observed = await asyncio.to_thread(_load_and_register)
    except Exception as error:
        logger.opt(exception=True).warning(
            f'后端日志：企微对账确认纳入记录范围失败 tenant={row["tenant_id"]} '
            f'error={type(error).__name__}')
        return
    if newly and conversation is not None:
        await asyncio.to_thread(_schedule_backfill, row['tenant_id'], row['user_id'],
                                conversation, device_id=conversation.get('device_id'))
    # 原发送（曾为 unknown）的核验片段在对账确认后同样尽力保存（设计 §3.1.1）。
    if isinstance(observed, dict):
        fragment = dict(observed)
        if isinstance(data.get('reconciliation'), dict) \
                and data['reconciliation'].get('message_id') is not None:
            fragment.setdefault('confirmed_message_id', data['reconciliation']['message_id'])
        try:
            await asyncio.to_thread(
                archive.store_send_fragment, _factory(), row['tenant_id'],
                row['user_id'], fragment, invocation_id=str(original_id))
        except Exception as error:
            await asyncio.to_thread(_note_scope_error, row['tenant_id'], row['user_id'],
                                    fragment.get('session'), error)


async def on_result_accepted(row):
    """结果已接受后的旁路日志回调入口（fire-and-forget，绝不抛出）。

    迟到成功（持久 state=unknown + _late_result）复用既有 observed_invocation
    可信完成视图参与旁路登记/片段保存；原 unknown 事实不改写、不重发（评审二 P1-7）。
    """
    try:
        from src.local_tools.repository import observed_invocation
        row = observed_invocation(dict(row))
        if row.get('provider_key') != 'wecom' or row.get('state') != 'succeeded':
            return
        tool = row.get('tool_name')
        if tool in READ_TOOLS:
            await _handle_read(row)
        elif tool == SEND_TOOL:
            await _handle_send(row)
        elif tool == SEND_STATUS_TOOL:
            await _handle_send_status(row)
    except Exception as error:
        logger.opt(exception=True).warning(
            f'后端日志：企微旁路日志回调失败 invocation={row.get("id")} '
            f'error={type(error).__name__}')
