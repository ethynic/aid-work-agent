"""旁路日志存储 SQL 测试（隔离库真实 PostgreSQL）。

覆盖设计 §3.2/§3.3：范围过滤（行存在即在范围，无人工开关）、唯一键去重、
确认回执的 Agent 代发标注、对账归因补写、自动登记范围。
"""

import base64
import json
import uuid

import pytest


@pytest.fixture(autouse=True)
def _require_db(db_ready):
    """SQL 用例统一声明隔离库依赖。"""
    yield


from src.db.database import get_db_connection
from src.wecom_conversation import archive


def _locator(account='acc1', conversation='R:group1'):
    return 'ws1.' + base64.urlsafe_b64encode(
        json.dumps({'account': account, 'conversation_id': conversation}).encode()).decode().rstrip('=')


def _message(msg_id, side='peer', text='你好', send_time=1790000000, **extra):
    return {'msg_id': msg_id, 'sender_id': 'peer1' if side == 'peer' else 'acc1',
            'sender_name': '张三' if side == 'peer' else '本人', 'sender_platform': 'wechat',
            'side': side, 'kind': 'text', 'text': text, 'time': '2026-11-01 10:00:00',
            'parse_trusted': True, 'content_type': 2, 'send_time': send_time, **extra}


def _read_data(locator, messages, generation='gen1'):
    return {'source_generation': generation, 'session': locator, 'messages': messages}


def _rows(tenant_id, user_id, conversation_id):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            '''SELECT source_message_id, text, sender_name, send_origin, origin_evidence
               FROM bs_wecom_messages
               WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
               ORDER BY source_message_id''',
            (tenant_id, user_id, conversation_id))
        return [dict(row) for row in cursor.fetchall()]


def _register_scope(factory, tenant_id, user_id, locator, **kwargs):
    """register_scope 不再内部提交（评审 P2-9）：测试侧补提交。"""
    with factory() as connection:
        result = archive.register_scope(connection, tenant_id, user_id, locator, **kwargs)
        connection.commit()
    return result


def test_store_read_result_only_saves_in_scope_conversations(tenant_id):
    user_id = 'user-1'
    in_scope = _locator('acc1', 'R:group1')
    out_scope = _locator('acc1', 'R:group2')
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            '''INSERT INTO bs_wecom_conversations
                   (tenant_id, user_id, source_account_id, source_conversation_id, session_locator)
               VALUES (%s,%s,'acc1','R:group1',%s) RETURNING id''',
            (tenant_id, user_id, in_scope))
        conversation_id = cursor.fetchone()['id']
        conn.commit()
    stats = archive.store_read_result(
        get_db_connection, tenant_id, user_id,
        {'source_generation': 'gen1', 'sessions': [
            {'session': in_scope, 'messages': [_message(1)]},
            {'session': out_scope, 'messages': [_message(2)]}]},
        {}, device_id='dev1', invocation_id=str(uuid.uuid4()))
    assert stats['conversations'] == 1
    rows = _rows(tenant_id, user_id, conversation_id)
    assert [row['source_message_id'] for row in rows] == [1]  # 范围外会话不纳入


def test_store_read_result_upsert_dedupes_by_unique_key(tenant_id):
    user_id = 'user-1'
    locator = _locator()
    _, newly = _register_scope(get_db_connection, tenant_id, user_id, locator,
                                      reason='daily_report')
    assert newly is True
    conversation_id = _register_scope(get_db_connection, tenant_id, user_id, locator, reason='daily_report')[0]['id']
    data_first = _read_data(locator, [_message(1, text='第一版'), _message(2)])
    archive.store_read_result(get_db_connection, tenant_id, user_id, data_first,
                              {'session': locator}, device_id='dev1', invocation_id=str(uuid.uuid4()))
    data_again = _read_data(locator, [_message(1, text='第一版'), _message(2), _message(3)])
    archive.store_read_result(get_db_connection, tenant_id, user_id, data_again,
                              {'session': locator}, device_id='dev1', invocation_id=str(uuid.uuid4()))
    rows = _rows(tenant_id, user_id, conversation_id)
    assert [row['source_message_id'] for row in rows] == [1, 2, 3]  # 重复结果不重复入库


def test_store_send_fragment_attributes_only_confirmed_message(tenant_id):
    user_id = 'user-1'
    locator = _locator()
    conversation_id = _register_scope(get_db_connection, tenant_id, user_id, locator, reason='agent_replied')[0]['id']
    fragment = {
        'session': locator, 'source_generation': 'gen9',
        'messages': [_message(11, side='self'), _message(12, side='self'), _message(13, side='peer')],
        'confirmed_message_id': 11,
    }
    stats = archive.store_send_fragment(get_db_connection, tenant_id, user_id, fragment,
                                        invocation_id=str(uuid.uuid4()))
    assert stats['messages'] == 3
    rows = _rows(tenant_id, user_id, conversation_id)
    by_id = {row['source_message_id']: row for row in rows}
    assert by_id[11]['send_origin'] == 'agent'  # 仅精确命中者标注代发
    assert by_id[11]['origin_evidence']['method'] == 'send_receipt'
    assert by_id[12]['send_origin'] is None     # 其余 self 消息不认领
    assert by_id[13]['send_origin'] is None


def test_store_send_fragment_skips_without_generation(tenant_id):
    user_id = 'user-1'
    locator = _locator()
    _register_scope(get_db_connection, tenant_id, user_id, locator, reason='agent_replied')
    stats = archive.store_send_fragment(
        get_db_connection, tenant_id, user_id,
        {'session': locator, 'messages': [_message(1)]}, invocation_id=str(uuid.uuid4()))
    assert stats == {'conversations': 0, 'messages': 0}  # 不伪造来源代次


