"""Prepared M5 real-PG ledger smoke; do not run before the wired source freezes.

This direct repository/RR-page contract does not prove the fresh-authorized read
manager, an SSE connection, provider dispatch, or a real Browser lifecycle.
"""
from concurrent.futures import ThreadPoolExecutor
import threading
import uuid

import pytest

from src.core.agent_engine.contracts import Identity
from src.services.agent_runner.contracts import Principal, RunnerSubmit
from src.services.agent_runner.event_repository import EventRepository
from src.services.agent_runner.execution_repository import ExecutionRepository
from src.services.agent_runner.public_view import decoded
from src.services.agent_runner.repository import RunnerRepository

from .test_storage import attempt

pytestmark = pytest.mark.integration


def page(database, identifier, after=0, **kwargs):
    # The real manager/fresh authorization is a later dedicated contract. This
    # smoke supplies the original accepted row, and uses the required real MVCC.
    with database.connect() as connection:
        connection.set_session(isolation_level='REPEATABLE READ', readonly=True)
        with connection.cursor() as cursor:
            cursor.execute('SELECT * FROM agent_runners WHERE runner_id=%s', (identifier,))
            row = decoded(cursor.fetchone())
            return EventRepository.read_page_in_tx(cursor, row, after, **kwargs)


def test_real_accept_winner_public_state_tail_and_rr_event_page_are_one_original_ledger(
        actors, service_database):
    actor = actors['a']
    principal = Principal(Identity(actor.tenant_id, actor.user_id, actor.session_id),
        'user', actor.user_id, 'event-smoke')
    request = RunnerSubmit(client_request_id='events-' + uuid.uuid4().hex,
        session={'kind': 'web', 'session_id': actor.session_id}, text='Private fixture event input')
    repository = RunnerRepository(service_database.connect)
    execution = ExecutionRepository(service_database.connect)
    barrier = threading.Barrier(2)

    def accept():
        barrier.wait(timeout=3)
        return repository.submit(principal, request, 'event-fixture-profile')

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: accept(), range(2)))
    assert sum(created for _, created in results) == 1
    identifiers = {row['runner_id'] for row, _ in results}
    assert len(identifiers) == 1
    identifier = identifiers.pop()
    created = repository.get(identifier)
    assert created['event_seq'] == 1 and created['event_floor_seq'] == 0
    first = page(service_database, identifier)
    assert first['head'] == first['last_seq'] == 1 and first['floor'] == 0
    assert first['reset'] is False and first['has_more'] is False
    assert [(item['seq'], item['kind']) for item in first['events']] == [(1, 'created')]

    owned = execution.acquire('event-owner-' + uuid.uuid4().hex, 30)
    assert owned['runner_id'] == identifier and owned['event_seq'] == 2
    # Private durable model/receipt/iteration facts are not public notifications.
    saved = execution.save_checkpoint(attempt(owned), owned['revision'], {
        'model_calls': [{'response': 'Private model fixture', 'usage': {'total_tokens': 9}}],
        'iteration': 4})
    assert saved['event_seq'] == 2
    cancelled = repository.cancel(principal, identifier)
    assert cancelled['cancel_requested'] is True and cancelled['event_seq'] == 3
    assert cancelled['revision'] == saved['revision']
    events = page(service_database, identifier)
    assert events['head'] == events['last_seq'] == 3 and not events['has_more'] and not events['reset']
    assert [(item['seq'], item['kind']) for item in events['events']] == [
        (1, 'created'), (2, 'revision_changed'), (3, 'revision_changed')]
    expected = {'runner_id', 'seq', 'kind', 'version', 'attempt', 'invalidate',
        'view_revision', 'control_revision', 'status', 'settlement_status'}
    assert all(set(item) == expected and item['invalidate'] is True for item in events['events'])
    assert events['events'][-1]['view_revision'] == cancelled['view_revision']
    assert events['events'][-1]['control_revision'] == cancelled['control_revision']
    assert all('Private' not in str(item) for item in events['events'])
    assert service_database.rows('SELECT owner_runner_id FROM agent_runner_session_claims WHERE owner_runner_id=%s',
        (identifier,)) == [{'owner_runner_id': identifier}]
    assert service_database.rows('SELECT 1 FROM chat_records WHERE session_id=%s', (actor.session_id,)) == []
