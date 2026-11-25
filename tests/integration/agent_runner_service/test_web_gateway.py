"""Real Web gateway/API/worker acceptance with isolated PG and local provider IO.

No auth, core runtime or durable repository is replaced.
"""

import base64
from datetime import datetime,timedelta,timezone
from decimal import Decimal
import json
import socket
import threading
import uuid

import httpx
import pytest

from .conftest import wait_for
from .test_api import start_api_pair, require_status
from .test_worker import workers, prices, decoded, runner, terminal
from .provider import Reply
from .web_gateway import WebGateway

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def api_pair(service_processes):
    # Explicitly configure this existing internal peer as the Web bridge. The
    # default-single policy must be tested with real trusted service auth, never
    # bypassed or enabled for a different/channel bridge.
    return start_api_pair(service_processes, extra_environment={
        "AGENT_RUNNER_WEB_SERVICE_ID":"runner-test", "REDIS_ENABLED":"false"})


class GatewayFleet:
    def __init__(self, worker_fleet):
        self.workers = worker_fleet
        self.children = []
        self.reserved = []

    def start(self, *, enabled=True, api_url=None, factory="src.api.agent_runner_web:create_app", extra_environment=None):
        environment = {**self.workers.environment,
            "AGENT_RUNNER_WEB_ENABLED":"true" if enabled else "false",
            "AGENT_RUNNER_API_URL":api_url or self.workers.api.urls[0],
            "AGENT_RUNNER_WEB_SERVICE_ID":self.workers.api.service_id,
            "AGENT_RUNNER_WEB_SERVICE_TOKEN":self.workers.api._service_token,
            **(extra_environment or {})}
        gateway = WebGateway(self.workers.processes, environment=environment,
                             factory=factory)
        self.children.append(gateway)
        return gateway

    def unreachable_service_url(self):
        # Keep a port reserved without listening. No other fixture may start an
        # accidental server at the purportedly unavailable service address.
        reserved = socket.socket()
        reserved.bind(("127.0.0.1",0))
        self.reserved.append(reserved)
        return f"http://127.0.0.1:{reserved.getsockname()[1]}"

    def close(self):
        for gateway in reversed(self.children):
            gateway.close()
        for reserved in self.reserved:
            reserved.close()


@pytest.fixture
def gateways(workers):
    fleet = GatewayFleet(workers)
    try:
        yield fleet
    finally:
        fleet.close()


def submission(actor, *, key=None, message="Web accepted input", **extra):
    return {"client_request_id":key or uuid.uuid4().hex,"message":message,"session_id":actor.session_id,**extra}


def submit(gateway, actor, request):
    return require_status(gateway.call("POST","/api/chat/runners",actor=actor,json=request),202)["runner"]


class LegacyPageGateway(WebGateway):
    """The real src.main application as the old already-opened page sees it.

    Only the readiness window differs: the legacy application boot (agent
    registries, database pools) is slower than the thin Web gateway factory.
    """

    def __init__(self, processes, *, environment):
        self.processes = processes
        with socket.socket() as reserve:
            reserve.bind(("127.0.0.1", 0))
            self.port = reserve.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        arguments = ["-m", "uvicorn", "tests.integration.agent_runner_service.legacy_page_app:create_app",
                     "--factory", "--host", "127.0.0.1", "--port", str(self.port), "--no-access-log"]
        self.child = processes.start(arguments, environment=environment, private_working_directory=True)
        try:
            wait_for(self._ready, timeout=90)
        except BaseException:
            self.close()
            raise

    def stream(self, method, path, *, actor=None, payload=None):
        client = httpx.Client(timeout=httpx.Timeout(connect=10, pool=10, write=30, read=180))

        class _LegacyStream:
            def __init__(self, response):
                self.response = response

            def __enter__(self):
                return self.response

            def __exit__(self, *_):
                client.close()

        request = client.build_request(method, self.url + path,
                                       headers=self.headers(actor), json=payload)
        response = client.send(request, stream=True)
        if response.status_code != 200:
            response.read()
            client.close()
        return _LegacyStream(response)


def iter_legacy_sse(response):
    """Yield parsed legacy ``data: {...}`` frames from one SSE response."""
    buffer = ""
    for chunk in response.iter_text():
        buffer += chunk
        while "\n\n" in buffer:
            block, buffer = buffer.split("\n\n", 1)
            for line in block.split("\n"):
                if line.startswith("data: "):
                    try:
                        yield json.loads(line[6:])
                    except ValueError:
                        continue


