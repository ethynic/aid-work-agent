"""Ordinary original-invocation recovery via fresh CLI and real Runtime/Proxy.

Only the external model HTTP and desktop effect are fictional. Device claim,
result, charging, invocation owner, Engine, checkpoints and controls remain real.
Prepared before the ordinary Local slice freeze; run only after handoff.
"""
from decimal import Decimal
import json
import re
import uuid

import pytest

from .conftest import wait_for
from .provider import Reply, tool_reply
from .test_worker import workers, prices, api_pair, accept, runner, terminal, decoded
from .test_worker_recovery import control

pytestmark = pytest.mark.integration

# This is an external test device, not an Engine/Owner replacement. It uses the
# original runtime DAL protocol and writes exactly one physical result twice to
# exercise transport idempotency at its real billing owner.
_DEVICE = r'''
import json,secrets,sys,time
from pathlib import Path
from src.db.database import init_postgres_pool,close_postgres_pool
from src.local_tools import repository
from src.local_tools.security import sha256_hex
init_postgres_pool()
try:
    tenant,user,folder=sys.argv[1:4]
    root=Path(folder)
    device=repository.create_device(tenant,user,sha256_hex(secrets.token_urlsafe(32)),
        name='Fictional recovery Runtime',platform='windows',
        capabilities={'provider_id':'ai.aidwork.boss-recruiting'})
    assert repository.select_device(tenant,user,str(device['id']))
    (root/'device-ready.json').write_text(json.dumps({'device_id':str(device['id'])}))
    claim_hash=sha256_hex(secrets.token_urlsafe(32))
    deadline=time.monotonic()+90
    invocation=None
    last_heartbeat=0
    def heartbeat():
        global last_heartbeat
        if time.monotonic()-last_heartbeat>=1:
            assert repository.touch_device_seen(str(device['id']))
            last_heartbeat=time.monotonic()
    while time.monotonic()<deadline:
        heartbeat()
        if (root/'allow-claim').exists():
            invocation=repository.claim_next(str(device['id']),tenant,claim_hash,90)
            if invocation: break
        time.sleep(.05)
    assert invocation and invocation['tool_name']=='boss_select_job'
    identifier=str(invocation['id'])
    assert repository.mark_started(identifier,tenant,claim_hash)['state']=='running'
    repository.append_event(identifier,tenant,claim_hash,'fixture-started',1,1,'Fixture started',90)
    (root/'device-claimed.json').write_text(json.dumps({'invocation_id':identifier}))
    while not (root/'allow-result').exists() and time.monotonic()<deadline:
        heartbeat()
        time.sleep(.05)
    assert (root/'allow-result').exists()
    state=repository.get_invocation(identifier,tenant)['state']
    cancelled=state=='cancel_requested'
    mode=(root/'result-mode').read_text() if (root/'result-mode').exists() else 'success'
    code='EXECUTION_UNKNOWN' if mode=='cancel_unknown' else 'CANCELLED' if cancelled else 'EXECUTION_UNKNOWN' if mode=='unknown' else 'UI_CHANGED' if mode=='heal' else None
    effect='unknown' if mode=='cancel_unknown' or mode=='unknown' and not cancelled else 'not_applied' if code else 'applied'
    first=repository.write_result(identifier,tenant,claim_hash,code is None,
        code=code,message='Fictional physical result',effect=effect,data={'fixture':'original-device-result'})
    second=repository.write_result(identifier,tenant,claim_hash,code is None,
        code=code,message='Duplicate result transport',effect=effect,data={'fixture':'original-device-result'})
    assert first['state']==second['state']
    (root/'device-result.json').write_text(json.dumps({'invocation_id':identifier,'state':first['state']}))
finally:
    close_postgres_pool()
'''


