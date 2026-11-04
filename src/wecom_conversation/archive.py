"""企微旁路聊天日志存储（设计 §3.1/§3.2）。

由服务器结果受理回调（logsink.py）在原结果**已接受并持久化之后**异步调用：
短批次事务 UPSERT 已取得的原消息；写入失败只记脱敏错误与最近成功时间，
不抛权威存储异常、不影响原 ACK、Agent 结果、发送结论或计费（设计 §3.1）。

本模块没有完成 marker、Runner 锁/phase 依赖或业务游标证明（设计 §7 删除清单）；
幂等去重仅靠原消息唯一键
`(tenant_id, conversation_id, source_generation, source_message_id)`。
"""
from __future__ import annotations

from psycopg2.extras import Json, execute_values

from .adapters import (DEFAULT_TIMEZONE, decode_locator, extract_archive_plan,
    map_message, parse_local_time_text)


def _receipt_candidates(cursor, tenant_id, user_id, message_ids):
    """本人消息的出站回执候选：现有发送核验与只读对账事实（设计 §3.2）。

    仅在同账号/会话/来源代次下按 verification_detail/reconciliation 的
    message_id 精确关联；旧回执缺 source_generation 时保持未确认。
    """
    if not message_ids:
        return []
    cursor.execute(
        '''SELECT id::text AS invocation_id,
                  result_json->'data'->>'session' AS session,
                  result_json->'data'->'verification_detail'->>'message_id' AS message_id,
                  result_json->'data'->'verification_detail'->>'source_generation' AS generation,
                  'verification' AS method
           FROM local_tool_invocations
           WHERE tenant_id=%s AND user_id=%s AND tool_name='wecom_message_send'
             AND provider_key='wecom' AND state='succeeded'
             AND result_json->'data'->'verification_detail' ? 'message_id'
             AND (result_json->'data'->'verification_detail'->>'message_id')::bigint = ANY(%s::bigint[])
           ORDER BY created_at DESC LIMIT 200''',
        (tenant_id, user_id, list(message_ids)))
    candidates = [dict(row) for row in cursor.fetchall()]
    cursor.execute(
        '''SELECT s.id::text AS invocation_id,
                  s.business_ref->>'send_invocation_id' AS send_invocation_id,
                  o.result_json->'data'->>'session' AS session,
                  s.result_json->'data'->'reconciliation'->>'message_id' AS message_id,
                  s.result_json->'data'->'reconciliation'->>'source_generation' AS generation,
                  'reconciliation' AS method
           FROM local_tool_invocations s
           JOIN local_tool_invocations o ON o.id::text = s.business_ref->>'send_invocation_id'
           WHERE s.tenant_id=%s AND s.user_id=%s AND s.tool_name='wecom_send_status'
             AND s.provider_key='wecom' AND s.state='succeeded'
             AND s.result_json->'data'->'reconciliation' ? 'message_id'
             AND (s.result_json->'data'->'reconciliation'->>'message_id')::bigint = ANY(%s::bigint[])
           ORDER BY s.created_at DESC LIMIT 200''',
        (tenant_id, user_id, list(message_ids)))
    candidates.extend(dict(row) for row in cursor.fetchall())
    return candidates


def _attribute_self_messages(cursor, tenant_id, user_id, session_rows, generation):
    """self 消息的 Agent 代发归因：同账号/会话/来源代次下按回执精确关联（设计 §3.2）。"""
    wanted = {}
    for row in session_rows:
        locator = row['locator']
        for message in row['messages']:
            if message['sender_side'] == 'self':
                wanted.setdefault(message['source_message_id'], locator)
    candidates = _receipt_candidates(cursor, tenant_id, user_id, list(wanted))
    by_key = {}
    for item in candidates:
        if item.get('session') and item.get('message_id') is not None and item.get('generation'):
            by_key.setdefault((str(item['session']), str(item['message_id']), str(item['generation'])), item)
    attributed = 0
    for row in session_rows:
        locator = row['locator']
        for message in row['messages']:
            if message['sender_side'] != 'self':
                continue
            receipt = by_key.get((locator, str(message['source_message_id']), str(generation)))
            if receipt is not None:
                message['send_origin'] = 'agent'
                message['origin_evidence'] = {'method': receipt['method'], 'generation': str(receipt['generation'])}
                message['send_invocation_id'] = receipt['send_invocation_id'] if receipt.get('send_invocation_id') else receipt['invocation_id']
                attributed += 1
    return attributed


