"""Protocol 3 real PostgreSQL transactions in a task-owned disposable database."""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from urllib.parse import urlsplit
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.local_tools import api, pairing, repository
from src.local_tools.security import generate_device_token, sha256_hex
from src.db.database import get_db_connection

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def isolated_database_only():
    name = urlsplit(os.environ.get("DATABASE_URL", "")).path.lstrip("/")
    if not name.startswith("aid_test_") or not name[9:].isalnum():
        pytest.skip("Requires dev_test.sh --isolated-db")


def scope():
    return "clean-tenant-" + uuid.uuid4().hex, "clean-user-" + uuid.uuid4().hex


def ticket(tenant, user, target=None):
    return pairing.create_pairing_ticket(tenant, user, str(target) if target else None)["code"]


def request(code, previous=None):
    return dict(code=code, pairing_protocol=3, registration_id=str(uuid.uuid4()),
                registration_token=generate_device_token(), previous_device_token=previous,
                name="Clean Runtime", platform="win32-x64", runtime_version="test-version")


def device(tenant, user):
    token = generate_device_token()
    row = repository.create_device(tenant, user, sha256_hex(token))
    return row["id"], token


def read(identifier):
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM local_tool_devices WHERE id = %s", (identifier,))
        return dict(cur.fetchone())


def test_new_registration_replaces_only_proven_identity_and_selects_atomically():
    tenant, user = scope()
    old, old_token = device(tenant, user)
    other, _ = device(tenant, user)
    repository.select_device(tenant, user, str(other))
    values = request(ticket(tenant, user), old_token)
    result = pairing.pair(**values)
    assert result["device_token"] == values["registration_token"]
    assert result["pairing_protocol"] == 3 and result["registration_completed"]
    assert result["replacement_completed"] and "replacement_pending" not in result
    assert read(old)["status"] == "revoked"
    assert str(read(old)["replaced_by_device_id"]) == result["device_id"]
    assert read(other)["status"] == "active" and not read(other)["selected"]
    assert read(result["device_id"])["selected"]
    assert repository.get_device_by_token_hash(sha256_hex(old_token)) is None


def test_response_loss_retry_without_code_after_expiry_does_not_reselect():
    tenant, user = scope()
    values = request(ticket(tenant, user))
    first = pairing.pair(**values)
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE local_tool_pairing_tickets SET expires_at = NOW() - INTERVAL '1 day' WHERE code_hash = %s", (sha256_hex(values["code"]),))
        conn.commit()
    other, _ = device(tenant, user)
    repository.select_device(tenant, user, str(other))
    values["code"] = None
    assert pairing.pair(**values) == first
    assert not read(first["device_id"])["selected"] and read(other)["selected"]
    assert len(repository.list_devices(tenant, user)) == 2


@pytest.mark.parametrize("field,value", [("registration_token", "b" * 64), ("name", "changed"), ("code", "ZZZZ9999")])
def test_changed_retry_identity_is_rejected(field, value):
    tenant, user = scope()
    values = request(ticket(tenant, user))
    first = pairing.pair(**values)
    values[field] = value
    with pytest.raises(repository.DeviceReplacementError, match="REGISTRATION_CONFLICT"):
        pairing.pair(**values)
    assert read(first["device_id"])["selected"]
    assert len(repository.list_devices(tenant, user)) == 1


def test_revoked_registration_is_never_revived_by_retry():
    tenant, user = scope()
    values = request(ticket(tenant, user))
    first = pairing.pair(**values)
    assert repository.revoke_device(tenant, user, first["device_id"])
    values["code"] = None
    with pytest.raises(repository.DeviceReplacementError, match="REGISTRATION_REVOKED"):
        pairing.pair(**values)
    assert read(first["device_id"])["status"] == "revoked"


@pytest.mark.parametrize("mode", ["unknown", "damaged", "revoked"])
def test_unusable_old_credentials_allow_clean_pair(mode):
    tenant, user = scope()
    old, token = device(tenant, user)
    if mode == "revoked":
        repository.revoke_device(tenant, user, str(old))
    else:
        token = generate_device_token() if mode == "unknown" else "broken-legacy-credential"
    result = pairing.pair(**request(ticket(tenant, user), token))
    assert result["registration_completed"] and not result["replacement_completed"]
    assert read(result["device_id"])["selected"]


@pytest.mark.parametrize("different_tenant", [False, True])
def test_foreign_previous_token_does_not_block_pair_or_revoke_other_account(different_tenant):
    tenant, user = scope()
    foreign_tenant, foreign_user = scope()
    foreign, token = device(foreign_tenant if different_tenant else tenant, foreign_user)
    values = request(ticket(tenant, user), token)
    result = pairing.pair(**values)
    assert result["registration_completed"] and not result["replacement_completed"]
    assert read(result["device_id"])["tenant_id"] == tenant
    assert read(result["device_id"])["user_id"] == user
    assert read(result["device_id"])["selected"]
    assert read(foreign)["status"] == "active"
    assert read(foreign)["replaced_by_device_id"] is None
    values["code"] = None
    assert pairing.pair(**values)["device_id"] == result["device_id"]


