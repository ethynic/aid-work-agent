"""Paired Host -> real BOSS visual client -> device API -> PostgreSQL ledger.

Only a disposable explicit test PostgreSQL schema is allowed. No browser, model,
installer, registry, real device, or deployed API is used by these tests.
"""

import asyncio
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from PIL import Image

pytestmark = [pytest.mark.integration, pytest.mark.db, pytest.mark.api]
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session", autouse=True)
def init_db_pool():
    """Override parent fixture: this suite owns an explicit isolated schema only."""
    yield


@pytest.fixture
def paired_pg(monkeypatch):
    """Use real DAO/authentication queries, never the developer's default DB."""
    dsn = os.environ.get("BOSS_VISUAL_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Requires explicit isolated BOSS_VISUAL_TEST_DATABASE_URL")
    import psycopg2
    from psycopg2 import sql
    from psycopg2.extras import RealDictCursor
    from src.db import client_binding_db
    from src.local_tools import repository

    schema = f"boss_paired_independent_{uuid4().hex}"
    admin = psycopg2.connect(dsn)
    admin.autocommit = True
    with admin.cursor() as cursor:
        cursor.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        cursor.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        cursor.execute("""
            CREATE TABLE tenants (tenant_id TEXT PRIMARY KEY, credit_balance NUMERIC(12,2));
            INSERT INTO tenants VALUES ('paired-tenant', 10), ('other-tenant', 10);
            CREATE TABLE client_usage_logs (
                id SERIAL PRIMARY KEY, tenant_id TEXT NOT NULL, binding_id TEXT NOT NULL,
                client_name TEXT, client_ref_id TEXT, stage TEXT, status TEXT, model TEXT,
                provider TEXT, detail TEXT, prompt_tokens INTEGER DEFAULT 0,
                completion_tokens INTEGER DEFAULT 0, cached_tokens INTEGER DEFAULT 0,
                total_tokens INTEGER DEFAULT 0, raw_credit_cost NUMERIC(12,2) DEFAULT 0,
                credit_cost NUMERIC(12,2) DEFAULT 0);
            CREATE UNIQUE INDEX tenant_ref ON client_usage_logs (tenant_id, client_ref_id)
                WHERE client_ref_id IS NOT NULL;
            CREATE TABLE local_tool_devices (
                id TEXT PRIMARY KEY, tenant_id TEXT, user_id TEXT, name TEXT,
                platform TEXT, runtime_version TEXT, capabilities_json JSONB,
                manifest_digest TEXT, selected BOOLEAN, status TEXT,
                last_seen_at TIMESTAMPTZ, token_hash TEXT);
        """)

    @contextmanager
    def connection():
        conn = psycopg2.connect(dsn, cursor_factory=RealDictCursor)
        try:
            with conn.cursor() as cursor:
                cursor.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            yield conn
        finally:
            conn.rollback()
            conn.close()

    monkeypatch.setattr(client_binding_db, "get_db_connection", connection)
    monkeypatch.setattr(client_binding_db, "invalidate_tenant_cache", lambda _: None)
    monkeypatch.setattr(repository, "get_db_connection", connection)
    try:
        yield connection
    finally:
        with admin.cursor() as cursor:
            cursor.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
        admin.close()


@pytest.fixture
def paired_api(paired_pg, monkeypatch):
    """Real production router, auth, service, billing and DAO; only LLM is fake."""
    import uvicorn
    from src.local_tools import api
    from src.local_tools.security import sha256_hex
    from src.services import boss_visual_service as visual

    device_id, token = str(uuid4()), f"fictional-device-{uuid4().hex}"
    other_id, other_token = str(uuid4()), f"fictional-other-{uuid4().hex}"
    with paired_pg() as conn:
        with conn.cursor() as cursor:
            for did, tok, tenant, user in [
                (device_id, token, "paired-tenant", "paired-user"),
                (other_id, other_token, "paired-tenant", "paired-user"),
            ]:
                cursor.execute("""INSERT INTO local_tool_devices
                    (id,tenant_id,user_id,status,selected,token_hash)
                    VALUES (%s,%s,%s,'active',TRUE,%s)""", (did, tenant, user, sha256_hex(tok)))
        conn.commit()
    monkeypatch.setattr(api.TenantDB, "get_by_id", lambda tenant: {"tenant_id": tenant, "status": "active"})
    monkeypatch.setattr(api.UserDB, "get_by_id", lambda user: {"user_id": user, "tenant_id": "paired-tenant", "status": "active"})
    monkeypatch.setattr(visual.TokenCostPriceDB, "get_by_model_name", lambda _: {"input_price_per_m": "0.8", "output_price_per_m": "2.8"})
    monkeypatch.setattr(visual.settings.boss_visual, "model", "zhipu/GLM-5.3-Flash")
    gateway = AsyncMock()
    monkeypatch.setattr(visual.llm_gateway, "chat_direct", gateway)
    app = FastAPI()

    # Unrelated queue/heartbeat boundaries; no invocation is issued or executed.
    @app.post("/api/local-tools/runtime/heartbeat")
    async def heartbeat():
        return {"success": True, "selected": True}

    @app.post("/api/local-tools/runtime/claim")
    async def claim():
        await asyncio.sleep(0.05)
        return {"invocation": None}

    app.include_router(api.router)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                         log_level="critical", access_log=False))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.started, "Isolated loopback API must start before any child is launched"
    try:
        yield {"url": f"http://127.0.0.1:{port}", "token": token,
               "device_id": device_id, "other_id": other_id, "other_token": other_token,
               "gateway": gateway, "connection": paired_pg}
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        assert not thread.is_alive(), "Loopback API must release its listener"


