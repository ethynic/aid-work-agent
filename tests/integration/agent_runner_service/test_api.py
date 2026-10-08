"""Actual independent API processes + opaque auth + PostgreSQL acceptance."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import json
import re
import secrets
import socket
import time
import uuid

import bcrypt
import httpx
from psycopg2 import sql
import pytest

from .conftest import wait_for

pytestmark = pytest.mark.integration


class ApiPair:
    def __init__(self, urls, service_id, service_token, processes, children):
        self.urls, self.service_id, self._service_token = urls, service_id, service_token
        self.processes, self.children = processes, children

    def headers(self, actor=None):
        headers = {"X-AgentRunner-Service": self.service_id,
                   "X-AgentRunner-Service-Token": self._service_token}
        if actor:
            headers["Authorization"] = "Bearer " + actor.token
        return headers

    def call(self, method, path, *, actor=None, api=0, headers=None, **kwargs):
        return httpx.request(method, self.urls[api] + path,
                             headers=headers if headers is not None else self.headers(actor),
                             timeout=10, **kwargs)


def body(actor, *, key=None, text="Acceptance input", session=None, **extra):
    return {"client_request_id": key or uuid.uuid4().hex,
            "session": {"kind": "web", "session_id": session or actor.session_id},
            "text": text, **extra}


def require_status(response, status):
    assert response.status_code == status, f"HTTP {response.status_code}, expected {status}"
    return response.json()


def start_api_pair(service_processes, *, extra_environment=None):
    credential = secrets.token_urlsafe(32)
    service_id = "runner-test"
    environment = {"AGENT_RUNNER_ENABLED": "true", "AGENT_RUNNER_SERVICE_ID": service_id,
                   "AGENT_RUNNER_SERVICE_TOKEN_HASH": bcrypt.hashpw(credential.encode(), bcrypt.gensalt(rounds=4)).decode(),
                   "AGENT_RUNNER_SERVICE_SOURCES": "chat,wecom_kf,feishu,dingtalk"}
    environment.update(extra_environment or {})
    urls, children = [], []
    for _ in range(2):
        with socket.socket() as reserve:
            reserve.bind(("127.0.0.1", 0))
            port = reserve.getsockname()[1]
        child = service_processes.start(["-m", "src.services.agent_runner.bootstrap", "--host", "127.0.0.1", "--port", str(port)],
                                        environment=environment)
        url = f"http://127.0.0.1:{port}"
        def ready():
            if child.poll() is not None:
                log = (service_processes.root / f"child-{len(service_processes.children) - 1}.log").read_text()
                # Emit only traceback locations and exception class names; config
                # validation messages can contain credentials and are never echoed.
                locations = re.findall(r'File "([^"]+)", line (\d+)', log)
                classes = re.findall(r'^([A-Za-z_.]+(?:Error|Exception)):', log, re.MULTILINE)
                raise AssertionError(f"API startup exited {child.returncode}; classes={classes}; locations={locations[-5:]}")
            try:
                return httpx.get(url + "/health", timeout=0.5).status_code == 200
            except httpx.TransportError:
                return False
        wait_for(ready, timeout=15)
        urls.append(url)
        children.append(child)
    return ApiPair(urls, service_id, credential, service_processes, children)


@pytest.fixture(scope="module")
def api_pair(service_processes):
    return start_api_pair(service_processes)


def test_accept_is_committed_and_readable_from_other_api_process(api_pair, actors, service_database):
    actor = actors["a"]
    started = time.monotonic()
    accepted = require_status(api_pair.call("POST", "/v1/runners", actor=actor, json=body(actor)), 202)
    assert time.monotonic() - started < 5
    runner = accepted["runner"]
    assert accepted["created"] and runner["status"] == "queued"
    rows = service_database.rows("SELECT * FROM agent_runners WHERE runner_id=%s", (runner["runner_id"],))
    assert len(rows) == 1 and rows[0]["attempt"] == 0
    assert rows[0]["worker_id"] is None
    # M3 freezes accepted routing/context independently of execution ownership.
    # Acceptance still must not create an Engine state or claim a worker.
    checkpoint = rows[0]["checkpoint"]
    assert set(checkpoint) == {"execution_context"}
    assert checkpoint["execution_context"] == {"version":1,"profile_id":"main",
        "profile_fingerprint":rows[0]["profile_fingerprint"],"prompt_augmentations":[],"request_data":{}}
    fetched = require_status(api_pair.call("GET", "/v1/runners/" + runner["runner_id"], actor=actor, api=1), 200)
    assert fetched["runner"] == runner
    listed = require_status(api_pair.call("GET", f"/v1/sessions/web/{actor.session_id}/runners", actor=actor, api=1), 200)
    assert [item["runner_id"] for item in listed["runners"]] == [runner["runner_id"]]
    assert service_database.rows("SELECT 1 FROM chat_messages WHERE session_id=%s", (actor.session_id,)) == []
    assert service_database.rows("SELECT 1 FROM chat_records WHERE session_id=%s", (actor.session_id,)) == []


def test_concurrent_identical_submissions_and_json_order_return_one_durable_runner(api_pair, actors, service_database):
    actor = actors["a"]
    request = body(actor)
    reverse = dict(reversed(list(request.items())))
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda index: api_pair.call("POST", "/v1/runners", actor=actor, api=index % 2,
                                                             json=request if index % 2 else reverse), range(4)))
    values = [require_status(response, 202) for response in responses]
    assert len({value["runner"]["runner_id"] for value in values}) == 1
    assert sum(value["created"] for value in values) == 1
    assert len(service_database.rows("SELECT runner_id FROM agent_runners WHERE actor_id=%s", (actor.user_id,))) == 1


@pytest.mark.parametrize("change", ["text", "session", "profile", "attachments", "prompt"])
def test_same_key_different_semantics_is_conflict(api_pair, actors, service_database, change):
    actor = actors["global"] if change == "profile" else actors["a"]
    request = body(actor)
    require_status(api_pair.call("POST", "/v1/runners", actor=actor, json=request), 202)
    changed = json.loads(json.dumps(request))
    if change == "text":
        changed["text"] = "Changed input"
    elif change == "session":
        second = f"runner_second_{uuid.uuid4().hex}"
        service_database.rows("INSERT INTO chat_sessions(session_id,user_id,tenant_id) VALUES (%s,%s,%s)",
                              (second, actor.user_id, actor.tenant_id))
        changed["session"]["session_id"] = second
    elif change == "profile":
        # Entitlement checks may reject an inaccessible profile before dedup. Use
        # a global administrator so this test reaches the semantic-key contract.
        # A separate test covers denied profile permissions.
        changed["profile_id"] = "recruiting-operator"
    elif change == "attachments":
        changed["attachments"] = [{"name": "fixture.txt", "content": "Zml4dHVyZQ=="}]
    else:
        changed["prompt_augmentations"] = ["PRIVATE_PROMPT_FIXTURE"]
    result = require_status(api_pair.call("POST", "/v1/runners", actor=actor, api=1, json=changed), 409)
    assert result["error"] == "IDEMPOTENCY_INPUT_MISMATCH"
    assert len(service_database.rows("SELECT runner_id FROM agent_runners WHERE actor_id=%s", (actor.user_id,))) == 1


def test_dedup_isolated_between_tenants_and_same_tenant_subjects(api_pair, actors):
    key = uuid.uuid4().hex
    accepted = [require_status(api_pair.call("POST", "/v1/runners", actor=actors[label], json=body(actors[label], key=key)), 202)
                for label in ("a", "a_other", "b")]
    assert len({value["runner"]["runner_id"] for value in accepted}) == 3


@pytest.mark.parametrize("caller", ["a_other", "b", "global"])
def test_foreign_subject_cannot_read_list_or_cancel(api_pair, actors, caller):
    owner, foreign = actors["a"], actors[caller]
    runner = require_status(api_pair.call("POST", "/v1/runners", actor=owner, json=body(owner)), 202)["runner"]
    for method, path in (("GET", "/v1/runners/" + runner["runner_id"]),
                         ("POST", "/v1/runners/" + runner["runner_id"] + "/cancel"),
                         ("GET", f"/v1/sessions/web/{owner.session_id}/runners")):
        require_status(api_pair.call(method, path, actor=foreign), 404)
    assert require_status(api_pair.call("GET", "/v1/runners/" + runner["runner_id"], actor=owner), 200)["runner"]["status"] == "queued"


def test_null_scope_is_only_admin_own_web_and_not_cross_tenant_access(api_pair, actors, service_database):
    admin = actors["global"]
    runner = require_status(api_pair.call("POST", "/v1/runners", actor=admin, json=body(admin)), 202)["runner"]
    row = service_database.rows("SELECT tenant_id,scope_key FROM agent_runners WHERE runner_id=%s", (runner["runner_id"],))[0]
    assert row["tenant_id"] is None and row["scope_key"] == "global"
    require_status(api_pair.call("GET", "/v1/runners/" + runner["runner_id"], actor=actors["global_other"]), 404)
    require_status(api_pair.call("POST", "/v1/runners", actor=actors["null_user"], json=body(actors["null_user"])), 403)


@pytest.mark.parametrize("missing", ["service", "user", "expired", "spoof"])
def test_real_service_and_opaque_user_auth_fail_closed(api_pair, actors, service_database, missing):
    actor = actors["a"]
    headers = api_pair.headers(actor)
    request = body(actor)
    if missing == "service":
        headers.pop("X-AgentRunner-Service-Token")
    elif missing == "user":
        headers.pop("Authorization")
    elif missing == "expired":
        expired = secrets.token_urlsafe(32)
        service_database.rows("INSERT INTO tokens(token,user_id,expires_at) VALUES (%s,%s,%s)",
                              (expired, actor.user_id, datetime.now() - timedelta(seconds=1)))
        headers["Authorization"] = "Bearer " + expired
    else:
        request["tenant_id"], request["user_id"] = actors["b"].tenant_id, actors["b"].user_id
    response = api_pair.call("POST", "/v1/runners", headers=headers, json=request)
    require_status(response, 422 if missing == "spoof" else 401)
    assert actor.token not in response.text
    assert service_database.rows("SELECT 1 FROM agent_runners WHERE actor_id=%s", (actor.user_id,)) == []


def test_queued_cancel_is_idempotent_and_has_no_execution_or_charge(api_pair, actors, service_database):
    actor = actors["a"]
    runner = require_status(api_pair.call("POST", "/v1/runners", actor=actor, json=body(actor)), 202)["runner"]
    path = "/v1/runners/" + runner["runner_id"] + "/cancel"
    first = require_status(api_pair.call("POST", path, actor=actor), 200)["runner"]
    second = require_status(api_pair.call("POST", path, actor=actor, api=1), 200)["runner"]
    assert first == second and first["status"] == "cancelled" and first["cancel_requested"]
    assert first["snapshot"]["input"]["text"] == "Acceptance input"
    assert first["settlement_status"] == "settled"
    assert service_database.rows("SELECT 1 FROM chat_messages WHERE session_id=%s", (actor.session_id,)) == []
    assert service_database.rows("SELECT 1 FROM chat_records WHERE session_id=%s", (actor.session_id,)) == []
    assert service_database.rows("SELECT 1 FROM agent_runner_usage_receipts WHERE runner_id=%s", (runner["runner_id"],)) == []
    assert service_database.rows("SELECT 1 FROM agent_runner_session_claims WHERE owner_runner_id=%s", (runner["runner_id"],)) == []
    assert service_database.rows("SELECT credit_balance FROM tenants WHERE tenant_id=%s", (actor.tenant_id,))[0]["credit_balance"] == 1000


def test_failed_accept_storage_never_returns_202_and_leaves_no_partial_row(api_pair, actors, service_database):
    actor = actors["a"]
    request = body(actor)
    constraint = "runner_fixture_" + uuid.uuid4().hex
    with service_database.connect() as connection, connection.cursor() as cursor:
        cursor.execute(sql.SQL("ALTER TABLE agent_runners ADD CONSTRAINT {} CHECK (client_request_id <> %s)").format(sql.Identifier(constraint)),
                       (request["client_request_id"],))
    try:
        response = api_pair.call("POST", "/v1/runners", actor=actor, json=request)
        require_status(response, 500)
        assert constraint not in response.text and actor.token not in response.text
        assert service_database.rows("SELECT 1 FROM agent_runners WHERE actor_id=%s", (actor.user_id,)) == []
    finally:
        with service_database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql.SQL("ALTER TABLE agent_runners DROP CONSTRAINT {}").format(sql.Identifier(constraint)))
    require_status(api_pair.call("POST", "/v1/runners", actor=actor, json=request), 202)


def test_credentials_and_private_checkpoint_are_not_in_durable_input_or_public_query(api_pair, actors, service_database):
    actor = actors["a"]
    request = body(actor, prompt_augmentations=["PRIVATE_PROMPT_FIXTURE"])
    runner = require_status(api_pair.call("POST", "/v1/runners", actor=actor, json=request), 202)["runner"]
    serialized = json.dumps(runner)
    assert "PRIVATE_PROMPT_FIXTURE" not in serialized and "checkpoint" not in runner and "input_digest" not in runner
    row = service_database.rows("SELECT input,checkpoint,public_snapshot FROM agent_runners WHERE runner_id=%s", (runner["runner_id"],))[0]
    stored = json.dumps(row)
    assert actor.token not in stored and api_pair._service_token not in stored


@pytest.mark.parametrize("mutation", ["deleted", "expired", "rebound"])
def test_warmed_opaque_token_cache_cannot_override_fresh_revocation_or_binding(cache_api, actors, service_database, mutation):
    api_pair = cache_api
    owner = actors["a"]
    runner = require_status(api_pair.call("POST", "/v1/runners", actor=owner, json=body(owner)), 202)["runner"]
    # Same process's real verify_token caches a six-day token on the successful
    # request. Mutate SQL directly so no cache invalidator is called.
    require_status(api_pair.call("GET", "/v1/runners/" + runner["runner_id"], actor=owner), 200)
    observed = json.loads(cache_api.report.read_text())[-1]
    assert observed["hit"] and observed["valid"]
    assert observed["cached_user_id"] == owner.user_id == observed["returned_user_id"]
    if mutation == "deleted":
        service_database.rows("DELETE FROM tokens WHERE token=%s", (owner.token,))
    elif mutation == "expired":
        service_database.rows("UPDATE tokens SET expires_at=%s WHERE token=%s", (datetime.now() - timedelta(seconds=1), owner.token))
    else:
        service_database.rows("UPDATE tokens SET user_id=%s WHERE token=%s", (actors["b"].user_id, owner.token))
    require_status(api_pair.call("GET", "/v1/runners/" + runner["runner_id"], actor=owner), 401)
    observed = json.loads(cache_api.report.read_text())[-1]
    assert observed["hit"] and observed["valid"] and observed["cached_user_id"] == owner.user_id
    assert observed["returned_user_id"] == owner.user_id
    require_status(api_pair.call("POST", "/v1/runners/" + runner["runner_id"] + "/cancel", actor=owner), 401)


@pytest.mark.parametrize("mutation", ["balance", "tenant_suspended", "tenant_deleted", "subscription", "user_permission"])
def test_owned_history_and_cancel_survive_execution_entitlement_loss(api_pair, actors, service_database, mutation):
    owner = actors["a"]
    profile = "recruiting-operator" if mutation in ("subscription", "user_permission") else "main"
    if profile != "main":
        service_database.rows("INSERT INTO subscriptions(subscription_id,tenant_id,subagent_type,status,starts_at) VALUES (%s,%s,%s,'active',%s)",
                              (uuid.uuid4().hex, owner.tenant_id, profile, datetime.now() - timedelta(days=1)))
        service_database.rows("INSERT INTO user_agent_permissions(user_id,tenant_id,agent_id) VALUES (%s,%s,%s)",
                              (owner.user_id, owner.tenant_id, profile))
    request = body(owner, profile_id=profile)
    runner = require_status(api_pair.call("POST", "/v1/runners", actor=owner, json=request), 202)["runner"]
    if mutation == "balance":
        service_database.rows("UPDATE tenants SET credit_balance=0 WHERE tenant_id=%s", (owner.tenant_id,))
    elif mutation.startswith("tenant_"):
        status = "suspended" if mutation == "tenant_suspended" else "deleted"
        service_database.rows("UPDATE tenants SET status=%s WHERE tenant_id=%s", (status, owner.tenant_id))
    elif mutation == "subscription":
        service_database.rows("UPDATE subscriptions SET status='expired' WHERE tenant_id=%s", (owner.tenant_id,))
    else:
        service_database.rows("DELETE FROM user_agent_permissions WHERE user_id=%s", (owner.user_id,))
    require_status(api_pair.call("GET", "/v1/runners/" + runner["runner_id"], actor=owner), 200)
    listed = require_status(api_pair.call("GET", f"/v1/sessions/web/{owner.session_id}/runners", actor=owner), 200)
    assert [item["runner_id"] for item in listed["runners"]] == [runner["runner_id"]]
    require_status(api_pair.call("POST", "/v1/runners/" + runner["runner_id"] + "/cancel", actor=owner), 200)
    new_request = {**request, "client_request_id": uuid.uuid4().hex}
    require_status(api_pair.call("POST", "/v1/runners", actor=owner, json=new_request), 402 if mutation == "balance" else 403)


@pytest.mark.parametrize("source", ["wecom_kf", "feishu", "dingtalk"])
def test_channel_current_actor_proof_is_required_for_read_and_cancel(api_pair, actors, service_database, source):
    owner = actors["a"]
    session = "runner_channel_" + uuid.uuid4().hex
    platform_user = "fixture-platform-user"
    platform_chat = "fixture-platform-chat"
    service_database.rows("""INSERT INTO channel_sessions(session_id,tenant_id,channel_type,channel_user_id,channel_chat_id,user_id,subagent_id)
        VALUES (%s,%s,%s,%s,%s,%s,'main')""", (session, owner.tenant_id, source, platform_user, platform_chat, owner.user_id))
    request = {"client_request_id": uuid.uuid4().hex, "session": {"kind": "channel", "session_id": session},
               "text": "Channel acceptance", "source": source, "channel_user_id": platform_user,
               "channel_chat_id": platform_chat}
    runner = require_status(api_pair.call("POST", "/v1/runners", json=request), 202)["runner"]
    own_headers = {**api_pair.headers(), "X-AgentRunner-Source": source,
                   "X-AgentRunner-Channel-User": platform_user, "X-AgentRunner-Channel-Chat": platform_chat}
    for method, path in (("GET", "/v1/runners/" + runner["runner_id"]),
                         ("POST", "/v1/runners/" + runner["runner_id"] + "/cancel")):
        missing = api_pair.call(method, path)
        assert missing.status_code in (400, 401, 403), f"Missing channel actor proof got HTTP {missing.status_code}"
        wrong = {**own_headers, "X-AgentRunner-Channel-User": "foreign-platform-user"}
        assert api_pair.call(method, path, headers=wrong).status_code in (403, 404)
    require_status(api_pair.call("GET", "/v1/runners/" + runner["runner_id"], api=1, headers=own_headers), 200)
    listed = require_status(api_pair.call("GET", f"/v1/sessions/channel/{session}/runners", headers=own_headers,
                                         params={"source": source, "channel_user_id": platform_user, "channel_chat_id": platform_chat}), 200)
    assert [item["runner_id"] for item in listed["runners"]] == [runner["runner_id"]]
    require_status(api_pair.call("POST", "/v1/runners/" + runner["runner_id"] + "/cancel", headers=own_headers), 200)
    assert service_database.rows("SELECT 1 FROM chat_messages WHERE session_id=%s", (session,)) == []
    assert service_database.rows("SELECT 1 FROM channel_messages WHERE session_id=%s", (session,)) == []


def test_actual_bootstrap_and_nonmain_profile_import_start_no_legacy_execution_services(api_pair, service_processes):
    report = service_processes.root / "startup-graph.json"
    script = '''
import asyncio, json, os, sys
from pathlib import Path
import psycopg2
from src.services.agent_runner.bootstrap import create_app
app = create_app()
from src.db.database import get_postgres_pool
assert get_postgres_pool() is None
async def inspect():
    async with app.router.lifespan_context(app):
        app.state.runner_manager.repository.assert_schema()
        app.state.runner_manager.profiles.resolve('main')
        app.state.runner_manager.profiles.resolve('recruiting-operator')
        with psycopg2.connect(os.environ['DATABASE_URL']) as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT current_database()')
                name = cur.fetchone()[0]
        assert name.startswith('aid_test_')
        Path(sys.argv[1]).write_text(json.dumps({'database': name, 'modules': sorted(sys.modules)}))
asyncio.run(inspect())
assert get_postgres_pool() is None
'''
    child = service_processes.start(["-c", script, str(report)], environment={
        "AGENT_RUNNER_ENABLED": "true", "AGENT_RUNNER_SERVICE_ID": api_pair.service_id,
        "AGENT_RUNNER_SERVICE_TOKEN_HASH": bcrypt.hashpw(api_pair._service_token.encode(), bcrypt.gensalt(rounds=4)).decode(),
        "AGENT_RUNNER_SERVICE_SOURCES": "chat"})
    assert child.wait(timeout=15) == 0, "Independent bootstrap/profile inspection failed; no credential output emitted"
    graph = json.loads(report.read_text())
    forbidden = {name for name in graph["modules"] if name in ("src.main", "src.core.agent", "src.core.agent_router", "src.channels.manager", "src.channels.session",
                 "src.api.auth", "src.api.web_subject")
                 or name.startswith("src.scheduler.")}
    assert forbidden == set()


@pytest.fixture(scope="module")
def cache_api(api_pair, service_processes):
    report = service_processes.root / "cache-observations.json"
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1]
    child = service_processes.start(["-m", "tests.integration.agent_runner_service.cache_probe", "--port", str(port), "--report", str(report)],
        environment={"AGENT_RUNNER_ENABLED": "true", "AGENT_RUNNER_SERVICE_ID": api_pair.service_id,
                     "AGENT_RUNNER_SERVICE_TOKEN_HASH": bcrypt.hashpw(api_pair._service_token.encode(), bcrypt.gensalt(rounds=4)).decode(),
                     "AGENT_RUNNER_SERVICE_SOURCES": "chat"})
    url = f"http://127.0.0.1:{port}"
    def ready():
        assert child.poll() is None, "Real create_app cache observation process failed to start"
        try:
            return httpx.get(url + "/health", timeout=0.5).status_code == 200
        except httpx.TransportError:
            return False
    wait_for(ready, timeout=15)
    client = ApiPair([url], api_pair.service_id, api_pair._service_token, service_processes, [child])
    client.report = report
    return client


def test_channel_internal_user_rebind_cannot_replay_read_or_list_old_owner_runner(api_pair, actors, service_database):
    owner, replacement = actors["a"], actors["a_other"]
    session = "runner_channel_" + uuid.uuid4().hex
    user, chat = "fixture-platform-user", "fixture-platform-chat"
    service_database.rows("""INSERT INTO channel_sessions(session_id,tenant_id,channel_type,channel_user_id,channel_chat_id,user_id,subagent_id)
        VALUES (%s,%s,'feishu',%s,%s,%s,'main')""", (session, owner.tenant_id, user, chat, owner.user_id))
    request = {"client_request_id": uuid.uuid4().hex, "session": {"kind": "channel", "session_id": session},
               "text": "Original private input", "source": "feishu", "channel_user_id": user, "channel_chat_id": chat}
    old = require_status(api_pair.call("POST", "/v1/runners", json=request), 202)["runner"]
    headers = {**api_pair.headers(), "X-AgentRunner-Source": "feishu", "X-AgentRunner-Channel-User": user,
               "X-AgentRunner-Channel-Chat": chat}
    service_database.rows("UPDATE channel_sessions SET user_id=%s WHERE session_id=%s", (replacement.user_id, session))
    require_status(api_pair.call("GET", "/v1/runners/" + old["runner_id"], headers=headers), 404)
    require_status(api_pair.call("POST", "/v1/runners", json=request), 404)
    fresh = require_status(api_pair.call("POST", "/v1/runners", json={**request, "client_request_id": uuid.uuid4().hex}), 202)["runner"]
    listed = require_status(api_pair.call("GET", f"/v1/sessions/channel/{session}/runners", headers=headers,
        params={"source": "feishu", "channel_user_id": user, "channel_chat_id": chat}), 200)
    assert [item["runner_id"] for item in listed["runners"]] == [fresh["runner_id"]]
    assert old["runner_id"] not in json.dumps(listed)


def test_pagination_can_find_older_runner_and_keeps_active_visible_behind_queued_items(api_pair, actors, service_database):
    owner = actors["a"]
    oldest = require_status(api_pair.call("POST", "/v1/runners", actor=owner, json=body(owner)), 202)["runner"]
    # This is a query projection fixture, not evidence of a real worker execution.
    service_database.rows("UPDATE agent_runners SET status='running' WHERE runner_id=%s", (oldest["runner_id"],))
    with ThreadPoolExecutor(max_workers=8) as pool:
        values = list(pool.map(lambda _: require_status(api_pair.call("POST", "/v1/runners", actor=owner, json=body(owner)), 202), range(100)))
    path = f"/v1/sessions/web/{owner.session_id}/runners"
    page = require_status(api_pair.call("GET", path, actor=owner), 200)
    assert len(page["runners"]) == 100 and page["has_more"] and page["next_cursor"]
    assert [item["runner_id"] for item in page["active_runners"]] == [oldest["runner_id"]]
    older = require_status(api_pair.call("GET", path, actor=owner, params={"before_runner_id": page["next_cursor"]}), 200)
    assert [item["runner_id"] for item in older["runners"]] == [oldest["runner_id"]]
    assert not older["has_more"]
    assert len({item["runner_id"] for item in page["runners"] + older["runners"]}) == 101


@pytest.mark.parametrize("db_chat", [None, ""])
def test_direct_channel_empty_and_null_route_are_canonical_and_keep_original_runner(api_pair, actors, service_database, db_chat):
    owner = actors["a"]
    session = "runner_direct_" + uuid.uuid4().hex
    user = "fixture-direct-user"
    service_database.rows("""INSERT INTO channel_sessions(session_id,tenant_id,channel_type,channel_user_id,channel_chat_id,user_id,subagent_id)
        VALUES (%s,%s,'feishu',%s,%s,%s,'main')""", (session, owner.tenant_id, user, db_chat, owner.user_id))
    request = {"client_request_id": uuid.uuid4().hex, "session": {"kind": "channel", "session_id": session},
               "text": "Direct acceptance", "source": "feishu", "channel_user_id": user, "channel_chat_id": ""}
    first = require_status(api_pair.call("POST", "/v1/runners", json=request), 202)
    request.pop("channel_chat_id")
    duplicate = require_status(api_pair.call("POST", "/v1/runners", api=1, json=request), 202)
    assert duplicate["runner"]["runner_id"] == first["runner"]["runner_id"] and not duplicate["created"]
    headers = {**api_pair.headers(), "X-AgentRunner-Source": "feishu", "X-AgentRunner-Channel-User": user}
    require_status(api_pair.call("GET", "/v1/runners/" + first["runner"]["runner_id"], headers=headers), 200)
    require_status(api_pair.call("POST", "/v1/runners/" + first["runner"]["runner_id"] + "/cancel", headers=headers), 200)


def test_foreign_pagination_cursor_is_rejected_without_leaking_other_session_order(api_pair, actors):
    owner, foreign = actors["a"], actors["b"]
    own = require_status(api_pair.call("POST", "/v1/runners", actor=owner, json=body(owner)), 202)["runner"]
    other = require_status(api_pair.call("POST", "/v1/runners", actor=foreign, json=body(foreign)), 202)["runner"]
    response = api_pair.call("GET", f"/v1/sessions/web/{owner.session_id}/runners", actor=owner,
                             params={"before_runner_id": other["runner_id"]})
    require_status(response, 404)
    assert other["runner_id"] not in response.text