def test_registration_preserves_original_task_device_and_facts():
    tenant, user = scope()
    old, token = device(tenant, user)
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("""INSERT INTO local_tool_invocations
            (tenant_id, user_id, device_id, tool_name, arguments_json, state, effect)
            VALUES (%s, %s, %s, 'clean-install-test', '{}', 'running', 'unknown') RETURNING id""", (tenant, user, old))
        invocation_id = cur.fetchone()["id"]
        conn.commit()
    result = pairing.pair(**request(ticket(tenant, user), token))
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT device_id, state, effect FROM local_tool_invocations WHERE id = %s AND tenant_id = %s", (invocation_id, tenant))
        row = cur.fetchone()
        assert str(row["device_id"]) == str(old) and str(row["device_id"]) != result["device_id"]
        assert row["state"] == "running" and row["effect"] == "unknown"


def test_v3_incremental_migration_is_idempotent_and_preserves_old_rows():
    from pathlib import Path
    from psycopg2 import sql
    import yaml
    blocks = yaml.safe_load((Path(__file__).resolve().parents[2] / "deploy/db_update.yaml").read_text(encoding="utf-8"))
    migration = next(block["statements"] for block in blocks if block["datetime"] == "2026-10-14 17:00:00")
    schema = "clean_migration_" + uuid.uuid4().hex
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        cur.execute(sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(schema)))
        cur.execute("CREATE TABLE local_tool_devices (id UUID PRIMARY KEY, token_hash TEXT)")
        identifier = str(uuid.uuid4())
        cur.execute("INSERT INTO local_tool_devices (id, token_hash) VALUES (%s, %s)", (identifier, sha256_hex("migration-placeholder")))
        cur.execute(migration)
        cur.execute(migration)
        cur.execute("SELECT registration_id, registration_request_hash, registration_code_hash FROM local_tool_devices WHERE id = %s", (identifier,))
        assert all(value is None for value in cur.fetchone().values())
        cur.execute("SELECT indexname FROM pg_indexes WHERE schemaname = %s", (schema,))
        assert "uq_lt_devices_registration" in {row["indexname"] for row in cur.fetchall()}
        conn.rollback()


@pytest.mark.parametrize("operation", ["claim", "renew", "permit", "session_claim", "session_renew"])
def test_inflight_auth_cannot_create_authority_after_pair_revocation(operation, monkeypatch):
    """Real revoke row lock forces authenticated in-flight callers to recheck.

    The pair transaction pauses after status update before commit. A caller that
    authenticated earlier then waits on its real device row, sees revoked on
    commit, and cannot claim/renew/issue permission. No task action is replayed.
    """
    from contextlib import contextmanager
    from threading import Event
    from src.local_tools import permits
    from src.session_tasks import service as sessions
    from src.session_tasks.constants import SessionTaskError
    tenant, user = scope()
    old, token = device(tenant, user)
    claim_hash = sha256_hex("synthetic-inflight-claim")
    state = "queued" if operation == "claim" else "running"
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("""INSERT INTO local_tool_invocations
            (tenant_id, user_id, device_id, tool_name, arguments_json, state, claim_token_hash,
             lease_expires_at, business_kind, business_ref)
            VALUES (%s,%s,%s,'clean-install-test','{}',%s,%s,NOW()+INTERVAL '1 hour','desktop_automation','{}') RETURNING id""",
            (tenant, user, old, state, claim_hash))
        invocation_id = str(cur.fetchone()["id"])
        conn.commit()
    monkeypatch.setattr(sessions, "tenant_allowed", lambda _: True)
    values = request(ticket(tenant, user), token)
    revoke_written, gate_entered, release_commit = Event(), Event(), Event()

    @contextmanager
    def instrumented_connection():
        with get_db_connection() as conn:
            class Cursor:
                def __init__(self): self.real = conn.cursor()
                def __getattr__(self, name): return getattr(self.real, name)
                def execute(self, query, args=None):
                    if "status = 'active' FOR SHARE" in query:
                        gate_entered.set()
                    result = self.real.execute(query, args)
                    if "SET status = 'revoked'" in query:
                        revoke_written.set()
                        assert release_commit.wait(10), "test did not release pair transaction"
                    return result
            class Connection:
                def cursor(self): return Cursor()
                def __getattr__(self, name): return getattr(conn, name)
            yield Connection()

    def inflight():
        if operation == "claim":
            return repository.claim_next(str(old), tenant, claim_hash, 60)
        if operation == "renew":
            return repository.append_event(invocation_id, tenant, claim_hash, None, None, None, None, 60)
        if operation == "permit":
            return permits.write_authorize(tenant_id=tenant, device_id=str(old), invocation_id=invocation_id,
                                          claim_token_hash=claim_hash, request_id="inflight", target_version=None, payload_hash=None)
        if operation == "session_claim":
            return sessions.claim_task({"id": old, "tenant_id": tenant}, "inflight-runtime")
        return sessions.renew_assignment(tenant, old, uuid.uuid4(), 1, 1)

    with patch.object(repository, "get_db_connection", instrumented_connection), \
         patch.object(permits, "get_db_connection", instrumented_connection), \
         patch.object(sessions, "_conn", instrumented_connection), \
         ThreadPoolExecutor(max_workers=2) as workers:
        paired = workers.submit(pairing.pair, **values)
        assert revoke_written.wait(10)
        caller = workers.submit(inflight)
        try:
            assert gate_entered.wait(10)
            assert not caller.done(), "authorization should wait on the real revoke row lock"
        finally:
            release_commit.set()
        assert paired.result(timeout=10)["registration_completed"]
        if operation in ("claim", "renew"):
            assert caller.result(timeout=10) is None
        else:
            with pytest.raises((permits.PermitError, SessionTaskError)) as error:
                caller.result(timeout=10)
            assert error.value.code == "DEVICE_REVOKED"
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT state, device_id FROM local_tool_invocations WHERE id=%s", (invocation_id,))
        row = cur.fetchone()
        assert row["state"] == state and str(row["device_id"]) == str(old)
        cur.execute("SELECT COUNT(*) AS n FROM local_tool_events WHERE invocation_id=%s", (invocation_id,))
        assert cur.fetchone()["n"] == 0


