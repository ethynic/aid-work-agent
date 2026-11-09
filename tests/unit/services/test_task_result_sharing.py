"""A task result link shows one owned terminal run and only its visible products."""

from contextlib import contextmanager
from datetime import datetime
from unittest.mock import MagicMock

from fastapi import HTTPException
import pytest

from src.services.task_result_sharing import service


@pytest.fixture(autouse=True)
def isolate_file_bind(monkeypatch):
    monkeypatch.setattr(service.reply_sharing, '_bind_resources', lambda *args: None)


def task():
    return {'task_id': 'sched_1', 'session_id': 'session_1', 'tenant_id': 'tenant_1',
            'user_id': 'user_1', 'name': '日报', 'status': 'active'}


def runner():
    return {'runner_id': 'runner_1', 'session_id': 'session_1', 'tenant_id': 'tenant_1',
            'user_id': 'user_1', 'source': 'scheduled', 'session_kind': 'web',
            'status': 'completed', 'finished_at': datetime(2026, 11, 6, 12)}


def share():
    return {'share_key': 'opaque_key', 'task_id': 'sched_1', 'runner_id': 'runner_1',
            'session_id': 'session_1', 'tenant_id': 'tenant_1', 'user_id': 'user_1'}


def database(monkeypatch, rows):
    cursor = MagicMock()
    cursor.fetchone.side_effect = rows
    connection = MagicMock()
    connection.cursor.return_value = cursor

    @contextmanager
    def connect():
        yield connection

    monkeypatch.setattr(service, 'get_db_connection', connect)
    return cursor


def test_share_creation_uses_owned_task_and_is_idempotent():
    cursor = MagicMock()
    cursor.fetchone.return_value = {'share_key': 'same_key'}
    first = service.ensure_task_result_share_in_tx(cursor, task(), runner())
    second = service.ensure_task_result_share_in_tx(cursor, task(), runner())
    assert first == second == 'same_key'
    insert_sql, insert_args = cursor.execute.call_args_list[0].args
    assert 'ON CONFLICT (task_id, runner_id) DO NOTHING' in insert_sql
    assert insert_args[1:6] == ('tenant_1', 'user_1', 'sched_1', 'runner_1', 'session_1')


def test_terminal_notification_creates_share_in_same_cursor_without_changing_return(monkeypatch):
    from src.notifications import events, producer

    current = {**runner(), 'scope_key': 'tenant:tenant_1', 'actor_kind': 'system',
               'actor_id': 'scheduler', 'accepted_at': datetime(2026, 11, 6, 11),
               'input': {'request_data': {'scheduled': {'task_id': 'sched_1'}}}}
    cursor = MagicMock()
    cursor.fetchone.side_effect = [{'title': '日报'}, task(), {'share_key': 'opaque_key'}]
    published = []

    def publish(same_cursor, **kwargs):
        published.append((same_cursor, kwargs))
        return 'notification_ref'

    monkeypatch.setattr(events, 'publish_in_tx', publish)
    assert producer.publish_runner_terminal_in_tx(cursor, current, type('C', (), {'ttl_seconds': 3600})()) == 'notification_ref'
    assert published[0][0] is cursor
    assert published[0][1]['event_type'] == 'task.completed'
    assert any('INSERT INTO task_result_shares' in call.args[0] for call in cursor.execute.call_args_list)


def test_deleted_task_does_not_publish_a_dead_share(monkeypatch):
    from src.notifications import events, producer

    current = {**runner(), 'scope_key': 'tenant:tenant_1', 'actor_kind': 'system',
               'actor_id': 'scheduler', 'accepted_at': datetime(2026, 11, 6, 11),
               'input': {'request_data': {'scheduled': {'task_id': 'sched_1'}}}}
    cursor = MagicMock()
    cursor.fetchone.side_effect = [{'title': '日报'}, None]
    called = []
    monkeypatch.setattr(events, 'publish_in_tx', lambda *args, **kwargs: called.append(True))
    assert producer.publish_runner_terminal_in_tx(cursor, current, type('C', (), {'ttl_seconds': 3600})()) is None
    assert called == []
    assert not any('INSERT INTO task_result_shares' in call.args[0] for call in cursor.execute.call_args_list)