@pytest.fixture
def ordinary_local(workers, actors, service_database):
    actor=actors['a']
    profile='recruiting-operator'
    subscription='fixture-local-recovery-'+uuid.uuid4().hex
    service_database.rows("INSERT INTO subscriptions(subscription_id,tenant_id,subagent_type,status,payment_status) VALUES(%s,%s,%s,'active','paid')",(subscription,actor.tenant_id,profile))
    service_database.rows('INSERT INTO user_agent_permissions(user_id,tenant_id,agent_id) VALUES(%s,%s,%s)',(actor.user_id,actor.tenant_id,profile))
    folder=workers.root/('desktop-'+uuid.uuid4().hex);folder.mkdir()
    device=workers.processes.start(['-c',_DEVICE,actor.tenant_id,actor.user_id,str(folder)],
                                  environment=workers.environment,private_working_directory=True)
    try:
        wait_for((folder/'device-ready.json').exists,timeout=15)
        yield actor,profile,folder,device
    finally:
        (folder/'allow-claim').touch(exist_ok=True);(folder/'allow-result').touch(exist_ok=True)
        workers.processes.stop(device)
        for table in ('local_tool_events','local_tool_invocations','local_tool_devices','client_usage_logs'):
            service_database.rows(f'DELETE FROM {table} WHERE tenant_id=%s',(actor.tenant_id,))
        service_database.rows('DELETE FROM subscriptions WHERE subscription_id=%s',(subscription,))
        service_database.rows('DELETE FROM user_agent_permissions WHERE user_id=%s AND tenant_id=%s AND agent_id=%s',(actor.user_id,actor.tenant_id,profile))


def committed_local(row):
    state=decoded(row['checkpoint']).get('execution',{})
    refs=state.get('resources',{}).get('local_invocations',{})
    return next(iter(refs.values()),None)


def interrupted(workers,database,accepted,child,*,hard=False):
    workers.processes.stop(child,force=hard)
    if not hard:
        workers.assert_clean_exit(child)
    else:
        assert child.returncode == -9
    if hard:
        # Real loss of this owner's lease; new undecorated CLI reaps it. No
        # synthetic terminal/checkpoint facts or resume permission are injected.
        database.rows("UPDATE agent_runners SET lease_until=clock_timestamp()-INTERVAL '1 second' WHERE runner_id=%s",(accepted['runner_id'],))
        reaper,_=workers.start();workers.assert_clean_exit(reaper)
    try:
        return wait_for(lambda:(row if (row:=runner(database,accepted['runner_id']))['status']=='interrupted' else None),timeout=15)
    except AssertionError:
        raise AssertionError('Original worker did not park; safe diagnosis='+json.dumps(local_diagnosis(workers,database,accepted['runner_id']))) from None


def local_diagnosis(workers,database,identifier):
    """Keep only statuses, phases, linkage and exception locations, never args."""
    row=runner(database,identifier)
    state=(decoded(row['checkpoint']) or {}).get('execution') or {}
    observations={'runner_status':row['status'],'attempt':row['attempt'],'revision':row['revision'],
        'worker_bound':row['worker_id'] is not None,'lease_bound':row['lease_until'] is not None,
        'outcome':state.get('outcome'),'pending_ids':[call.get('id') for call in state.get('pending',[])],
        'tools':[{'call_id':key,'phase':value.get('phase'),'invocation_id':value.get('invocation_id'),
                  'result_recorded':value.get('result_recorded')} for key,value in state.get('tools',{}).items()],
        'controls':database.rows('SELECT status,error_code,consumed_attempt,consumed_at IS NOT NULL AS stamped FROM agent_runner_controls WHERE runner_id=%s',(identifier,)),
        'local_states':database.rows('SELECT state,count(*) AS n FROM local_tool_invocations WHERE session_id=%s GROUP BY state',(row['session_id'],)),
        'receipt_phases':database.rows('SELECT phase,count(*) AS n FROM agent_runner_usage_receipts WHERE runner_id=%s GROUP BY phase',(identifier,))}
    observations['cli']=[]
    for child,_ in workers.children:
        log=(workers.processes.root/f'child-{workers.processes.children.index(child)}.log').read_text(errors='replace')
        observations['cli'].append({'exit':child.poll(),'classes':re.findall(r'^([A-Za-z_.]+(?:Error|Exception|Failure)):',log,re.MULTILINE),
            'locations':re.findall(r'File "([^"]+)", line (\d+)',log)[-10:],
            'codes':sorted(set(re.findall(r'\b(?:RUNNER|RECOVERY|LOCAL|CHECKPOINT)_[A-Z0-9_]+\b',log)))})
    return observations


