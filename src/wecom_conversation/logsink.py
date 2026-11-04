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
                            kind=None, peer_platform=None):
    """登记明确定位的会话；登记本身不创建计划或补录任务。"""
    decoded = archive.decode_locator(locator)
    if decoded is None:
        return None, False
    cursor = connection.cursor()
    cursor.execute('SELECT pg_advisory_xact_lock(hashtext(%s), hashtext(%s))',
                   (str(send_row['tenant_id']), str(send_row['user_id'])))
    cursor.execute(
        '''SELECT device_id FROM bs_wecom_conversations
           WHERE tenant_id=%s AND user_id=%s AND source_account_id=%s AND source_conversation_id=%s''',
        (send_row['tenant_id'], send_row['user_id'], *decoded))
    existing = cursor.fetchone()
    if existing and existing['device_id'] and str(existing['device_id']) != str(send_row.get('device_id')):
        logger.warning(f'后端日志：企微记录范围来源设备不匹配 invocation={send_row["id"]}')
        return None, False
    conversation, newly = archive.register_scope(
        connection, send_row['tenant_id'], send_row['user_id'], locator, name=name,
        kind=kind, peer_platform=peer_platform,
        device_id=send_row.get('device_id'), reason=reason)
    return conversation, newly


def _register_from_send(row, *, locator, reason, name=None, kind=None):
    """Agent 已成功回复的会话自动纳入记录范围并并入合适日报计划（含提交）。"""
    with _open_connection() as connection:
        result = _register_in_connection(connection, row, locator=locator,
                                         reason=reason, name=name, kind=kind)
        connection.commit()
    return result


def _register_read_targets(row, data, arguments):
    """Only accepted Agent monitoring reads with explicit stable targets enroll scope."""
    if arguments.get('date') is not None or str(arguments.get('cursor', '')).startswith('wd1.'):
        return
    if row.get('business_kind') != 'agent_runner':
        return
    if row.get('tool_name') == 'wecom_read_session':
        requested = arguments.get('session')
        if isinstance(requested, str) and requested.startswith('ws1.') and requested != data.get('session'):
            return
        targets = [{'session': data.get('session'), 'name': data.get('title'),
                    'kind': data.get('kind'), 'peer_platform': data.get('peer_platform')}]
    elif isinstance(arguments.get('sessions'), list) and arguments['sessions']:
        targets = data.get('resolved_targets') or []
        if not isinstance(targets, list):
            return
        if len(targets) > len(arguments['sessions']):
            return
        stable_requested = {item for item in arguments['sessions'] if isinstance(item, str) and item.startswith('ws1.')}
        if len(stable_requested) == len(arguments['sessions']) and any(
                not isinstance(item, dict) or item.get('session') not in stable_requested for item in targets):
            return
    else:
        return
    if not isinstance(targets, list):
        return
    seen = set()
    with _open_connection() as connection:
        for target in targets[:50]:
            if not isinstance(target, dict):
                continue
            locator = target.get('session')
            if not isinstance(locator, str) or not locator.startswith('ws1.'):
                continue
            decoded = archive.decode_locator(locator)
            if decoded is None or decoded[0] != data.get('self_user_id'):
                continue
            if locator in seen:
                continue
            seen.add(locator)
            _register_in_connection(
                connection, row, locator=locator, reason='agent_monitor',
                name=target.get('name') if isinstance(target.get('name'), str) else None,
                kind=target.get('kind') if target.get('kind') in ('group', 'private') else None,
                peer_platform=target.get('peer_platform') if target.get('peer_platform') in ('wechat', 'wecom', 'unknown') else None)
        connection.commit()


async def _handle_read(row):
    arguments = _arguments_of(row)
    if arguments.get('attachment') is not None or arguments.get('resolve_only') is True:
        return
    data = _data_of(row)
    if data.get('channel') not in (None, 'local-db'):
        return
    try:
        await asyncio.to_thread(_register_read_targets, row, data, arguments)
    except Exception as error:
        logger.opt(exception=True).warning(
            f'后端日志：企微监控范围登记失败 invocation={row["id"]} error={type(error).__name__}')
    stats = await asyncio.to_thread(
        archive.store_read_result, _factory(), row['tenant_id'], row['user_id'],
        data, arguments, device_id=row.get('device_id'),
        invocation_id=str(row['id']))
    reconciled = data.get('reconciled_sends')
    if isinstance(reconciled, list) and reconciled:
        await asyncio.to_thread(archive.store_reconciled_sends, _factory(),
                                row['tenant_id'], row['user_id'], reconciled,
                                device_id=row.get('device_id'))
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
    fragment = data.get('observed_chat')
    if isinstance(fragment, dict):
        try:
            await asyncio.to_thread(
                archive.store_send_fragment, _factory(), row['tenant_id'],
                row['user_id'], fragment, invocation_id=str(row['id']),
                device_id=row.get('device_id'))
        except Exception as error:
            await asyncio.to_thread(_note_scope_error, row['tenant_id'], row['user_id'],
                                    fragment.get('session'), error)
    reconciled = data.get('reconciled_sends')
    if isinstance(reconciled, list) and reconciled:
        await _store_reconciled(row, reconciled)


async def _store_reconciled(row, reconciled):
    try:
        await asyncio.to_thread(archive.store_reconciled_sends, _factory(),
                                row['tenant_id'], row['user_id'], reconciled,
                                device_id=row.get('device_id'))
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
                return None, False, None, None
            # 迟到成功视图（评审二 P1-7）：原发送可能仍为 unknown 但 _late_result
            # 已含有效 session/observed_chat；保持持久事实，仅读可信完成视图。
            original = observed_invocation(dict(original))
            original_data = (original.get('result_json') or {}).get('data') or {}
            locator = original_data.get('session')
            if not isinstance(locator, str) or not locator.startswith('ws1.'):
                return None, False, None, None
            send_row = {**row, 'device_id': original.get('device_id')}
            result = _register_in_connection(
                connection, send_row, locator=locator, reason='agent_replied',
                name=original_data.get('title') if isinstance(original_data.get('title'), str) else None)
            connection.commit()
            return *result, original_data.get('observed_chat'), original.get('device_id')

    try:
        conversation, newly, observed, source_device = await asyncio.to_thread(_load_and_register)
    except Exception as error:
        logger.opt(exception=True).warning(
            f'后端日志：企微对账确认纳入记录范围失败 tenant={row["tenant_id"]} '
            f'error={type(error).__name__}')
        return
    # 原发送（曾为 unknown）的核验片段在对账确认后同样尽力保存（设计 §3.1.1）。
    if isinstance(observed, dict):
        fragment = dict(observed)
        if isinstance(data.get('reconciliation'), dict) \
                and data['reconciliation'].get('message_id') is not None:
            fragment.setdefault('confirmed_message_id', data['reconciliation']['message_id'])
        try:
            await asyncio.to_thread(
                archive.store_send_fragment, _factory(), row['tenant_id'],
                row['user_id'], fragment, invocation_id=str(original_id),
                device_id=source_device)
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
