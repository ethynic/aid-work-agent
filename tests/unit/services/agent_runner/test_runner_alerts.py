"""Long-running alerts follow the attempt's committed state, not stale scans."""

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.services.agent_runner.event_repository import EventRepository
from src.services.agent_runner.execution_repository import ExecutionRepository
from src.services.agent_runner.finalizer import RunnerFinalizer
from src.services.agent_runner.ownership import Attempt
from src.services.agent_runner.runner_alerts import RunnerAlertRepository, SCAN_LIMIT


class Connection:
    def __init__(self, rows):
        self.rows = list(rows)
        self.statements = []
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def cursor(self):
        return self

    def execute(self, sql, params=()):
        self.statements.append((sql, params))

    def fetchone(self):
        return self.rows.pop(0)

    def fetchall(self):
        return self.rows.pop(0)

    def commit(self):
        self.committed = True


def test_stale_scan_cannot_alert_a_new_attempt():
    started = datetime(2026, 11, 6, tzinfo=timezone.utc)
    connection = Connection([{'runner_id': 'r', 'attempt': 4, 'status': 'running',
                              'tenant_id': 't', 'profile_id': 'p',
                              'execution_started_at': started + timedelta(minutes=2),
                              'database_now': started + timedelta(hours=1)}])
    repository = RunnerAlertRepository(lambda: connection)
    assert repository.inspect_candidate({'runner_id': 'r', 'attempt': 3,
                                         'execution_started_at': started}) is False
    assert len(connection.statements) == 1
    assert connection.committed is False


def test_scan_capacity_failure_cannot_report_a_successful_scan():
    connection = Connection([[{'runner_id': f'r{i}'} for i in range(SCAN_LIMIT + 1)]])
    repository = RunnerAlertRepository(lambda: connection)
    with pytest.raises(RuntimeError, match='RUNNER_ALERT_SCAN_CAPACITY_EXCEEDED'):
        repository.scan_once()
    assert len(connection.statements) == 1
    assert 'LIMIT %s' in connection.statements[0][0]
    assert connection.committed is False


def test_threshold_override_is_read_after_runner_lock():
    started = datetime(2026, 11, 6, tzinfo=timezone.utc)
    connection = Connection([{'runner_id': 'r', 'attempt': 2, 'status': 'running',
                              'tenant_id': 't', 'profile_id': 'p',
                              'execution_started_at': started,
                              'database_now': started + timedelta(minutes=20)},
                             {'threshold_minutes': 25}])
    repository = RunnerAlertRepository(lambda: connection)
    assert repository.inspect_candidate({'runner_id': 'r', 'attempt': 2,
                                         'execution_started_at': started}) is False
    assert 'FOR UPDATE' in connection.statements[0][0]
    assert 'agent_runner_alert_thresholds' in connection.statements[1][0]
    assert len(connection.statements) == 2
    assert connection.committed is False


def test_alert_and_notification_share_the_same_commit(monkeypatch):
    from src.notifications import producer
    started = datetime(2026, 11, 6, tzinfo=timezone.utc)
    runner = {'runner_id': 'r', 'attempt': 2, 'status': 'running',
              'tenant_id': 't', 'profile_id': 'p', 'execution_started_at': started,
              'database_now': started + timedelta(minutes=31)}
    connection = Connection([runner, None, {'alert_id': 'a', 'notification_ref': None}])
    calls = []
    monkeypatch.setattr(producer, 'publish_platform_runner_alert_in_tx',
                        lambda cursor, *, runner, duration_seconds:
                        calls.append((runner['runner_id'], duration_seconds)) or 'ref')
    repository = RunnerAlertRepository(lambda: connection)
    assert repository.inspect_candidate({'runner_id': 'r', 'attempt': 2,
                                         'execution_started_at': started}) is True
    assert calls == [('r', 1860)]
    assert connection.committed is True
    assert any('SET notification_ref=%s' in sql and params == ('ref', 'a')
               for sql, params in connection.statements)