def original_count(database,actor):
    return len(database.rows('SELECT 1 FROM local_tool_invocations WHERE tenant_id=%s AND user_id=%s',(actor.tenant_id,actor.user_id)))


@pytest.mark.parametrize('hard', [False,True])
@pytest.mark.parametrize('new_phase', [False,True])
def test_original_device_terminal_result_after_worker_stop_is_reused_and_billed_once(
        workers,ordinary_local,service_database,hard,new_phase):
    actor,profile,folder,device=ordinary_local
    (folder/'allow-claim').touch()
    replies=[tool_reply('boss_select_job',{'job_name':'Fixture exact job'},call_id='original-local-call')]
    if new_phase: replies.append(tool_reply('boss_select_job',{'job_name':'Different exact job'},call_id='forbidden-new-local-call'))
    replies.append(Reply(content='same-invocation-recovered-result'))
    marker=workers.provider.register(*replies)
    accepted=accept(workers.api,actor,marker,profile_id=profile)
    first,_=workers.start()
    wait_for((folder/'device-claimed.json').exists,timeout=20)
    saved=wait_for(lambda:committed_local(runner(service_database,accepted['runner_id'])),timeout=10)
    original_id=json.loads((folder/'device-claimed.json').read_text())['invocation_id']
    assert saved['invocation_id']==original_id
    before=runner(service_database,accepted['runner_id'])
    stopped=interrupted(workers,service_database,accepted,first,hard=hard)
    physical=service_database.rows('SELECT state,deadline_at FROM local_tool_invocations WHERE id=%s',(original_id,))[0]
    assert physical['state']=='running' # internal shutdown never requests cancel
    assert committed_local(stopped)==saved
    (folder/'allow-result').touch();workers.assert_clean_exit(device)
    device_id=json.loads((folder/'device-ready.json').read_text())['device_id']
    # Reading the already committed physical result does not require this old
    # device to remain selected/active; a new phase still has a fresh gate.
    service_database.rows("UPDATE local_tool_devices SET status='revoked',selected=false WHERE id=%s",(device_id,))
    resumed,_=control(workers.api,actor,accepted['runner_id'],'resume')
    second,_=workers.start();workers.assert_clean_exit(second)
    finished=terminal(service_database,accepted['runner_id'])
    assert finished['status']=='completed'
    assert decoded(finished['result'])['output']=='same-invocation-recovered-result'
    assert finished['attempt']==before['attempt']+1 and original_count(service_database,actor)==1
    assert committed_local(finished)==saved
    invocation=service_database.rows('SELECT state,deadline_at,credit_cost FROM local_tool_invocations WHERE id=%s',(original_id,))[0]
    assert invocation=={'state':'succeeded','deadline_at':physical['deadline_at'],'credit_cost':Decimal('0.50')}
    expected_calls=3 if new_phase else 2
    assert len(workers.provider.requests(marker))==expected_calls and not workers.provider.errors
    if new_phase:
        observed=workers.provider.requests(marker)[-1]['messages']
        assert any(message.get('role')=='tool' and message.get('tool_call_id')=='forbidden-new-local-call'
                   and 'DEVICE_UNAVAILABLE' in message['content'] for message in observed)
    receipts=service_database.rows('SELECT phase,applied FROM agent_runner_usage_receipts WHERE runner_id=%s',(accepted['runner_id'],))
    assert receipts==[{'phase':'observed','applied':True}]*expected_calls
    logs=service_database.rows('SELECT credit_cost FROM client_usage_logs WHERE tenant_id=%s',(actor.tenant_id,))
    assert logs==[{'credit_cost':Decimal('0.50')}]
    assert service_database.rows('SELECT credit_cost FROM chat_records WHERE session_id=%s',(actor.session_id,))==[{'credit_cost':Decimal('0.01')}]
    assert service_database.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s',(actor.tenant_id,))[0]['credit_balance']==Decimal('999.49')
    state=decoded(finished['checkpoint'])['execution']
    assert state['tools']['original-local-call']['phase']=='completed'
    assert state['tools']['original-local-call']['invocation_id']==original_id
    status=workers.api.call('GET',f"/v1/runners/{accepted['runner_id']}/controls/{resumed['control']['control_id']}",actor=actor)
    assert status.status_code==200 and status.json()['control']['status']=='consumed'


