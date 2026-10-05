"""Native owned-call contracts against real PG; no browser runtime is faked.

The root/child checkpoint shape is a declared storage-contract fixture. Calling
activate_run exercises its database transition, not actual executor startup.
Real Browser/Redis/HTTP/WS runtime acceptance belongs to the later slice.
"""
import copy
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta
import queue
import uuid

from psycopg2 import sql
from psycopg2.errors import RaiseException
import pytest

from .conftest import wait_for
from .test_storage import storage, attempt

pytestmark = pytest.mark.integration


@pytest.fixture
def browser_owner(storage, actors, service_database):
    from src.core.agent_engine.contracts import AgentMode, ExecutionState, ToolCall, ToolFact
    from src.services.agent_runner.browser_binding import BrowserOwnerRepository
    repository, execution, submit = storage
    submitted, principal = submit(actors['a'])
    row = execution.acquire('fixture-browser-storage-owner', 120)
    assert row['runner_id'] == submitted['runner_id']
    root = ExecutionState(principal.identity, row['runner_id'], AgentMode.MASTER, 'Native Browser owner fixture', [])
    call_id = 'native-original-browser-call'
    arguments = {'steps': [{'action': 'snapshot'}]}
    root.tools[call_id] = ToolFact(ToolCall(call_id, 'browser_automation', arguments), phase='dispatching')
    parent_call = 'native-browser-delegate'
    root.tools[parent_call] = ToolFact(ToolCall(parent_call, 'delegate', {'subagent_name': 'fixture-child'}), phase='dispatching')
    child = ExecutionState(principal.identity, 'browser-child-' + uuid.uuid4().hex, AgentMode.SUBAGENT,
                           'Native Browser child fixture', [])
    child.tools[call_id] = ToolFact(ToolCall(call_id, 'browser_automation', arguments), phase='dispatching')
    root.children[parent_call] = {'execution_id': child.execution_id, 'checkpoint': child.checkpoint()}
    saved = execution.save_checkpoint(attempt(row), row['revision'], {'execution': root.checkpoint()})
    owner = BrowserOwnerRepository(service_database.connect)
    record = {'tenant_id': row['tenant_id'], 'user_id': row['user_id'], 'session_id': row['session_id'],
              'run_id': 'br_fixture_' + uuid.uuid4().hex, 'execution_target': 'server', 'state': 'CREATED'}
    values = dict(repository=repository, execution=execution, owner=owner, row=saved, attempt=attempt(row),
                  root_id=root.execution_id, child_id=child.execution_id, parent_call=parent_call,
                  call_id=call_id, record=record, boot='boot-' + uuid.uuid4().hex,
                  epoch='epoch-' + uuid.uuid4().hex, endpoint='http://127.0.0.1:39999/internal/runner-browser')
    try:
        yield values
    finally:
        # Exact fictional runner identity only; no broad tenant cleanup.
        service_database.rows('DELETE FROM bs_browser_assistance_requests WHERE runner_id=%s', (row['runner_id'],))
        service_database.rows('DELETE FROM bs_browser_runs WHERE runner_id=%s', (row['runner_id'],))


def bind(values, *, owner=None, row=None, execution_id=None, record=None):
    return (owner or values['owner']).bind_run(values['attempt'], (row or values['row'])['revision'],
        execution_id=execution_id or values['root_id'], call_id=values['call_id'],
        record=record or values['record'], worker_boot=values['boot'], browser_epoch=values['epoch'],
        endpoint=values['endpoint'], lease_seconds=60)


def activate(values, execution_id=None):
    return values['owner'].activate_run(values['attempt'], execution_id=execution_id or values['root_id'],
        call_id=values['call_id'], run_id=values['record']['run_id'], worker_boot=values['boot'],
        browser_epoch=values['epoch'], lease_seconds=60)


def assistance(values, execution_id=None):
    return {**{key: values['record'][key] for key in ('tenant_id', 'user_id', 'session_id', 'run_id')},
        'assistance_id': 'ba_fixture_' + uuid.uuid4().hex,
        'agent_execution_id': execution_id or values['root_id'], 'tool_call_id': values['call_id'],
        'reason_code': 'CAPTCHA_REQUIRED', 'instruction_code': 'PAGE_VERIFICATION',
        'completion_mode': 'auto_or_confirm', 'expires_at': datetime.now() + timedelta(minutes=5),
        # M7 旧缓存卡片：原随机 bac 随每新 wait 持久写入（独立于 completion 相位）。
        'continuation_id': 'bac_fixture_' + uuid.uuid4().hex}