def test_notification_failure_rolls_back_alert_write(monkeypatch):
    from src.notifications import producer
    started = datetime(2026, 11, 6, tzinfo=timezone.utc)
    runner = {'runner_id': 'r', 'attempt': 2, 'status': 'running',
              'tenant_id': 't', 'profile_id': 'p', 'execution_started_at': started,
              'database_now': started + timedelta(minutes=31)}
    connection = Connection([runner, None, {'alert_id': 'a', 'notification_ref': None}])
    def fail(*_args, **_kwargs):
        raise RuntimeError('notification unavailable')
    monkeypatch.setattr(producer, 'publish_platform_runner_alert_in_tx', fail)
    with pytest.raises(RuntimeError, match='notification unavailable'):
        RunnerAlertRepository(lambda: connection).inspect_candidate(
            {'runner_id': 'r', 'attempt': 2, 'execution_started_at': started})
    assert connection.committed is False


def test_waiting_commit_closes_only_original_attempt_alert(monkeypatch):
    monkeypatch.setattr(EventRepository, 'append_in_tx',
                        staticmethod(lambda _cursor, _before, after, **_: (after, None)))
    connection = Connection([])
    before = {'runner_id': 'r', 'attempt': 2, 'status': 'running'}
    after = {**before, 'status': 'waiting'}
    EventRepository.notify_in_tx(connection, before, after=after)
    assert connection.statements[0][1] == ('r', 2)
    assert 'SET ended_at=clock_timestamp()' in connection.statements[0][0]


def test_finalizing_remains_timed_until_business_terminal(monkeypatch):
    monkeypatch.setattr(EventRepository, 'append_in_tx',
                        staticmethod(lambda _cursor, _before, after, **_: (after, None)))
    connection = Connection([])
    before = {'runner_id': 'r', 'attempt': 2, 'status': 'running'}
    EventRepository.notify_in_tx(connection, before, after={**before, 'status': 'finalizing'})
    assert connection.statements == []


def test_reclaimed_finalizer_ends_old_attempt_alert_in_claim_transaction(monkeypatch):
    from src.services.agent_runner import execution_repository as module
    previous = {'runner_id': 'r', 'attempt': 2, 'status': 'finalizing',
                'checkpoint': {}, 'tenant_id': 't'}
    current = {**previous, 'attempt': 3}
    connection = Connection([previous, current])
    monkeypatch.setattr(module, 'decoded', lambda row: row)
    monkeypatch.setattr(module, 'sync_public_display', lambda _cursor, row, **_: row)
    monkeypatch.setattr(EventRepository, 'notify_in_tx',
                        staticmethod(lambda _cursor, _before, *, after: after))
    result = ExecutionRepository(lambda: connection).acquire('worker', 60)
    assert result['attempt'] == 3
    assert 'UPDATE agent_runner_alerts SET ended_at=clock_timestamp()' in connection.statements[1][0]
    assert connection.statements[1][1] == ('r', 2)
    assert 'UPDATE agent_runners SET status=' in connection.statements[2][0]
    assert connection.committed is True


def test_abandoned_finalization_closes_alert_even_when_public_event_fails(monkeypatch):
    from src.services.agent_runner import finalizer as module
    runner = {'runner_id': 'r', 'attempt': 2, 'checkpoint': {}, 'public_snapshot': {}}
    connection = Connection([{**runner, 'status': 'failed'}])
    closed = []
    monkeypatch.setattr(module, 'close_pending_controls', lambda *_args: None, raising=False)
    monkeypatch.setattr(module, 'project_terminal_public_state',
                        lambda *_args: ({}, {}))
    monkeypatch.setattr(module.EventRepository, 'notify_in_tx',
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError('event failed')))
    import src.services.agent_runner.runner_alerts as alerts
    monkeypatch.setattr(alerts, 'close_alert_in_tx',
                        lambda _cursor, runner_id, attempt: closed.append((runner_id, attempt)))
    finalizer = RunnerFinalizer()
    monkeypatch.setattr(finalizer, '_after_commit', lambda *_args: None)
    finalizer._abandon(connection, connection, Attempt('r', 'w', 2), runner, 'test')
    assert closed == [('r', 2)]
    assert connection.committed is True


