"""Actual PG faults must cross existing Browser retry/catch boundaries.

These tests inject only a PostgreSQL trigger fault, not owner/core/runtime/tool
results. The happy real Chromium chain lives in test_worker_browser_producer.
"""
from contextlib import contextmanager
import json
import uuid

from psycopg2 import sql
import pytest

from .browser_io import browser_page, browser_redis
from .conftest import wait_for
from .provider import tool_reply
from .test_worker import workers, api_pair, prices, accept, runner, decoded

pytestmark = [pytest.mark.integration, pytest.mark.real_browser]


@contextmanager
def browser_pg_fault(database, identifier, boundary):
    name = 'browser_fixture_failure_' + uuid.uuid4().hex
    predicate = sql.SQL('NEW.runner_id={}').format(sql.Literal(identifier))
    if boundary == 'activate':
        predicate += sql.SQL(" AND NEW.runtime_state='live'")
    with database.connect() as connection, connection.cursor() as cursor:
        cursor.execute(sql.SQL("""CREATE FUNCTION {}() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN IF {} THEN RAISE EXCEPTION 'BROWSER_FIXTURE_STORAGE_FAILURE'; END IF;
            RETURN NEW; END $$""").format(sql.Identifier(name), predicate))
        cursor.execute(sql.SQL('CREATE TRIGGER {} BEFORE {} ON bs_browser_runs FOR EACH ROW EXECUTE FUNCTION {}()')
                       .format(sql.Identifier(name), sql.SQL('INSERT' if boundary == 'bind' else 'UPDATE'), sql.Identifier(name)))
    try:
        yield
    finally:
        with database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql.SQL('DROP TRIGGER IF EXISTS {} ON bs_browser_runs').format(sql.Identifier(name)))
            cursor.execute(sql.SQL('DROP FUNCTION IF EXISTS {}()').format(sql.Identifier(name)))


@pytest.mark.parametrize('boundary', ['bind', 'activate'])
def test_browser_mandatory_pg_failure_interrupts_without_failed_tool_or_claim_release(
        workers, actors, service_database, browser_page, browser_redis, boundary):
    actor = actors['a']
    client, prefix, environment = browser_redis
    workers.environment.update(environment)
    workers.environment['AGENT_RUNNER_BROWSER_OWNER_ENABLED'] = 'true'
    initial = tool_reply('browser_automation', {}, call_id='browser-authority-original-call')
    marker = workers.provider.register(initial)
    initial.tool_calls[0]['function']['arguments'] = json.dumps(
        {'task': marker + ' inspect fixture', 'url': browser_page.url, 'headless': True})
    accepted = accept(workers.api, actor, marker)
    identifier = accepted['runner_id']
    child = None
    try:
        with browser_pg_fault(service_database, identifier, boundary):
            child, _ = workers.start()
            wait_for(lambda: runner(service_database, identifier)['status'] == 'interrupted', timeout=30)
            workers.assert_clean_exit(child)
        row = runner(service_database, identifier)
        root = decoded(row['checkpoint'])['execution']
        original = root['tools']['browser-authority-original-call']
        assert not original.get('result_recorded')
        assert original['phase'] == 'dispatching'
        assert row['result'] is None and row['finished_at'] is None
        assert service_database.rows('SELECT owner_runner_id FROM agent_runner_session_claims WHERE owner_runner_id=%s',
                                     (identifier,)) == [{'owner_runner_id': identifier}]
        assert service_database.rows('SELECT 1 FROM chat_records WHERE session_id=%s', (actor.session_id,)) == []
        assert len(workers.provider.requests(marker)) == 1 and not workers.provider.errors
        assert browser_page.requests == []
        runs = service_database.rows('SELECT run_id,runtime_state,owner_lease_until FROM bs_browser_runs WHERE runner_id=%s', (identifier,))
        if boundary == 'bind':
            assert runs == []
            assert list(client.scan_iter(match=prefix + ':browser_run:*')) == []
        else:
            assert len(runs) == 1 and runs[0]['runtime_state'] == 'closed'
            assert runs[0]['owner_lease_until'] is None
            assert client.get(f"{prefix}:browser_owner:{actor.tenant_id}:{runs[0]['run_id']}") is None
    finally:
        if child is not None:
            workers.processes.stop(child)
        service_database.rows('DELETE FROM bs_browser_assistance_requests WHERE runner_id=%s', (identifier,))
        service_database.rows('DELETE FROM bs_browser_runs WHERE runner_id=%s', (identifier,))