def _run_paired_host(tmp_path, api, api_version):
    """Launch actual LegacyExecution; child calls actual compiled visual client."""
    if sys.platform != "win32":
        pytest.skip("Actual Windows DPAPI/process-supervision test")
    node = shutil.which("node")
    assert node, "Node must be available, never replaced with a fake successful probe"
    runtime = ROOT / "clients/agent-tool-runtime/dist"
    boss = ROOT / "clients/boss-resume-assistant/dist"
    assert (runtime / "src/runtimeHost.js").is_file(), "Build Runtime before this integration test"
    assert (boss / "src/main/boss/visualDiagnoseClient.js").is_file(), "Build BOSS before this integration test"
    q = lambda value: json.dumps(str(value))
    module = lambda value: q(value.as_uri())
    home = tmp_path / "runtime-home"
    home.mkdir()
    png = io.BytesIO()
    Image.new("RGB", (12, 8), "white").save(png, "PNG")
    image = tmp_path / "synthetic-page.png"
    image.write_bytes(png.getvalue())
    result = tmp_path / "receipt.json"
    request_id = str(uuid4())
    context = {
        "purpose": "verify", "operation": {"name": "open-detail", "goal": "show detail",
            "expected": "experience title visible"},
        "execution": {"stage": "postcheck", "input_status": "attempted",
            "attempts_used": 2, "ordinary_retries_used": 1},
        "error": None, "verification": {"status": "inconclusive", "evidence": []},
        "available_actions": ["dismiss_click"],
    }
    provider = tmp_path / "provider.mjs"
    provider.write_text(f"""
import {{writeFileSync}} from 'node:fs';
import {{visualDiagnose}} from {module(boss / 'src/main/boss/visualDiagnoseClient.js')};
const opts={{requestId:{q(request_id)}, image:{q(image)}, apiVersion:{api_version},
  ...( {api_version} === 2 ? {{context:{json.dumps(context)}}} : {{}} )}};
const first=await visualDiagnose(opts);
const again=await visualDiagnose(opts);
const retrieved=await visualDiagnose({{requestId:opts.requestId,apiVersion:{api_version},retrieveOnly:true}});
writeFileSync({q(result)},JSON.stringify({{first,again,retrieved,
 legacyPresent:['AID_BOSS_SERVER_URL','AID_BOSS_SERVER_TOKEN','ZHIPU_API_KEY'].some(k=>k in process.env)}}));
const {{startFakeProvider}}=await import({module(runtime / 'tests/helpers/fakeProvider.js')});
await startFakeProvider();
""", encoding="utf-8")
    (home / "config.json").write_text(json.dumps({"server": api["url"], "device_id": api["device_id"],
                                                 "providers": {"boss-recruiting": {"entry": str(provider)}}}), encoding="utf-8")
    launch = tmp_path / "host.mjs"
    launch.write_text(f"""
import {{LegacyExecution}} from {module(runtime / 'src/runtimeHost.js')};
import {{configureRuntimeHome}} from {module(runtime / 'src/config.js')};
import {{saveDeviceToken}} from {module(runtime / 'src/credentials.js')};
import {{readFileSync}} from 'node:fs';
const home={q(home)};
configureRuntimeHome(home);
await saveDeviceToken({q(api['token'])});
const credentials=readFileSync(home+'/credentials.bin','utf8');
if(credentials.includes({q(api['token'])})) throw new Error('DPAPI did not protect test identity');
const adapter=new LegacyExecution(process.execPath,home,undefined,true);
try {{
 await adapter.initialize();
 await adapter.executionMarker({q(str(uuid4()))});
 await adapter.start(()=>{{}},()=>{{}});
 await adapter.providers.get('boss-recruiting').ensureStarted();
}} finally {{await adapter.stop(); configureRuntimeHome(undefined);}}
""", encoding="utf-8")
    env = dict(os.environ)
    env.update(AID_BOSS_SERVER_URL="https://wrong-legacy.invalid", AID_BOSS_SERVER_TOKEN="fictional-legacy",
               AIDWORK_RUNTIME_SERVER="https://wrong-parent.invalid", AIDWORK_RUNTIME_DEVICE_TOKEN="fictional-parent",
               ZHIPU_API_KEY="fictional-vendor")
    completed = subprocess.run([node, str(launch)], cwd=ROOT, env=env, capture_output=True,
                               encoding="utf-8", timeout=90)
    assert completed.returncode == 0, f"Isolated actual Host/client failed: {completed.stderr[-3000:]}"
    assert api["token"] not in completed.stdout + completed.stderr, "Device secret must not enter process logs"
    return request_id, json.loads(result.read_text(encoding="utf-8"))


