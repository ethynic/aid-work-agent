"""Real SQL, only in a nonce DB created by dev_test.sh --isolated-db.

No existing tenant data, application startup or scheduler is used.
"""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta
from urllib.parse import urlsplit
from unittest.mock import patch

import psycopg2
import pytest

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def disposable_only():
    name = urlsplit(os.environ.get("DATABASE_URL", "")).path.lstrip("/")
    if not name.startswith("aid_test_") or not name[9:].isalnum():
        pytest.skip("Requires dev_test.sh --isolated-db nonce database")


def scope():
    return "test-tenant-" + uuid.uuid4().hex, "test-user-" + uuid.uuid4().hex


def old_device(tenant, user):
    from src.local_tools import repository
    return repository.create_device(tenant, user, uuid.uuid4().hex + uuid.uuid4().hex)["id"]


def replacement(tenant, user, old):
    from src.local_tools import repository
    code_hash = uuid.uuid4().hex
    repository.issue_pairing_ticket(tenant, user, code_hash, datetime.now() + timedelta(minutes=5), str(old))
    return repository.pair_device_from_ticket(code_hash, uuid.uuid4().hex + uuid.uuid4().hex, pairing_protocol=2)


def read_device(device_id):
    from src.db.database import get_db_connection
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM local_tool_devices WHERE id = %s", (str(device_id),))
        return dict(cur.fetchone())


def test_concurrent_same_old_only_one_winner_and_third_selected_unchanged():
    from src.local_tools import repository
    tenant, user = scope()
    old, third = old_device(tenant, user), old_device(tenant, user)
    a, b = replacement(tenant, user, old), replacement(tenant, user, old)
    assert read_device(old)["status"] == "active"  # Both pairs must preserve old.
    assert repository.select_device(tenant, user, str(third))

    def complete(device):
        try:
            return str(device["id"]), repository.complete_device_replacement(tenant, user, str(device["id"]))
        except repository.DeviceReplacementError as error:
            return str(device["id"]), error.code

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(complete, [a, b]))
    winner = next(device for device, status in results if status is True)
    loser = next(device for device, status in results if status == "REPLACEMENT_ALREADY_COMPLETED")
    assert read_device(old)["status"] == "revoked"
    assert str(read_device(old)["replaced_by_device_id"]) == winner
    assert read_device(third)["selected"] is True
    assert read_device(third)["status"] == "active"
    assert read_device(winner)["selected"] is False
    assert read_device(loser)["replacement_completed_at"] is None
    assert repository.complete_device_replacement(tenant, user, winner) is True
    with pytest.raises(repository.DeviceReplacementError, match="REPLACEMENT_ALREADY_COMPLETED"):
        repository.complete_device_replacement(tenant, user, loser)


def test_protocol_rejection_does_not_consume_and_insert_failure_rolls_back():
    from src.local_tools import repository
    from src.db.database import get_db_connection
    tenant, user = scope()
    old = old_device(tenant, user)
    code_hash = uuid.uuid4().hex
    repository.issue_pairing_ticket(tenant, user, code_hash, datetime.now() + timedelta(minutes=5), str(old))
    with pytest.raises(repository.DeviceReplacementError, match="PAIRING_PROTOCOL_REQUIRED"):
        repository.pair_device_from_ticket(code_hash, "synthetic-unused-tokenhash")
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT used_at FROM local_tool_pairing_tickets WHERE code_hash = %s", (code_hash,))
        assert cur.fetchone()["used_at"] is None
    existing_hash = read_device(old)["token_hash"]
    with pytest.raises(psycopg2.IntegrityError):
        repository.pair_device_from_ticket(code_hash, existing_hash, pairing_protocol=2)
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT used_at FROM local_tool_pairing_tickets WHERE code_hash = %s", (code_hash,))
        assert cur.fetchone()["used_at"] is None
    assert read_device(old)["status"] == "active"
    assert replacement(tenant, user, old)["replacement_device_id"] == old


def test_complete_second_update_failure_rolls_back_old_revoke():
    from src.local_tools import repository
    from src.db.database import get_db_connection
    tenant, user = scope()
    old = old_device(tenant, user)
    new = replacement(tenant, user, old)
    original = repository.get_db_connection

    @contextmanager
    def failing_connection():
        with original() as conn:
            cursor = conn.cursor()
            execute = cursor.execute
            class Cursor:
                def __getattr__(self, key):
                    return getattr(cursor, key)
                def execute(self, sql, args):
                    if "SET replacement_completed_at" in sql:
                        raise RuntimeError("SYNTHETIC_REPLACEMENT_COMPLETE_FAILURE")
                    return execute(sql, args)
            conn.cursor = lambda: Cursor()
            yield conn

    with patch.object(repository, "get_db_connection", failing_connection):
        with pytest.raises(RuntimeError, match="SYNTHETIC_REPLACEMENT_COMPLETE_FAILURE"):
            repository.complete_device_replacement(tenant, user, str(new["id"]))
    assert read_device(old)["status"] == "active"
    assert read_device(old)["replaced_by_device_id"] is None
    assert read_device(new["id"])["replacement_completed_at"] is None
    assert repository.complete_device_replacement(tenant, user, str(new["id"]))


