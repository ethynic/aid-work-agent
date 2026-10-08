"""Prepared original standalone service/Web SSE contracts; not yet executed.

All auth/ownership/state/fee are original PG ports. The smoke uses declared
native durable transitions, not a physical model/Engine producer.
"""
from decimal import Decimal
import json
import re
import time

import pytest

from src.services.agent_runner.execution_repository import ExecutionRepository
from src.services.agent_runner.finalizer import RunnerFinalizer
from src.services.agent_runner.repository import RunnerRepository

from .event_stream import StreamObserver
from .test_api import start_api_pair, body, require_status
from .test_storage import attempt, storage
from .test_usage_storage import ledger, prices, started
from .web_gateway import WebGateway

pytestmark = pytest.mark.integration
STATE_KEYS = {'runner_id', 'version', 'attempt', 'status', 'settlement_status',
              'view_revision', 'control_revision'}
EVENT_KEYS = STATE_KEYS | {'seq', 'kind', 'invalidate'}


@pytest.fixture(scope='module')
def event_apis(service_processes):
    return start_api_pair(service_processes, extra_environment={
        'AGENT_RUNNER_WEB_SERVICE_ID': 'runner-test', 'REDIS_ENABLED': 'false'})


@pytest.fixture
def event_web(event_apis, service_processes):
    gateway = WebGateway(service_processes, environment={
        'AGENT_RUNNER_WEB_ENABLED': 'true',
        'AGENT_RUNNER_API_URL': event_apis.urls[0],
        'AGENT_RUNNER_WEB_SERVICE_ID': event_apis.service_id,
        'AGENT_RUNNER_WEB_SERVICE_TOKEN': event_apis._service_token},
        factory='src.api.agent_runner_web:create_app', access_log=False)
    try:
        yield gateway
    finally:
        gateway.close()


def event_url(api, identifier, *, web=False):
    return (api.url + '/api/chat/runners/' if web else api.urls[0] + '/v1/runners/') + identifier + '/events'


def assert_event(frame, identifier, seq, kind):
    assert frame.event == kind and frame.identifier == str(seq)
    assert set(frame.payload) == EVENT_KEYS
    assert frame.payload['runner_id'] == identifier and frame.payload['seq'] == seq
    assert type(frame.payload['version']) is int and frame.payload['version'] == 1
    assert type(frame.payload['attempt']) is int and frame.payload['attempt'] >= 0
    assert frame.payload['invalidate'] is True


def assert_hint(frame, identifier, head, floor):
    assert frame.event == 'reset' and frame.identifier == str(head)
    assert set(frame.payload) == {'reset', 'query_required', 'head', 'floor', 'last_seq', 'state'}
    assert frame.payload['reset'] is True and frame.payload['query_required'] is True
    assert frame.payload['head'] == frame.payload['last_seq'] == head
    assert frame.payload['floor'] == floor and set(frame.payload['state']) == STATE_KEYS
    assert frame.payload['state']['runner_id'] == identifier


def safe_handshake_diagnostic(processes, service, web):
    """Read only allowlisted enums/locations while original logs still exist."""
    reports = []
    for ordinal, path in enumerate(sorted(processes.root.glob('child-*.log'))):
        log = path.read_text(errors='replace')[-65536:]
        kinds = re.findall(r'HTTP operation failed: ([A-Za-z_][A-Za-z0-9_]{0,80});', log)
        kinds += re.findall(r'^([A-Za-z_][A-Za-z0-9_.]{0,80}(?:Error|Exception|Failure)):', log, re.MULTILINE)
        locations = re.findall(r'/app/(src/[A-Za-z0-9_./-]+\.py):(\d+):([A-Za-z0-9_<>]+)', log)
        locations += re.findall(r'File "/app/(src/[A-Za-z0-9_./-]+\.py)", line (\d+), in ([A-Za-z0-9_<>]+)', log)
        reports.append({'child_ordinal': ordinal, 'exception_kinds': sorted(set(kinds))[:8],
                        'locations': [list(item) for item in locations[-12:]]})
    return {'service': {'status': service.status, 'error_code': service.error_code,
                        'reader_exception': service.error_class},
            'web': {'status': web.status, 'error_code': web.error_code,
                    'reader_exception': web.error_class}, 'original_child_logs': reports}