_MESSAGE_COLUMNS = ('tenant_id', 'user_id', 'conversation_id', 'source_generation', 'source_message_id',
                    'server_id', 'source_invocation_id', 'sender_id', 'sender_name', 'name_source',
                    'sender_platform', 'sender_side', 'kind', 'text', 'text_format', 'system',
                    'parse_trusted', 'baseline', 'sent_at', 'source_time_text', 'source_fields',
                    'attachment_ref', 'send_origin', 'origin_evidence', 'send_invocation_id')

_MESSAGE_UPSERT = '''
INSERT INTO bs_wecom_messages ({columns})
VALUES %s
ON CONFLICT (tenant_id, conversation_id, source_generation, source_message_id) DO UPDATE SET
    server_id = COALESCE(EXCLUDED.server_id, bs_wecom_messages.server_id),
    source_invocation_id = EXCLUDED.source_invocation_id,
    sender_id = COALESCE(EXCLUDED.sender_id, bs_wecom_messages.sender_id),
    sender_name = COALESCE(EXCLUDED.sender_name, bs_wecom_messages.sender_name),
    name_source = COALESCE(EXCLUDED.name_source, bs_wecom_messages.name_source),
    sender_platform = CASE WHEN bs_wecom_messages.sender_platform = 'unknown'
        AND EXCLUDED.sender_platform <> 'unknown' THEN EXCLUDED.sender_platform
        ELSE bs_wecom_messages.sender_platform END,
    sender_side = EXCLUDED.sender_side,
    kind = CASE WHEN EXCLUDED.parse_trusted THEN EXCLUDED.kind ELSE bs_wecom_messages.kind END,
    text = CASE WHEN EXCLUDED.parse_trusted THEN EXCLUDED.text ELSE bs_wecom_messages.text END,
    text_format = CASE WHEN EXCLUDED.parse_trusted THEN EXCLUDED.text_format ELSE bs_wecom_messages.text_format END,
    system = bs_wecom_messages.system OR EXCLUDED.system,
    parse_trusted = bs_wecom_messages.parse_trusted OR EXCLUDED.parse_trusted,
    baseline = bs_wecom_messages.baseline OR EXCLUDED.baseline,
    sent_at = CASE WHEN EXCLUDED.source_fields ? 'send_time' THEN EXCLUDED.sent_at
        ELSE COALESCE(bs_wecom_messages.sent_at, EXCLUDED.sent_at) END,
    source_time_text = COALESCE(EXCLUDED.source_time_text, bs_wecom_messages.source_time_text),
    source_fields = COALESCE(bs_wecom_messages.source_fields, '{{}}'::jsonb)
        || COALESCE(EXCLUDED.source_fields, '{{}}'::jsonb),
    attachment_ref = COALESCE(EXCLUDED.attachment_ref, bs_wecom_messages.attachment_ref),
    send_origin = CASE WHEN EXCLUDED.send_origin IS NOT NULL THEN EXCLUDED.send_origin
        ELSE bs_wecom_messages.send_origin END,
    origin_evidence = CASE WHEN EXCLUDED.origin_evidence IS NOT NULL THEN EXCLUDED.origin_evidence
        ELSE bs_wecom_messages.origin_evidence END,
    send_invocation_id = CASE WHEN EXCLUDED.send_invocation_id IS NOT NULL THEN EXCLUDED.send_invocation_id
        ELSE bs_wecom_messages.send_invocation_id END,
    updated_at = CURRENT_TIMESTAMP
'''.format(columns=', '.join(_MESSAGE_COLUMNS))