@pytest.mark.parametrize("api_version", [1, 2])
def test_paired_process_real_device_route_charges_once_and_isolates_receipt(tmp_path, paired_api, api_version):
    api = paired_api
    diagnosis = ({"page_summary": "synthetic clear page", "verdict": "inconclusive", "reason": "no blocker",
                  "proposed_action": None} if api_version == 1 else
                 {"page_summary": "synthetic clear page", "goal_state": "satisfied", "obstruction": "absent",
                  "reason": "visible state", "proposed_action": None, "missing_evidence": []})
    api["gateway"].return_value = {"content": json.dumps(diagnosis),
                                   "usage": {"prompt_tokens": 9000, "completion_tokens": 1000}}
    request_id, observed = _run_paired_host(tmp_path, api, api_version)
    assert observed["first"]["status"] == 200
    assert observed["first"] == observed["again"] == observed["retrieved"]
    assert not observed["legacyPresent"], "Pairing alone must suffice; old/manual and vendor credentials stay stripped"
    receipt = observed["first"]["body"]["receipt"]
    assert receipt["factor"] == 20 and Decimal(receipt["credit_cost"]) == Decimal("0.20")
    api["gateway"].assert_awaited_once()
    retrieve = {"api_version": api_version, "request_id": request_id, "retrieve_only": True}
    url = api["url"] + "/api/local-tools/runtime/boss/visual-diagnose"
    headers = lambda token: {"Authorization": f"Bearer {token}"}
    with httpx.Client() as client:
        assert client.post(url, json=retrieve, headers=headers(api["other_token"])).status_code == 403
        assert client.post(url, json=retrieve, headers=headers("fictional-unpaired")).status_code == 401
        assert client.post(url, json={**retrieve, "tenant_id": "forged"}, headers=headers(api["token"])).status_code == 422
        with api["connection"]() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT * FROM client_usage_logs")
                rows = cursor.fetchall()
                assert len(rows) == 1, "Duplicates and cross-device probes must not create another bill"
                assert rows[0]["binding_id"] == "runtime-device:" + api["device_id"]
                assert json.loads(rows[0]["detail"])["caller"] == {
                    "kind": "runtime_device", "device_id": api["device_id"], "user_id": "paired-user"}
                cursor.execute("SELECT credit_balance FROM tenants WHERE tenant_id='paired-tenant'")
                assert cursor.fetchone()["credit_balance"] == Decimal("9.80")
                cursor.execute("UPDATE local_tool_devices SET user_id='new-user' WHERE id=%s", (api["device_id"],))
            conn.commit()
        assert client.post(url, json=retrieve, headers=headers(api["token"])).status_code == 403
        with api["connection"]() as conn:
            with conn.cursor() as cursor:
                cursor.execute("UPDATE local_tool_devices SET user_id='paired-user',status='revoked' WHERE id=%s", (api["device_id"],))
            conn.commit()
        assert client.post(url, json=retrieve, headers=headers(api["token"])).status_code == 401
    api["gateway"].assert_awaited_once()


def test_paired_model_unknown_remains_unknown_on_original_id_without_second_dispatch(tmp_path, paired_api):
    api = paired_api
    api["gateway"].side_effect = TimeoutError("fictional provider timeout")
    request_id, observed = _run_paired_host(tmp_path, api, 2)
    assert observed["first"]["status"] == 502
    assert observed["again"]["status"] == observed["retrieved"]["status"] == 409
    for result in [observed["first"], observed["again"], observed["retrieved"]]:
        assert result["body"]["detail"]["code"] == "MODEL_UNKNOWN"
    api["gateway"].assert_awaited_once()
    with api["connection"]() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM client_usage_logs")
            rows = cursor.fetchall()
            assert len(rows) == 1
            assert rows[0]["client_ref_id"] == "boss.visual:" + request_id
            assert rows[0]["status"] == "model_unknown"
            assert Decimal(rows[0]["credit_cost"]) == 0
            cursor.execute("SELECT credit_balance FROM tenants WHERE tenant_id='paired-tenant'")
            assert cursor.fetchone()["credit_balance"] == Decimal("10.00")
