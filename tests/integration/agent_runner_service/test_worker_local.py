"""Default worker and original Runtime result/billing owner; no real desktop IO."""
from decimal import Decimal
import json
import uuid

import pytest

from .conftest import wait_for
from .provider import Reply, tool_reply
from .test_worker import workers, api_pair, prices, accept, runner, terminal, decoded

pytestmark = pytest.mark.integration


def test_real_worker_local_invocation_keeps_original_runtime_billing_owner_exactly_once(workers, actors, service_database):
    actor = actors["a"]
    subscription = "runner-local-subscription-" + uuid.uuid4().hex
    profile = "recruiting-operator"
    ready, release, proof = (workers.root / name for name in ("runtime-ready.json", "runtime-release", "runtime-result.json"))
    service_database.rows("INSERT INTO subscriptions(subscription_id,tenant_id,subagent_type,status,payment_status) VALUES (%s,%s,%s,'active','paid')",
                          (subscription,actor.tenant_id,profile))
    service_database.rows("INSERT INTO user_agent_permissions(user_id,tenant_id,agent_id) VALUES (%s,%s,%s)",
                          (actor.user_id,actor.tenant_id,profile))
    script = """
import json,secrets,sys,time
from pathlib import Path
from src.db.database import init_postgres_pool,close_postgres_pool
from src.local_tools import repository
from src.local_tools.security import sha256_hex
from src.local_tools.pricing import tool_credit_price
init_postgres_pool()
try:
    tenant,user=sys.argv[1:3]
    device=repository.create_device(tenant,user,sha256_hex(secrets.token_urlsafe(32)),name='Fictional test Runtime',
        platform='windows',capabilities={'provider_id':'ai.aidwork.boss-recruiting'})
    assert repository.select_device(tenant,user,str(device['id']))
    assert tool_credit_price('boss_select_job')==0.5
    Path(sys.argv[3]).write_text(json.dumps({'device_id':str(device['id'])}))
    claim_hash=sha256_hex(secrets.token_urlsafe(32))
    deadline=time.monotonic()+25
    invocation=None
    while time.monotonic()<deadline:
        invocation=repository.claim_next(str(device['id']),tenant,claim_hash,60)
        if invocation: break
        time.sleep(.05)
    assert invocation and invocation['tool_name']=='boss_select_job'
    identifier=str(invocation['id'])
    assert repository.mark_started(identifier,tenant,claim_hash)['state']=='running'
    repository.append_event(identifier,tenant,claim_hash,'fixture-progress',1,1,'Fictional Runtime progress',60)
    while not Path(sys.argv[4]).exists() and time.monotonic()<deadline:
        time.sleep(.05)
    assert Path(sys.argv[4]).exists()
    first=repository.write_result(identifier,tenant,claim_hash,True,message='Fictional Runtime result',effect='applied',data={'fixture':'done'})
    second=repository.write_result(identifier,tenant,claim_hash,True,message='Duplicate transport result',effect='applied',data={'fixture':'done'})
    assert first['state']==second['state']=='succeeded' and first['credit_cost']==second['credit_cost']==.5
    Path(sys.argv[5]).write_text(json.dumps({'invocation_id':identifier,'device_id':str(device['id'])}))
finally:
    close_postgres_pool()
"""
    runtime = workers.processes.start(["-c",script,actor.tenant_id,actor.user_id,str(ready),str(release),str(proof)],
                                      environment=workers.environment,private_working_directory=True)
    try:
        wait_for(ready.exists,timeout=15)
        marker = workers.provider.register(tool_reply("boss_select_job",{"job_name":"Fictional exact job"},call_id="original-runtime-call"),
                                            Reply(content="local-result-from-original-owner"))
        accepted = accept(workers.api,actor,marker,profile_id=profile)
        worker,_ = workers.start()
        invocation = wait_for(lambda: next(iter(service_database.rows("SELECT * FROM local_tool_invocations WHERE tenant_id=%s AND state='running'",(actor.tenant_id,))),None),timeout=20)
        # The actual local invocation ID is checkpointed before completion.
        wait_for(lambda: decoded(runner(service_database,accepted["runner_id"])["checkpoint"]).get("execution",{}).get("tools",{})
                 .get("original-runtime-call",{}).get("invocation_id")==str(invocation["id"]),timeout=10)
        observed = workers.api.call("GET","/v1/runners/"+accepted["runner_id"],actor=actor)
        assert observed.status_code==200 and worker.poll() is None
        observed.close()
        release.write_text("release-fictional-runtime")
        workers.assert_clean_exit(runtime)
        workers.assert_clean_exit(worker)
        finished = terminal(service_database,accepted["runner_id"])
        assert finished["status"]=="completed" and finished["settlement_status"]=="settled"
        original = json.loads(proof.read_text())
        invocation = service_database.rows("SELECT tenant_id,user_id,session_id,credit_cost,state FROM local_tool_invocations WHERE id=%s",(original["invocation_id"],))[0]
        assert invocation=={"tenant_id":actor.tenant_id,"user_id":actor.user_id,"session_id":actor.session_id,
                            "credit_cost":Decimal("0.50"),"state":"succeeded"}
        local_logs=service_database.rows("SELECT credit_cost,session_id,detail FROM client_usage_logs WHERE tenant_id=%s",(actor.tenant_id,))
        assert len(local_logs)==1 and local_logs[0]["credit_cost"]==Decimal("0.50") and local_logs[0]["session_id"]==actor.session_id
        assert decoded(local_logs[0]["detail"])["invocation_id"]==original["invocation_id"]
        records=service_database.rows("SELECT credit_cost FROM chat_records WHERE session_id=%s",(actor.session_id,))
        assert records==[{"credit_cost":Decimal("0.01")}]
        receipts=service_database.rows("SELECT owner,phase,applied FROM agent_runner_usage_receipts WHERE runner_id=%s",(accepted["runner_id"],))
        assert receipts==[{"owner":"llm","phase":"observed","applied":True}]*2
        assert service_database.rows("SELECT credit_balance FROM tenants WHERE tenant_id=%s",(actor.tenant_id,))[0]["credit_balance"]==Decimal("999.49")
        assert len(workers.provider.requests(marker))==2 and not workers.provider.errors
        tool_results=[item for item in workers.provider.requests(marker)[1]["messages"] if item.get("role")=="tool"]
        assert any(original["invocation_id"] in item["content"] and "done" in item["content"] for item in tool_results)
    finally:
        release.touch(exist_ok=True)
        workers.processes.stop(runtime)
        for table in ("local_tool_events","local_tool_invocations","local_tool_devices","client_usage_logs"):
            service_database.rows(f"DELETE FROM {table} WHERE tenant_id=%s",(actor.tenant_id,))
        service_database.rows("DELETE FROM subscriptions WHERE subscription_id=%s",(subscription,))
        service_database.rows("DELETE FROM user_agent_permissions WHERE user_id=%s AND tenant_id=%s AND agent_id=%s",(actor.user_id,actor.tenant_id,profile))