def _message_row_values(tenant_id, user_id, conversation_id, generation, message, tz_name):
    if message.get('sent_at') is None and message.get('source_time_text'):
        message['sent_at'] = parse_local_time_text(message['source_time_text'], tz_name)
    return (tenant_id, user_id, conversation_id, generation, message['source_message_id'],
            message.get('server_id'), message.get('source_invocation_id'), message.get('sender_id'),
            message.get('sender_name'), message.get('name_source'), message.get('sender_platform'),
            message['sender_side'], message.get('kind'), message.get('text'), message.get('text_format'),
            message['system'], message['parse_trusted'], message['baseline'], message.get('sent_at'),
            message.get('source_time_text'), Json(message['source_fields']) if message.get('source_fields') else None,
            Json(message['attachment_ref']) if message.get('attachment_ref') else None,
            message.get('send_origin'), Json(message['origin_evidence']) if message.get('origin_evidence') else None,
            message.get('send_invocation_id'))


def _scoped_rows(cursor, tenant_id, user_id, locators):
    """记录范围内的会话行（行存在即在范围；is_followed 不再承担门禁，设计 §3.3）。"""
    if not locators:
        return {}
    cursor.execute(
        '''SELECT id, session_locator, timezone, coverage FROM bs_wecom_conversations
           WHERE tenant_id=%s AND user_id=%s AND session_locator = ANY(%s)''',
        (tenant_id, user_id, sorted(locators)))
    return {row['session_locator']: dict(row) for row in cursor.fetchall()}


def _apply_reconciled_sends(cursor, tenant_id, user_id, conversation_ids, reconciled):
    """新对账回执确认时补写已存消息的归因（设计 §3.2），不重置已有结论。"""
    updated = 0
    for item in reconciled:
        locator = item.get('session')
        reconciliation = item.get('reconciliation') or {}
        generation = reconciliation.get('source_generation')
        message_id = reconciliation.get('message_id')
        conversation_id = conversation_ids.get(locator)
        if not conversation_id or not generation or message_id is None:
            continue
        cursor.execute(
            '''UPDATE bs_wecom_messages SET send_origin='agent',
                   origin_evidence=COALESCE(origin_evidence, %s),
                   send_invocation_id=%s, updated_at=CURRENT_TIMESTAMP
               WHERE tenant_id=%s AND conversation_id=%s AND source_generation=%s
                 AND source_message_id=%s AND (send_origin IS NULL OR send_origin='unknown')''',
            (Json({'method': 'reconciliation', 'generation': str(generation)}),
             item.get('invocation_id'), tenant_id, conversation_id,
             str(generation), int(message_id)))
        updated += cursor.rowcount
    return updated


def _coverage_snapshot(data, generation):
    coverage = {}
    if isinstance(data.get('history_truncated'), bool):
        coverage['history_truncated'] = data['history_truncated']
    if isinstance(data.get('baseline'), bool):
        coverage['baseline'] = data['baseline']
    coverage['source_generation'] = str(generation)
    return coverage


def _merge_coverage(existing, snapshot):
    """保留首次覆盖事实：历史截断/基线一经成立不因后续读取翻转消除。"""
    merged = dict(existing) if isinstance(existing, dict) else {}
    for key in ('history_truncated', 'baseline'):
        if snapshot.get(key) is True:
            merged[key] = True
        elif key not in merged and key in snapshot:
            merged[key] = snapshot[key]
    merged['source_generation'] = snapshot.get('source_generation', merged.get('source_generation'))
    return merged


