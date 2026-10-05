"""Legacy JSON presentation observes accepted work over real HTTP without owning it."""
from concurrent.futures import ThreadPoolExecutor
import json
import threading

import httpx
import pytest

from .test_web_gateway import api_pair, gateways, workers, prices, submission
from .test_worker import decoded, runner, terminal
from .test_api import require_status
from .conftest import wait_for
from .provider import Reply

pytestmark=pytest.mark.integration


def accepted_for(database, request):
    rows=database.rows("SELECT * FROM agent_runners WHERE session_id=%s AND client_request_id=%s",
                       (request["session_id"],request["client_request_id"]))
    return rows[0] if rows else None


def test_real_sync_json_waits_for_actual_worker_and_repeated_key_does_not_execute_again(gateways, actors, service_database):
    gateway=gateways.start()
    actor=actors["a"]
    marker=gateways.workers.provider.register(Reply(content="Original synchronous response"))
    request=submission(actor,message=marker,subagent="main")
    with ThreadPoolExecutor(max_workers=1) as observers:
        response=observers.submit(gateway.call,"POST","/api/chat/runners/sync",actor=actor,json=request)
        accepted=wait_for(lambda:accepted_for(service_database,request),timeout=10)
        child,_=gateways.workers.start();gateways.workers.assert_clean_exit(child)
        result=require_status(response.result(timeout=10),200)
    assert result=={"success":True,"response":"Original synchronous response","session_id":actor.session_id,
                    "agent_type":"master","runner_id":accepted["runner_id"],"status":"completed"}
    repeated=require_status(gateway.call("POST","/api/chat/runners/sync",actor=actor,json=request),200)
    assert repeated==result and len(gateways.workers.provider.requests(marker))==1


def test_sync_observer_connection_timeout_does_not_cancel_accepted_default_worker(gateways, actors, service_database):
    gateway=gateways.start()
    actor=actors["a"]
    release=threading.Event()
    reply=Reply(content="Completed after synchronous caller detached",release=release)
    marker=gateways.workers.provider.register(reply)
    request=submission(actor,message=marker)
    try:
        with pytest.raises(httpx.ReadTimeout):
            httpx.post(gateway.url+"/api/chat/runners/sync",headers=gateway.headers(actor),json=request,timeout=.8)
        accepted=wait_for(lambda:accepted_for(service_database,request),timeout=10)
        child,_=gateways.workers.start()
        assert reply.arrived.wait(timeout=15)
        row=runner(service_database,accepted["runner_id"])
        assert row["status"]=="running" and not row["cancel_requested"] and child.poll() is None
        release.set();gateways.workers.assert_clean_exit(child)
        finished=terminal(service_database,accepted["runner_id"])
        assert finished["status"]=="completed" and not finished["cancel_requested"]
        assert decoded(finished["result"])["output"]=="Completed after synchronous caller detached"
    finally:
        release.set()


def test_sync_queued_cancel_keeps_existing_json_contract_and_pending_input(gateways, actors, service_database):
    gateway=gateways.start()
    actor=actors["a"]
    request=submission(actor,message="cancelled synchronous pending input")
    with ThreadPoolExecutor(max_workers=1) as observers:
        response=observers.submit(gateway.call,"POST","/api/chat/runners/sync",actor=actor,json=request)
        accepted=wait_for(lambda:accepted_for(service_database,request),timeout=10)
        require_status(gateways.workers.api.call("POST","/v1/runners/"+accepted["runner_id"]+"/cancel",actor=actor),200)
        result=require_status(response.result(timeout=10),200)
    assert result=={"success":True,"response":"","session_id":actor.session_id,"agent_type":"master",
                   "runner_id":accepted["runner_id"],"status":"cancelled"}
    public=require_status(gateway.call("GET","/api/chat/runners/"+accepted["runner_id"],actor=actor),200)["runner"]
    assert public["snapshot"]["input"]["text"]==request["message"]
    assert service_database.rows("SELECT 1 FROM chat_records WHERE session_id=%s",(actor.session_id,))==[]


def test_real_main_opt_in_route_delegates_over_http_without_reexecuting_terminal_runner(gateways, actors, service_database):
    from .test_web_gateway import submit
    gateway=gateways.start()
    actor=actors["a"]
    marker=gateways.workers.provider.register(Reply(content="Actual main opt-in response"))
    request=submission(actor,message=marker,subagent="main")
    accepted=submit(gateway,actor,request)
    worker,_=gateways.workers.start();gateways.workers.assert_clean_exit(worker)
    incoming=gateways.workers.root/"main-opt-in-input.json"
    report=gateways.workers.root/"main-opt-in-report.json"
    incoming.write_text(json.dumps(request))
    environment={**gateways.workers.environment,"AGENT_RUNNER_WEB_ENABLED":"true",
                 "AGENT_RUNNER_API_URL":gateways.workers.api.urls[0],
                 "AGENT_RUNNER_WEB_SERVICE_ID":gateways.workers.api.service_id,
                 "AGENT_RUNNER_WEB_SERVICE_TOKEN":gateways.workers.api._service_token,
                 "RUNNER_TEST_OPAQUE_TOKEN":actor.token}
    child=gateways.workers.processes.start(["-m","tests.integration.agent_runner_service.main_route_probe",
                                          "--input",str(incoming),"--report",str(report)],
                                         environment=environment,private_working_directory=True)
    gateways.workers.children.append((child,"fixture-real-main-route"))
    gateways.workers.assert_clean_exit(child)
    result=json.loads(report.read_text())
    assert result=={"status":200,"body":{"success":True,"response":"Actual main opt-in response",
        "session_id":actor.session_id,"agent_type":"master","runner_id":accepted["runner_id"],"status":"completed"}}
    assert len(gateways.workers.provider.requests(marker))==1