@pytest.fixture
def legacy_page(workers):
    started = []

    def start(*, api_url=None):
        environment = {**workers.environment,
            "AGENT_RUNNER_WEB_ENABLED": "true",
            "AGENT_RUNNER_API_URL": api_url or workers.api.urls[0],
            "AGENT_RUNNER_WEB_SERVICE_ID": workers.api.service_id,
            "AGENT_RUNNER_WEB_SERVICE_TOKEN": workers.api._service_token}
        gateway = LegacyPageGateway(workers.processes, environment=environment)
        started.append(gateway)
        return gateway

    def reserve_unreachable():
        reserved = socket.socket()
        reserved.bind(("127.0.0.1", 0))
        started.append(reserved)
        return f"http://127.0.0.1:{reserved.getsockname()[1]}"

    start.unreachable = reserve_unreachable
    try:
        yield start
    finally:
        for gateway in reversed(started):
            gateway.close()


def instant(value):
    parsed=datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def test_web_capabilities_is_fresh_authenticated_local_config_not_service_reachability(gateways, actors, service_database):
    gateway=gateways.start(api_url=gateways.unreachable_service_url())
    require_status(gateway.call("GET","/api/chat/runners/capabilities"),401)
    response=require_status(gateway.call("GET","/api/chat/runners/capabilities",actor=actors["a"]),200)
    assert response=={"web_enabled":True,"contract_version":1,"transport":"runner_poll","observe_existing":True,"events_supported":True}
    failure=require_status(gateway.call("POST","/api/chat/runners",actor=actors["a"],json=submission(actors["a"])),503)
    assert failure["code"]=="RUNNER_SERVICE_UNAVAILABLE"
    assert service_database.rows("SELECT 1 FROM agent_runners WHERE user_id=%s",(actors["a"].user_id,))==[]
    assert not gateways.workers.provider.errors


@pytest.mark.parametrize("mutation",["expired","revoked","inactive"])
def test_web_capabilities_rechecks_current_sql_identity_after_successful_cached_auth(gateways, actors, service_database, mutation):
    gateway=gateways.start()
    actor=actors["a"]
    require_status(gateway.call("GET","/api/chat/runners/capabilities",actor=actor),200)
    if mutation=="expired":
        service_database.rows("UPDATE tokens SET expires_at=%s WHERE token=%s",(datetime.now()-timedelta(seconds=1),actor.token))
    elif mutation=="revoked":
        service_database.rows("DELETE FROM tokens WHERE token=%s",(actor.token,))
    else:
        service_database.rows("UPDATE users SET status='inactive' WHERE user_id=%s",(actor.user_id,))
    require_status(gateway.call("GET","/api/chat/runners/capabilities",actor=actor),401)


def test_web_public_auth_headers_cannot_replace_bridge_or_actor_and_private_data_is_not_returned(gateways, actors, service_database):
    gateway=gateways.start()
    actor=actors["a"]
    encoded=base64.b64encode(b"fictional-private-Web-file").decode()
    request=submission(actor,files=[{"name":"Web-upload.txt","content":encoded,"size":26}])
    headers={**gateway.headers(actor),"X-AgentRunner-Service":"spoofed-channel-bridge",
             "X-AgentRunner-Service-Token":"fictional-untrusted-service-token",
             "X-AgentRunner-Source":"feishu","X-AgentRunner-Channel-User":"spoofed-actor",
             "X-AgentRunner-Accept-New":"false"}
    accepted=require_status(gateway.call("POST","/api/chat/runners",headers=headers,json=request),202)["runner"]
    persisted=runner(service_database,accepted["runner_id"])
    assert persisted["tenant_id"]==actor.tenant_id and persisted["user_id"]==actor.user_id and persisted["source"]=="chat"
    assert persisted["service_id"]==gateways.workers.api.service_id
    private=decoded(persisted["input"])
    assert private["attachments"][0]["content"]==encoded
    assert accepted["snapshot"]["input"]["message_id"]==accepted["runner_id"]+":user"
    assert "content" not in accepted["snapshot"]["input"]["attachments"][0]
    public=json.dumps(accepted)
    assert encoded not in public and gateways.workers.api._service_token not in public
    assert all(key not in accepted for key in ("checkpoint","profile_fingerprint","input","request_data","prompt_augmentations"))


