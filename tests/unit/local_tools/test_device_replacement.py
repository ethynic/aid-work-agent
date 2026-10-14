"""Repository/API transaction boundaries; no application DB or server accessed."""
from contextlib import contextmanager
from datetime import datetime
from unittest.mock import MagicMock, patch
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.local_tools import repository, api

pytestmark = pytest.mark.unit
OLD, NEW = str(uuid.uuid4()), str(uuid.uuid4())


@contextmanager
def database(rows, fail_statement=None):
    conn = MagicMock()
    cursor = conn.cursor.return_value
    cursor.fetchone.side_effect = rows
    if fail_statement:
        def execute(sql, params):
            if fail_statement in sql:
                raise RuntimeError("synthetic database failure")
        cursor.execute.side_effect = execute
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise


def test_pair_is_single_commit_and_does_not_revoke_old():
    ticket = {"id": "ticket", "tenant_id": "t", "user_id": "u", "replacement_device_id": OLD}
    with database([ticket, {"id": OLD}, {"id": NEW, "replacement_device_id": OLD}]) as conn:
        with patch.object(repository, "get_db_connection", return_value=database_proxy(conn)):
            result = repository.pair_device_from_ticket("codehash", "tokenhash", pairing_protocol=2)
        assert result["replacement_device_id"] == OLD
        conn.commit.assert_called_once()
        sqls = [call.args[0] for call in conn.cursor().execute.call_args_list]
        assert "FOR UPDATE" in sqls[0]
        assert "tenant_id = %s AND user_id = %s" in sqls[1]
        assert not any("status = 'revoked'" in sql for sql in sqls)
        assert "INSERT INTO local_tool_devices" in sqls[2]
        assert "UPDATE local_tool_pairing_tickets" in sqls[3]


@contextmanager
def database_proxy(conn):
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise


@pytest.mark.parametrize("protocol", [None, 1, True, "2", 2.0])
def test_incompatible_client_rejected_before_ticket_consumption(protocol):
    ticket = {"id": "ticket", "tenant_id": "t", "user_id": "u", "replacement_device_id": OLD}
    with database([ticket]) as conn:
        with patch.object(repository, "get_db_connection", return_value=database_proxy(conn)):
            with pytest.raises(repository.DeviceReplacementError, match="PAIRING_PROTOCOL_REQUIRED"):
                repository.pair_device_from_ticket("hash", "tokenhash", pairing_protocol=protocol)
        conn.commit.assert_not_called()
        assert len(conn.cursor().execute.call_args_list) == 1
        conn.rollback.assert_called_once()


def test_failed_device_insert_rolls_back_ticket_consumption():
    ticket = {"id": "ticket", "tenant_id": "t", "user_id": "u", "replacement_device_id": None}
    with database([ticket], "INSERT INTO local_tool_devices") as conn:
        with patch.object(repository, "get_db_connection", return_value=database_proxy(conn)):
            with pytest.raises(RuntimeError):
                repository.pair_device_from_ticket("hash", "tokenhash")
        conn.commit.assert_not_called()
        conn.rollback.assert_called_once()
        assert not any("UPDATE local_tool_pairing_tickets" in c.args[0] for c in conn.cursor().execute.call_args_list)


@pytest.mark.parametrize("status", ["active", "revoked"])
def test_complete_binds_only_owned_old_and_does_not_select_new(status):
    device = {"id": NEW, "replacement_device_id": OLD, "replacement_completed_at": None}
    with database([device, {"id": OLD, "status": status, "replaced_by_device_id": None}]) as conn:
        with patch.object(repository, "get_db_connection", return_value=database_proxy(conn)):
            assert repository.complete_device_replacement("t", "u", NEW)
        calls = conn.cursor().execute.call_args_list
        assert all("tenant_id = %s AND user_id = %s" in c.args[0] for c in calls[1:])
        assert calls[3].args[1] == (NEW, OLD, "t", "u")
        assert calls[4].args[1] == (NEW, "t", "u")
        assert not any("selected = TRUE" in c.args[0] for c in calls)
        conn.commit.assert_called_once()


