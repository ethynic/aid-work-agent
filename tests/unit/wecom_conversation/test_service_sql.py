"""服务层 SQL 测试（隔离库）：日报 UPSERT 新鲜度、按日聚合、时间线 keyset。"""

import base64
import json
import uuid
from datetime import datetime, timezone

import pytest


@pytest.fixture(autouse=True)
def _require_db(db_ready):
    yield


from src.db.database import get_db_connection
from src.wecom_conversation import archive, service
from src.wecom_conversation.adapters import DEFAULT_TIMEZONE


def _locator(account='acc1', conversation='R:group1'):
    return 'ws1.' + base64.urlsafe_b64encode(
        json.dumps({'account': account, 'conversation_id': conversation}).encode()).decode().rstrip('=')


def _message(msg_id, send_time, text='你好'):
    return {'msg_id': msg_id, 'sender_id': 'peer1', 'sender_name': '张三',
            'sender_platform': 'wechat', 'side': 'peer', 'kind': 'text', 'text': text,
            'parse_trusted': True, 'content_type': 2, 'send_time': send_time}


def _register(tenant_id, user_id, locator, name='售前群'):
    return _register_scope(get_db_connection, tenant_id, user_id, locator,
                                  name=name, reason='daily_report')[0]


def _register_scope(factory, tenant_id, user_id, locator, **kwargs):
    """register_scope 不再内部提交（评审 P2-9）：测试侧补提交。"""
    with factory() as connection:
        result = archive.register_scope(connection, tenant_id, user_id, locator, **kwargs)
        connection.commit()
    return result


def test_save_daily_report_freshness_prefers_newer_snapshot(tenant_id):
    user_id = 'user-1'
    conversation = _register(tenant_id, user_id, _locator())
    early = datetime(2026, 11, 2, 1, 0, tzinfo=timezone.utc)
    late = datetime(2026, 11, 2, 2, 0, tzinfo=timezone.utc)
    first = service.save_daily_report(
        tenant_id, user_id, conversation['id'], '2026-11-01',
        {'summary': {'overview': '新快照'}}, snapshot_at=late, generated_at=late)
    assert first['summary']['overview'] == '新快照'
    stale = service.save_daily_report(
        tenant_id, user_id, conversation['id'], '2026-11-01',
        {'summary': {'overview': '旧数据晚完成'}}, snapshot_at=early, generated_at=datetime.now(timezone.utc))
    assert stale['summary']['overview'] == '新快照'  # 旧数据晚完成不覆盖新数据
    same_snapshot = service.save_daily_report(
        tenant_id, user_id, conversation['id'], '2026-11-01',
        {'summary': {'overview': '同快照重算'}}, snapshot_at=late,
        generated_at=datetime.now(timezone.utc))
    assert same_snapshot['summary']['overview'] == '同快照重算'  # 同快照按生成时间更新


def test_daily_summaries_for_date_includes_missing_conversations(tenant_id):
    user_id = 'user-1'
    with_plan = _register(tenant_id, user_id, _locator('acc1', 'R:has'), '有报告')
    without = _register(tenant_id, user_id, _locator('acc1', 'R:none'), '无报告')
    service.save_daily_report(
        tenant_id, user_id, with_plan['id'], '2026-11-02',
        {'summary': {'overview': 'ok'}, 'coverage': {'day_count': 3}},
        snapshot_at=datetime(2026, 11, 3, 1, 0, tzinfo=timezone.utc),
        generated_at=datetime(2026, 11, 3, 1, 5, tzinfo=timezone.utc))
    rows = service.daily_summaries_for_date(tenant_id, user_id, '2026-11-02')
    by_id = {row['conversation_id']: row for row in rows}
    assert by_id[with_plan['id']]['summary']['overview'] == 'ok'
    assert by_id[without['id']]['summary'] is None  # 缺失会话如实展示


def test_list_messages_keyset_desc_pagination(tenant_id):
    user_id = 'user-1'
    conversation = _register(tenant_id, user_id, _locator())
    base = 1790000000
    archive.store_read_result(
        get_db_connection, tenant_id, user_id,
        {'source_generation': 'gen1', 'session': conversation['session_locator'],
         'messages': [_message(i, base + i * 60) for i in range(1, 8)]},
        {'session': conversation['session_locator']}, device_id='dev',
        invocation_id=str(uuid.uuid4()))
    first = service.list_messages(tenant_id, user_id, conversation['id'], limit=3, order='desc')
    assert [row['source_message_id'] for row in first] == [7, 6, 5]
    tail = first[-1]
    second = service.list_messages(
        tenant_id, user_id, conversation['id'], limit=3, order='desc',
        before_id=tail['id'], before_sent_at=tail['sent_at'].isoformat())
    assert [row['source_message_id'] for row in second] == [4, 3, 2]  # 无重漏
    undated = service.list_messages(tenant_id, user_id, conversation['id'], undated=True)
    assert undated == []


def test_plan_conversations_and_linking(tenant_id):
    user_id = 'user-1'
    row = _register(tenant_id, user_id, _locator('acc1', 'R:plan1'))
    updated = service.link_conversations_to_plan(tenant_id, user_id, 'sched_plan_1', [row['id']])
    assert updated == 1
    managed = service.plan_conversations(tenant_id, user_id, 'sched_plan_1')
    assert [item['id'] for item in managed] == [row['id']]
    assert service.plan_conversations(tenant_id, 'other-user', 'sched_plan_1') == []  # 租户/用户隔离
