"""Real PostgreSQL and Runtime HTTP pairing protocol 4."""
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from loguru import logger

from src.db.database import get_db_connection
from src.local_tools import api, pairing, repository
from src.local_tools.security import generate_device_token, sha256_hex


pytestmark = pytest.mark.integration
BASE = "/api/local-tools/runtime"


@pytest.fixture
def setup_pairing():
    name = urlsplit(os.environ.get("DATABASE_URL", "")).path.lstrip("/")
    if not name.startswith("aid_test_") or not name[9:].isalnum():
        pytest.skip("Requires dev_test.sh --isolated-db")
    app = FastAPI()
    app.include_router(api.router)
    tenant, user = uuid.uuid4().hex, uuid.uuid4().hex
    token = generate_device_token()
    old = str(repository.create_device(tenant, user, sha256_hex(token))["id"])
    assert repository.select_device(tenant, user, old)
    body = dict(code=pairing.create_pairing_ticket(tenant, user, old)["code"], pairing_protocol=4,
                registration_id=str(uuid.uuid4()), registration_token=generate_device_token(),
                previous_device_token=token, name="fixture")
    with TestClient(app) as client:
        yield client, tenant, user, old, token, body


def prepare(client, body):
    response = client.post(BASE + "/pair/prepare", json=body)
    assert response.status_code == 200
    prepared = response.json()
    assert prepared["registration_id"] == body["registration_id"]
    return dict(preparation_id=prepared["preparation_id"], preparation_token=prepared["preparation_token"],
                registration_id=body["registration_id"], registration_token=body["registration_token"])


def test_only_confirmation_consumes_code_and_replaces_old_identity(setup_pairing):
    client, tenant, user, old, old_token, body = setup_pairing
    proof = prepare(client, body)
    assert len(repository.list_devices(tenant, user)) == 1
    assert repository.list_devices(tenant, user)[0]["selected"]
    assert client.post(BASE + "/heartbeat", json={}, headers={"Authorization": f"Bearer {old_token}"}).status_code == 200
    assert client.post(BASE + "/claim?wait=0", headers={"Authorization": f"Bearer {body['registration_token']}"}).status_code == 401
    assert client.post(BASE + "/pair/status", json=proof).json()["state"] == "prepared"
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT used_at FROM local_tool_pairing_tickets WHERE id = %s", (proof["preparation_id"],))
        assert cur.fetchone()["used_at"] is None

    confirmed = client.post(BASE + "/pair/confirm", json=proof)
    assert confirmed.status_code == 200
    device_id = confirmed.json()["device_id"]
    rows = {str(row["id"]): row for row in repository.list_devices(tenant, user)}
    assert rows[old]["status"] == "revoked" and not rows[old]["selected"]
    assert rows[device_id]["selected"] and rows[device_id]["status"] == "active"
    assert client.post(BASE + "/pair/status", json=proof).json()["device_id"] == device_id
    assert client.post(BASE + "/claim?wait=0", headers={"Authorization": f"Bearer {body['registration_token']}"}).status_code == 200


@pytest.mark.parametrize("expire", [True, False])
def test_expired_or_invalidated_preparation_never_replaces_old_identity(setup_pairing, expire):
    client, tenant, user, old, _, body = setup_pairing
    proof = prepare(client, body)
    if expire:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE local_tool_pairing_tickets SET expires_at = clock_timestamp() - INTERVAL '1 second' WHERE id=%s",
                        (proof["preparation_id"],))
            conn.commit()
    else:
        pairing.create_pairing_ticket(tenant, user, old)
    state = "expired" if expire else "rejected"
    assert client.post(BASE + "/pair/status", json=proof).json()["state"] == state
    assert client.post(BASE + "/pair/confirm", json=proof).status_code == 409
    rows = repository.list_devices(tenant, user)
    assert len(rows) == 1 and rows[0]["status"] == "active" and rows[0]["selected"]


def test_proofs_bind_one_candidate_and_parallel_confirm_has_one_winner(setup_pairing):
    client, tenant, user, _, _, body = setup_pairing
    proof = prepare(client, body)
    for field in ["preparation_token", "registration_token", "registration_id"]:
        value = generate_device_token() if field.endswith("token") else str(uuid.uuid4())
        assert client.post(BASE + "/pair/confirm", json={**proof, field: value}).status_code == 403
        assert client.post(BASE + "/pair/status", json={**proof, field: value}).status_code == 403
    second = {**body, "registration_id": str(uuid.uuid4()), "registration_token": generate_device_token()}
    assert client.post(BASE + "/pair/prepare", json=second).status_code == 409
    with ThreadPoolExecutor(2) as workers:
        results = list(workers.map(lambda _: client.post(BASE + "/pair/confirm", json=proof).status_code, range(2)))
    assert sorted(results) == [200, 409]
    assert len(repository.list_devices(tenant, user)) == 2


