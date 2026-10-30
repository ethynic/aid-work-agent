"""Real active record deltas, pending facts and restart-safe history IDs."""

import copy
from decimal import Decimal
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.core.agent_engine.contracts import AgentMode, ExecutionState, Identity
from src.services.agent_runner.continuous_history import archive_and_compact
from src.services.agent_runner.finalizer import RunnerFinalizer
from src.services.agent_runner.ownership import StopRequested
from src.services.agent_runner.usage_pricing import grouped_cost
from src.services.agent_runner.worker import execution_result
from src.services.agent_runner.transaction_repository import write_history
from .test_storage import attempt,storage
from .test_usage_storage import ledger,prices,observed,started
from .conftest import wait_for

pytestmark=pytest.mark.integration


@pytest.fixture
def active(ledger,service_database,monkeypatch):
    monkeypatch.setattr(RunnerFinalizer,'_after_commit',staticmethod(lambda *args:None))
    row=ledger[3]
    saved=ledger[1].save_checkpoint(attempt(row),row['revision'],
        {'continuous':{'version':1,'enabled':True,'source':'creation','policy_revision':1}})
    return ledger,RunnerFinalizer(service_database.connect),saved


def test_each_active_delta_matches_original_group_rounding_and_retries_do_not_debit_again(active,service_database):
    ledger,finalizer,row=active
    receipts=[]
    balance=service_database.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s',(row['tenant_id'],))[0]['credit_balance']
    for index in range(6):
        receipt=observed(ledger,model=ledger[5][1],fact={'prompt_tokens':80 if index%2 else 150,'completion_tokens':3})
        receipts.append(receipt)
        projected=finalizer.settle_active(attempt(row))
        expected,totals,_=grouped_cost(receipts)
        record=service_database.rows('SELECT * FROM chat_records WHERE record_id=%s',(row['record_id'],))[0]
        assert Decimal(str(record['credit_cost']))==expected
        assert record['prompt_tokens']==totals['prompt_tokens']
        assert projected['public_snapshot']['usage']['task_credits']==float(expected)
        finalizer.settle_active(attempt(row))
        actual=service_database.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s',(row['tenant_id'],))[0]['credit_balance']
        assert Decimal(str(actual))==Decimal(str(balance))-expected
    assert service_database.rows('SELECT * FROM agent_runner_session_claims WHERE owner_runner_id=%s',(row['runner_id'],))
    assert ledger[0].get(row['runner_id'])['status']=='running'


def test_unknown_usage_remains_pending_and_is_charged_when_original_fact_arrives(active,service_database):
    ledger,finalizer,row=active
    unknown=started(ledger)
    ledger[2].uncertain(attempt(row),unknown['receipt_id'])
    first=finalizer.settle_active(attempt(row))
    assert first['settlement_status']=='pending' and first['public_snapshot']['usage']['pending'] is True
    ledger[2].observe(attempt(row),unknown['receipt_id'],{'prompt_tokens':100,'completion_tokens':10})
    second=finalizer.settle_active(attempt(row))
    assert second['settlement_status']=='settled' and second['public_snapshot']['usage']['pending'] is False
    assert second['public_snapshot']['usage']['task_credits']>0


def test_actual_balance_blocks_new_receipt_but_still_allows_settlement(active,service_database):
    ledger,finalizer,row=active
    observed(ledger)
    finalizer.settle_active(attempt(row))
    service_database.rows('UPDATE tenants SET credit_balance=0 WHERE tenant_id=%s',(row['tenant_id'],))
    before=service_database.rows('SELECT count(*) AS count FROM agent_runner_usage_receipts WHERE runner_id=%s',(row['runner_id'],))[0]['count']
    with pytest.raises(StopRequested):
        started(ledger,call='must-not-dispatch')
    assert service_database.rows('SELECT count(*) AS count FROM agent_runner_usage_receipts WHERE runner_id=%s',(row['runner_id'],))[0]['count']==before
    assert finalizer.settle_active(attempt(row))['status']=='running'


def test_finalization_updates_active_record_status_without_recharging(active,service_database):
    ledger,finalizer,row=active
    observed(ledger)
    active_row=finalizer.settle_active(attempt(row))
    expected=active_row['public_snapshot']['usage']['task_credits']
    staged=ledger[1].stage_finalization(attempt(row),active_row['revision'],active_row['checkpoint'],
        {'status':'completed','output':'done','messages':[],'images':[]},active_row['public_snapshot'])
    result=finalizer.finalize(attempt(staged))
    record=service_database.rows('SELECT status,credit_cost,assistant_message FROM chat_records WHERE record_id=%s',(row['record_id'],))[0]
    assert record['status']=='completed' and record['assistant_message']=='done'
    assert float(record['credit_cost'])==expected
    assert result['status']=='completed'