@pytest.mark.parametrize('physical_state',['queued','running'])
def test_resume_keeps_original_inflight_invocation_and_deadline_without_enqueue(
        workers,ordinary_local,service_database,physical_state):
    actor,profile,folder,device=ordinary_local
    if physical_state=='running': (folder/'allow-claim').touch()
    marker=workers.provider.register(tool_reply('boss_select_job',{'job_name':'Fixture exact job'},call_id='inflight-local-call'),
                                     Reply(content='original-inflight-result'))
    accepted=accept(workers.api,actor,marker,profile_id=profile)
    first,_=workers.start()
    reference=wait_for(lambda:committed_local(runner(service_database,accepted['runner_id'])),timeout=20)
    original_id=reference['invocation_id']
    if physical_state=='running': wait_for((folder/'device-claimed.json').exists,timeout=15)
    physical=service_database.rows('SELECT state,deadline_at FROM local_tool_invocations WHERE id=%s',(original_id,))[0]
    assert physical['state']==physical_state and physical['deadline_at'] is not None
    stopped=interrupted(workers,service_database,accepted,first)
    assert service_database.rows('SELECT state FROM local_tool_invocations WHERE id=%s',(original_id,))[0]['state']==physical_state
    control(workers.api,actor,accepted['runner_id'],'resume')
    second,_=workers.start()
    wait_for(lambda:runner(service_database,accepted['runner_id'])['attempt']==stopped['attempt']+1,timeout=15)
    assert second.poll() is None
    assert original_count(service_database,actor)==1 and len(workers.provider.requests(marker))==1
    assert committed_local(runner(service_database,accepted['runner_id']))==reference
    assert service_database.rows('SELECT deadline_at FROM local_tool_invocations WHERE id=%s',(original_id,))[0]['deadline_at']==physical['deadline_at']
    (folder/'allow-claim').touch();wait_for((folder/'device-claimed.json').exists,timeout=15)
    assert json.loads((folder/'device-claimed.json').read_text())['invocation_id']==original_id
    (folder/'allow-result').touch();workers.assert_clean_exit(device);workers.assert_clean_exit(second)
    finished=terminal(service_database,accepted['runner_id'])
    assert finished['status']=='completed' and decoded(finished['result'])['output']=='original-inflight-result'
    assert original_count(service_database,actor)==1 and len(workers.provider.requests(marker))==2


