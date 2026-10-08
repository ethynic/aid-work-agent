"""Real PG outer notice tails. Device ACK/receipt data are explicit IO fixtures.

The Local case uses original bind/claim/result and readiness sweep, not a fake
completion proof or a Runtime/desktop conversation. Clock is always real DB time.
"""
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from types import SimpleNamespace
from pathlib import Path
import re
import uuid

import pytest

from src.core.agent_engine.contracts import CheckpointFailure
from src.local_tools import repository as devices
from src.services.agent_runner.contracts import Principal
from src.services.agent_runner.event_contracts import only_output_changed
from src.services.agent_runner.event_repository import EventRepository
from src.services.agent_runner.local_invocations import LocalInvocationRepository, LocalPhase
from src.services.agent_runner.local_recovery import release_ready_cancellations
from src.services.agent_runner.worker import RunnerWorker
from src.services.agent_runner.ownership import lock_runner
from src.services.agent_runner.public_view import decoded
from src.services.agent_runner.repository import RunnerRepository

from .test_local_cancel_authority import claimed_local
from .test_runner_event_ledger import page
from .test_runner_event_pages import seeded
from .test_runner_event_transactions import reject_event_insert
from .test_storage import attempt, storage
from .test_usage_storage import ledger, prices, started

pytestmark = pytest.mark.integration


def test_actual_explicit_maintenance_subprocess_deletes_one_bound_and_exits_without_execution(
        storage, actors, service_database, service_processes):
    repository, _, _, row = seeded(storage, actors['a'])
    child = service_processes.start(['-m', 'src.services.agent_runner.event_maintenance',
        '--keep-last', '0', '--roots', '1', '--delete-limit', '2'])
    assert child.wait(timeout=15) == 0
    # Read only the finite public count line, never print private process logs.
    content = (service_processes.root / f'child-{len(service_processes.children) - 1}.log').read_text()
    match = re.findall(r'^events_deleted=(\d+) next_cursor=(\d+)$', content, re.MULTILINE)
    assert match == [('2', str(row['queue_order']))]
    current = repository.get(row['runner_id'])
    assert current['event_seq'] == row['event_seq'] and current['event_floor_seq'] == 2
    for key in ('status', 'attempt', 'revision', 'checkpoint', 'worker_id', 'lease_until'):
        assert current[key] == row[key]
    assert len(page(service_database, row['runner_id'], 2)['events']) == 6