def node(checkpoint, values, execution_id):
    root = checkpoint['execution']
    return root if execution_id == values['root_id'] else root['children'][values['parent_call']]['checkpoint']


@pytest.mark.parametrize('scope', ['root', 'child'])
def test_original_browser_call_run_and_wait_are_atomic_and_stable(browser_owner, service_database, scope):
    from src.services.agent_runner.ownership import LeaseLost
    values = browser_owner
    execution_id = values[scope + '_id']
    bound = bind(values, execution_id=execution_id)
    current = node(bound['checkpoint'], values, execution_id)
    fact = current['resources']['browser_runs'][values['call_id']]
    assert fact['run_id'] == values['record']['run_id'] and fact['runner_execution_id'] == execution_id
    # A repeated starting bind is idempotent, not another run or revision.
    assert bind(values, row=bound, execution_id=execution_id)['revision'] == bound['revision']
    different = {**values['record'], 'run_id': 'br_fixture_' + uuid.uuid4().hex}
    with pytest.raises(LeaseLost):
        bind(values, row=bound, execution_id=execution_id, record=different)
    activate(values, execution_id)
    request = assistance(values, execution_id)
    waited = values['owner'].bind_wait(values['attempt'], bound['revision'], execution_id=execution_id,
                                      call_id=values['call_id'], assistance=request, worker_boot=values['boot'], browser_epoch=values['epoch'])
    wait_rows = service_database.rows('SELECT * FROM bs_browser_assistance_requests WHERE assistance_id=%s', (request['assistance_id'],))
    assert len(wait_rows) == 1
    wait = wait_rows[0]
    assert wait['runner_id'] == values['row']['runner_id'] and wait['agent_execution_id'] == execution_id
    assert wait['continuation_id'] == request['continuation_id']
    assert node(waited['checkpoint'], values, execution_id)['resources']['browser_runs'][values['call_id']]['waits'] == {wait['runner_wait_id']: request['assistance_id']}
    assert values['owner'].bind_wait(values['attempt'], waited['revision'], execution_id=execution_id,
                                    call_id=values['call_id'], assistance=request, worker_boot=values['boot'], browser_epoch=values['epoch'])['revision'] == waited['revision']
    second_request = assistance(values, execution_id)
    second = values['owner'].bind_wait(values['attempt'], waited['revision'], execution_id=execution_id,
                                     call_id=values['call_id'], assistance=second_request, worker_boot=values['boot'], browser_epoch=values['epoch'])
    waits = node(second['checkpoint'], values, execution_id)['resources']['browser_runs'][values['call_id']]['waits']
    assert len(waits) == 2 and len(set(waits)) == 2
    # 幂等重绑不覆盖原 bac；第二个新 wait 持久化自己的独立 bac。
    assert service_database.rows('SELECT continuation_id FROM bs_browser_assistance_requests WHERE assistance_id=%s',
        (request['assistance_id'],))[0]['continuation_id'] == request['continuation_id']
    assert service_database.rows('SELECT continuation_id FROM bs_browser_assistance_requests WHERE assistance_id=%s',
        (second_request['assistance_id'],))[0]['continuation_id'] == second_request['continuation_id'] != request['continuation_id']
    assert service_database.rows('SELECT run_id FROM bs_browser_runs WHERE runner_id=%s', (values['row']['runner_id'],)) == [{'run_id': values['record']['run_id']}]


@pytest.mark.parametrize('identity_field', ['tenant_id', 'user_id', 'session_id'])
def test_foreign_browser_record_cannot_bind_original_owned_call(browser_owner, service_database, identity_field):
    from src.services.agent_runner.ownership import LeaseLost
    values = browser_owner
    original = values['row']
    with pytest.raises(LeaseLost):
        bind(values, record={**values['record'], identity_field: 'foreign-fictional-identity'})
    current = values['repository'].get(original['runner_id'])
    assert current['checkpoint'] == original['checkpoint'] and current['revision'] == original['revision']
    assert service_database.rows('SELECT 1 FROM bs_browser_runs WHERE runner_id=%s', (original['runner_id'],)) == []


