"""Prepared bounded M5 real-PG cursor contracts; no SSE or fabricated owner.

Explicit event-row corruption below is a storage fault-injection fixture. It
cannot claim a naturally produced bad event or bypass a read manager's auth.
"""
import json

import pytest

from src.services.agent_runner.event_contracts import EventContractError, encoded_bytes
from src.services.agent_runner.event_maintenance import prune_batch
from src.services.agent_runner.ownership import Attempt

from .test_runner_event_ledger import page
from .test_storage import storage

pytestmark = pytest.mark.integration


def seeded(storage, actor):
    repository, execution, submit = storage
    initial, _ = submit(actor)
    row = execution.acquire('page-owner', 30)
    assert row['runner_id'] == initial['runner_id']
    authority = Attempt(row['runner_id'], row['worker_id'], row['attempt'])
    # Real generic checkpoint transactions with visible important progress;
    # no model calls, manufactured completed ToolFacts, or altered DB clock.
    for number in range(6):
        row = execution.save_checkpoint(authority, row['revision'], row['checkpoint'],
            {**row['public_snapshot'], 'progress': 'Fixture visible progress ' + str(number)})
    assert row['event_seq'] == 8
    return repository, execution, authority, row


def test_real_bounded_retention_all_deleted_keeps_head_and_reset_then_new_seq(
        storage, actors, service_database):
    repository, execution, authority, row = seeded(storage, actors['a'])
    identifier = row['runner_id']
    window = page(service_database, identifier, 0, limit=100, max_bytes=1024)
    assert not window['reset'] and window['has_more']
    assert sum(encoded_bytes(event) for event in window['events']) <= 1024
    assert window['last_seq'] == window['events'][-1]['seq'] < row['event_seq']
    following = page(service_database, identifier, window['last_seq'], limit=2)
    assert not following['reset'] and following['events'][0]['seq'] == window['last_seq'] + 1
    deleted = 0
    for _ in range(4):
        outcome = prune_batch(connection_factory=service_database.connect,
            keep_last=0, roots=1, delete_limit=2)
        assert outcome['deleted'] == 2
        deleted += outcome['deleted']
    current = repository.get(identifier)
    assert deleted == current['event_floor_seq'] == current['event_seq'] == 8
    assert current['revision'] == row['revision'] and current['checkpoint'] == row['checkpoint']
    assert service_database.rows('SELECT 1 FROM agent_runner_events WHERE runner_id=%s', (identifier,)) == []
    expired = page(service_database, identifier, 0)
    future = page(service_database, identifier, 9)
    assert expired == future and expired['reset'] and expired['head'] == expired['last_seq'] == 8
    assert expired['floor'] == 8 and expired['events'] == [] and not expired['has_more']
    assert expired['runner']['snapshot']['progress'] == row['public_snapshot']['progress']
    exact = page(service_database, identifier, 8)
    assert not exact['reset'] and exact['events'] == [] and exact['last_seq'] == 8
    next_row = execution.save_checkpoint(authority, current['revision'], current['checkpoint'],
        {**current['public_snapshot'], 'progress': 'Fixture after all retention'})
    assert next_row['event_seq'] == 9 and next_row['event_floor_seq'] == 8
    assert [(event['seq'], event['kind']) for event in page(service_database, identifier, 8)['events']] == [(9, 'revision_changed')]


def test_real_short_full_page_tail_hole_and_middle_hole_reset_in_same_snapshot(
        storage, actors, service_database):
    repository, _, _, row = seeded(storage, actors['a'])
    identifier = row['runner_id']
    # A full SQL page is ordinary pagination; the complete short result must
    # reset immediately, and the following empty tail page must also reset.
    service_database.rows('DELETE FROM agent_runner_events WHERE runner_id=%s AND seq=8', (identifier,))
    full = page(service_database, identifier, 5, limit=2)
    assert not full['reset'] and full['has_more'] and full['last_seq'] == 7
    assert [event['seq'] for event in full['events']] == [6, 7]
    assert page(service_database, identifier, 7)['reset']
    tail = page(service_database, identifier, 5, limit=3)
    assert tail['reset'] is True and tail['events'] == []
    assert tail['head'] == tail['last_seq'] == 8 and not tail['has_more']
    service_database.rows('DELETE FROM agent_runner_events WHERE runner_id=%s AND seq=3', (identifier,))
    middle = page(service_database, identifier, 1, limit=100)
    assert middle['reset'] and middle['head'] == 8 and middle['events'] == []
    assert repository.get(identifier)['event_seq'] == 8


def test_real_stored_payload_rejects_float_seq_version_numeric_invalidate_and_bool_attempt(
        storage, actors, service_database):
    _, _, _, row = seeded(storage, actors['a'])
    identifier = row['runner_id']
    original = service_database.rows('SELECT payload FROM agent_runner_events WHERE runner_id=%s AND seq=2', (identifier,))[0]['payload']
    assert type(original['seq']) is int and type(original['version']) is int
    assert original['invalidate'] is True and type(original['attempt']) is int
    for key, value in [('seq', 2.0), ('version', 1.0), ('invalidate', 1), ('attempt', True)]:
        malformed = {**original, key: value}
        service_database.rows('UPDATE agent_runner_events SET payload=%s::jsonb WHERE runner_id=%s AND seq=2',
            (json.dumps(malformed), identifier))
        with pytest.raises(EventContractError):
            page(service_database, identifier, 1)
        service_database.rows('UPDATE agent_runner_events SET payload=%s::jsonb WHERE runner_id=%s AND seq=2',
            (json.dumps(original), identifier))
    assert page(service_database, identifier, 1)['events'][0] == original
