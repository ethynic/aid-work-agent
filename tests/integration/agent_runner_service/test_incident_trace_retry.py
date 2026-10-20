"""Independent regression: real Worker error capture and finalizer-only retry."""
import uuid
from contextlib import contextmanager

from psycopg2 import sql
import pytest

from .provider import Reply
from .test_worker import workers, api_pair, prices, accept, runner, terminal
from .test_worker_recovery import logs_schema

pytestmark = pytest.mark.integration


@pytest.mark.parametrize('status', ['interrupted', 'waiting', 'paused', 'cancelled'])
def test_unfinished_tool_persists_exact_runner_status_in_real_postgres(
        service_database, logs_schema, monkeypatch, status):
    from src.core.trace_collector import TraceCollector

    @contextmanager
    def isolated_logs_connection():
        with service_database.connect() as connection, connection.cursor() as cursor:
            class Cursor:
                def __getattr__(self, name):
                    return getattr(cursor, name)

                def commit(self):
                    connection.commit()
            yield Cursor()

    # Only choose the disposable connection; execute production SQL unchanged.
    monkeypatch.setattr('src.db.database.get_logs_connection', isolated_logs_connection)
    collector = TraceCollector(session_id='fixture', tenant_id='fixture', user_id='fixture', input_msg='next')
    collector.on_event({'type': 'tool_start', 'toolName': 'boss_resume_next'})
    collector.trace.status = status
    collector.on_complete(persist_sync=True)
    stored = service_database.rows('SELECT status,metadata FROM obs_spans WHERE trace_id=%s', (collector.trace_id,))
    assert len(stored) == 1
    assert stored[0]['status'] == status
    assert stored[0]['metadata']['success'] is False
    trace = service_database.rows('SELECT status,error_message FROM obs_traces WHERE trace_id=%s', (collector.trace_id,))[0]
    assert trace == {'status': status, 'error_message': None}


def test_worker_storage_failure_records_safe_error_then_retry_clears_it(
        workers, actors, service_database, logs_schema):
    marker = workers.provider.register(Reply(content='fixture-recovered-response'))
    accepted = accept(workers.api, actors['a'], marker)
    identifier = accepted['runner_id']
    trace_id = 'tr_' + identifier
    constraint = 'trace_fixture_fault_' + uuid.uuid4().hex
    with service_database.connect() as connection, connection.cursor() as cursor:
        cursor.execute(sql.SQL("ALTER TABLE agent_runners ADD CONSTRAINT {} CHECK (runner_id <> {} OR status NOT IN ('completed','failed','cancelled'))").format(
            sql.Identifier(constraint), sql.Literal(identifier)))
    try:
        child, _ = workers.start()
        workers.assert_clean_exit(child)
        assert runner(service_database, identifier)['status'] == 'finalizing'
        initial = service_database.rows('SELECT status,error_message FROM obs_traces WHERE trace_id=%s', (trace_id,))
        assert len(initial) == 1
        assert initial[0]['error_message'] == 'EXECUTION_INTERRUPTED'
        # Never leak PostgreSQL constraint details or the row's private fields.
        assert constraint not in initial[0]['error_message']
    finally:
        with service_database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql.SQL('ALTER TABLE agent_runners DROP CONSTRAINT IF EXISTS {}').format(sql.Identifier(constraint)))
    service_database.rows("UPDATE agent_runners SET lease_until=clock_timestamp()-INTERVAL '1 second' WHERE runner_id=%s", (identifier,))
    retry, _ = workers.start()
    workers.assert_clean_exit(retry)
    assert terminal(service_database, identifier)['status'] == 'completed'
    completed = service_database.rows('SELECT status,error_message,output FROM obs_traces WHERE trace_id=%s', (trace_id,))[0]
    assert completed == {'status': 'completed', 'error_message': None, 'output': 'fixture-recovered-response'}
    assert len(workers.provider.requests(marker)) == 1