def test_another_replacement_winner_is_a_conflict_not_success():
    device = {"id": NEW, "replacement_device_id": OLD, "replacement_completed_at": None}
    with database([device, {"id": OLD, "status": "revoked", "replaced_by_device_id": str(uuid.uuid4())}]) as conn:
        with patch.object(repository, "get_db_connection", return_value=database_proxy(conn)):
            with pytest.raises(repository.DeviceReplacementError, match="REPLACEMENT_ALREADY_COMPLETED"):
                repository.complete_device_replacement("t", "u", NEW)
        conn.commit.assert_not_called()
        assert not any(c.args[0].lstrip().startswith("UPDATE") for c in conn.cursor().execute.call_args_list)


@pytest.mark.parametrize("row,expected", [
    ({"id": NEW, "replacement_device_id": None, "replacement_completed_at": None}, False),
    ({"id": NEW, "replacement_device_id": OLD, "replacement_completed_at": datetime.now()}, True),
])
def test_no_binding_and_already_completed_do_not_touch_other_devices(row, expected):
    with database([row]) as conn:
        with patch.object(repository, "get_db_connection", return_value=database_proxy(conn)):
            assert repository.complete_device_replacement("t", "u", NEW) is expected
        assert len(conn.cursor().execute.call_args_list) == 2


def test_cross_scope_or_deleted_old_cannot_be_revoked():
    device = {"id": NEW, "replacement_device_id": OLD, "replacement_completed_at": None}
    with database([device, None]) as conn:
        with patch.object(repository, "get_db_connection", return_value=database_proxy(conn)):
            with pytest.raises(repository.DeviceReplacementError, match="REPLACEMENT_TARGET_NOT_FOUND"):
                repository.complete_device_replacement("t", "u", NEW)
        conn.commit.assert_not_called()


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api._require_device] = lambda: {"id": NEW, "tenant_id": "t", "user_id": "u"}
    with TestClient(app) as value:
        yield value


def test_complete_accepts_empty_body_and_rejects_forged_target(client):
    with patch.object(repository, "complete_device_replacement", return_value=True) as complete:
        assert client.post("/api/local-tools/runtime/replacement/complete").json() == {"success": True, "replacement_completed": True}
        assert client.post("/api/local-tools/runtime/replacement/complete", json={}).status_code == 200
        assert client.post("/api/local-tools/runtime/replacement/complete", json={"old_device_id": OLD}).status_code == 422
        assert complete.call_count == 2
        complete.assert_called_with("t", "u", NEW)


def test_old_protocol_http_error_is_explicit_without_consumption(client):
    with patch("src.local_tools.pairing.pair", side_effect=repository.DeviceReplacementError("PAIRING_PROTOCOL_REQUIRED")):
        response = client.post("/api/local-tools/runtime/pair", json={"code": "ABCD2345"})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "PAIRING_PROTOCOL_REQUIRED"
    assert "新版" in response.json()["detail"]["error"]


@pytest.mark.parametrize("protocol", [True, "2", 2.0, 3])
def test_protocol_schema_is_strict(client, protocol):
    with patch("src.local_tools.pairing.pair") as pair:
        assert client.post("/api/local-tools/runtime/pair", json={"code": "ABCD2345", "pairing_protocol": protocol}).status_code == 422
        pair.assert_not_called()


def test_web_target_auth_is_context_only(client):
    with patch.object(api, "_current_user_and_tenant", return_value=({"user_id": "u"}, "t")), \
         patch("src.local_tools.pairing.create_pairing_ticket", return_value={"code": "ABCD2345", "expires_at": datetime.now(), "replacement_device_id": OLD}) as issue:
        response = client.post(f"/api/local-tools/devices/{OLD}/replacement-ticket", json={"user_id": "foreign", "tenant_id": "foreign"})
    assert response.status_code == 200
    issue.assert_called_once_with("t", "u", OLD)