@pytest.mark.parametrize('physical_state',['queued','running'])
def test_user_cancel_targets_original_owned_invocation_without_new_dispatch(
        workers,ordinary_local,service_database,physical_state):
    actor,profile,folder,device=ordinary_local
    if physical_state=='running': (folder/'allow-claim').touch()
    marker=workers.provider.register(tool_reply('boss_select_job',{'job_name':'Fixture exact job'},call_id='cancel-owned-local-call'))
    accepted=accept(workers.api,actor,marker,profile_id=profile)
    first,_=workers.start()
    reference=wait_for(lambda:committed_local(runner(service_database,accepted['runner_id'])),timeout=20)
    original_id=reference['invocation_id']
    if physical_state=='running': wait_for((folder/'device-claimed.json').exists,timeout=15)
    stopped=interrupted(workers,service_database,accepted,first)
    assert service_database.rows('SELECT state FROM local_tool_invocations WHERE id=%s',(original_id,))[0]['state']==physical_state
    response=workers.api.call('POST',f"/v1/runners/{accepted['runner_id']}/cancel",actor=actor)
    assert response.status_code==200
    sweeper,_=workers.start()
    if physical_state=='running':
        wait_for(lambda:service_database.rows('SELECT state FROM local_tool_invocations WHERE id=%s',(original_id,))[0]['state']=='cancel_requested',timeout=15)
        # The device effect is not stopped merely because cancellation was sent.
        # Hold its real terminal result and make another actual worker try FIFO.
        next_marker=workers.provider.register(Reply(content='later-after-local-cancel'))
        later=accept(workers.api,actor,next_marker,profile_id=profile)
        contender,_=workers.start();workers.assert_clean_exit(contender)
        # The cancellation owner has a bounded acknowledgement poll. Its
        # clean --once exit proves the blocked commit is now observable.
        workers.assert_clean_exit(sweeper)
        parked=runner(service_database,accepted['runner_id'])
        assert parked['status'] not in {'completed','failed','cancelled'},local_diagnosis(workers,service_database,accepted['runner_id'])
        blocked=decoded(parked['checkpoint'])['cancel_completion_blocked']
        assert parked['attempt']==stopped['attempt']+1 and blocked['attempt']==parked['attempt']
        assert blocked['ready'] is False and blocked['error_code']=='LOCAL_CANCEL_ACK_PENDING'
        assert service_database.rows('SELECT owner_runner_id FROM agent_runner_session_claims WHERE owner_runner_id=%s',(accepted['runner_id'],))==[{'owner_runner_id':accepted['runner_id']}]
        assert runner(service_database,later['runner_id'])['status']=='queued'
        assert workers.provider.requests(next_marker)==[]
        (folder/'allow-result').touch();workers.assert_clean_exit(device)
        # A new undecorated operator pass sees the late original acknowledgement.
        sweeper,_=workers.start()
    else:
        wait_for(lambda:service_database.rows('SELECT state FROM local_tool_invocations WHERE id=%s',(original_id,))[0]['state']=='cancelled',timeout=15)
        workers.processes.stop(device)
    workers.assert_clean_exit(sweeper)
    finished=terminal(service_database,accepted['runner_id'])
    expected_attempt=parked['attempt']+1 if physical_state=='running' else stopped['attempt']+1
    assert finished['status']=='cancelled' and finished['attempt']==expected_attempt
    assert original_count(service_database,actor)==1 and committed_local(finished)==reference
    assert len(workers.provider.requests(marker))==1 and not workers.provider.errors
    assert service_database.rows('SELECT 1 FROM agent_runner_session_claims WHERE owner_runner_id=%s',(accepted['runner_id'],))==[]
    assert service_database.rows('SELECT 1 FROM client_usage_logs WHERE tenant_id=%s',(actor.tenant_id,))==[]
    assert service_database.rows('SELECT credit_cost FROM chat_records WHERE session_id=%s',(actor.session_id,))==[{'credit_cost':Decimal('0.01')}]
    assert service_database.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s',(actor.tenant_id,))[0]['credit_balance']==Decimal('999.99')


