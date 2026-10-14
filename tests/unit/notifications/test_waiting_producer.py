"""runner_waiting_input 生产者：过滤口径、wait 去重键、参数与资源类型（Phase 2）。

对应 docs/system/design-ask-user-interactive-question.md §5.6 与计划 §4：
仅 chat/web 用户自有 runner 进入 waiting(带表单) 时生产 task.waiting_input，
event_key 按 runner+wait 去重（同 wait 重复 park 不重复通知）。
"""
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.config.settings import NotificationsConfig
from src.notifications.producer import publish_runner_waiting_in_tx
from src.notifications.repository import unprotect


def runner(**kw):
    return dict(source='chat', session_kind='web', actor_kind='user', actor_id='user',
        user_id='user', tenant_id='tenant', scope_key='tenant:tenant', status='waiting',
        session_id='session', runner_id='runner', accepted_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc), public_snapshot={}, **kw)


def form(wait_id='wait-1', title='归档范围'):
    return {'form_id': 'frm_1', 'wait_id': wait_id, 'title': title, 'allow_skip': True,
            'questions': [{'id': 'q1', 'prompt': '以下哪些任务归档？', 'type': 'multi'}]}


def waiting_runner(waiting_kw=None, **kw):
    waiting = {'kind': 'clarification', 'form': form(), **(waiting_kw or {})}
    snapshot = {'waiting': waiting, **kw.pop('public_snapshot', {})}
    # runner() 模板内置 public_snapshot 字面键，不能再用同名关键字传入——
    # 先构造基础行再覆写快照。
    row = runner(**kw)
    row['public_snapshot'] = snapshot
    return row


def capturing_cursor(fetch_side=None):
    """fetch_side 提供时按序返回（如会话已删除场景）；否则按 SQL 分发：
    会话查询返回标题、INSERT/去重查询返回 notification_ref。"""
    cursor = MagicMock()
    statements = []
    cursor.execute.side_effect = lambda sql, params=None: statements.append((sql, params))

    if fetch_side is not None:
        cursor.fetchone.side_effect = iter(fetch_side)
        return cursor, statements

    def fetchone():
        sql = statements[-1][0] if statements else ''
        if 'INSERT INTO notification_messages' in sql or 'FROM notification_messages' in sql:
            return {'notification_ref': 'fixture'}
        return {'title': '项目会话'}

    cursor.fetchone.side_effect = fetchone
    return cursor, statements


@pytest.mark.parametrize('field,value', [('source', 'wecom'), ('session_kind', 'channel'),
                                         ('actor_kind', 'tool'), ('actor_id', 'someone-else')])
def test_non_user_owned_runners_do_not_notify(field, value):
    row = waiting_runner(); row[field] = value
    cursor, _ = capturing_cursor()
    publish_runner_waiting_in_tx(cursor, row, NotificationsConfig())
    cursor.execute.assert_not_called()


def test_waiting_without_form_does_not_notify():
    # legacy clarify（无 form）不通知：范围限定结构化提问。
    cursor, _ = capturing_cursor()
    row = waiting_runner(waiting_kw={'form': None, 'question': '哪种城市？'})
    publish_runner_waiting_in_tx(cursor, row, NotificationsConfig())
    cursor.execute.assert_not_called()


def _inserts(statements):
    return [(sql, params) for sql, params in statements
            if 'INSERT INTO notification_messages' in sql]


def test_waiting_message_shape_dedup_key_and_resource():
    cursor, statements = capturing_cursor()
    publish_runner_waiting_in_tx(cursor, waiting_runner(), NotificationsConfig())
    insert_sql, args = _inserts(statements)[0]
    assert 'ON CONFLICT(scope_key,recipient_user_id,producer,event_key)' in insert_sql
    assert args[4] == 'task.waiting_input'
    assert args[5] == 'waiting:runner:wait-1'  # runner+wait 去重键
    assert json.loads(args[9]) == {'type': 'runner_waiting', 'id': 'runner'}
    assert unprotect(args[11]) == {'session_title': '项目会话', 'event_kind': 'waiting_input',
        'question_title': '归档范围', 'session_id': 'session'}