def test_original_output_only_burst_uses_real_250ms_clock_but_cancel_is_immediate(
        storage, actors, service_database):
    repository, execution, submit = storage
    initial, principal = submit(actors['a'])
    owned = execution.acquire('event-output-owner', 30)
    assert owned['runner_id'] == initial['runner_id']
    # Finite cursor-level event contract: state writes below are explicit PG
    # fixtures under the original root fence, not an Execution burst throughput
    # claim. One bounded output change is coalesced; the next follows the
    # interval. Original EventRepository uses the real DB clock/default250ms.
    with service_database.connect() as connection, connection.cursor() as cursor:
        previous = lock_runner(cursor, owned['runner_id'], attempt(owned))
        cursor.execute("""UPDATE agent_runners SET
            public_snapshot=jsonb_set(public_snapshot,'{progress}',to_jsonb(%s::text)),
            view_revision=view_revision+1 WHERE runner_id=%s RETURNING *""",
            ('output fixture prepared', owned['runner_id']))
        row = EventRepository.notify_in_tx(cursor, previous, after=decoded(cursor.fetchone()))
        head = row['event_seq']
        cursor.execute('SELECT created_at FROM agent_runner_events WHERE runner_id=%s AND seq=%s',
            (owned['runner_id'], head))
        last_created = cursor.fetchone()['created_at']
        for value in ('first fixture output',):
            previous = row
            cursor.execute("""UPDATE agent_runners SET
                public_snapshot=jsonb_set(public_snapshot,'{output}',to_jsonb(%s::text)),
                view_revision=view_revision+1 WHERE runner_id=%s RETURNING *""",
                (value, owned['runner_id']))
            row = EventRepository.notify_in_tx(cursor, previous, after=decoded(cursor.fetchone()))
            # Original append returns its real decision clock on coalescence.
            # If it appended, use that original event's exact DB timestamp;
            # neither observation is a later round-trip timing approximation.
            decision_now = row.get('database_now')
            if decision_now is None:
                cursor.execute('SELECT created_at FROM agent_runner_events WHERE runner_id=%s AND seq=%s',
                    (owned['runner_id'], row['event_seq']))
                decision_now = cursor.fetchone()['created_at']
            elapsed = (decision_now - last_created).total_seconds()
            assert only_output_changed(previous, row), 'Original public meaning must change only output'
            assert elapsed < .250, 'Fixture burst must actually finish within original DB window'
            assert row['public_snapshot']['output'] == value and row['event_seq'] == head
        cursor.execute('SELECT pg_sleep(GREATEST(0, EXTRACT(EPOCH FROM (%s::timestamptz+INTERVAL \'251 milliseconds\'-clock_timestamp()))))',
            (last_created,))
        previous = row
        cursor.execute("""UPDATE agent_runners SET
            public_snapshot=jsonb_set(public_snapshot,'{output}',to_jsonb(%s::text)),
            view_revision=view_revision+1 WHERE runner_id=%s RETURNING *""",
            ('final fixture output', owned['runner_id']))
        row = EventRepository.notify_in_tx(cursor, previous, after=decoded(cursor.fetchone()))
        assert row['event_seq'] == head + 1
        cursor.execute('SELECT created_at FROM agent_runner_events WHERE runner_id=%s AND seq=%s',
            (owned['runner_id'], row['event_seq']))
        output_created = cursor.fetchone()['created_at']
        connection.commit()
        @contextmanager
        def connected():
            yield connection
        # Important cancellation is still the original whole DAL transaction,
        # committed immediately even inside the current head's 250ms interval.
        cancelled = RunnerRepository(connected).cancel(principal, owned['runner_id'])
        cursor.execute('SELECT created_at FROM agent_runner_events WHERE runner_id=%s AND seq=%s',
            (owned['runner_id'], cancelled['event_seq']))
        assert (cursor.fetchone()['created_at'] - output_created).total_seconds() < .250
    assert cancelled['event_seq'] == head + 2 and cancelled['cancel_requested']
    events = page(service_database, owned['runner_id'])['events']
    assert events[-1]['control_revision'] == cancelled['control_revision']
    assert all('output' not in event and 'first fixture' not in str(event) for event in events)


def test_original_usage_pending_notice_fault_rolls_back_receipt_and_replay_does_not_republish(
        ledger, service_database):
    repository, _, usage, owned, _, _ = ledger
    # Explicit storage-state fixture for a previously settled root beginning
    # further work. This does not claim the default accepted root is settled or
    # prove a Runtime continuation producer. Publish that declared baseline via
    # the real outer event tail, then exercise original Usage.observe unchanged.
    with service_database.connect() as connection, connection.cursor() as cursor:
        cursor.execute('SELECT * FROM agent_runners WHERE runner_id=%s FOR UPDATE', (owned['runner_id'],))
        previous = decoded(cursor.fetchone())
        cursor.execute("UPDATE agent_runners SET settlement_status='settled',view_revision=view_revision+1 WHERE runner_id=%s",
            (owned['runner_id'],))
        EventRepository.notify_in_tx(cursor, previous)
    receipt = started(ledger, call='event-pending-original')
    usage.uncertain(attempt(owned), receipt['receipt_id'])
    before = repository.get(owned['runner_id'])
    assert before['settlement_status'] == 'settled'
    with reject_event_insert(service_database):
        with pytest.raises(CheckpointFailure, match='^RUNNER_PUBLIC_EVENT_COMMIT_FAILED$'):
            usage.observe(attempt(owned), receipt['receipt_id'], {'prompt_tokens': 10, 'completion_tokens': 0})
        assert repository.get(owned['runner_id']) == before
        assert service_database.rows('SELECT phase,usage FROM agent_runner_usage_receipts WHERE receipt_id=%s',
            (receipt['receipt_id'],)) == [{'phase': 'unknown', 'usage': None}]
    usage.observe(attempt(owned), receipt['receipt_id'], {'prompt_tokens': 10, 'completion_tokens': 0})
    pending = repository.get(owned['runner_id'])
    assert pending['settlement_status'] == 'pending' and pending['event_seq'] == before['event_seq'] + 1
    assert pending['revision'] == before['revision'] and pending['checkpoint'] == before['checkpoint']
    assert page(service_database, owned['runner_id'])['events'][-1]['kind'] == 'settlement_changed'
    usage.observe(attempt(owned), receipt['receipt_id'], {'prompt_tokens': 10, 'completion_tokens': 0})
    assert repository.get(owned['runner_id']) == pending