def test_view_uses_final_message_and_never_exposes_internal_references(monkeypatch):
    message = {'content': '![plot](/api/files/secret_file/download)',
               'metadata': {'runner_id': 'runner_1', 'images': [{'file_id': 'secret_file',
                            'download_url': '/api/files/secret_file/download'}]},
               'created_at': datetime(2026, 11, 6, 12)}
    cursor = database(monkeypatch, [share(), task(), runner(), message])
    result = service.view_task_result('opaque_key')
    assert result['task_name'] == '日报'
    assert result['message']['images'][0]['file_id'] == 'asset_0'
    assert '/api/public/task-result-shares/opaque_key/files/asset_0?ticket=' in result['message']['content']
    assert 'secret_file' not in str(result) and 'session_1' not in str(result)
    assert result['viewer_is_owner'] is False
    message_sql = cursor.execute.call_args_list[-1].args[0]
    assert "(metadata::jsonb)->>'runner_id'" in message_sql
    assert 'tool_calls' in message_sql and 'tool_call_id' in message_sql and 'wait_id' in message_sql


def test_view_ends_pool_probe_transaction_before_snapshot_isolation(monkeypatch):
    # 连接池检出时健康探针 SELECT 1 已隐式开启事务；SET TRANSACTION 必须是事务
    # 首条语句，否则线上报 ActiveSqlTransaction（2026-11-09 agent2 500 事故）。
    message = {'content': 'done', 'metadata': {'runner_id': 'runner_1'}, 'created_at': datetime(2026, 11, 6, 12)}
    cursor = MagicMock()
    cursor.fetchone.side_effect = [share(), task(), runner(), message]
    connection = MagicMock()
    connection.cursor.return_value = cursor

    @contextmanager
    def connect():
        yield connection

    monkeypatch.setattr(service, 'get_db_connection', connect)
    service.view_task_result('opaque_key')
    calls = [(name.rsplit('.', 1)[-1], args[0] if args else '')
             for name, args, kwargs in connection.mock_calls
             if name.rsplit('.', 1)[-1] in ('rollback', 'execute')]
    first_set_tx = next(i for i, (name, sql) in enumerate(calls)
                        if name == 'execute' and 'SET TRANSACTION' in sql)
    assert any(name == 'rollback' for name, _ in calls[:first_set_tx])


def test_file_only_result_uses_live_runner_result_when_no_history_reply(monkeypatch):
    live = {**runner(), 'result': {'output': '', 'images': [],
        'assistant_metadata': {'downloadableFiles': [{'file_id': 'secret_report', 'file_name': 'report.pdf'}]}}}
    database(monkeypatch, [share(), task(), live, None, {'present': 1}])
    result = service.view_task_result('opaque_key')
    assert result['message']['downloadableFiles'][0]['file_id'] == 'asset_0'
    assert result['message']['downloadableFiles'][0]['file_name'] == 'report.pdf'
    assert 'secret_report' not in str(result)


def test_deleted_text_reply_does_not_reappear_from_runner_result(monkeypatch):
    live = {**runner(), 'result': {'output': '已删除的最终回复'}}
    database(monkeypatch, [share(), task(), live, None, {'present': 1}])
    with pytest.raises(HTTPException) as error:
        service.view_task_result('opaque_key')
    assert error.value.status_code == 404
    assert error.value.detail['code'] == 'TASK_GONE'


def test_invalid_file_ticket_never_queries_live_result(monkeypatch):
    monkeypatch.setattr(service, 'get_db_connection',
                        lambda: pytest.fail('invalid ticket must not reach the database'))
    with pytest.raises(HTTPException) as error:
        service.task_result_file('opaque_key', 'asset_0', 'invalid')
    assert error.value.status_code == 403
    assert error.value.detail['code'] == 'SHARE_ACCESS_REQUIRED'