@pytest.mark.parametrize('boundary', ['run', 'wait'])
def test_browser_binding_checkpoint_failure_rolls_back_original_audit_insert(browser_owner, service_database, boundary):
    values = browser_owner
    before = values['row']
    request = None
    if boundary == 'wait':
        before = bind(values)
        activate(values)
        request = assistance(values)
    function = 'fixture_browser_cas_' + uuid.uuid4().hex
    trigger = function + '_trigger'
    with service_database.connect() as connection, connection.cursor() as cursor:
        cursor.execute(sql.SQL('''CREATE FUNCTION {}() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN IF NEW.runner_id={} AND NEW.revision>OLD.revision THEN
                RAISE EXCEPTION 'FIXTURE_BROWSER_CHECKPOINT_REFUSED'; END IF; RETURN NEW; END $$''')
            .format(sql.Identifier(function), sql.Literal(before['runner_id'])))
        cursor.execute(sql.SQL('CREATE TRIGGER {} BEFORE UPDATE ON agent_runners FOR EACH ROW EXECUTE FUNCTION {}()')
                       .format(sql.Identifier(trigger), sql.Identifier(function)))
    try:
        with pytest.raises(RaiseException):
            if boundary == 'run':
                bind(values)
            else:
                values['owner'].bind_wait(values['attempt'], before['revision'], execution_id=values['root_id'],
                                         call_id=values['call_id'], assistance=request, worker_boot=values['boot'], browser_epoch=values['epoch'])
        current = values['repository'].get(before['runner_id'])
        assert current['revision'] == before['revision'] and current['checkpoint'] == before['checkpoint']
        table = 'bs_browser_runs' if boundary == 'run' else 'bs_browser_assistance_requests'
        assert service_database.rows(sql.SQL('SELECT 1 FROM {} WHERE runner_id=%s').format(sql.Identifier(table)), (before['runner_id'],)) == []
    finally:
        with service_database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql.SQL('DROP TRIGGER {} ON agent_runners').format(sql.Identifier(trigger)))
            cursor.execute(sql.SQL('DROP FUNCTION {}()').format(sql.Identifier(function)))


@pytest.mark.parametrize('operation', ['bind', 'wait'])
def test_browser_transaction_started_before_lease_expiry_rechecks_after_real_lock_wait(browser_owner, service_database, operation):
    from src.services.agent_runner.browser_binding import BrowserOwnerRepository
    from src.services.agent_runner.ownership import LeaseLost
    values = browser_owner
    before = values['row']
    request = None
    if operation == 'wait':
        before = bind(values)
        activate(values)
        request = assistance(values)
    service_database.rows("UPDATE agent_runners SET lease_until=clock_timestamp()+interval '2 seconds' WHERE runner_id=%s", (before['runner_id'],))
    backend = queue.Queue()
    @contextmanager
    def connection_factory():
        with service_database.connect() as connection, connection.cursor() as cursor:
            cursor.execute('SELECT pg_backend_pid() AS pid')
            backend.put(cursor.fetchone()['pid'])
            yield connection
    delayed = BrowserOwnerRepository(connection_factory)
    def action():
        if operation == 'bind':
            return bind(values, owner=delayed)
        return delayed.bind_wait(values['attempt'], before['revision'], execution_id=values['root_id'],
                                 call_id=values['call_id'], assistance=request, worker_boot=values['boot'], browser_epoch=values['epoch'])
    with ThreadPoolExecutor(max_workers=1) as pool:
        with service_database.connect() as lock, lock.cursor() as cursor:
            cursor.execute('SELECT runner_id FROM agent_runners WHERE runner_id=%s FOR UPDATE', (before['runner_id'],))
            future = pool.submit(action)
            try:
                pid = backend.get(timeout=2)
                wait_for(lambda: service_database.rows('''SELECT a.wait_event_type='Lock' AND
                    a.xact_start<r.lease_until AND clock_timestamp()<r.lease_until AS blocked
                    FROM pg_stat_activity a CROSS JOIN agent_runners r WHERE a.pid=%s AND r.runner_id=%s''',
                    (pid, before['runner_id']))[0]['blocked'], timeout=1)
                wait_for(lambda: service_database.rows('SELECT clock_timestamp()>=lease_until AS expired FROM agent_runners WHERE runner_id=%s', (before['runner_id'],))[0]['expired'], timeout=3)
            finally:
                lock.commit()
        with pytest.raises(LeaseLost):
            future.result(timeout=3)
    after = values['repository'].get(before['runner_id'])
    assert after['revision'] == before['revision'] and after['checkpoint'] == before['checkpoint']
    table = 'bs_browser_runs' if operation == 'bind' else 'bs_browser_assistance_requests'
    assert service_database.rows(sql.SQL('SELECT 1 FROM {} WHERE runner_id=%s').format(sql.Identifier(table)), (before['runner_id'],)) == []