def test_web_lost_accepted_response_retry_survives_default_profile_change_and_submit_switch_off(gateways, actors, service_database):
    actor=actors["a"]
    subscription="web-default-"+uuid.uuid4().hex
    profile="ai-lead-gen"
    service_database.rows("INSERT INTO subscriptions(subscription_id,tenant_id,subagent_type,status,payment_status) VALUES (%s,%s,%s,'active','paid')",(subscription,actor.tenant_id,profile))
    service_database.rows("INSERT INTO user_agent_permissions(user_id,tenant_id,agent_id) VALUES (%s,%s,%s)",(actor.user_id,actor.tenant_id,profile))
    try:
        enabled=gateways.start()
        request=submission(actor,message="Original raw request frozen before routing change")
        # Discard the successful response without retaining its runner ID, as a
        # caller whose response is lost would. Query durable acceptance by key.
        response=enabled.call("POST","/api/chat/runners",actor=actor,json=request)
        assert response.status_code==202
        response.close()
        existing=service_database.rows("SELECT * FROM agent_runners WHERE client_request_id=%s AND user_id=%s",(request["client_request_id"],actor.user_id))[0]
        assert existing["profile_id"]==profile
        assert decoded(existing["input"])["profile_id"]=="main"
        assert decoded(existing["input"])["routing_policy"]=="default_single"
        service_database.rows("UPDATE subscriptions SET status='cancelled' WHERE subscription_id=%s",(subscription,))
        # Default routing is now different and executing the original profile
        # is no longer licensed. Acceptance retry still returns its old owner.
        retried=submit(enabled,actor,request)
        assert retried["runner_id"]==existing["runner_id"]
        disabled=gateways.start(enabled=False)
        retried_disabled=submit(disabled,actor,request)
        assert retried_disabled["runner_id"]==existing["runner_id"]
        unchanged=runner(service_database,existing["runner_id"])
        assert unchanged["profile_id"]==profile and unchanged["profile_fingerprint"]==existing["profile_fingerprint"]
        assert decoded(unchanged["input"])==decoded(existing["input"]) and unchanged["input_digest"]==existing["input_digest"]
        different={**request,"message":"Different raw intent using the same request key"}
        conflict=require_status(disabled.call("POST","/api/chat/runners",actor=actor,json=different),409)
        assert conflict["code"]=="IDEMPOTENCY_INPUT_MISMATCH"
        refused=require_status(disabled.call("POST","/api/chat/runners",actor=actor,json=submission(actor)),503)
        assert refused["code"]=="WEB_SUBMISSIONS_DISABLED"
        assert len(service_database.rows("SELECT 1 FROM agent_runners WHERE user_id=%s",(actor.user_id,)))==1
        assert service_database.rows("SELECT 1 FROM chat_messages WHERE session_id=%s",(actor.session_id,))==[]
    finally:
        service_database.rows("DELETE FROM subscriptions WHERE subscription_id=%s",(subscription,))
        service_database.rows("DELETE FROM user_agent_permissions WHERE user_id=%s AND tenant_id=%s AND agent_id=%s",(actor.user_id,actor.tenant_id,profile))


def test_disabled_web_gateway_can_recover_and_cancel_queued_input_after_credit_is_exhausted(gateways, actors, service_database):
    actor=actors["a"]
    enabled=gateways.start()
    accepted=submit(enabled,actor,submission(actor,message="Queued input survives page reload and cancellation"))
    disabled=gateways.start(enabled=False)
    service_database.rows("UPDATE tenants SET credit_balance=0 WHERE tenant_id=%s",(actor.tenant_id,))
    require_status(disabled.call("GET","/api/chat/runners/"+accepted["runner_id"],actor=actor),200)
    cancelled=require_status(disabled.call("POST","/api/chat/runners/"+accepted["runner_id"]+"/cancel",actor=actor),200)["runner"]
    assert cancelled["status"]=="cancelled" and cancelled["snapshot"]["input"]["text"]=="Queued input survives page reload and cancellation"
    restored=require_status(disabled.call("GET","/api/chat/sessions/"+actor.session_id+"/runners",actor=actor),200)
    assert [row["runner_id"] for row in restored["runners"]]==[accepted["runner_id"]]
    assert restored["runners"][0]["snapshot"]["input"]["message_id"]==accepted["runner_id"]+":user"
    history=require_status(disabled.call("GET","/api/sessions/"+actor.session_id+"/messages",actor=actor),200)
    assert history["messages"]==[]
    assert service_database.rows("SELECT 1 FROM chat_records WHERE session_id=%s",(actor.session_id,))==[]