def test_file_only_result_expires_when_its_session_messages_are_cleared(monkeypatch):
    live = {**runner(), 'result': {'assistant_metadata': {'downloadableFiles': [
        {'file_id': 'secret_report', 'file_name': 'report.pdf'}]}}}
    database(monkeypatch, [share(), task(), live, None, None])
    with pytest.raises(HTTPException) as error:
        service.view_task_result('opaque_key')
    assert error.value.status_code == 404
    assert error.value.detail['code'] == 'TASK_GONE'


@pytest.mark.parametrize('rows,code', [
    ([None], 'SHARE_NOT_FOUND'),
    ([share(), None], 'TASK_GONE'),
    ([share(), task(), None], 'TASK_GONE'),
    ([share(), task(), {**runner(), 'tenant_id': 'tenant_2'}], 'TASK_GONE'),
    ([share(), task(), {**runner(), 'status': 'running'}], 'TASK_GONE'),
    ([share(), task(), runner(), None, None], 'TASK_GONE'),
])
def test_missing_or_foreign_resource_is_never_readable(monkeypatch, rows, code):
    database(monkeypatch, rows)
    with pytest.raises(HTTPException) as error:
        service.view_task_result('opaque_key')
    assert error.value.status_code == 404
    assert error.value.detail['code'] == code


def test_viewer_owner_requires_same_user_and_tenant(monkeypatch):
    message = {'content': 'Finished', 'metadata': {}, 'created_at': datetime(2026, 11, 6)}
    for identity, expected in [
        ({'user_id': 'user_1', 'tenant_id': 'tenant_1'}, True),
        ({'user_id': 'user_1', 'tenant_id': 'tenant_2'}, False),
        ({'user_id': 'user_2', 'tenant_id': 'tenant_1'}, False),
        (None, False),
    ]:
        database(monkeypatch, [share(), task(), runner(), message])
        assert service.view_task_result('opaque_key', viewer=identity)['viewer_is_owner'] is expected


def test_file_ticket_is_bound_to_this_share_and_alias(monkeypatch):
    message = {'content': 'See [report](file_id:secret_file)', 'metadata': {},
               'created_at': datetime(2026, 11, 6)}
    database(monkeypatch, [share(), task(), runner(), message])
    ticket = service.view_task_result('opaque_key')['access_ticket']
    database(monkeypatch, [share(), task(), runner(), message])
    with pytest.raises(HTTPException) as error:
        service.task_result_file('opaque_key', 'asset_0', ticket + 'tampered')
    assert error.value.status_code == 403
    database(monkeypatch, [share(), task(), runner(), message])
    with pytest.raises(HTTPException) as error:
        service.task_result_file('opaque_key', 'asset_9', ticket)
    assert error.value.status_code == 404


def test_valid_file_ticket_resolves_only_the_original_tenant_file(monkeypatch):
    message = {'content': '[report](file_id:secret_file)', 'metadata': {},
               'created_at': datetime(2026, 11, 6)}
    database(monkeypatch, [share(), task(), runner(), message])
    ticket = service.view_task_result('opaque_key')['access_ticket']
    resolved = []
    monkeypatch.setattr(service.reply_sharing, 'get_file_asset', lambda file_id: None)
    monkeypatch.setattr(service.reply_sharing, 'resolve_scoped_uploaded_file',
                        lambda file_id, tenant_id, metadata: resolved.append((file_id, tenant_id)) or
                        {'path': '/tmp/report.pdf', 'name': 'report.pdf', 'mime_type': 'application/pdf'})
    database(monkeypatch, [share(), task(), runner(), message])
    assert service.task_result_file('opaque_key', 'asset_0', ticket)['name'] == 'report.pdf'
    assert resolved == [('secret_file', 'tenant_1')]
    database(monkeypatch, [share(), task(), runner(), message])
    with pytest.raises(HTTPException) as error:
        service.task_result_file('another_key', 'asset_0', ticket)
    assert error.value.status_code == 403
