"""评审二（第二轮 CR）修复的定向测试。

- 空背景续页（py 驱动已在 clients 测试覆盖；这里测 TS 侧语义相关服务端不受影响）；
- 显式时区更新/未指定保留（评审二 P2-6，隔离库）；
- 手动生成会话清单写入受信描述（评审二 P1-3，替身边界）；
- 部分失败通知生产（评审二 P1-4，真实 publish 入口）。
"""
import base64
import json
from datetime import datetime, timezone as _tz
from unittest.mock import MagicMock, patch

import pytest

from src.wecom_conversation import archive, plans


@pytest.fixture(autouse=True)
def isolate_share_creation(monkeypatch):
    """These notification wording tests predate the terminal share side effect."""
    monkeypatch.setattr('src.services.task_result_sharing.service.ensure_task_result_share_in_tx',
                        lambda *args: 'share_key')


def _locator(account='acc1', conversation='R:m1'):
    return 'ws1.' + base64.urlsafe_b64encode(
        json.dumps({'account': account, 'conversation_id': conversation}).encode()).decode().rstrip('=')


def archive_register_scope(tenant_id, user_id, locator, **kwargs):
    """register_scope 需要打开连接且不内部提交（评审 P2-9）：测试侧补提交。"""
    from src.db.database import get_db_connection
    with get_db_connection() as connection:
        row, newly = archive.register_scope(connection, tenant_id, user_id, locator, **kwargs)
        connection.commit()
    return row


# ---------- 评审二 P1-3：手动清单 ----------

@pytest.mark.usefixtures("db_ready")
@pytest.mark.asyncio
async def test_manual_report_with_conversation_ids_only_carries_list(tenant_id):
    user_id = 'user-1'
    locator = _locator('acc1', 'R:m1')
    conversation = archive_register_scope(tenant_id, user_id, locator, reason='daily_report')
    captured = {}

    class _Client:
        async def request(self, method, path, *, authorization, tenant, body, accept_new):
            captured['body'] = body
            return {'runner': {'runner_id': 'run-x'}, 'created': True}

    with patch.object(plans, '_runner_client', lambda: _Client()), \
         patch.object(plans, '_ensure_session_committed', lambda *a, **k: None):
        await plans.submit_manual_report(
            'Bearer x', tenant_id, user_id,
            conversation_ids=[conversation['id']], account='acc1', device_id='device-a',
            business_date='2026-11-04')
    descriptor = captured['body']['request_data'][plans.DESCRIPTOR_KEY]
    assert descriptor['conversation_ids'] == [conversation['id']]
    assert 'task_id' not in descriptor  # 仅清单：无计划范围兜底
    assert captured['body']['profile_id'] == 'main'
    assert 'instance_id' not in captured['body']


# ---------- 评审二 P1-4：部分失败的真实通知生产入口 ----------

def _daily_plan_task_row(task_id='sched_daily_x'):
    config = {'kind': 'daily', 'timezone': 'Asia/Shanghai', 'account': 'acc1'}
    return {'task_id': task_id, 'name': '企微会话日报', 'notification_rule_id': None,
            'notification_target_id': None,
            'task_prompt': plans.plan_prompt('daily', config, '要求A')}


def _runner_with_daily_result(event_kind='completed', completed=('群A',), failed=None,
                              task_id='sched_daily_x'):
    result = {'business_date': '2026-11-03', 'runner_id': 'run-1',
              'task_id': task_id,
              'scope_conversation_ids': [1 + i for i in range(len(completed))],
              'completed': [{'conversation_id': 1 + i, 'name': name}
                            for i, name in enumerate(completed)],
              'failed': failed or []}
    return {'runner_id': 'run-1', 'tenant_id': 't1', 'user_id': 'u1',
            'session_id': 'sess-1', 'session_kind': 'web', 'source': 'scheduled',
            'actor_kind': 'user', 'actor_id': 'u1', 'user_id2': None,
            'scope_key': 'tenant:t1', 'status': event_kind,
            'accepted_at': datetime(2026, 11, 4, 8, tzinfo=_tz.utc),
            'finished_at': datetime(2026, 11, 4, 8, 5, tzinfo=_tz.utc),
            'checkpoint': {'execution': {'resources': {'wecom_daily_result': result}}},
            'input': {'request_data': {'scheduled': {'task_id': 'sched_daily_x'}}}}


def test_scheduled_failure_before_business_checkpoint_keeps_date_link():
    """定时受理事实已冻结日期，业务前失败也应给页面链接且不虚报生成数。"""
    from src.notifications.producer import _wecom_daily_terminal_extras

    runner = _runner_with_daily_result(event_kind='failed')
    runner['checkpoint'] = {}
    runner['input']['request_data']['wecom_conversation_business'] = {
        'schema_version': 2, 'kind': 'daily', 'trigger': 'scheduled',
        'task_id': 'sched_daily_x', 'business_date': '2026-11-03',
        'conversation_ids': [1, 2]}
    extras = _wecom_daily_terminal_extras(None, runner,
                                          _daily_plan_task_row(), 'failed')
    assert extras['daily_report_date'] == '2026-11-03'
    assert 'date=2026-11-03' in extras['daily_report_link']
    assert 'daily_report_summary' not in extras


