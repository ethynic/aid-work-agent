"""Real PG ownership and fresh Worker continuation after a server interruption."""

import copy
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from psycopg2.extras import Json

from src.core.agent_engine.contracts import AgentMode, ExecutionState, Outcome
from src.services.agent_runner.ownership import LeaseLost
from src.services.agent_runner.recovery import RecoveryCoordinator
from src.services.agent_runner.recovery_repository import RecoveryRepository
from src.services.agent_runner.runtime_manifest import configuration_fingerprint
from src.services.agent_runner.source_receipts import SourceUnavailable
from src.services.agent_runner.timer_repository import TimerRepository

from .test_storage import storage, attempt
from .conftest import wait_for
from .test_durable_sleep import park, due, prepare
from .test_durable_sleep_worker_independent import service_processes, api_pair
from .test_worker import workers, prices, accept, runner, decoded
from .provider import Reply, tool_reply

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def enable_continuous(monkeypatch):
    from src.config.settings import settings
    monkeypatch.setattr(settings.agent_runner,'durable_sleep_enabled',True)


def interrupted(storage, actor, database, *, prepared=True):
    repository, execution, submit = storage
    _, principal = submit(actor)
    owned = execution.acquire('original-worker',30)
    checkpoint = {'continuous':{'version':1,'enabled':True}}
    if prepared:
        state = ExecutionState(principal.identity,owned['runner_id'],AgentMode.MASTER,
            'accepted-role',[],profile_id='main',outcome=Outcome.RUNNING,iteration=2)
        state.resources = {'configuration_fingerprint':configuration_fingerprint('fixture-profile'),
            'continuous_enabled':True,'business_iterations':2}
        checkpoint['execution'] = state.checkpoint()
    saved = execution.save_checkpoint(attempt(owned),owned['revision'],checkpoint)
    database.rows("UPDATE agent_runners SET lease_until=clock_timestamp()-INTERVAL '1 second' WHERE runner_id=%s",
        (owned['runner_id'],))
    execution.reap_expired()
    row = repository.get(owned['runner_id'])
    authorizer = SimpleNamespace(authorize_persisted=lambda *a,**k:principal,assert_credit=lambda p:None)
    profiles = SimpleNamespace(resolve=lambda *a:(None,'fixture-profile'))
    return row, principal, RecoveryCoordinator(authorizer,profiles), owned


def test_competing_restart_workers_continue_the_original_claim_once_without_user_controls(storage, actors, service_database):
    row, _, coordinator, old = interrupted(storage,actors['a'],service_database)
    prepared = coordinator.prepare(row,continuous_trigger=True).checkpoint
    recovery = RecoveryRepository(service_database.connect)
    assert [item['runner_id'] for item in recovery.next_continuous_candidates()] == [row['runner_id']]
    with ThreadPoolExecutor(max_workers=2) as pool:
        claimed = list(pool.map(lambda owner:recovery.claim_continuous(row,owner,30,prepared),('new-a','new-b')))
    claimed = [item for item in claimed if item is not None]
    assert len(claimed) == 1
    result = claimed[0]
    assert result['runner_id'] == row['runner_id'] and result['record_id'] == row['record_id']
    assert result['attempt'] == old['attempt']+1
    assert result['checkpoint']['execution'] == prepared['execution']
    assert result['checkpoint']['continuous_activation']['attempt'] == result['attempt']
    assert service_database.rows('SELECT 1 FROM agent_runner_controls WHERE runner_id=%s',(row['runner_id'],)) == []
    with pytest.raises(LeaseLost):
        storage[1].assert_dispatch(attempt(old))


@pytest.mark.parametrize('flag',['pause_requested','cancel_requested'])
def test_user_stop_after_preflight_wins_the_claim_transaction(storage, actors, service_database, flag):
    row, _, coordinator, _ = interrupted(storage,actors['a'],service_database)
    prepared = coordinator.prepare(row,continuous_trigger=True).checkpoint
    assert flag in {'pause_requested','cancel_requested'}
    service_database.rows('UPDATE agent_runners SET '+flag+'=TRUE WHERE runner_id=%s',(row['runner_id'],))
    assert RecoveryRepository(service_database.connect).claim_continuous(row,'new',30,prepared) is None
    assert storage[0].get(row['runner_id'])['attempt'] == row['attempt']


def test_lost_source_after_claim_rolls_back_without_consuming_progress(storage, actors, service_database):
    row, _, coordinator, _ = interrupted(storage,actors['a'],service_database)
    prepared = coordinator.prepare(row,continuous_trigger=True).checkpoint
    calls = []
    def check(cursor, current, **kwargs):
        calls.append(current['status'])
        if current['status'] == 'running':
            raise SourceUnavailable('temporary-after-cas')
    port = SimpleNamespace(authorize_row_in_tx=check)
    with pytest.raises(SourceUnavailable):
        RecoveryRepository(service_database.connect).claim_continuous(row,'new',30,prepared,source_port=port)
    assert calls == ['interrupted','running']
    actual = storage[0].get(row['runner_id'])
    assert actual['status'] == 'interrupted' and actual['attempt'] == row['attempt']
    assert actual['checkpoint'] == row['checkpoint']


def test_restart_before_initial_prepare_can_reassemble_only_without_admitted_io(storage, actors, service_database):
    row, _, coordinator, _ = interrupted(storage,actors['a'],service_database,prepared=False)
    prepared = coordinator.prepare(row,continuous_trigger=True).checkpoint
    claimed = RecoveryRepository(service_database.connect).claim_continuous(row,'fresh',30,prepared)
    assert claimed['runner_id'] == row['runner_id'] and 'execution' not in claimed['checkpoint']
    assert 'applied_control_id' not in claimed['checkpoint']