def test_confirm_rechecks_inferred_old_token_authority_but_allows_explicit_replacement(setup_pairing):
    client, tenant, user, old, _, body = setup_pairing
    inferred = {**body, "code": pairing.create_pairing_ticket(tenant, user)["code"]}
    proof = prepare(client, inferred)
    assert repository.revoke_device(tenant, user, old)
    assert client.post(BASE + "/pair/status", json=proof).json()["state"] == "rejected"
    denied = client.post(BASE + "/pair/confirm", json=proof)
    assert denied.status_code == 404 and denied.json()["detail"]["code"] == "REPLACEMENT_TARGET_NOT_FOUND"
    assert len(repository.list_devices(tenant, user)) == 1

    explicit = {**body, "code": pairing.create_pairing_ticket(tenant, user, old)["code"],
                "registration_id": str(uuid.uuid4()), "registration_token": generate_device_token()}
    assert client.post(BASE + "/pair/confirm", json=prepare(client, explicit)).status_code == 200
    assert len(repository.list_devices(tenant, user)) == 2


def test_old_pairing_entry_and_validation_cannot_leak_or_mutate(setup_pairing):
    client, tenant, user, _, _, body = setup_pairing
    previous = client.post(BASE + "/pair", json={**body, "pairing_protocol": 3})
    assert previous.status_code == 426
    assert previous.json()["detail"]["code"] == "PAIRING_UPGRADE_REQUIRED"
    sentinel = "sensitive-fixture-must-not-echo"
    messages = []
    sink = logger.add(messages.append)
    try:
        for action in ("prepare", "confirm", "status"):
            result = client.post(BASE + f"/pair/{action}", json={
                "code": sentinel, "registration_token": sentinel, "preparation_token": sentinel,
            })
            assert result.status_code == 422 and sentinel not in result.text
            assert result.json()["detail"]["code"] == "PAIRING_INPUT_INVALID"
    finally:
        logger.remove(sink)
    assert all(sentinel not in message for message in messages)
    assert len(repository.list_devices(tenant, user)) == 1


def test_windows_client_round_trip_to_real_api_recovers_lost_confirm(setup_pairing, tmp_path):
    import uvicorn
    from fastapi.responses import JSONResponse

    node = shutil.which("node")
    root = Path(__file__).resolve().parents[2]
    entry = root / "clients/agent-tool-runtime/dist/src/cleanRegistration.js"
    if sys.platform != "win32" or not node or not entry.is_file():
        pytest.skip("Requires Windows CurrentUser DPAPI and compiled agent-tool-runtime")

    _, tenant, user, old, _, body = setup_pairing
    app = FastAPI()
    app.include_router(api.router)
    calls = []

    @app.middleware("http")
    async def lose_confirm_reply(request, call_next):
        calls.append(request.url.path)
        reply = await call_next(request)
        if request.url.path == BASE + "/pair/confirm" and reply.status_code == 200:
            return JSONResponse(status_code=503, content={})
        return reply

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
        server = uvicorn.Server(uvicorn.Config(app, access_log=False, log_level="error", lifespan="off"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            for _ in range(200):
                if server.started:
                    break
                time.sleep(0.025)
            assert server.started
            script = root / "clients/agent-desktop/scripts/tests/runtime-pairing-api-roundtrip.mjs"
            result = subprocess.run([node, str(script), origin, str(tmp_path)],
                                    input=body["code"], text=True, capture_output=True, timeout=30, check=False)
            assert result.returncode == 0, "Windows Runtime round trip did not complete"
            assert calls == [BASE + suffix for suffix in ("/pair/prepare", "/pair/confirm", "/pair/status", "/heartbeat")]
            rows = {str(row["id"]): row for row in repository.list_devices(tenant, user)}
            assert len(rows) == 2 and rows[old]["status"] == "revoked"
            assert any(row["selected"] and row["status"] == "active" for row in rows.values())
        finally:
            server.should_exit = True
            thread.join(5)
            assert not thread.is_alive()
