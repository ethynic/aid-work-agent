"""Unwired local ownership stores and real recruiting cursor DAL, isolated PG.

These tests establish transaction/fence contracts, not physical desktop recovery
or the unwired recognition/overlay charging policy.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import copy
import queue
import uuid

import psycopg2
from psycopg2 import sql
import pytest

from src.core.agent_engine.contracts import AgentMode, CheckpointFailure, ExecutionState, Identity, ToolCall, ToolFact
from src.services.agent_runner.local_invocations import LocalInvocationRepository, LocalPhase
from src.services.agent_runner.local_phases import LocalPhaseRepository
from src.services.agent_runner.ownership import LeaseLost
from src.services.recruiting_resume_service import create_resume_record_in_tx
from src.services.recruiting_resume_timeline_service import create_comm_log_in_tx

from .conftest import wait_for
from .test_storage import attempt, storage

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def recruiting_schema(service_database):
    # Normal domain-owned lazy schema initialization, on this disposable DB.
    from src.services.recruiting_job_service import init_recruiting_job_tables
    from src.services.recruiting_resume_service import init_recruiting_operator_tables
    from src.services.recruiting_resume_timeline_service import init_recruiting_timeline_tables
    with service_database.connect() as connection:
        init_recruiting_job_tables(connection)
        init_recruiting_operator_tables(connection)
        init_recruiting_timeline_tables(connection)
        connection.commit()


@pytest.fixture
def local_store(storage, actors, service_database):
    repository, execution, submit = storage
    devices = {}
    for label in ('a', 'a_other', 'b'):
        actor = actors[label]
        devices[label] = str(service_database.rows(
            'INSERT INTO local_tool_devices(tenant_id,user_id,token_hash) VALUES(%s,%s,%s) RETURNING id',
            (actor.tenant_id, actor.user_id, 'fictional-device-hash-' + uuid.uuid4().hex))[0]['id'])
    devices['a_second'] = str(service_database.rows(
        'INSERT INTO local_tool_devices(tenant_id,user_id,token_hash) VALUES(%s,%s,%s) RETURNING id',
        (actors['a'].tenant_id, actors['a'].user_id, 'fictional-device-hash-' + uuid.uuid4().hex))[0]['id'])

    def prepare(child=False, lease=60):
        row, _ = submit(actors['a'])
        owned = execution.acquire('local-storage-owner', lease)
        assert owned['runner_id'] == row['runner_id']
        identity = Identity(*(row[key] for key in ('tenant_id', 'user_id', 'session_id', 'source', 'session_kind')))
        root = ExecutionState(identity, row['runner_id'], AgentMode.MASTER, 'Fixture', [])
        target = root
        if child:
            target = ExecutionState(identity, 'child-' + uuid.uuid4().hex, AgentMode.SUBAGENT, 'Fixture child', [])
        call = ToolCall('local-call', 'boss_fixture_operation', {'item': 'fictional'})
        target.tools[call.id] = ToolFact(call, phase='dispatching')
        if child:
            root.children['delegate-call'] = {'execution_id': target.execution_id, 'checkpoint': target.checkpoint()}
        saved = execution.save_checkpoint(attempt(owned), owned['revision'], {'execution': root.checkpoint()})
        return saved, LocalPhase(target.execution_id, call.id, 'fetch', 0)

    try:
        yield repository, execution, prepare, devices
    finally:
        user_ids = [a.user_id for a in actors.values()]
        service_database.rows('DELETE FROM bs_recruiting_operator_resume_comm_logs WHERE user_id=ANY(%s)', (user_ids,))
        service_database.rows('DELETE FROM bs_recruiting_operator_resumes WHERE user_id=ANY(%s)', (user_ids,))
        service_database.rows('DELETE FROM local_tool_events WHERE invocation_id IN (SELECT id FROM local_tool_invocations WHERE user_id=ANY(%s))', (user_ids,))
        service_database.rows('DELETE FROM local_tool_invocations WHERE user_id=ANY(%s)', (user_ids,))
        service_database.rows('DELETE FROM local_tool_devices WHERE user_id=ANY(%s)', (user_ids,))


def node(row, phase):
    root = row['checkpoint']['execution']
    return root if root['execution_id'] == phase.execution_id else root['children']['delegate-call']['checkpoint']


def bind(repo, row, phase, device, **changes):
    options = dict(device_id=device, tool_name='boss_fixture_operation', arguments={'item': 'fictional'},
                   provider_key='fixture-provider', authorization_epoch=4, execution_lane='standard')
    options.update(changes)
    return repo.bind(attempt(row), row['revision'], phase, **options)


@pytest.mark.parametrize('child', [False, True])
def test_original_invocation_and_exact_owned_phase_commit_once(local_store, service_database, child):
    repository, _, prepare, devices = local_store
    before, phase = prepare(child)
    local = LocalInvocationRepository(service_database.connect)
    saved = bind(local, before, phase, devices['a'])
    reference = node(saved, phase)['resources']['local_invocations'][phase.key(saved['runner_id'])]
    repeated = bind(local, saved, phase, devices['a'])
    assert repeated['revision'] == saved['revision'] == before['revision'] + 1
    assert reference['runner_id'] == saved['runner_id'] and reference['execution_id'] == phase.execution_id
    assert reference['tool_call_id'] == phase.tool_call_id and reference['branch'] == 'fetch' and reference['ordinal'] == 0
    rows = service_database.rows('SELECT * FROM local_tool_invocations WHERE session_id=%s', (saved['session_id'],))
    assert len(rows) == 1 and str(rows[0]['id']) == reference['invocation_id']
    assert rows[0]['state'] == 'queued' and rows[0]['credit_cost'] is None
    assert rows[0]['business_ref'] == {key: reference[key] for key in ('runner_id', 'execution_id', 'tool_call_id', 'branch', 'ordinal')}
    assert rows[0]['arguments_json'] == {'item': 'fictional'}
    assert repository.get(saved['runner_id'])['checkpoint'] == repeated['checkpoint']


@pytest.mark.parametrize('change', ['arguments', 'device_id', 'provider_key', 'authorization_epoch'])
def test_same_phase_changed_immutable_intent_cannot_create_another_invocation(local_store, service_database, change):
    repository, _, prepare, devices = local_store
    before, phase = prepare()
    local = LocalInvocationRepository(service_database.connect)
    saved = bind(local, before, phase, devices['a'])
    values = {'arguments': {'item': 'changed'}, 'device_id': devices['a_second'], 'provider_key': 'different', 'authorization_epoch': 5}
    with pytest.raises(LeaseLost, match='LOCAL_PHASE_INTENT_CHANGED'):
        bind(local, saved, phase, devices['a'], **{change: values[change]})
    assert repository.get(saved['runner_id'])['checkpoint'] == saved['checkpoint']
    assert len(service_database.rows('SELECT 1 FROM local_tool_invocations WHERE session_id=%s', (saved['session_id'],))) == 1


def test_branch_and_ordinal_own_distinct_original_operations(local_store, service_database):
    _, _, prepare, devices = local_store
    row, original = prepare()
    local = LocalInvocationRepository(service_database.connect)
    for branch, ordinal in [('fetch', 0), ('fetch', 1), ('heal', 0)]:
        row = bind(local, row, LocalPhase(original.execution_id, original.tool_call_id, branch, ordinal), devices['a'])
    references = node(row, original)['resources']['local_invocations']
    assert len(references) == len({item['invocation_id'] for item in references.values()}) == 3
    assert len(service_database.rows('SELECT 1 FROM local_tool_invocations WHERE session_id=%s', (row['session_id'],))) == 3


@pytest.mark.parametrize('fault', ['root', 'child', 'identity', 'device_user', 'device_tenant', 'device_revoked'])
def test_untrusted_owner_or_device_cannot_bind_local_operation(local_store, service_database, fault):
    repository, execution, prepare, devices = local_store
    row, phase = prepare(child=fault == 'child')
    cp = copy.deepcopy(row['checkpoint'])
    device = devices['a']
    codes = {'root': 'LOCAL_ROOT_OWNER_MISMATCH', 'child': 'LOCAL_CHILD_OWNER_MISMATCH',
             'identity': 'LOCAL_EXECUTION_IDENTITY_MISMATCH', 'device_user': 'LOCAL_DEVICE_OWNER_MISMATCH',
             'device_tenant': 'LOCAL_DEVICE_OWNER_MISMATCH', 'device_revoked': 'LOCAL_DEVICE_OWNER_MISMATCH'}
    if fault == 'root': cp['execution']['execution_id'] = 'foreign-root'
    if fault == 'child': cp['execution']['children']['delegate-call']['execution_id'] = 'foreign-child'
    if fault == 'identity': cp['execution']['identity']['user_id'] = 'foreign-user'
    if fault in ('root', 'child', 'identity'):
        row = execution.save_checkpoint(attempt(row), row['revision'], cp)
    if fault == 'device_user': device = devices['a_other']
    if fault == 'device_tenant': device = devices['b']
    if fault == 'device_revoked': service_database.rows("UPDATE local_tool_devices SET status='revoked' WHERE id=%s", (device,))
    with pytest.raises(LeaseLost, match=codes[fault]):
        bind(LocalInvocationRepository(service_database.connect), row, phase, device)
    assert repository.get(row['runner_id'])['checkpoint'] == row['checkpoint']
    assert service_database.rows('SELECT 1 FROM local_tool_invocations WHERE session_id=%s', (row['session_id'],)) == []


@contextmanager
def reject_next_checkpoint(database, row):
    name = 'fixture_local_atomic_' + uuid.uuid4().hex
    with database.connect() as conn, conn.cursor() as cursor:
        cursor.execute(sql.SQL('ALTER TABLE agent_runners ADD CONSTRAINT {} CHECK(runner_id <> {} OR revision <= {})').format(
            sql.Identifier(name), sql.Literal(row['runner_id']), sql.Literal(row['revision'])))
    try:
        yield
    finally:
        with database.connect() as conn, conn.cursor() as cursor:
            cursor.execute(sql.SQL('ALTER TABLE agent_runners DROP CONSTRAINT {}').format(sql.Identifier(name)))


def test_late_checkpoint_sql_failure_rolls_back_new_queued_invocation(local_store, service_database):
    repository, _, prepare, devices = local_store
    row, phase = prepare()
    local = LocalInvocationRepository(service_database.connect)
    with reject_next_checkpoint(service_database, row):
        with pytest.raises(psycopg2.errors.CheckViolation): bind(local, row, phase, devices['a'])
    assert repository.get(row['runner_id'])['checkpoint'] == row['checkpoint']
    assert service_database.rows('SELECT 1 FROM local_tool_invocations WHERE session_id=%s', (row['session_id'],)) == []
    saved = bind(local, row, phase, devices['a'])
    bind(local, saved, phase, devices['a'])
    assert len(service_database.rows('SELECT 1 FROM local_tool_invocations WHERE session_id=%s', (row['session_id'],))) == 1


def test_device_lock_wait_across_lease_expiry_rolls_back_invocation_and_checkpoint(local_store, service_database):
    repository, _, prepare, devices = local_store
    row, phase = prepare(lease=3)
    backend = queue.Queue()
    @contextmanager
    def observed_connection():
        with service_database.connect() as conn, conn.cursor() as cursor:
            cursor.execute('SELECT pg_backend_pid() AS pid')
            backend.put(cursor.fetchone()['pid'])
            yield conn
    local = LocalInvocationRepository(observed_connection)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with service_database.connect() as lock, lock.cursor() as cursor:
            cursor.execute('SELECT id FROM local_tool_devices WHERE id=%s FOR UPDATE', (devices['a'],))
            future = pool.submit(bind, local, row, phase, devices['a'])
            try:
                pid = backend.get(timeout=3)
                wait_for(lambda: service_database.rows('SELECT wait_event_type FROM pg_stat_activity WHERE pid=%s', (pid,))[0]['wait_event_type'] == 'Lock', timeout=2)
                assert service_database.rows('SELECT clock_timestamp()<lease_until AS valid FROM agent_runners WHERE runner_id=%s', (row['runner_id'],))[0]['valid']
                wait_for(lambda: service_database.rows('SELECT clock_timestamp()>=lease_until AS expired FROM agent_runners WHERE runner_id=%s', (row['runner_id'],))[0]['expired'], timeout=4)
            finally:
                lock.commit()
        with pytest.raises(LeaseLost, match='RUNNER_ATTEMPT_EXPIRED'): future.result(timeout=3)
    assert repository.get(row['runner_id'])['checkpoint'] == row['checkpoint']
    assert service_database.rows('SELECT 1 FROM local_tool_invocations WHERE session_id=%s', (row['session_id'],)) == []


@pytest.mark.parametrize('domain', ['resume', 'communication'])
def test_real_recruiting_cursor_effect_and_checkpoint_commit_once_or_both_rollback(local_store, service_database, actors, domain):
    repository, _, prepare, _ = local_store
    row, phase = prepare(child=domain == 'communication')
    store = LocalPhaseRepository(service_database.connect)
    actor = actors['a']
    seed_id = None
    if domain == 'communication':
        with service_database.connect() as conn:
            seed_id = create_resume_record_in_tx(conn.cursor(), actor.tenant_id, actor.user_id,
                                               candidate_name='Fixture candidate')['id']
    calls = []
    def writer(cursor, owned, intent):
        calls.append(True)
        if domain == 'resume':
            result = create_resume_record_in_tx(cursor, owned['tenant_id'], owned['user_id'], candidate_name=intent['name'],
                                               images=[{'file_id': 'fixture-image', 'name': 'candidate.png'}], source='boss')
        else:
            result = create_comm_log_in_tx(cursor, owned['tenant_id'], seed_id, 'out', 'boss', intent['content'], owned['user_id'])
        return {'record_id': result['id']}
    intent = {'name': 'Fixture candidate', 'content': 'Fixture communication'}
    table = 'bs_recruiting_operator_resumes' if domain == 'resume' else 'bs_recruiting_operator_resume_comm_logs'
    def count():
        return len(service_database.rows(sql.SQL('SELECT id FROM {} WHERE user_id=%s').format(sql.Identifier(table)), (actor.user_id,)))
    before_count = count()
    with reject_next_checkpoint(service_database, row):
        with pytest.raises(CheckpointFailure, match='LOCAL_PHASE_STORAGE_FAILED'):
            store.commit(attempt(row), row['revision'], phase, intent, writer)
    assert count() == before_count and repository.get(row['runner_id'])['checkpoint'] == row['checkpoint']
    saved, fact = store.commit(attempt(row), row['revision'], phase, intent, writer)
    repeated, again = store.commit(attempt(saved), saved['revision'], phase, intent, writer)
    assert count() == before_count + 1 and len(calls) == 2
    assert fact == again and repeated['revision'] == saved['revision'] == row['revision'] + 1
    assert fact['result']['record_id'] in [item['id'] for item in service_database.rows(sql.SQL('SELECT id FROM {} WHERE user_id=%s').format(sql.Identifier(table)), (actor.user_id,))]
    with pytest.raises(LeaseLost, match='LOCAL_PHASE_INTENT_CHANGED'):
        store.commit(attempt(saved), saved['revision'], phase, dict(intent, content='different'), writer)
    assert len(calls) == 2 and count() == before_count + 1