def test_browser_owner_lease_survives_parked_runner_but_cannot_change_boot_or_epoch(browser_owner, service_database):
    from src.services.agent_runner.ownership import LeaseLost
    values = browser_owner
    bound = bind(values)
    live = activate(values)
    # Native safe parked CP contract: this is not proof an executor stopped.
    values['execution'].park(values['attempt'], bound['revision'], status='waiting',
                             checkpoint=bound['checkpoint'], snapshot=None)
    kwargs = dict(tenant_id=values['row']['tenant_id'], run_id=values['record']['run_id'],
                  worker_id=values['attempt'].worker_id, worker_boot=values['boot'],
                  browser_epoch=values['epoch'], lease_seconds=60)
    renewed = values['owner'].renew_owner(**kwargs)
    assert renewed['owner_lease_until'] >= live['owner_lease_until']
    for field in ('worker_boot', 'browser_epoch'):
        with pytest.raises(LeaseLost):
            values['owner'].renew_owner(**{**kwargs, field: 'foreign-native-owner'})
    service_database.rows("UPDATE bs_browser_runs SET owner_lease_until=clock_timestamp()-interval '1 second' WHERE run_id=%s", (values['record']['run_id'],))
    with pytest.raises(LeaseLost):
        values['owner'].renew_owner(**kwargs)


def test_child_browser_binding_rejects_foreign_checkpoint_identity(browser_owner, service_database):
    from src.services.agent_runner.ownership import LeaseLost
    values = browser_owner
    foreign = copy.deepcopy(values['row']['checkpoint'])
    foreign['execution']['children'][values['parent_call']]['checkpoint']['identity']['tenant_id'] = 'foreign-fictional-tenant'
    saved = values['execution'].save_checkpoint(values['attempt'], values['row']['revision'], foreign)
    with pytest.raises(LeaseLost):
        bind(values, row=saved, execution_id=values['child_id'])
    assert values['repository'].get(saved['runner_id'])['checkpoint'] == saved['checkpoint']
    assert service_database.rows('SELECT 1 FROM bs_browser_runs WHERE runner_id=%s', (saved['runner_id'],)) == []


def test_stale_browser_binding_revision_does_not_create_an_orphan_run(browser_owner, service_database):
    from src.services.agent_runner.ownership import LeaseLost
    values = browser_owner
    saved = values['execution'].save_checkpoint(values['attempt'], values['row']['revision'], values['row']['checkpoint'])
    with pytest.raises(LeaseLost):
        bind(values)
    assert values['repository'].get(saved['runner_id'])['revision'] == saved['revision']
    assert service_database.rows('SELECT 1 FROM bs_browser_runs WHERE runner_id=%s', (saved['runner_id'],)) == []


def test_browser_owner_renewal_checks_real_expiry_after_waiting_for_original_run_lock(browser_owner, service_database):
    from src.services.agent_runner.browser_binding import BrowserOwnerRepository
    from src.services.agent_runner.ownership import LeaseLost
    values = browser_owner
    bind(values)
    activate(values)
    original = service_database.rows("UPDATE bs_browser_runs SET owner_lease_until=clock_timestamp()+interval '2 seconds' WHERE run_id=%s RETURNING *", (values['record']['run_id'],))[0]
    backend = queue.Queue()
    @contextmanager
    def connection_factory():
        with service_database.connect() as connection, connection.cursor() as cursor:
            cursor.execute('SELECT pg_backend_pid() AS pid')
            backend.put(cursor.fetchone()['pid'])
            yield connection
    delayed = BrowserOwnerRepository(connection_factory)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with service_database.connect() as lock, lock.cursor() as cursor:
            cursor.execute('SELECT run_id FROM bs_browser_runs WHERE run_id=%s FOR UPDATE', (original['run_id'],))
            future = pool.submit(delayed.renew_owner, tenant_id=original['tenant_id'], run_id=original['run_id'],
                worker_id=values['attempt'].worker_id, worker_boot=values['boot'], browser_epoch=values['epoch'], lease_seconds=60)
            try:
                pid = backend.get(timeout=2)
                wait_for(lambda: service_database.rows('''SELECT a.wait_event_type='Lock' AND
                    a.xact_start<r.owner_lease_until AND clock_timestamp()<r.owner_lease_until AS blocked
                    FROM pg_stat_activity a CROSS JOIN bs_browser_runs r WHERE a.pid=%s AND r.run_id=%s''',
                    (pid, original['run_id']))[0]['blocked'], timeout=1)
                wait_for(lambda: service_database.rows('SELECT clock_timestamp()>=owner_lease_until AS expired FROM bs_browser_runs WHERE run_id=%s', (original['run_id'],))[0]['expired'], timeout=3)
            finally:
                lock.commit()
        with pytest.raises(LeaseLost):
            future.result(timeout=3)
    current = service_database.rows('SELECT * FROM bs_browser_runs WHERE run_id=%s', (original['run_id'],))[0]
    assert current['owner_lease_until'] == original['owner_lease_until'] and current['runtime_state'] == 'live'