@pytest.mark.parametrize('fault',['unknown','missing_binding','heal'])
def test_original_unknown_legacy_unbound_or_heal_phase_rejects_before_consumption_without_new_io(
        workers,ordinary_local,service_database,fault):
    actor,profile,folder,device=ordinary_local
    (folder/'allow-claim').touch()
    marker=workers.provider.register(tool_reply('boss_select_job',{'job_name':'Fixture exact job'},call_id='verification-local-call'))
    accepted=accept(workers.api,actor,marker,profile_id=profile)
    first,_=workers.start()
    wait_for((folder/'device-claimed.json').exists,timeout=20)
    original=wait_for(lambda:committed_local(runner(service_database,accepted['runner_id'])),timeout=10)
    stopped=interrupted(workers,service_database,accepted,first)
    if fault!='missing_binding': (folder/'result-mode').write_text(fault)
    (folder/'allow-result').touch();workers.assert_clean_exit(device)
    if fault=='missing_binding':
        # Explicit legacy checkpoint-contract fault: an old invocation id alone
        # has no complete execution/call/branch/ordinal proof. The original
        # physical result remains real; no success ToolFact is fabricated.
        cp=decoded(stopped['checkpoint'])
        cp['execution']['resources']['local_invocations']={}
        cp['execution']['tools']['verification-local-call']['invocation_id']=original['invocation_id']
        service_database.rows('UPDATE agent_runners SET checkpoint=%s::jsonb,revision=revision+1 WHERE runner_id=%s',
                              (json.dumps(cp),accepted['runner_id']))
    before=runner(service_database,accepted['runner_id'])
    claim_before=service_database.rows('SELECT * FROM agent_runner_session_claims WHERE owner_runner_id=%s',(accepted['runner_id'],))
    receipt_before=service_database.rows('SELECT receipt_id,phase FROM agent_runner_usage_receipts WHERE runner_id=%s',(accepted['runner_id'],))
    resumed,_=control(workers.api,actor,accepted['runner_id'],'resume')
    next_worker,_=workers.start();workers.assert_clean_exit(next_worker)
    response=workers.api.call('GET',f"/v1/runners/{accepted['runner_id']}/controls/{resumed['control']['control_id']}",actor=actor)
    assert response.status_code==200
    receipt=response.json()['control']
    assert receipt['status']=='rejected' and receipt['error_code']=='RECOVERY_TOOL_VERIFICATION_REQUIRED'
    after=runner(service_database,accepted['runner_id'])
    private=service_database.rows('SELECT consumed_attempt FROM agent_runner_controls WHERE control_id=%s',(receipt['control_id'],))[0]
    assert private['consumed_attempt'] is None, local_diagnosis(workers,service_database,accepted['runner_id'])
    # Existing control DTO uses this timestamp for rejected closure as well;
    # consumed_attempt, runner epoch and private facts prove no consumption.
    assert receipt['consumed_at'] is not None
    assert after['status']=='interrupted' and after['attempt']==before['attempt']
    assert decoded(after['checkpoint'])==decoded(before['checkpoint'])
    assert after['input']==before['input']
    assert service_database.rows('SELECT * FROM agent_runner_session_claims WHERE owner_runner_id=%s',(accepted['runner_id'],))==claim_before
    assert service_database.rows('SELECT receipt_id,phase FROM agent_runner_usage_receipts WHERE runner_id=%s',(accepted['runner_id'],))==receipt_before
    assert original_count(service_database,actor)==1 and len(workers.provider.requests(marker))==1
    assert service_database.rows('SELECT 1 FROM chat_records WHERE session_id=%s',(actor.session_id,))==[]