@pytest.mark.asyncio
async def test_real_original_local_ack_ready_sweep_publishes_once_and_event_fault_keeps_blocked_claim(
        claimed_local, service_database):
    repository, execution, actor, device, prepare = claimed_local
    row, state, control, call = prepare()
    phase = LocalPhase(state.execution_id, call.id, 'main', 0)
    row = LocalInvocationRepository(service_database.connect).bind(attempt(row), row['revision'], phase,
        device_id=str(device['id']), tool_name=call.name, arguments={},
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=60))
    original = row['checkpoint']['execution']['resources']['local_invocations'][phase.key(row['runner_id'])]['invocation_id']
    claim = 'event-ack-' + uuid.uuid4().hex
    assert devices.claim_next(str(device['id']), actor.tenant_id, claim, 60, expected_invocation_id=original)
    assert devices.mark_started(original, actor.tenant_id, claim)['state'] == 'running'
    repository.cancel(Principal(state.identity, 'user', actor.user_id, row['service_id']), row['runner_id'])
    worker = RunnerWorker(SimpleNamespace(lease_seconds=30), worker_id='event-ready-observer',
        execution_repository=execution, repository=repository)
    facts = await worker._cancel_saved_invocations(control.attempt)
    assert facts[0]['state'] == 'cancel_requested'
    blocked = execution.block_cancel(control.attempt, 'LOCAL_CANCEL_ACK_PENDING')
    release_ready_cancellations(service_database.connect)
    assert repository.get(row['runner_id']) == blocked
    assert devices.write_result(original, actor.tenant_id, claim, False, code='CANCELLED', effect='none')['state'] == 'failed'
    with reject_event_insert(service_database):
        # Sweep isolates this typed event failure, but cannot partially commit
        # ready CP/revision, release the original claim or publish a notice.
        release_ready_cancellations(service_database.connect)
        assert repository.get(row['runner_id']) == blocked
    release_ready_cancellations(service_database.connect)
    ready = repository.get(row['runner_id'])
    assert ready['status'] == 'waiting' and ready['checkpoint']['cancel_completion_blocked']['ready'] is True
    assert ready['event_seq'] == blocked['event_seq'] + 1 and ready['revision'] == blocked['revision'] + 1
    assert ready['worker_id'] is None and ready['lease_until'] is None
    assert service_database.rows('SELECT owner_runner_id FROM agent_runner_session_claims WHERE owner_runner_id=%s',
        (row['runner_id'],)) == [{'owner_runner_id': row['runner_id']}]
    release_ready_cancellations(service_database.connect)
    assert repository.get(row['runner_id']) == ready
    assert service_database.rows('SELECT COUNT(*) AS count FROM local_tool_invocations WHERE tenant_id=%s',
        (actor.tenant_id,)) == [{'count': 1}]
    assert service_database.rows('SELECT 1 FROM chat_records WHERE session_id=%s', (actor.session_id,)) == []