def test_repeated_park_of_same_wait_reuses_dedup_key():
    cursor, statements = capturing_cursor()
    publish_runner_waiting_in_tx(cursor, waiting_runner(), NotificationsConfig())
    publish_runner_waiting_in_tx(cursor, waiting_runner(), NotificationsConfig())
    keys = [params[5] for _, params in _inserts(statements)]
    assert keys == ['waiting:runner:wait-1', 'waiting:runner:wait-1']


def test_question_title_falls_back_to_prompt_then_default():
    cursor, statements = capturing_cursor()
    row = waiting_runner()
    row['public_snapshot']['waiting']['form'] = form(title='')
    publish_runner_waiting_in_tx(cursor, row, NotificationsConfig())
    assert unprotect(_inserts(statements)[0][1][11])['question_title'] == '以下哪些任务归档？'
    row['public_snapshot']['waiting']['form']['questions'][0]['prompt'] = ''
    publish_runner_waiting_in_tx(cursor, row, NotificationsConfig())
    assert unprotect(_inserts(statements)[-1][1][11])['question_title'] == '请回答提问'


def test_deleted_session_does_not_notify():
    cursor, _ = capturing_cursor(fetch_side=[{'title': '项目会话'}, None])
    cursor.fetchone.side_effect = [None]
    publish_runner_waiting_in_tx(cursor, waiting_runner(), NotificationsConfig())
    # 会话查不到（已删除）时不进入 publish（execute 仅会话查询一次）。
    assert cursor.execute.call_count == 1


def test_nested_child_wait_leaf_notifies_with_leaf_wait_id():
    # 五轮评审 P2-3：子执行嵌套链（child_wait → clarification/form）必须经叶
    # 节点解析通知，event_key 按叶节点真实 wait_id 去重。
    cursor, statements = capturing_cursor()
    nested = waiting_runner(waiting_kw={
        'kind': 'child_wait',
        'child_wait': {'kind': 'child_wait',
                       'child_wait': {'kind': 'clarification',
                                      'form': form(wait_id='wait-leaf')}},
    })
    publish_runner_waiting_in_tx(cursor, nested, NotificationsConfig())
    insert_sql, args = _inserts(statements)[0]
    assert args[5] == 'waiting:runner:wait-leaf'
    assert json.loads(args[9]) == {'type': 'runner_waiting', 'id': 'runner'}


def test_effective_event_types_backfills_waiting_for_legacy_default():
    # 五轮评审 P1-2：存量旧默认偏好读取时兼容扩展 waiting_input；
    # 显式自定义集合与空集合不受影响。
    from src.notifications.repository import (
        TASK_EVENT_TYPES, _effective_event_types)
    assert _effective_event_types(['task.completed', 'task.failed']) == TASK_EVENT_TYPES
    custom = ('task.completed', 'task.waiting_input', 'billing.credit_low')
    assert _effective_event_types(custom) == custom
    assert _effective_event_types([]) == ()
    assert _effective_event_types(None) == ()


def test_inbox_link_for_waiting_uses_notification_entry_with_tenant():
    # 六轮评审 P2-1：站内信复用既有通知解析入口 /notification?ref=，且必须
    # 携带 tenant 查询参数——入口（NotificationEntry）按 tenant 选择
    # saas_token_{tenant}，缺失时租户用户落在 portal 登录页（真实入口复现）。
    from src.notifications.contracts import Subject
    from src.notifications.repository import NotificationRepository
    link = NotificationRepository._inbox_link(
        'task.waiting_input', {'session_id': 'session-1'},
        Subject('user', 'tenant'), notification_ref='abc123')
    assert link == '/notification?ref=abc123&tenant=tenant'
    # portal 用户（无租户）不带 tenant 参数，入口读 portal_token。
    portal = NotificationRepository._inbox_link(
        'task.waiting_input', {'session_id': 'session-1'},
        Subject('user', None), notification_ref='abc123')
    assert portal == '/notification?ref=abc123'
    assert NotificationRepository._inbox_link(
        'task.waiting_input', {'session_id': 'session-1'},
        Subject('user', 'tenant'), notification_ref=None) is None