def test_web_preserves_existing_no_credit_error_without_accepting_or_executing(gateways, actors, service_database):
    gateway=gateways.start()
    actor=actors["a"]
    service_database.rows("UPDATE tenants SET credit_balance=0 WHERE tenant_id=%s",(actor.tenant_id,))
    result=require_status(gateway.call("POST","/api/chat/runners",actor=actor,json=submission(actor)),403)
    assert result["code"]=="NO_CREDIT" and "积分余额" in result["error"]
    assert service_database.rows("SELECT 1 FROM agent_runners WHERE user_id=%s",(actor.user_id,))==[]


@pytest.mark.parametrize("caller",["a_other","b","global_other"])
def test_web_gateway_uses_current_session_owner_for_submit_read_list_and_cancel(gateways, actors, caller):
    gateway=gateways.start()
    owner=actors["global"] if caller=="global_other" else actors["a"]
    accepted=submit(gateway,owner,submission(owner))
    foreign=actors[caller]
    for method,path in (("GET","/api/chat/runners/"+accepted["runner_id"]),
                        ("POST","/api/chat/runners/"+accepted["runner_id"]+"/cancel"),
                        ("GET","/api/chat/sessions/"+owner.session_id+"/runners")):
        require_status(gateway.call(method,path,actor=foreign),404)
    require_status(gateway.call("POST","/api/chat/runners",actor=foreign,json=submission(owner)),404)
    require_status(gateway.call("GET","/api/chat/runners/"+accepted["runner_id"],actor=owner),200)


def test_real_web_gateway_refresh_close_and_queued_turns_keep_display_time_and_model_history_order(gateways, actors, service_database):
    actor=actors["a"]
    gateway=gateways.start()
    release=threading.Event()
    first_reply=Reply(content="Web-first-assistant",release=release)
    first_marker=gateways.workers.provider.register(first_reply)
    second_marker=gateways.workers.provider.register(Reply(content="Web-second-assistant"))
    third_marker=gateways.workers.provider.register(Reply(content="Web-third-assistant"))
    first=submit(gateway,actor,submission(actor,message=first_marker))
    worker,_=gateways.workers.start()
    try:
        assert first_reply.arrived.wait(timeout=15)
        second=submit(gateway,actor,submission(actor,message=second_marker))
        before_history=require_status(gateway.call("GET","/api/sessions/"+actor.session_id+"/messages",actor=actor),200)
        before_list=require_status(gateway.call("GET","/api/chat/sessions/"+actor.session_id+"/runners",actor=actor),200)
        assert before_history["messages"]==[]
        assert {item["runner_id"] for item in before_list["runners"]}=={first["runner_id"],second["runner_id"]}
        assert {item["runner_id"] for item in before_list["active_runners"]}=={first["runner_id"]}
        assert second_marker not in json.dumps(gateways.workers.provider.requests(first_marker)[0]["messages"])
        # Close only the owned Web gateway process while its worker is executing.
        gateway.close()
        assert worker.poll() is None
        release.set()
        gateways.workers.assert_clean_exit(worker)
        next_worker,_=gateways.workers.start()
        gateways.workers.assert_clean_exit(next_worker)
        reopened=gateways.start()
        history=require_status(reopened.call("GET","/api/sessions/"+actor.session_id+"/messages",actor=actor),200)["messages"]
        assert [(row["role"],row["content"]) for row in history]==[
            ("user",first_marker),("assistant","Web-first-assistant"),
            ("user",second_marker),("assistant","Web-second-assistant")]
        assert {row["metadata"]["runner_id"] for row in history}=={first["runner_id"],second["runner_id"]}
        assert [row["message_id"] for row in history if row["role"]=="user"]==[first["runner_id"]+":user",second["runner_id"]+":user"]
        # The accepted second input precedes the first assistant's final commit,
        # but its physical created_at follows that complete first turn.
        assert instant(second["accepted_at"]) < instant(history[1]["created_at"])
        assert instant(history[1]["created_at"]) < instant(history[2]["created_at"])
        assert instant(history[0]["metadata"]["accepted_at"])==instant(first["accepted_at"])
        assert instant(history[2]["metadata"]["accepted_at"])==instant(second["accepted_at"])
        recent=require_status(reopened.call("GET","/api/chat/sessions/"+actor.session_id+"/runners",actor=actor),200)
        assert {item["runner_id"] for item in recent["runners"]}=={first["runner_id"],second["runner_id"]} and recent["active_runners"]==[]
        third=submit(reopened,actor,submission(actor,message=third_marker))
        final_worker,_=gateways.workers.start()
        gateways.workers.assert_clean_exit(final_worker)
        assert terminal(service_database,third["runner_id"])["status"]=="completed"
        context=gateways.workers.provider.requests(third_marker)[0]["messages"]
        turns=[(item["role"],item["content"]) for item in context if item["role"]!="system"]
        assert turns[:-1]==[
            ("user",first_marker),("assistant","Web-first-assistant"),
            ("user",second_marker),("assistant","Web-second-assistant")]
        assert turns[-1][0]=="user"
        assert turns[-1][1].startswith("[当前时间: ") and turns[-1][1].endswith("\n\n"+third_marker)
        assert service_database.rows("SELECT credit_balance FROM tenants WHERE tenant_id=%s",(actor.tenant_id,))[0]["credit_balance"]==Decimal("999.97")
    finally:
        release.set()