def test_temporary_timer_failure_preserves_deadline_and_automatically_becomes_due_again(storage, actors, service_database):
    row, principal = park(storage,actors['a'])
    row = due(service_database,row)
    timers = TimerRepository(service_database.connect)
    assert timers.defer(row,'SOURCE_RECOVERY_UNAVAILABLE')
    deferred = storage[0].get(row['runner_id'])
    assert deferred['wake_at'] == row['wake_at']
    assert deferred['checkpoint']['execution'] == row['checkpoint']['execution']
    assert 'sleep_blocked' not in deferred['checkpoint']
    assert timers.next_candidates() == []
    checkpoint = copy.deepcopy(deferred['checkpoint'])
    checkpoint['sleep_retry']['retry_at'] = (datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
    service_database.rows('UPDATE agent_runners SET checkpoint=%s WHERE runner_id=%s',(Json(checkpoint),row['runner_id']))
    ready = timers.next_candidates()[0]
    result = timers.claim(ready,'recovered',30,prepare(ready,principal))
    assert result['status'] == 'running' and result['runner_id'] == row['runner_id']
    assert 'sleep_retry' not in result['checkpoint']


def test_legacy_source_hold_revives_but_configuration_hold_stays_explicit(storage, actors, service_database):
    row, principal = park(storage,actors['a'])
    row = due(service_database,row)
    timers = TimerRepository(service_database.connect)
    assert timers.hold(row,'SOURCE_RECOVERY_UNAVAILABLE')
    revived = timers.next_candidates()[0]
    assert revived['wake_at'] is None
    result = timers.claim(revived,'new-after-source-restored',30,prepare(revived,principal))
    assert result['status'] == 'running'
    blocked, _ = park(storage,actors['b'])
    blocked = due(service_database,blocked)
    assert timers.hold(blocked,'RECOVERY_CONFIGURATION_CHANGED')
    assert timers.next_candidates() == []
    assert storage[0].get(blocked['runner_id'])['checkpoint']['sleep_blocked']['error_code'] == 'RECOVERY_CONFIGURATION_CHANGED'


def test_actual_fresh_worker_continues_inflight_model_interruption_without_a_resume_control(
        workers, actors, service_database):
    workers.environment['AGENT_RUNNER_DURABLE_SLEEP_ENABLED'] = 'true'
    released = threading.Event()
    held = Reply(content='old-response-never-dispatched',release=released)
    marker = workers.provider.register(held,
        tool_reply('agent_sleep',{'seconds':60},call_id='continued-original-task-sleep'))
    accepted = accept(workers.api,actors['a'],marker,execution_mode='continuous')
    process, _ = workers.start()
    try:
        assert held.arrived.wait(timeout=20)
        before = runner(service_database,accepted['runner_id'])
        before_tree = decoded(before['checkpoint'])['execution']
        assert before_tree['model_calls'][-1]['phase'] == 'started'
        before_receipts = service_database.rows('''SELECT receipt_id,call_id,authorized_attempt,phase,usage,applied
            FROM agent_runner_usage_receipts WHERE runner_id=%s''',(accepted['runner_id'],))
        assert len(before_receipts) == 1 and before_receipts[0]['phase'] == 'started'
        workers.processes.stop(process,force=True)
        service_database.rows("UPDATE agent_runners SET lease_until=clock_timestamp()-INTERVAL '1 second' WHERE runner_id=%s",
            (accepted['runner_id'],))
        released.set()
        replacement, _ = workers.start()
        workers.assert_clean_exit(replacement)
        # A continuous task keeps running after text output. Its next durable
        # sleep proves the replacement actually resumed and dispatched once.
        result = wait_for(lambda:(row if (row:=runner(service_database,accepted['runner_id']))['status']=='waiting'
            else None),timeout=20)
        assert result['record_id'] == before['record_id'] and result['wake_at'] is not None, {
            'wait':(decoded(result['checkpoint'])['execution'].get('waiting') or {}).get('kind'),
            'reason':(decoded(result['checkpoint'])['execution'].get('waiting') or {}).get('reason'),
            'error':decoded(result['checkpoint'])['execution'].get('error_code'),
            'timer':decoded(result['checkpoint']).get('durable_sleep')}
        assert result['attempt'] > before['attempt']
        tree = decoded(result['checkpoint'])['execution']
        assert tree['execution_id'] == before_tree['execution_id']
        assert tree['model_calls'][0]['call_id'] == before_tree['model_calls'][0]['call_id']
        assert tree['model_calls'][0]['phase'] == 'unknown'
        assert len({item['call_id'] for item in tree['model_calls']}) == 2
        assert len(workers.provider.requests(marker)) == 2
        receipts = service_database.rows('''SELECT receipt_id,call_id,authorized_attempt,phase,usage,applied
            FROM agent_runner_usage_receipts WHERE runner_id=%s ORDER BY created_at''',(accepted['runner_id'],))
        assert len(receipts) == 2 and receipts[0] == before_receipts[0]
        assert receipts[1]['receipt_id'] != receipts[0]['receipt_id'] and receipts[1]['call_id'] != receipts[0]['call_id']
        assert receipts[1]['authorized_attempt'] == result['attempt']
        assert receipts[1]['phase'] == 'observed' and receipts[1]['applied']
        assert result['settlement_status'] == 'pending'  # No invented free usage for the interrupted call.
        assert service_database.rows('SELECT 1 FROM agent_runner_controls WHERE runner_id=%s',(accepted['runner_id'],)) == []
    finally:
        released.set()