def test_ticket_target_takes_precedence_over_another_owned_previous_token():
    tenant, user = scope()
    target, _ = device(tenant, user)
    other, token = device(tenant, user)
    result = pairing.pair(**request(ticket(tenant, user, target), token))
    assert read(target)["status"] == "revoked"
    assert read(other)["status"] == "active"
    assert str(read(result["device_id"])["replacement_device_id"]) == str(target)


def test_same_registration_concurrent_retries_create_one_device():
    tenant, user = scope()
    values = request(ticket(tenant, user))
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: pairing.pair(**values), range(2)))
    assert results[0] == results[1]
    assert len(repository.list_devices(tenant, user)) == 1


def test_same_ticket_concurrent_registrations_have_one_winner():
    tenant, user = scope()
    code = ticket(tenant, user)
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda values: pairing.pair(**values), [request(code), request(code)]))
    assert sum(result is not None for result in results) == 1
    assert len(repository.list_devices(tenant, user)) == 1


def test_new_ticket_cannot_replace_target_with_existing_winner():
    tenant, user = scope()
    old, _ = device(tenant, user)
    result = pairing.pair(**request(ticket(tenant, user, old)))
    with pytest.raises(repository.DeviceReplacementError, match="REPLACEMENT_ALREADY_COMPLETED"):
        pairing.pair(**request(ticket(tenant, user, old)))
    assert str(read(old)["replaced_by_device_id"]) == result["device_id"]
    assert len(repository.list_devices(tenant, user)) == 2


def test_api_retry_and_activation_are_authenticated_and_target_free():
    tenant, user = scope()
    app = FastAPI()
    app.include_router(api.router)
    values = request(ticket(tenant, user))
    with TestClient(app) as client:
        paired = client.post("/api/local-tools/runtime/pair", json=values)
        assert paired.status_code == 200
        values.pop("code")
        assert client.post("/api/local-tools/runtime/pair", json=values).json() == paired.json()
        assert client.post("/api/local-tools/runtime/activate", json={}).status_code == 401
        headers = {"Authorization": "Bearer " + values["registration_token"]}
        assert client.post("/api/local-tools/runtime/activate", json={"device_id": str(uuid.uuid4())}, headers=headers).status_code == 422
        assert client.post("/api/local-tools/runtime/activate", json={}, headers=headers).json() == {"success": True, "selected": True}


def test_mid_transaction_failure_rolls_back_identity_and_ticket():
    tenant, user = scope()
    old, token = device(tenant, user)
    values = request(ticket(tenant, user), token)
    from contextlib import contextmanager

    @contextmanager
    def fail_selection():
        with get_db_connection() as conn:
            class Cursor:
                def execute(self, query, args=None):
                    if "SET selected = TRUE" in query:
                        raise RuntimeError("synthetic selection failure")
                    return real_cursor.execute(query, args)
                def fetchone(self):
                    return real_cursor.fetchone()
            real_cursor = conn.cursor()
            class Connection:
                def cursor(self): return Cursor()
                def commit(self): return conn.commit()
            yield Connection()

    with patch.object(repository, "get_db_connection", fail_selection):
        with pytest.raises(RuntimeError, match="synthetic selection failure"):
            pairing.pair(**values)
    assert read(old)["status"] == "active"
    assert len(repository.list_devices(tenant, user)) == 1
    assert pairing.pair(**values)["registration_completed"]