def test_actual_service_and_web_proxy_observe_original_pg_ledger_then_drain_settled_terminal(
        event_apis, event_web, actors, service_database):
    actor = actors['a']
    accepted = require_status(event_apis.call('POST', '/v1/runners', actor=actor,
        json=body(actor)), 202)['runner']
    identifier = accepted['runner_id']
    with StreamObserver(event_url(event_apis, identifier), headers=event_apis.headers(actor)) as service, \
            StreamObserver(event_url(event_web, identifier, web=True), headers=event_web.headers(actor)) as web:
        if service.status != 200 or web.status != 200:
            print('SAFE_SSE_HANDSHAKE_DIAGNOSTIC=' + json.dumps(
                safe_handshake_diagnostic(event_web.processes, service, web), sort_keys=True))
        for stream in (service, web):
            assert stream.status == 200 and stream.content_type.startswith('text/event-stream')
            assert_event(stream.next(), identifier, 1, 'created')
        execution = ExecutionRepository(service_database.connect)
        owned = execution.acquire('sse-smoke-original-owner', 30)
        assert owned['runner_id'] == identifier
        for stream in (service, web):
            assert_event(stream.next(), identifier, 2, 'revision_changed')
        staged = execution.stage_finalization(attempt(owned), owned['revision'], {},
            {'status': 'completed', 'output': 'Finite SSE fixture output', 'images': [], 'messages': []}, {})
        terminal = RunnerFinalizer(service_database.connect).finalize(attempt(owned))
        assert terminal['settlement_status'] == 'settled'
        for stream in (service, web):
            assert_event(stream.next(), identifier, staged['event_seq'], 'revision_changed')
            assert_event(stream.next(), identifier, terminal['event_seq'], 'terminal')
            stream.closed()
        query = require_status(event_web.call('GET', '/api/chat/runners/' + identifier, actor=actor), 200)['runner']
        assert query['status'] == 'completed' and query['result']['output'] == 'Finite SSE fixture output'
    assert service_database.rows('SELECT 1 FROM agent_runner_session_claims WHERE owner_runner_id=%s', (identifier,)) == []
    assert service_database.rows('SELECT credit_cost FROM chat_records WHERE record_id=%s', (terminal['record_id'],)) == [{'credit_cost': Decimal('0')}]
    assert service_database.rows('SELECT 1 FROM agent_runner_usage_receipts WHERE runner_id=%s', (identifier,)) == []
    assert len(service_database.rows('SELECT 1 FROM chat_messages WHERE session_id=%s', (actor.session_id,))) == 2


def test_actual_idle_http_stream_rechecks_revoked_token_and_detach_does_not_cancel_other_observer(
        event_apis, actors, service_database):
    actor = actors['a']
    accepted = require_status(event_apis.call('POST', '/v1/runners', actor=actor,
        json=body(actor)), 202)['runner']
    identifier = accepted['runner_id']
    with StreamObserver(event_url(event_apis, identifier), headers=event_apis.headers(actor), params={'after_seq': 1}) as one, \
            StreamObserver(event_url(event_apis, identifier), headers=event_apis.headers(actor), params={'after_seq': 1}) as two:
        assert_hint(one.next(), identifier, 1, 0)
        assert_hint(two.next(), identifier, 1, 0)
        one.close()
        detach_elapsed = one.close_finished_at - one.close_started_at
        assert not one._thread.is_alive() and detach_elapsed < 25
        assert not two.finished.is_set()
        before = service_database.rows('SELECT * FROM agent_runners WHERE runner_id=%s', (identifier,))[0]
        service_database.rows('DELETE FROM tokens WHERE token=%s', (actor.token,))
        revoked_at = time.monotonic()
        try:
            two.closed(timeout=5)
        finally:
            print('SAFE_IDLE_TIMING=' + json.dumps({'detach_seconds': round(detach_elapsed, 6),
                'detached_reader_finished': one.finished.is_set(),
                'other_reader_finished': two.finished.is_set(),
                'other_reader_error_class': two.error_class,
                'other_finish_relative_to_token_commit':
                    round(two.finished_at - revoked_at, 6) if two.finished_at is not None else None}, sort_keys=True))
        assert service_database.rows('SELECT * FROM agent_runners WHERE runner_id=%s', (identifier,)) == [before]
        require_status(event_apis.call('GET', '/v1/runners/' + identifier + '/events', actor=actor), 401)