def test_store_reconciled_sends_updates_attribution(tenant_id):
    user_id = 'user-1'
    locator = _locator()
    conversation_id = _register_scope(get_db_connection, tenant_id, user_id, locator, reason='agent_replied')[0]['id']
    archive.store_read_result(
        get_db_connection, tenant_id, user_id, _read_data(locator, [_message(21, side='self')]),
        {'session': locator}, device_id='dev1', invocation_id=str(uuid.uuid4()))
    result = archive.store_reconciled_sends(get_db_connection, tenant_id, user_id, [{
        'session': locator, 'invocation_id': str(uuid.uuid4()),
        'reconciliation': {'message_id': 21, 'source_generation': 'gen1'}}])
    assert result['attributed'] == 1
    rows = _rows(tenant_id, user_id, conversation_id)
    assert rows[0]['send_origin'] == 'agent'
    assert rows[0]['origin_evidence']['method'] == 'reconciliation'


def test_register_scope_merges_reasons_and_is_idempotent(tenant_id):
    user_id = 'user-1'
    locator = _locator('acc1', 'S:acc1_peer1')
    row1, newly1 = _register_scope(get_db_connection, tenant_id, user_id, locator,
                                          name='张三', reason='agent_replied')
    row2, newly2 = _register_scope(get_db_connection, tenant_id, user_id, locator,
                                          reason='daily_report')
    assert newly1 is True and newly2 is False
    assert row2['id'] == row1['id']
    assert sorted(row2['enroll_reasons']) == ['agent_replied', 'daily_report']
    # 名称变化不创建重复会话（唯一键）
    row3, _ = _register_scope(get_db_connection, tenant_id, user_id, locator,
                                     name='张三（新备注）', reason='agent_replied')
    assert row3['id'] == row1['id']
    assert row3['name'] == '张三（新备注）'


def test_store_read_result_updates_last_archived_at_and_keeps_coverage_facts(tenant_id):
    user_id = 'user-1'
    locator = _locator()
    _register_scope(get_db_connection, tenant_id, user_id, locator, reason='daily_report')
    archive.store_read_result(
        get_db_connection, tenant_id, user_id,
        _read_data(locator, [_message(1)], ), {'session': locator},
        device_id='dev1', invocation_id=str(uuid.uuid4()))
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT last_archived_at, coverage FROM bs_wecom_conversations WHERE tenant_id=%s AND user_id=%s',
            (tenant_id, user_id))
        row = dict(cursor.fetchone())
    assert row['last_archived_at'] is not None
    assert row['coverage']['source_generation'] == 'gen1'


def test_register_scope_explicit_timezone_updates_and_unspecified_preserves(tenant_id):
    """评审二 P2-6：显式传入时区才更新；未指定保留原值（发送回调不覆盖）。"""
    user_id = 'user-1'
    locator = _locator('acc1', 'S:tz1')
    row1, _ = _register_scope(get_db_connection, tenant_id, user_id, locator,
                              reason='agent_replied')
    assert row1['timezone'] == 'Asia/Shanghai'
    row2, _ = _register_scope(get_db_connection, tenant_id, user_id, locator,
                              reason='daily_report', timezone='UTC')
    assert row2['timezone'] == 'UTC'  # 计划明确指定 → 更新
    row3, _ = _register_scope(get_db_connection, tenant_id, user_id, locator,
                              reason='agent_replied')
    assert row3['timezone'] == 'UTC'  # 未指定 → 保留，不被默认值覆盖


def test_placeholder_read_preserves_saved_trusted_text_and_identity(tenant_id):
    """评审四 P2-2：超长消息占位（parse_trusted=False）不覆盖已保存的可信原文；
    身份字段（发言人/平台/类型）保留。"""
    user_id = 'user-1'
    locator = _locator()
    conversation_id = _register_scope(get_db_connection, tenant_id, user_id, locator,
                                      reason='daily_report')[0]['id']

    def _msg(text, trusted):
        return {'msg_id': 5, 'sender_id': 'peer1', 'sender_name': '张三',
                'sender_platform': 'wechat', 'side': 'peer', 'kind': 'text',
                'text': text, 'time': '2026-11-03 10:00:00', 'parse_trusted': trusted,
                'content_type': 2, 'send_time': 1790000000}

    data_full = {'source_generation': 'gen1', 'session': locator,
                 'messages': [_msg('完整原文通知正文', True)]}
    archive.store_read_result(get_db_connection, tenant_id, user_id, data_full,
                              {'session': locator}, device_id='dev',
                              invocation_id=str(uuid.uuid4()))
    placeholder = '[该条内容超出单页大小限制，本页返回占位记录并继续后续消息]'
    data_placeholder = {'source_generation': 'gen1', 'session': locator,
                        'messages': [_msg(placeholder, False)]}
    archive.store_read_result(get_db_connection, tenant_id, user_id, data_placeholder,
                              {'session': locator}, device_id='dev',
                              invocation_id=str(uuid.uuid4()))
    rows = _rows(tenant_id, user_id, conversation_id)
    assert len(rows) == 1
    row = rows[0]
    assert row['text'] == '完整原文通知正文'  # 占位不覆盖已存可信原文
    # 身份/类型保留
    cursor_check = None
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            '''SELECT sender_name, sender_platform, kind, parse_trusted
               FROM bs_wecom_messages WHERE tenant_id=%s AND conversation_id=%s''',
            (tenant_id, conversation_id))
        detail = dict(cursor.fetchone())
    assert detail['sender_name'] == '张三'
    assert detail['sender_platform'] == 'wechat'
    assert detail['kind'] == 'text'
    assert detail['parse_trusted'] is True
