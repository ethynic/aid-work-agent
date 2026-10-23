"""Real Worker/gateway acceptance for local receipt and dialogue recovery.

Only the model peer and physical desktop IO are fictional.
"""
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import pytest
from .test_storage import storage
from .test_local_wait_recovery import lost_local
from .test_worker import workers, prices, terminal, accept, runner
from .test_web_gateway import api_pair, gateways, submission
from .provider import Reply
from .provider import tool_reply
from .test_worker_recovery import control
from . import test_local_invocation_recovery as original_flow
from .test_local_invocation_recovery import ordinary_local
from src.local_tools import repository as devices
from src.services.agent_runner.local_wait import end_waiting

pytestmark = pytest.mark.integration

def test_real_public_end_waiting_cancel_worker_and_fresh_conversation(lost_local, workers, gateways, actors, service_database):
    repository, _, principal, row, device, invocation, claim = lost_local
    gateway = gateways.start()
    path = '/api/chat/runners/' + row['runner_id'] + '/end-waiting'
    assert gateway.call('POST', path, json={}).status_code == 401
    assert gateway.call('POST', path, actor=actors['b'], json={}).status_code in {403,404}
    assert gateway.call('POST', path, actor=actors['a_other'], json={}).status_code in {403,404}
    assert gateway.call('POST', path, actor=actors['a'], json={'detached_local_wait':True}).status_code == 422
    assert not repository.get(row['runner_id'])['checkpoint'].get('detached_local_wait')
    service_database.rows("UPDATE agent_runners SET status='running',worker_id='fixture-active-worker',lease_until=clock_timestamp()+INTERVAL '60 seconds' WHERE runner_id=%s",(row['runner_id'],))
    assert gateway.call('POST',path,actor=actors['a'],json={}).status_code==409
    service_database.rows("UPDATE agent_runners SET status='interrupted',worker_id=NULL,lease_until=NULL WHERE runner_id=%s",(row['runner_id'],))
    accepted = gateway.call('POST', path, actor=actors['a'], json={})
    assert accepted.status_code == 200
    cancellation,_ = workers.start()
    finished = terminal(service_database,row['runner_id'])
    workers.assert_clean_exit(cancellation)
    assert finished['status']=='cancelled'
    assert service_database.rows('SELECT 1 FROM agent_runner_session_claims WHERE owner_runner_id=%s',(row['runner_id'],)) == []
    assert devices.get_invocation(invocation,row['tenant_id'])['state']=='unknown'
    marker=workers.provider.register(Reply(content='new-chat-after-explicit-end'))
    response=gateway.call('POST','/api/chat/runners',actor=actors['a'],json=submission(actors['a'],message=marker))
    assert response.status_code==202
    new_id=response.json()['runner']['runner_id']
    child,_=workers.start()
    assert terminal(service_database,new_id)['status']=='completed'
    workers.assert_clean_exit(child)
    assert runner(service_database,new_id)['result']['output']=='new-chat-after-explicit-end'
    with pytest.raises(devices.DeviceOperationUnresolved):
        devices.create_invocation(row['tenant_id'],row['user_id'],str(device['id']),'boss_goto',{'target':'recommend'})
    internal='/v1/runners/'+row['runner_id']+'/end-waiting'
    assert workers.api.call('POST',internal,actor=actors['a'],json={'force':True}).status_code==422
    assert workers.api.call('POST',internal,actor=actors['a'],json={}).status_code==200

def test_guard_same_machine_concurrent_create_and_unknown_remain_blocked(lost_local,service_database,actors):
    repository, _, principal, row, device, invocation, claim = lost_local
    fingerprint=service_database.rows('SELECT machine_fingerprint_hash FROM local_tool_devices WHERE id=%s',(device['id'],))[0]['machine_fingerprint_hash']
    sibling=devices.create_device(row['tenant_id'],row['user_id'],uuid.uuid4().hex,machine_fingerprint_hash=fingerprint)
    other=devices.create_device(row['tenant_id'],actors['a_other'].user_id,uuid.uuid4().hex,machine_fingerprint_hash=fingerprint)
    queued=devices.create_invocation(row['tenant_id'],row['user_id'],str(sibling['id']),'boss_goto',{'target':'recommend'})
    end_waiting(repository,principal,row['runner_id'])
    with pytest.raises(devices.DeviceOperationUnresolved):
        devices.claim_next(str(sibling['id']),row['tenant_id'],'preexisting-queued-claim',60,expected_invocation_id=queued)
    def create(target):
        try:
            devices.create_invocation(row['tenant_id'],row['user_id'],str(target),'boss_goto',{'target':'recommend'})
            return 'unexpected-admission'
        except devices.DeviceOperationUnresolved:
            return 'blocked'
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(create,[device['id'],sibling['id']]))==['blocked','blocked']
    assert devices.create_invocation(row['tenant_id'],actors['a_other'].user_id,str(other['id']),'boss_goto',{'target':'recommend'})
    assert devices.write_result(invocation,row['tenant_id'],'wrong-claim',True,effect='none') is None
    assert devices.write_result(invocation,row['tenant_id'],claim,True,effect='none',device_id=str(sibling['id'])) is None
    devices.write_result(invocation,row['tenant_id'],claim,False,code='CANCELLED',effect='none',device_id=str(device['id']))
    assert create(device['id'])==create(sibling['id'])=='blocked'