def test_actual_proxy_elapsed_deadline_parks_without_cancel_and_new_cli_resume_rejects_same_deadline(
        workers,ordinary_local,service_database):
    actor,profile,folder,_=ordinary_local
    marker=workers.provider.register(tool_reply('boss_select_job',{'job_name':'Fixture exact job'},call_id='elapsed-local-call'))
    accepted=accept(workers.api,actor,marker,profile_id=profile)
    # Only timeout configuration on the actual registered proxy instance changes;
    # real Worker/Runtime/Engine/Owner/PG/poll and the wall clock execute normally.
    from pathlib import Path
    probe=workers.processes.start([str(Path(__file__).with_name('local_deadline_probe.py'))],
        environment=workers.environment,private_working_directory=True)
    workers.assert_clean_exit(probe)
    parked=runner(service_database,accepted['runner_id'])
    assert parked['status']=='waiting'
    waiting=decoded(parked['public_snapshot'])['waiting']
    assert waiting['kind']=='verification' and waiting['error_code']=='LOCAL_DEADLINE_VERIFICATION_REQUIRED'
    reference=committed_local(parked)
    original_id=reference['invocation_id']
    physical=service_database.rows('SELECT state,deadline_at,clock_timestamp()>deadline_at AS expired FROM local_tool_invocations WHERE id=%s',(original_id,))[0]
    assert physical['state']=='queued' and physical['expired']
    assert original_count(service_database,actor)==1
    resumed,_=control(workers.api,actor,accepted['runner_id'],'resume')
    retry,_=workers.start();workers.assert_clean_exit(retry)
    receipt=workers.api.call('GET',f"/v1/runners/{accepted['runner_id']}/controls/{resumed['control']['control_id']}",actor=actor).json()['control']
    assert receipt['status']=='rejected' and receipt['error_code']=='LOCAL_DEADLINE_VERIFICATION_REQUIRED'
    assert receipt['consumed_at'] is not None
    assert service_database.rows('SELECT consumed_attempt FROM agent_runner_controls WHERE control_id=%s',(receipt['control_id'],))==[{'consumed_attempt':None}]
    after=runner(service_database,accepted['runner_id'])
    assert after['attempt']==parked['attempt'] and committed_local(after)==reference
    assert service_database.rows('SELECT state,deadline_at FROM local_tool_invocations WHERE id=%s',(original_id,))[0]=={
        'state':'queued','deadline_at':physical['deadline_at']}
    assert original_count(service_database,actor)==1 and len(workers.provider.requests(marker))==1


def test_unknown_device_cancel_ack_keeps_claim_and_is_not_hot_reclaimed(workers,ordinary_local,service_database):
    actor,profile,folder,device=ordinary_local
    (folder/'allow-claim').touch()
    marker=workers.provider.register(tool_reply('boss_select_job',{'job_name':'Fixture exact job'},call_id='unknown-cancel-call'))
    accepted=accept(workers.api,actor,marker,profile_id=profile)
    first,_=workers.start()
    wait_for((folder/'device-claimed.json').exists,timeout=20)
    reference=wait_for(lambda:committed_local(runner(service_database,accepted['runner_id'])),timeout=10)
    interrupted(workers,service_database,accepted,first)
    assert workers.api.call('POST',f"/v1/runners/{accepted['runner_id']}/cancel",actor=actor).status_code==200
    sweeper,_=workers.start();workers.assert_clean_exit(sweeper)
    original=reference['invocation_id']
    assert service_database.rows('SELECT state FROM local_tool_invocations WHERE id=%s',(original,))==[{'state':'cancel_requested'}]
    (folder/'result-mode').write_text('cancel_unknown')
    (folder/'allow-result').touch();workers.assert_clean_exit(device)
    assert service_database.rows('SELECT state,effect FROM local_tool_invocations WHERE id=%s',(original,))==[{'state':'unknown','effect':'unknown'}]
    before=runner(service_database,accepted['runner_id'])
    retry,_=workers.start();workers.assert_clean_exit(retry)
    after=runner(service_database,accepted['runner_id'])
    assert after['status'] not in {'completed','failed','cancelled'}
    assert after['attempt']==before['attempt'] and committed_local(after)==reference
    assert service_database.rows('SELECT owner_runner_id FROM agent_runner_session_claims WHERE owner_runner_id=%s',(accepted['runner_id'],))==[{'owner_runner_id':accepted['runner_id']}]
    assert original_count(service_database,actor)==1 and len(workers.provider.requests(marker))==1
    assert service_database.rows('SELECT 1 FROM chat_records WHERE session_id=%s',(actor.session_id,))==[]