def test_all_superseded_notification_preserves_link_without_claiming_saved():
    """Completed execution with newer reports uses existing notification fields truthfully."""
    from src.notifications.producer import _wecom_daily_terminal_extras

    runner = _runner_with_daily_result(completed=())
    result = runner['checkpoint']['execution']['resources']['wecom_daily_result']
    result['superseded'] = [{'conversation_id': 1, 'name': '群A'}]
    extras = _wecom_daily_terminal_extras(None, runner,
                                          _daily_plan_task_row(), 'completed')
    assert extras['daily_report_summary'] == '本次已生成 0 个会话日报'
    assert '本次未覆盖 1 个会话：群A' in extras['daily_report_missing']
    assert 'date=2026-11-03' in extras['daily_report_link']


def test_partial_failure_notification_lists_failed_names():
    """一个成功一个失败（正常部分失败）→ 通知生产不抛错、缺参含失败名（评审二 P1-4）。"""
    from src.notifications import producer
    from src.notifications.repository import unprotect
    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        {'title': '企微会话日报'},                      # chat_sessions
        _daily_plan_task_row(),                        # scheduled_tasks（含 task_id/task_prompt）
        {'notification_ref': 'fixture'},               # publish_in_tx INSERT RETURNING
        {'notification_ref': 'fixture'},               # 幂等回读
    ]
    runner = _runner_with_daily_result(
        'completed', completed=['群A'],
        failed=[{'name': '群B', 'code': 'WECOM_DAY_READ_FAILED'}])
    producer.publish_runner_terminal_in_tx(cursor, runner, type('C', (), {'ttl_seconds': 3600})())
    statement, args = cursor.execute.call_args_list[2].args
    parameters = unprotect(args[11])
    assert parameters['daily_report_date'] == '2026-11-03'
    assert parameters['daily_report_summary'] == '本次已生成 1 个会话日报'
    assert '群B' in parameters['daily_report_missing']
    assert 'sched_daily_x' in parameters['daily_report_link']


def test_all_failed_notification_still_reports_missing():
    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        {'title': '企微会话日报'},
        _daily_plan_task_row(),
        {'notification_ref': 'fixture'},
        {'notification_ref': 'fixture'},
    ]
    from src.notifications import producer
    from src.notifications.repository import unprotect
    runner = _runner_with_daily_result(
        'completed', completed=[],
        failed=[{'conversation_id': 1, 'name': '群A', 'code': 'X'},
                {'conversation_id': 2, 'name': '群B', 'code': 'Y'}])
    producer.publish_runner_terminal_in_tx(cursor, runner, type('C', (), {'ttl_seconds': 3600})())
    statement, args = cursor.execute.call_args_list[2].args
    parameters = unprotect(args[11])
    assert parameters['daily_report_summary'] == '本次已生成 0 个会话日报'
    assert '群A' in parameters['daily_report_missing'] and '群B' in parameters['daily_report_missing']


def test_all_failed_terminal_notification_still_carries_date_and_link():
    """评审三 P1-4：全部失败（终态 failed）也形成执行事实——通知附日期/链接
    与未完成明细，不虚构"已生成"。"""
    from src.notifications import producer
    from src.notifications.repository import unprotect
    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        {'title': '企微会话日报'},
        _daily_plan_task_row(),
        {'notification_ref': 'fixture'},
        {'notification_ref': 'fixture'},
    ]
    runner = _runner_with_daily_result(
        'failed', completed=[],
        failed=[{'conversation_id': 1, 'name': '群A', 'code': 'X'}])
    producer.publish_runner_terminal_in_tx(cursor, runner, type('C', (), {'ttl_seconds': 3600})())
    statement, args = cursor.execute.call_args_list[2].args
    parameters = unprotect(args[11])
    assert parameters['daily_report_date'] == '2026-11-03'
    assert 'sched_daily_x' in parameters['daily_report_link']
    assert parameters.get('daily_report_summary') is None  # 不虚构已生成
    assert '群A' in parameters['daily_report_missing']


def test_no_daily_result_keeps_plain_notification():
    """普通定时任务（无 wecom_daily_result）不加日报参数，行为不变。"""
    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        {'title': '普通任务'}, {'task_id': 'sched_norm', 'name': '普通任务',
                                'notification_rule_id': None, 'notification_target_id': None,
                                'task_prompt': '普通任务定义'},
        {'notification_ref': 'fixture'},
        {'notification_ref': 'fixture'},
    ]
    from src.notifications import producer
    from src.notifications.repository import unprotect
    runner = _runner_with_daily_result('completed')
    runner['checkpoint'] = {'execution': {'resources': {}}}
    runner['input'] = {'request_data': {'scheduled': {'task_id': 'sched_norm'}}}
    producer.publish_runner_terminal_in_tx(cursor, runner, type('C', (), {'ttl_seconds': 3600})())
    statement, args = cursor.execute.call_args_list[2].args
    parameters = unprotect(args[11])
    assert parameters.get('daily_report_date') is None
    assert parameters.get('daily_report_link') is None