# Each item is an observable user/permission boundary, not an implementation
# method to mirror. Transport and backend owner evidence remain separate.
BACKEND_ACCEPTANCE_BOUNDARIES = (
    "Opaque-token submit is durably accepted; server peer credentials never appear in public output",
    "Submit response lost then same idempotency key repeats exactly one runner; changed intent conflicts",
    "Same raw Web intent still finds original runner after default-profile permission/config changes",
    "Web feature switch chooses one path; service unavailable cannot run an old fallback loop",
    "Switch off new submission preserves read/list/cancel of accepted runners",
    "Lost accepted response retries original key/raw payload after switch-off without creating or old-loop execution",
    "Instance/default profile permissions, existing no-credit UI error and NULL admin own scope",
    "Versioned video request data reaches trusted worker ToolContext without overriding identity",
    "Submitted input survives queued cancellation; stable message IDs merge with committed history once",
    "Refresh finds recent terminal plus independently active runners across the history/list race",
    "Queue input is not future model history; third execution observes two complete ordered turns",
    "Displayed accepted/sent time is retained while committed created time preserves turn order",
    "Closing Web observation leaves the default worker executing; explicit cancel is durable",
    "Existing synchronous chat JSON compatibility detaches without cancelling accepted execution",
    "Foreign actor/tenant cannot submit to, recover, read or cancel another session",
)


def test_old_page_stream_bridges_owned_session_into_single_runner_execution(legacy_page, workers, actors, service_database):
    """M7 旧页面门槛：旧页直连 stream 在 fresh 登录 + owned 会话上服务端转接。

    转接经 Runner 持久 claim 单执行；旧 SSE 协议帧由公开投影只读转换，
    桥层不写历史、不结费用，模型只被调用一次（legacy loop 不再并行执行）。
    """
    gateway = legacy_page()
    actor = actors["a"]
    marker = workers.provider.register(Reply(content="Legacy bridge single execution"))
    worker, _ = workers.start(once=False)
    try:
        with gateway.stream("POST", "/api/chat/stream", actor=actor,
                            payload={"message": marker, "session_id": actor.session_id}) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            events = list(iter_legacy_sse(response))
        types = [item["type"] for item in events]
        assert types[0] == "connected"
        assert "complete" in types and "cancelled" not in types and "error" not in types
        text = "".join(item.get("data", "") for item in events if item["type"] == "response")
        assert "Legacy bridge single execution" in text
        rows = service_database.rows(
            "SELECT runner_id,status,session_id,source,session_kind FROM agent_runners WHERE user_id=%s",
            (actor.user_id,))
        assert len(rows) == 1
        assert rows[0]["status"] == "completed" and rows[0]["session_id"] == actor.session_id
        assert rows[0]["source"] == "chat" and rows[0]["session_kind"] == "web"
        assert len(workers.provider.requests(marker)) == 1 and not workers.provider.errors
        assert terminal(service_database, rows[0]["runner_id"])["settlement_status"] == "settled"
    finally:
        workers.processes.stop(worker)