def store_read_result(connection_factory, tenant_id, user_id, result_data, arguments, *,
                      device_id, invocation_id):
    """成功读取结果 → 旁路保存已返回消息（只保存已取得数据，不额外拉取）。

    仅记录范围内的会话入库；范围外会话不自动纳入（设计 §3.3）。
    返回保存统计；存储异常由调用方（logsink）脱敏记录，不向上抛业务语义。
    """
    plan = extract_archive_plan(result_data, arguments, device_id=device_id, invocation_id=invocation_id)
    if plan is None:
        return {'conversations': 0, 'messages': 0}
    sessions = [row for row in plan['sessions']]
    with connection_factory() as connection:
        cursor = connection.cursor()
        scoped = _scoped_rows(cursor, tenant_id, user_id, {row['locator'] for row in sessions})
        if not scoped:
            return {'conversations': 0, 'messages': 0}
        generation = plan['generation']
        ordered_rows = [row for row in sessions if row['locator'] in scoped]
        _attribute_self_messages(cursor, tenant_id, user_id, ordered_rows, generation)
        total = 0
        for row in ordered_rows:
            conversation = scoped[row['locator']]
            values = [_message_row_values(tenant_id, user_id, conversation['id'], generation,
                                          message, conversation['timezone']) for message in row['messages']]
            if values:
                execute_values(cursor, _MESSAGE_UPSERT, values, page_size=100)
                total += len(values)
            merged = _merge_coverage(conversation.get('coverage'), _coverage_snapshot(result_data, generation))
            cursor.execute(
                '''UPDATE bs_wecom_conversations SET
                       name = COALESCE(%(name)s, name),
                       kind = COALESCE(%(kind)s, kind),
                       peer_platform = COALESCE(%(peer_platform)s, peer_platform),
                       last_archived_at = CURRENT_TIMESTAMP,
                       coverage = %(coverage)s,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE id = %(id)s''',
                {'name': row.get('name'), 'kind': row.get('kind'),
                 'peer_platform': row.get('peer_platform'),
                 'coverage': Json(merged), 'id': conversation['id']})
        connection.commit()
    return {'conversations': len(ordered_rows), 'messages': total}


def store_send_fragment(connection_factory, tenant_id, user_id, fragment, *, invocation_id):
    """发送核验片段 observed_chat → 旁路保存（设计 §3.1.1）。

    只消费片段已读到的标准原消息；`confirmed_message_id` 仅在原 DB 核验精确
    命中时把该条本人消息标注 Agent 代发，不认领片段中其他 self 消息。
    """
    if not isinstance(fragment, dict):
        return {'conversations': 0, 'messages': 0}
    locator = fragment.get('session')
    decoded = decode_locator(locator) if isinstance(locator, str) else None
    messages = fragment.get('messages')
    if decoded is None or not isinstance(messages, list):
        return {'conversations': 0, 'messages': 0}
    account, conversation_id = decoded
    generation = fragment.get('source_generation')
    if not isinstance(generation, str) or not generation:
        return {'conversations': 0, 'messages': 0}  # 不伪造来源代次
    mapped, confirmed = [], fragment.get('confirmed_message_id')
    for message in messages:
        row = map_message(message, account=account, locator=locator, generation=generation,
                          device_id=None, invocation_id=invocation_id)
        if row is None:
            continue
        if confirmed is not None and row['source_message_id'] == confirmed \
                and row['sender_side'] == 'self':
            row['send_origin'] = 'agent'
            row['origin_evidence'] = {'method': 'send_receipt', 'generation': str(generation)}
            row['send_invocation_id'] = invocation_id
        mapped.append(row)
    if not mapped:
        return {'conversations': 0, 'messages': 0}
    with connection_factory() as connection:
        cursor = connection.cursor()
        scoped = _scoped_rows(cursor, tenant_id, user_id, [locator])
        if not scoped:
            return {'conversations': 0, 'messages': 0}
        conversation = scoped[locator]
        values = [_message_row_values(tenant_id, user_id, conversation['id'], generation,
                                      message, conversation['timezone']) for message in mapped]
        execute_values(cursor, _MESSAGE_UPSERT, values, page_size=100)
        cursor.execute(
            '''UPDATE bs_wecom_conversations SET last_archived_at=CURRENT_TIMESTAMP,
                   updated_at=CURRENT_TIMESTAMP WHERE id=%s''',
            (conversation['id'],))
        connection.commit()
    return {'conversations': 1, 'messages': len(mapped)}


