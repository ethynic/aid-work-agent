from datetime import datetime, timezone
from unittest.mock import MagicMock
import pytest

from src.config.settings import NotificationsConfig
from src.notifications.producer import publish_runner_terminal_in_tx
from src.notifications.repository import unprotect


def runner(**kw):
    return dict(source='chat',session_kind='web',actor_kind='user',actor_id='user',
        user_id='user',tenant_id='tenant',scope_key='tenant:tenant',status='completed',
        session_id='session',runner_id='runner',finished_at=datetime.now(timezone.utc),
        accepted_at=datetime.now(timezone.utc),**kw)


@pytest.mark.parametrize('field,value',[('source','wecom'),('status','cancelled'),('status','running'),('session_kind','channel')])
def test_nonterminal_or_unmapped_sources_do_not_notify(field,value):
    row=runner();row[field]=value
    cursor=MagicMock()
    publish_runner_terminal_in_tx(cursor,row,NotificationsConfig())
    cursor.execute.assert_not_called()


def test_terminal_message_uses_server_snapshot_and_stable_key():
    cursor=MagicMock();cursor.fetchone.side_effect=[{'title':'项目\x00\u202e名称'},{'notification_ref':'fixture'}]
    publish_runner_terminal_in_tx(cursor,runner(),NotificationsConfig())
    statement,args=cursor.execute.call_args_list[1].args
    assert 'ON CONFLICT(scope_key,recipient_user_id,producer,event_key)' in statement
    assert args[5]=='terminal:runner'
    assert unprotect(args[11])=={'session_title':'项目名称','event_kind':'completed',
        'task_id':None,'task_name':None,'notify_rule_id':None,'notify_target_id':None}
    assert '项目名称' not in str(args)


def test_outbox_insert_failure_is_not_swallowed():
    cursor=MagicMock();cursor.fetchone.return_value={'title':'任务'}
    cursor.execute.side_effect=[None,RuntimeError('db-failure')]
    with pytest.raises(RuntimeError):
        publish_runner_terminal_in_tx(cursor,runner(),NotificationsConfig())


def scheduled_runner():
    row=runner()
    row.update(source='scheduled',actor_kind='system',actor_id='scheduler',
        input={'request_data':{'scheduled':{'task_id':'sched_1','trigger':'scheduled'}}})
    return row


def test_scheduled_parameters_prefer_rule_over_legacy_target():
    """定时任务接入通知规则：任务行有 notification_rule_id 时快照 notify_rule_id；
    旧 notification_target_id 仅在无规则的存量任务上兜底（两者互斥）。"""
    from src.notifications.producer import _scheduled_parameters
    cursor=MagicMock()
    cursor.fetchone.side_effect=[{'title':'每日日报'},
        {'notification_rule_id':'rule_1','notification_target_id':'target_9','name':'每日日报'}]
    params=_scheduled_parameters(cursor,scheduled_runner(),'completed')
    assert params['notify_rule_id']=='rule_1'
    assert 'notify_target_id' not in params
    cursor.fetchone.side_effect=[{'title':'每日日报'},
        {'notification_rule_id':None,'notification_target_id':'target_9','name':'每日日报'}]
    params=_scheduled_parameters(cursor,scheduled_runner(),'completed')
    assert params['notify_target_id']=='target_9'
    assert 'notify_rule_id' not in params