def test_actual_pending_terminal_stream_stays_until_original_late_receipt_settlement_only(
        ledger, event_apis, actors, service_database):
    repository, execution, usage, owned, _, _ = ledger
    receipt = started(ledger, call='sse-original-late-receipt')
    usage.uncertain(attempt(owned), receipt['receipt_id'])
    execution.stage_finalization(attempt(owned), owned['revision'], {},
        {'status': 'completed', 'output': 'Pending SSE fixture', 'images': [], 'messages': []}, {})
    finalizer = RunnerFinalizer(service_database.connect)
    terminal = finalizer.finalize(attempt(owned))
    assert terminal['settlement_status'] == 'pending'
    identifier = owned['runner_id']
    with StreamObserver(event_url(event_apis, identifier), headers=event_apis.headers(actors['a']),
                        params={'after_seq': terminal['event_seq']}) as observer:
        hint = observer.next()
        assert_hint(hint, identifier, terminal['event_seq'], 0)
        assert hint.payload['state']['settlement_status'] == 'pending'
        assert not observer.finished.is_set()
        history = service_database.rows('SELECT * FROM chat_messages WHERE session_id=%s ORDER BY message_id', (actors['a'].session_id,))
        usage.observe(attempt(owned), receipt['receipt_id'], {'prompt_tokens': 1000, 'completion_tokens': 0})
        settled = finalizer.settle_pending_usage(identifier)
        assert_event(observer.next(), identifier, settled['event_seq'], 'settlement_changed')
        observer.closed()
        for key in ('checkpoint', 'revision', 'attempt', 'result', 'status'):
            assert settled[key] == terminal[key]
        assert service_database.rows('SELECT * FROM chat_messages WHERE session_id=%s ORDER BY message_id', (actors['a'].session_id,)) == history
        assert service_database.rows('SELECT credit_cost FROM chat_records WHERE record_id=%s', (terminal['record_id'],)) == [{'credit_cost': Decimal('.10')}]


def test_actual_cursor_header_validation_and_small_floor_reset_with_no_snapshot_body(
        storage, event_apis, actors, service_database):
    _, _, submit = storage
    row, _ = submit(actors['a'])
    identifier = row['runner_id']
    path = '/v1/runners/' + identifier + '/events'
    for params, value in (({'after_seq': 1}, '2'), ({}, '-1'), ({}, '1.0'), ({}, '9223372036854775808')):
        require_status(event_apis.call('GET', path, actor=actors['a'], params=params,
            headers={**event_apis.headers(actors['a']), 'Last-Event-ID': value}), 422)
    from src.services.agent_runner.event_repository import EventRepository
    with service_database.connect() as connection, connection.cursor() as cursor:
        cursor.execute('SELECT * FROM agent_runners WHERE runner_id=%s FOR UPDATE', (identifier,))
        retained, deleted = EventRepository.prune_in_tx(cursor, row, row['event_seq'], limit=10)
        assert deleted == 1 and retained['event_floor_seq'] == retained['event_seq'] == 1
    with StreamObserver(event_url(event_apis, identifier), headers=event_apis.headers(actors['a'])) as stream:
        assert_hint(stream.next(), identifier, 1, 1)
    current = RunnerRepository(service_database.connect).get(identifier)
    assert current['status'] == 'queued' and current['attempt'] == 0
    # Real original terminal first; only the absent pre-ledger history is an
    # explicit legacy storage-state fixture. No synthesized replay event.
    execution = ExecutionRepository(service_database.connect)
    owned = execution.acquire('sse-legacy-terminal-owner', 30)
    execution.stage_finalization(attempt(owned), owned['revision'], {},
        {'status': 'completed', 'output': 'Historical SSE fixture', 'images': [], 'messages': []}, {})
    terminal = RunnerFinalizer(service_database.connect).finalize(attempt(owned))
    with service_database.connect() as connection, connection.cursor() as cursor:
        cursor.execute('SELECT runner_id FROM agent_runners WHERE runner_id=%s FOR UPDATE', (identifier,))
        cursor.execute('DELETE FROM agent_runner_events WHERE runner_id=%s', (identifier,))
        cursor.execute('UPDATE agent_runners SET event_seq=0,event_floor_seq=0 WHERE runner_id=%s', (identifier,))
    with StreamObserver(event_url(event_apis, identifier), headers=event_apis.headers(actors['a'])) as stream:
        hint = stream.next()
        assert_hint(hint, identifier, 0, 0)
        assert hint.payload['state']['status'] == 'completed'
        stream.closed()
    legacy = RunnerRepository(service_database.connect).get(identifier)
    assert legacy['event_seq'] == 0 and legacy['result'] == terminal['result']