def test_owned_revoked_target_allowed_foreign_target_and_revoked_new_rejected():
    from src.local_tools import repository
    tenant, user = scope()
    foreign = old_device(tenant + "-foreign", user)
    with pytest.raises(repository.DeviceReplacementError, match="REPLACEMENT_TARGET_NOT_FOUND"):
        repository.issue_pairing_ticket(tenant, user, uuid.uuid4().hex, datetime.now() + timedelta(minutes=5), str(foreign))
    old = old_device(tenant, user)
    assert repository.revoke_device(tenant, user, str(old))
    new = replacement(tenant, user, old)
    assert repository.complete_device_replacement(tenant, user, str(new["id"]))
    other = replacement(tenant, user, old)
    assert repository.revoke_device(tenant, user, str(other["id"]))
    with pytest.raises(repository.DeviceReplacementError, match="REPLACEMENT_DEVICE_REVOKED"):
        repository.complete_device_replacement(tenant, user, str(other["id"]))


def test_concurrent_generation_leaves_only_one_unused_ticket():
    from src.local_tools import repository
    from src.db.database import get_db_connection
    tenant, user = scope()
    def issue(_):
        return repository.issue_pairing_ticket(tenant, user, uuid.uuid4().hex, datetime.now() + timedelta(minutes=5))
    with ThreadPoolExecutor(max_workers=4) as workers:
        assert len(set(workers.map(issue, range(8)))) == 8
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS n FROM local_tool_pairing_tickets WHERE tenant_id = %s AND user_id = %s AND used_at IS NULL", (tenant, user))
        assert cur.fetchone()["n"] == 1


def test_incremental_schema_updates_old_tables_and_is_idempotent():
    from pathlib import Path
    import yaml
    from src.db.database import get_db_connection
    blocks = yaml.safe_load((Path(__file__).resolve().parents[2] / "deploy/db_update.yaml").read_text(encoding="utf-8"))
    sql = next(block["statements"] for block in blocks if block["datetime"] == "2026-10-13 17:00:00")
    schema = "lt_migration_" + uuid.uuid4().hex
    with get_db_connection() as conn:
        cur = conn.cursor()
        # Generated identifier, not caller input. Everything rolls back at exit.
        cur.execute(f'CREATE SCHEMA "{schema}"')
        cur.execute(f'SET LOCAL search_path TO "{schema}"')
        cur.execute("CREATE TABLE local_tool_devices (id UUID)")
        cur.execute("CREATE TABLE local_tool_pairing_tickets (id UUID)")
        cur.execute(sql)
        cur.execute(sql)
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = %s AND table_name = 'local_tool_devices'", (schema,))
        assert {row["column_name"] for row in cur.fetchall()} == {"id", "replacement_device_id", "replacement_completed_at", "replaced_by_device_id"}


def test_runtime_http_auth_only_new_token_completes_bound_old():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.local_tools import repository, api
    from src.local_tools.security import generate_device_token, sha256_hex
    tenant, user = scope()
    old_token, new_token, other_token = (generate_device_token() for _ in range(3))
    old = repository.create_device(tenant, user, sha256_hex(old_token))["id"]
    other = repository.create_device(tenant, user, sha256_hex(other_token))["id"]
    code = uuid.uuid4().hex
    repository.issue_pairing_ticket(tenant, user, code, datetime.now() + timedelta(minutes=5), str(old))
    new = repository.pair_device_from_ticket(code, sha256_hex(new_token), pairing_protocol=2)
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as client:
        route = "/api/local-tools/runtime/replacement/complete"
        assert client.post(route).status_code == 401
        assert client.post(route, headers={"Authorization": "Bearer SYNTHETIC-WEB-AUTH"}).status_code == 401
        assert client.post(route, headers={"Authorization": "Bearer " + old_token}, json={}).json()["replacement_completed"] is False
        assert read_device(old)["status"] == "active"
        headers = {"Authorization": "Bearer " + new_token, "X-Tenant-Id": "spoofed-other-tenant"}
        assert client.post(route, headers=headers, json={"old_device_id": str(other)}).status_code == 422
        assert client.post(route, headers=headers, json={}).json()["replacement_completed"] is True
        assert client.post(route, headers=headers, json={}).json()["replacement_completed"] is True
        assert client.post("/api/local-tools/runtime/heartbeat", headers={"Authorization": "Bearer " + old_token}, json={}).status_code == 401
        assert client.post("/api/local-tools/runtime/heartbeat", headers={"Authorization": "Bearer " + other_token}, json={}).status_code == 200
    assert read_device(other)["status"] == "active"
    assert read_device(new["id"])["replacement_completed_at"] is not None