def test_old_page_stop_cancels_active_runner_and_never_fakes_success(legacy_page, workers, actors, service_database):
    """M7 旧 Stop 门槛：取消 activeRunner 而非只打旧 SSE 标记，并如实返回清单。"""
    gateway = legacy_page()
    actor = actors["a"]
    release = threading.Event()
    marker = workers.provider.register(Reply(content="Legacy stop honest cancel", release=release))
    worker, _ = workers.start(once=False)
    stream = None
    frames = None
    try:
        with gateway.stream("POST", "/api/chat/stream", actor=actor,
                            payload={"message": marker, "session_id": actor.session_id}) as response:
            assert response.status_code == 200
            stream = response
            frames = iter_legacy_sse(stream)
            assert next(frames)["type"] == "connected"
            accepted = wait_for(lambda: service_database.rows(
                "SELECT runner_id,status FROM agent_runners WHERE user_id=%s", (actor.user_id,)), timeout=20)
            assert len(accepted) == 1
            wait_for(lambda: workers.provider.requests(marker), timeout=25)
            assert runner(service_database, accepted[0]["runner_id"])["status"] == "running"
            cancelled = require_status(gateway.call(
                "POST", "/api/chat/" + actor.session_id + "/cancel", actor=actor), 200)
            assert cancelled["success"] is True
            assert cancelled["cancelled_runners"] == [accepted[0]["runner_id"]]
            release.set()
            trailing = [next(frames, None) for _ in range(64)]
            trailing = [item for item in trailing if item is not None]
            assert any(item["type"] == "cancelled" for item in trailing)
            final = terminal(service_database, accepted[0]["runner_id"])
            assert final["status"] == "cancelled"
            # 终态后无 active runner：旧 Stop 不再返回无条件假成功。
            again = require_status(gateway.call(
                "POST", "/api/chat/" + actor.session_id + "/cancel", actor=actor), 200)
            assert again["success"] is False and again["error_code"] == "NO_ACTIVE_RUNNER"
            assert again["cancelled_runners"] == []
            assert len(workers.provider.requests(marker)) == 1
    finally:
        release.set()
        workers.processes.stop(worker)


def test_old_page_stop_and_stream_do_not_fall_back_when_service_is_unavailable(legacy_page, workers, actors, service_database):
    """服务不可达按透传 502/503 处理，绝不静默回退 legacy loop 或假成功。"""
    gateway = legacy_page(api_url=legacy_page.unreachable())
    actor = actors["a"]
    stop = gateway.call("POST", "/api/chat/" + actor.session_id + "/cancel", actor=actor)
    assert stop.status_code in (502, 503)
    assert stop.json()["success"] is False
    refused = gateway.stream("POST", "/api/chat/stream", actor=actor,
                             payload={"message": "Unreachable bridge submit", "session_id": actor.session_id})
    assert refused.response.status_code in (502, 503)
    assert refused.response.json()["success"] is False
    refused.response.close()
    assert service_database.rows("SELECT 1 FROM agent_runners WHERE user_id=%s", (actor.user_id,)) == []
    assert not workers.provider.errors


def test_anonymous_memory_session_keeps_its_legacy_scope(legacy_page, workers, service_database):
    """分流边界：匿名内存会话不经 Runner，保持原 legacy 授权范围与行为。"""
    gateway = legacy_page()
    marker = workers.provider.register(Reply(content="Legacy anonymous unchanged"))
    session_id = "legacy_anon_" + uuid.uuid4().hex
    with gateway.stream("POST", "/api/chat/stream",
                        payload={"message": marker, "session_id": session_id}) as response:
        assert response.status_code == 200
        events = list(iter_legacy_sse(response))
    types = [item["type"] for item in events]
    assert types[0] == "connected" and "complete" in types
    text = "".join(item.get("data", "") for item in events if item["type"] == "response")
    assert "Legacy anonymous unchanged" in text
    assert service_database.rows("SELECT 1 FROM agent_runners WHERE session_id=%s", (session_id,)) == []
    assert len(workers.provider.requests(marker)) == 1