def store_reconciled_sends(connection_factory, tenant_id, user_id, reconciled):
    """只读对账确认（wecom_send_status confirmed）→ 补写已存消息的代发归因。"""
    if not isinstance(reconciled, list) or not reconciled:
        return {'attributed': 0}
    locators = {item.get('session') for item in reconciled
                if isinstance(item, dict) and isinstance(item.get('session'), str)}
    with connection_factory() as connection:
        cursor = connection.cursor()
        scoped = _scoped_rows(cursor, tenant_id, user_id, locators)
        if not scoped:
            return {'attributed': 0}
        ids = {locator: row['id'] for locator, row in scoped.items()}
        updated = _apply_reconciled_sends(cursor, tenant_id, user_id, ids, reconciled)
        connection.commit()
    return {'attributed': updated}


def register_scope(connection, tenant_id, user_id, locator, *, name=None, kind=None,
                   peer_platform=None, device_id=None, reason, timezone=None):
    """自动登记记录范围（设计 §3.3）：行存在即在范围；返回 (行, 是否首次纳入)。

    enroll_reasons 合并去重；名称变化不创建重复会话（唯一键约束）。
    本函数不提交事务（评审 P2-9）：调用方负责在范围登记与计划并入等
    关联写入全部完成后统一 commit，避免后续更新被悄悄回滚。
    """
    decoded = decode_locator(locator)
    if decoded is None:
        return None, False
    account, conversation_id = decoded
    # 时区语义（评审二 P2-6）：显式传入才更新（计划建立/扩展）；发送回调等
    # 未指定时保留原值，避免默认值反复覆盖用户配置。INSERT 用缺省兜底（列 NOT NULL）。
    explicit_timezone = timezone
    insert_timezone = timezone or DEFAULT_TIMEZONE
    if device_id is not None:
        device_id = str(device_id)  # 调用方可能传 uuid.UUID（psycopg2 不识别）
    cursor = connection.cursor()
    cursor.execute(
        '''SELECT * FROM bs_wecom_conversations
           WHERE tenant_id=%s AND user_id=%s AND source_account_id=%s AND source_conversation_id=%s''',
        (tenant_id, user_id, account, conversation_id))
    existing = cursor.fetchone()
    reasons = list((existing or {}).get('enroll_reasons') or []) if existing else []
    if reason not in reasons:
        reasons.append(reason)
    if existing is None:
        cursor.execute(
            '''INSERT INTO bs_wecom_conversations
                   (tenant_id, user_id, source_account_id, source_conversation_id, session_locator,
                    name, kind, peer_platform, device_id, timezone, enroll_reasons)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               RETURNING *''',
            (tenant_id, user_id, account, conversation_id, locator, name, kind,
             peer_platform, device_id, insert_timezone, Json(reasons)))
        row = dict(cursor.fetchone())
        return row, True
    cursor.execute(
        '''UPDATE bs_wecom_conversations SET
               session_locator = %(locator)s,
               name = COALESCE(%(name)s, bs_wecom_conversations.name),
               kind = COALESCE(%(kind)s, bs_wecom_conversations.kind),
               peer_platform = COALESCE(%(peer_platform)s, bs_wecom_conversations.peer_platform),
               device_id = COALESCE(%(device_id)s, bs_wecom_conversations.device_id),
               timezone = COALESCE(%(explicit_timezone)s, bs_wecom_conversations.timezone),
               enroll_reasons = %(reasons)s,
               updated_at = CURRENT_TIMESTAMP
           WHERE bs_wecom_conversations.id = %(id)s
           RETURNING *''',
        {'locator': locator, 'name': name, 'kind': kind, 'peer_platform': peer_platform,
         'device_id': device_id, 'explicit_timezone': explicit_timezone,
         'reasons': Json(reasons), 'id': existing['id']})
    row = dict(cursor.fetchone())
    return row, False
