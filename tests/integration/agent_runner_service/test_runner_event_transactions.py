"""Prepared real PostgreSQL event-tail failure/CAS contracts, no provider IO."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import threading
import uuid

from psycopg2 import sql
import pytest

from src.core.agent_engine.contracts import CheckpointFailure
from src.services.agent_runner.ownership import LeaseLost

from .test_runner_event_ledger import page
from .test_storage import attempt, storage

pytestmark = pytest.mark.integration


@contextmanager
def reject_event_insert(database):
    # Test-owned trigger on the actual ledger, not a fake EventRepository result.
    name = 'event_fault_' + uuid.uuid4().hex
    with database.connect() as connection, connection.cursor() as cursor:
        cursor.execute(sql.SQL('''CREATE FUNCTION {}() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'FIXTURE_EVENT_INSERT_REJECTED'; END $$''').format(sql.Identifier(name)))
        cursor.execute(sql.SQL('''CREATE TRIGGER {} BEFORE INSERT ON agent_runner_events
            FOR EACH ROW EXECUTE FUNCTION {}()''').format(sql.Identifier(name), sql.Identifier(name)))
    try:
        yield
    finally:
        with database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql.SQL('DROP TRIGGER IF EXISTS {} ON agent_runner_events').format(sql.Identifier(name)))
            cursor.execute(sql.SQL('DROP FUNCTION IF EXISTS {}()').format(sql.Identifier(name)))


def test_real_event_insert_fault_rolls_back_original_checkpoint_cancel_and_claim_together(
        storage, actors, service_database):
    repository, execution, submit = storage
    row, principal = submit(actors['a'])
    owned = execution.acquire('event-rollback-owner', 30)
    assert owned['runner_id'] == row['runner_id']
    before = repository.get(row['runner_id'])
    events = service_database.rows('SELECT * FROM agent_runner_events WHERE runner_id=%s ORDER BY seq', (row['runner_id'],))
    claim = service_database.rows('SELECT * FROM agent_runner_session_claims WHERE owner_runner_id=%s', (row['runner_id'],))
    with reject_event_insert(service_database):
        for original_transaction in (
            lambda: execution.save_checkpoint(attempt(owned), owned['revision'], {'private': 'unchanged if rollback'},
                {**owned['public_snapshot'], 'progress': 'Visible failed commit'}),
            lambda: repository.cancel(principal, row['runner_id']),
        ):
            with pytest.raises(CheckpointFailure, match='^RUNNER_PUBLIC_EVENT_COMMIT_FAILED$'):
                original_transaction()
            assert repository.get(row['runner_id']) == before
            assert service_database.rows('SELECT * FROM agent_runner_events WHERE runner_id=%s ORDER BY seq',
                (row['runner_id'],)) == events
            assert service_database.rows('SELECT * FROM agent_runner_session_claims WHERE owner_runner_id=%s',
                (row['runner_id'],)) == claim
            assert service_database.rows('SELECT 1 FROM chat_records WHERE session_id=%s', (actors['a'].session_id,)) == []
    committed = execution.save_checkpoint(attempt(owned), owned['revision'], {'private': 'committed'},
        {**owned['public_snapshot'], 'progress': 'Visible committed once'})
    assert committed['revision'] == before['revision'] + 1
    assert committed['event_seq'] == before['event_seq'] + 1


def test_real_parallel_original_checkpoint_cas_only_winner_publishes_one_contiguous_event(
        storage, actors, service_database):
    repository, execution, submit = storage
    initial, _ = submit(actors['a'])
    owned = execution.acquire('event-cas-owner', 30)
    assert owned['runner_id'] == initial['runner_id']
    barrier = threading.Barrier(2)

    def save(number):
        barrier.wait(timeout=3)
        try:
            return execution.save_checkpoint(attempt(owned), owned['revision'], {'fixture_owner': number},
                {**owned['public_snapshot'], 'progress': 'Visible winner ' + str(number)})
        except LeaseLost as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, range(2)))
    winners = [result for result in results if isinstance(result, dict)]
    losers = [result for result in results if isinstance(result, LeaseLost)]
    assert len(winners) == len(losers) == 1
    assert str(losers[0]) == 'CHECKPOINT_REVISION_CHANGED'
    current = repository.get(initial['runner_id'])
    assert current['revision'] == owned['revision'] + 1
    assert current['checkpoint'] == winners[0]['checkpoint']
    assert current['event_seq'] == owned['event_seq'] + 1
    events = page(service_database, initial['runner_id'])
    assert [event['seq'] for event in events['events']] == list(range(1, current['event_seq'] + 1))
    assert events['events'][-1]['view_revision'] == current['view_revision']
