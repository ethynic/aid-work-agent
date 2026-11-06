"""Only scheduled terminal notices point to the public result; old notices still work."""

from contextlib import contextmanager
from datetime import datetime
from unittest.mock import MagicMock, patch

from src.notifications.contracts import Subject
from src.notifications.events import scheduled_result_link
from src.notifications.repository import NotificationRepository
from src.notifications.web_push import WebPushAdapter
from src.services.task_result_sharing.service import task_result_links_for_runners_in_tx


def notice(*, task=True, share_key=None, event='task.completed'):
    params = {'session_title': '日报', 'event_kind': 'completed'}
    if task:
        params['task_id'] = 'sched_1'
        params['task_name'] = '日报'
    return {'notification_ref': 'ref_1', 'event_type': event, 'tenant_id': 'tenant_1',
            'parameters': params, 'resource_ref': {'type': 'runner_result', 'id': 'runner_1'},
            '_task_result_share_key': share_key,
            'expires_at': datetime(2026, 11, 7)}


def test_external_links_switch_only_when_a_scheduled_share_is_ready():
    settings = MagicMock()
    settings.app.public_base_url = 'https://agent.example.test/'
    with patch('src.config.settings.settings', settings):
        assert scheduled_result_link(notice(share_key='safe_key')) == (
            'https://agent.example.test/share/task-result/safe_key')
        assert scheduled_result_link(notice()) == (
            'https://agent.example.test/notification?ref=ref_1&tenant=tenant_1')
        assert scheduled_result_link(notice(task=False, share_key='safe_key')) is None
        assert scheduled_result_link(notice(event='task.paused', share_key='safe_key')) is None


def test_push_payload_only_carries_public_path_for_scheduled_terminal():
    adapter = WebPushAdapter(MagicMock())
    target = {'installation_ref': 'install_1', 'binding_version': 1}
    assert adapter.render(notice(share_key='safe_key'), target)['share_url'] == (
        '/share/task-result/safe_key')
    assert 'share_url' not in adapter.render(notice(), target)
    assert 'share_url' not in adapter.render(notice(task=False, share_key='safe_key'), target)
    assert 'share_url' not in adapter.render(notice(event='task.paused', share_key='safe_key'), target)


def test_backfill_deduplicates_runner_ids_and_returns_existing_mapping():
    cursor = MagicMock()
    cursor.fetchall.return_value = [{'runner_id': 'runner_1', 'share_key': 'persisted_key'}]
    result = task_result_links_for_runners_in_tx(
        cursor, ['runner_1', 'runner_1'], 'tenant_1', 'user_1')
    assert result == {'runner_1': 'persisted_key'}
    insert_sql, insert_values = cursor.execute.call_args_list[0].args
    assert 'ON CONFLICT (task_id, runner_id) DO NOTHING' in insert_sql
    assert insert_values[1] == ['runner_1']
    assert insert_values[2:] == ('user_1', 'tenant_1')


def test_inbox_link_switches_scheduled_terminal_only():
    subject = Subject('user_1', 'tenant_1')
    params = notice()['parameters']
    assert NotificationRepository._inbox_link('task.completed', params, subject,
        notification_ref='ref_1', share_key='safe_key') == '/share/task-result/safe_key'
    assert NotificationRepository._inbox_link('task.failed', params, subject,
        notification_ref='ref_1', share_key='safe_key') == '/share/task-result/safe_key'
    assert NotificationRepository._inbox_link('task.completed', {'session_title': '聊天'}, subject,
        notification_ref='ref_1', share_key='safe_key') == '/notification?ref=ref_1&tenant=tenant_1'
    assert NotificationRepository._inbox_link('task.paused', params, subject,
        notification_ref='ref_1', share_key='safe_key') == '/notification?ref=ref_1&tenant=tenant_1'


def test_inbox_backfills_old_runner_once_for_list_and_keeps_chat_link(monkeypatch):
    import src.notifications.repository as repository

    rows = [
        {'notification_ref': 'ref_1', 'event_type': 'task.completed', 'category': 'task',
         'priority': 0, 'read_at': None, 'created_at': datetime(2026, 11, 6),
         'parameters_encrypted': notice()['parameters'],
         'resource_ref': {'type': 'runner_result', 'id': 'runner_1'}},
        {'notification_ref': 'ref_2', 'event_type': 'task.failed', 'category': 'task',
         'priority': 0, 'read_at': None, 'created_at': datetime(2026, 11, 6),
         'parameters_encrypted': {'session_title': '聊天', 'event_kind': 'failed'},
         'resource_ref': {'type': 'runner_result', 'id': 'runner_chat'}},
    ]
    cursor = MagicMock()
    cursor.fetchone.return_value = {'unread': 2}
    cursor.fetchall.return_value = rows
    connection = MagicMock()
    connection.cursor.return_value = cursor

    @contextmanager
    def connect():
        yield connection

    repo = NotificationRepository.__new__(NotificationRepository)
    repo.connection_factory = connect
    repo.guard = lambda *args: {'role': 'user'}
    monkeypatch.setattr(repository, 'unprotect', lambda raw: raw)
    called = []
    monkeypatch.setattr('src.services.task_result_sharing.service.task_result_links_for_runners_in_tx',
        lambda cur, ids, tenant, user: called.append((cur, ids, tenant, user)) or {'runner_1': 'safe_key'})
    result = repo.inbox(Subject('user_1', 'tenant_1'))
    assert called == [(cursor, ['runner_1'], 'tenant_1', 'user_1')]
    assert result['items'][0]['link'] == '/share/task-result/safe_key'
    assert result['items'][1]['link'] == '/notification?ref=ref_2&tenant=tenant_1'
    connection.commit.assert_called_once()


def test_expand_rolls_back_failed_backfill_and_keeps_delivery_processing(monkeypatch):
    import src.notifications.repository as repository

    cursor = MagicMock()
    cursor.fetchall.return_value = [{
        'notification_id': 'notification_1', 'event_type': 'task.completed',
        'resource_ref': {'type': 'runner_result', 'id': 'runner_1'},
        'tenant_id': 'tenant_1', 'recipient_user_id': 'user_1',
        'expires_at': datetime(2026, 11, 7), 'database_now': datetime(2026, 11, 6),
        'policy_key': repository.RULE_EVENT_POLICY_KEY,
    }]
    connection = MagicMock()
    connection.cursor.return_value = cursor

    @contextmanager
    def connect():
        yield connection

    repo = NotificationRepository.__new__(NotificationRepository)
    repo.connection_factory = connect
    repo.config = MagicMock(batch_size=1)
    rendered = []
    repo._expand_rule_event = lambda cur, message: (rendered.append(message.copy()) or 'expanded', [])
    monkeypatch.setattr('src.services.task_result_sharing.service.task_result_links_for_runners_in_tx',
                        lambda *args: (_ for _ in ()).throw(RuntimeError('private details')))
    assert repo.expand() == 1
    assert rendered[0].get('_task_result_share_key') is None
    assert any(call.args[0] == 'ROLLBACK TO SAVEPOINT delivery_result_share'
               for call in cursor.execute.call_args_list)
    connection.commit.assert_called_once()