def test_archival_retry_after_crash_and_final_history_have_no_reused_ids(storage,actors,service_database):
    repository,execution,submit=storage
    accepted,principal=submit(actors['a'])
    owned=execution.acquire('history',30)
    root=ExecutionState(principal.identity,owned['runner_id'],AgentMode.MASTER,'prompt',
        [{'role':'assistant','content':'message-'+str(i)} for i in range(200)])
    original=copy.deepcopy(root)
    for current in (root,original):
        current.resources.update(continuous_summary='outer summarizer retained task state',continuous_summary_through=152)
    control=SimpleNamespace(attempt=attempt(owned),repository=execution,envelope={},snapshot={})
    archive_and_compact(control,root)
    # The previous archive transaction committed, but assume the park checkpoint
    # did not. A replacement owner repeats original state using the same IDs.
    archive_and_compact(control,original)
    rows=service_database.rows('SELECT message_id FROM chat_messages WHERE session_id=%s',(actors['a'].session_id,))
    assert len(rows)==153 and len({row['message_id'] for row in rows})==153
    # The terminal projection keeps the remaining feature messages with their
    # original sequence numbers, rather than renumbering its filtered array.
    root.output='finished'
    result=execution_result(root,'completed')
    assert root.resources['history_version']==1
    with service_database.connect() as connection:
        cursor=connection.cursor()
        runner={**repository.get(owned['runner_id']),'checkpoint':{'execution':root.checkpoint()}}
        write_history(cursor,runner,result)
    final=service_database.rows('SELECT message_id FROM chat_messages WHERE session_id=%s',(actors['a'].session_id,))
    assert owned['runner_id']+':final' in {row['message_id'] for row in final}
    assert len(final)==202


def test_receipt_lock_wait_cannot_dispatch_after_lease_expires(active,service_database):
    ledger,finalizer,row=active
    service_database.rows("UPDATE agent_runners SET lease_until=clock_timestamp()+interval '1 second' WHERE runner_id=%s",(row['runner_id'],))
    from src.services.agent_runner.ownership import LeaseLost
    with ThreadPoolExecutor(max_workers=1) as pool:
        with service_database.connect() as blocked:
            cursor=blocked.cursor()
            cursor.execute('SELECT credit_balance FROM tenants WHERE tenant_id=%s FOR UPDATE',(row['tenant_id'],))
            request=pool.submit(started,ledger,call='expired-at-tenant-lock')
            wait_for(lambda:service_database.rows("SELECT 1 FROM pg_stat_activity WHERE datname=current_database() AND wait_event_type='Lock' AND query LIKE %s",('SELECT credit_balance FROM tenants%',)))
            service_database.rows('SELECT pg_sleep(1.1)')
        with pytest.raises(LeaseLost):
            request.result(timeout=5)
    assert service_database.rows('SELECT receipt_id FROM agent_runner_usage_receipts WHERE runner_id=%s',(row['runner_id'],))==[]


def test_retained_supplement_is_not_written_early_or_given_another_id_on_next_archive(storage,actors,service_database):
    repository,execution,submit=storage
    accepted,principal=submit(actors['a'])
    owned=execution.acquire('supplement-history',30)
    root=ExecutionState(principal.identity,owned['runner_id'],AgentMode.MASTER,'prompt',
        [{'role':'assistant','content':'message-'+str(i)} for i in range(100)])
    root.messages[80]={'role':'user','content':'enhanced rule',
        'metadata':{'control_id':'control-rule','submitted_text':'only important'}}
    envelope={'execution':root.checkpoint(),'supplemental_inputs':[{'control_id':'control-rule',
        'text':'only important','accepted_at':str(owned['accepted_at'])}]}
    execution.save_checkpoint(attempt(owned),owned['revision'],envelope)
    control=SimpleNamespace(attempt=attempt(owned),repository=execution,envelope=envelope,snapshot={})
    root.resources.update(continuous_summary='summary',continuous_summary_through=52)
    archive_and_compact(control,root)
    assert service_database.rows("SELECT message_id FROM chat_messages WHERE session_id=%s AND role='user'",(actors['a'].session_id,))==[{'message_id':owned['runner_id']+':user'}]
    root.messages.extend({'role':'assistant','content':'later-'+str(i)} for i in range(100))
    root.resources['continuous_summary_through']=152
    archive_and_compact(control,root)
    users=service_database.rows("SELECT message_id,content FROM chat_messages WHERE session_id=%s AND role='user' ORDER BY created_at",(actors['a'].session_id,))
    assert len(users)==2 and users[1]=={'message_id':'control-rule:user','content':'only important'}


def test_physical_summary_receipt_atomically_fences_timer_handoff_before_provider_io(active,service_database):
    ledger,finalizer,row=active
    from psycopg2.extras import Json
    checkpoint=copy.deepcopy(row['checkpoint'])
    checkpoint['timer_activation']={'version':1,'attempt':row['attempt'],'wait_id':'original-wait','action_started':False}
    service_database.rows('UPDATE agent_runners SET checkpoint=%s WHERE runner_id=%s',(Json(checkpoint),row['runner_id']))
    receipt=started(ledger,call='summary-start',purpose='compression')
    stored=ledger[0].get(row['runner_id'])
    assert stored['checkpoint']['timer_activation']['action_started'] is True
    assert stored['revision']==row['revision']
    ledger[2].uncertain(attempt(row),receipt['receipt_id'])
    service_database.rows("UPDATE agent_runners SET lease_until=clock_timestamp()-interval '1 second' WHERE runner_id=%s",(row['runner_id'],))
    ledger[1].reap_expired()
    from src.services.agent_runner.timer_repository import TimerRepository
    assert TimerRepository(service_database.connect).next_candidates()==[]