def test_late_paid_result_two_writers_one_charge_and_original_unknown(lost_local,service_database):
    _,_,_,row,device,invocation,claim=lost_local
    service_database.rows("UPDATE local_tool_invocations SET tool_name='boss_select_job' WHERE id=%s",(invocation,))
    before=service_database.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s',(row['tenant_id'],))[0]['credit_balance']
    def write(_):
        return devices.write_result(invocation,row['tenant_id'],claim,True,effect='applied',device_id=str(device['id']))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(write,range(2)))
    assert all(result['state']=='unknown' and result['effect']=='unknown' for result in results)
    assert service_database.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s',(row['tenant_id'],))[0]['credit_balance']==before-Decimal('0.50')
    assert len(service_database.rows('SELECT 1 FROM client_usage_logs WHERE tenant_id=%s',(row['tenant_id'],)))==1

def test_late_billing_sql_rollback_keeps_receipt_without_partial_debit(lost_local,service_database):
    _,_,_,row,device,invocation,claim=lost_local
    service_database.rows("UPDATE local_tool_invocations SET tool_name='boss_select_job' WHERE id=%s",(invocation,))
    before=service_database.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s',(row['tenant_id'],))[0]['credit_balance']
    service_database.rows("CREATE FUNCTION aid_test_fail_bill() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'fixture billing unavailable'; END $$")
    service_database.rows('CREATE TRIGGER aid_test_fail_bill BEFORE INSERT ON client_usage_logs FOR EACH ROW EXECUTE FUNCTION aid_test_fail_bill()')
    try:
        result=devices.write_result(invocation,row['tenant_id'],claim,True,effect='applied',device_id=str(device['id']))
        assert result['state']=='unknown' and result['result_json']['_late_result']['stopped'] is True
        assert result['credit_cost'] is None
        assert service_database.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s',(row['tenant_id'],))[0]['credit_balance']==before
        assert not service_database.rows('SELECT 1 FROM client_usage_logs WHERE tenant_id=%s',(row['tenant_id'],))
    finally:
        service_database.rows('DROP TRIGGER aid_test_fail_bill ON client_usage_logs')
        service_database.rows('DROP FUNCTION aid_test_fail_bill()')

@pytest.fixture
def late_device(request,monkeypatch):
    # Only the fictional external device gains a lost-lease before its receipt.
    script=original_flow._DEVICE.replace("state=repository.get_invocation(identifier,tenant)['state']",
        "with repository.get_db_connection() as conn:\n        cursor=conn.cursor(); cursor.execute(\"UPDATE local_tool_invocations SET lease_expires_at=NOW()-INTERVAL '1 second' WHERE id=%s\",(identifier,)); conn.commit()\n    repository.expire_stale_claims()\n    state=repository.get_invocation(identifier,tenant)['state']")
    monkeypatch.setattr(original_flow,'_DEVICE',script)
    return request.getfixturevalue('ordinary_local')

def test_actual_new_worker_consumes_late_original_receipt_without_dispatch(workers,late_device,service_database):
    import json
    from .conftest import wait_for
    actor,profile,folder,device=late_device
    (folder/'allow-claim').touch()
    marker=workers.provider.register(tool_reply('boss_select_job',{'job_name':'Fixture exact job'},call_id='late-original-call'),
        Reply(content='late-original-actual-worker-finished'))
    accepted=accept(workers.api,actor,marker,profile_id=profile)
    first,_=workers.start()
    wait_for((folder/'device-claimed.json').exists,timeout=20)
    original_id=json.loads((folder/'device-claimed.json').read_text())['invocation_id']
    original_flow.interrupted(workers,service_database,accepted,first)
    (folder/'allow-result').touch()
    workers.assert_clean_exit(device)
    stored=devices.get_invocation(original_id,actor.tenant_id)
    assert stored['state']==stored['effect']=='unknown'
    assert stored['result_json']['_late_result']['success'] is True
    control(workers.api,actor,accepted['runner_id'],'resume')
    second,_=workers.start()
    workers.assert_clean_exit(second)
    finished=terminal(service_database,accepted['runner_id'])
    assert finished['status']=='completed' and finished['result']['output']=='late-original-actual-worker-finished'
    assert original_flow.original_count(service_database,actor)==1
    assert len(workers.provider.requests(marker))==2 and not workers.provider.errors
    assert len(service_database.rows('SELECT 1 FROM client_usage_logs WHERE tenant_id=%s',(actor.tenant_id,)))==1


def test_actual_normal_cancel_blocked_unknown_then_explicit_end_can_finalize(lost_local,workers,gateways,actors,service_database):
    repository,_,_,row,device,invocation,_=lost_local
    gateway=gateways.start()
    base='/api/chat/runners/'+row['runner_id']
    assert gateway.call('POST',base+'/cancel',actor=actors['a'],json={}).status_code==200
    child,_=workers.start()
    workers.assert_clean_exit(child)
    blocked=repository.get(row['runner_id'])
    assert blocked['status']=='waiting' and blocked['cancel_requested']
    assert not blocked['checkpoint'].get('detached_local_wait')
    visible=gateway.call('GET',base,actor=actors['a']).json()['runner']
    assert visible['local_recovery'] is True
    assert gateway.call('POST',base+'/end-waiting',actor=actors['a'],json={}).status_code==200
    finisher,_=workers.start()
    assert terminal(service_database,row['runner_id'])['status']=='cancelled'
    workers.assert_clean_exit(finisher)
    assert service_database.rows('SELECT 1 FROM agent_runner_session_claims WHERE owner_runner_id=%s',(row['runner_id'],))==[]
    with pytest.raises(devices.DeviceOperationUnresolved):
        devices.create_invocation(row['tenant_id'],row['user_id'],str(device['id']),'boss_goto',{'target':'recommend'})