def _detail_row(*, current_attempt):
    started = datetime(2026, 11, 6, tzinfo=timezone.utc)
    return {'alert_id': 'a', 'runner_id': 'r', 'attempt': 2, 'current_attempt': current_attempt,
            'tenant_id': 't', 'profile_id': 'p', 'started_at': started,
            'alerted_at': started + timedelta(minutes=30), 'ended_at': None,
            'elapsed_seconds': 1800, 'threshold_minutes': 30,
            'input': {'text': '完整原问题'},
            'checkpoint': {'execution': {'system_prompt': 'system',
                'messages': [{'role': 'assistant', 'content': 'x' * 300,
                              'tool_calls': [{'function': {'arguments': '{"token":"secret"}'}}]}],
                'model_calls': [{'phase': 'completed', 'end_ts': started.timestamp(),
                                 'response': {'content': '', 'tool_calls': [
                                     {'function': {'arguments': '{"password":"secret"}'}}]}}]}}}


def test_historical_alert_does_not_show_later_attempt_model_context():
    result = RunnerAlertRepository(lambda: Connection([_detail_row(current_attempt=3)])).detail('a')
    assert result['runner']['request_text'] == '完整原问题'
    assert result['runner']['latest_model_response'] is None
    assert result['runner']['latest_model_context'] is None
    assert result['runner']['model_unavailable_reason'] == 'CURRENT_ATTEMPT_CHANGED'


def test_current_alert_masks_tool_credentials_and_marks_truncated_context():
    result = RunnerAlertRepository(lambda: Connection([_detail_row(current_attempt=2)])).detail('a')
    runner = result['runner']
    assert 'secret' not in str(runner['latest_model_response'])
    assert 'secret' not in str(runner['latest_model_context'])
    assert '***' in str(runner['latest_model_response'])
    assert '…' in runner['latest_model_context']['messages'][0]['content']
    assert runner['context_redacted'] is True


def test_new_database_initializers_include_runner_alert_schema():
    root = Path(__file__).resolve().parents[4]
    for name in ('deploy/init-postgres.sql', 'deploy/nuodu/init-postgres.sql'):
        schema = (root / name).read_text(encoding='utf-8')
        assert 'execution_started_at TIMESTAMPTZ' in schema
        assert 'CREATE TABLE IF NOT EXISTS agent_runner_alerts' in schema
        assert 'CREATE TABLE IF NOT EXISTS agent_runner_monitor_status' in schema


def test_platform_alert_api_rechecks_fresh_role_and_global_context(monkeypatch):
    from src.api import runner_alerts as api
    request = SimpleNamespace(headers={'Authorization': 'Bearer token'})
    monkeypatch.setattr(api, 'get_current_tenant_id', lambda: None)
    monkeypatch.setattr(api, 'fresh_web_subject',
                        lambda _authorization: {'role': 'tenant_admin', 'tenant_id': 't'})
    with pytest.raises(HTTPException) as denied:
        asyncio.run(api._require_platform_context(request))
    assert denied.value.status_code == 403
    assert denied.value.detail['debug'] == 'PLATFORM_CONTEXT_REQUIRED'

    monkeypatch.setattr(api, 'fresh_web_subject',
                        lambda _authorization: {'role': 'platform_admin', 'tenant_id': None})
    request.headers['X-Tenant-Id'] = 't'
    with pytest.raises(HTTPException) as tenant_header:
        asyncio.run(api._require_platform_context(request))
    assert tenant_header.value.status_code == 403
