"""视觉用量的预约/派发/结算边界；所有 DB 连接均替换，不访问默认库。"""

import json
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from src.db import client_binding_db as db


def _row(status="reserved", binding_id="binding", digest="digest"):
    return {"id": 42, "binding_id": binding_id, "status": status,
            "provider": "zhipu", "model": "GLM-5.3-Flash",
            "detail": json.dumps({"input_digest": digest,
                                  "price_snapshot": {"input": "1.00"}, "billing_factor": 20})}


@pytest.fixture
def connection(monkeypatch):
    conn = MagicMock()
    cursor = conn.cursor.return_value
    factory = MagicMock()
    factory.return_value.__enter__.return_value = conn
    monkeypatch.setattr(db, "get_db_connection", factory)
    monkeypatch.setattr(db, "invalidate_tenant_cache", MagicMock())
    return conn, cursor


def _writes(cursor, fragment):
    return [call for call in cursor.execute.call_args_list if fragment in call.args[0]]


def test_reservation_freezes_price_and_new_factor_without_debit(connection):
    conn, cursor = connection
    cursor.fetchone.side_effect = [None, {"credit_balance": Decimal("1")}, _row()]
    row = db.BossVisualUsageDB.reserve("tenant", "binding", "req", "digest", "zhipu", "GLM-5.3-Flash", {"input": "1.00"})
    assert row["detail"]["price_snapshot"] == {"input": "1.00"}
    assert row["model"] == "GLM-5.3-Flash"
    inserted = json.loads(_writes(cursor, "INSERT")[0].args[1][-1])
    assert inserted["billing_factor"] == 20
    assert not _writes(cursor, "UPDATE tenants")
    assert len(_writes(cursor, "ON CONFLICT (tenant_id, client_ref_id)")) == 1
    conn.commit.assert_called_once()


def test_runtime_device_caller_audit_is_frozen_without_creating_a_client_binding(connection):
    conn, cursor = connection
    caller = {"kind": "runtime_device", "device_id": "device", "user_id": "user"}
    row = _row(binding_id="runtime-device:device")
    detail = json.loads(row["detail"])
    detail["caller"] = caller
    row["detail"] = json.dumps(detail)
    cursor.fetchone.side_effect = [None, {"credit_balance": Decimal("1")}, row]
    actual = db.BossVisualUsageDB.reserve("tenant", "runtime-device:device", "req", "digest", "zhipu", "model", {}, caller_detail=caller)
    assert actual["detail"]["caller"] == caller
    inserted = json.loads(_writes(cursor, "INSERT")[0].args[1][-1])
    assert inserted["caller"] == caller
    assert not _writes(cursor, "client_bindings")
    conn.commit.assert_called_once()


def test_runtime_device_reservation_cannot_resume_a_different_user_or_missing_audit(connection):
    conn, cursor = connection
    cursor.fetchone.return_value = _row(binding_id="runtime-device:device")
    with pytest.raises(PermissionError):
        db.BossVisualUsageDB.reserve("tenant", "runtime-device:device", "req", "digest", "zhipu", "model", {},
            caller_detail={"kind": "runtime_device", "device_id": "device", "user_id": "user"})
    conn.commit.assert_not_called()


def test_concurrent_reservation_reads_winner_instead_of_replacing_price(connection):
    _, cursor = connection
    cursor.fetchone.side_effect = [None, {"credit_balance": Decimal("1")}, None, _row()]
    row = db.BossVisualUsageDB.reserve("tenant", "binding", "req", "digest", "other", "new-model", {"input": "999"})
    assert row["provider"] == "zhipu"
    assert row["detail"]["price_snapshot"] == {"input": "1.00"}


def test_completed_reservation_does_not_require_new_balance(connection):
    _, cursor = connection
    cursor.fetchone.return_value = _row("completed")
    assert db.BossVisualUsageDB.reserve("tenant", "binding", "req", "digest", "zhipu", "new", {})["status"] == "completed"
    assert not _writes(cursor, "FROM tenants")


@pytest.mark.parametrize("binding,digest,error", [
    ("other", "digest", PermissionError), ("binding", "different", db.BossVisualConflictError),
])
def test_id_reuse_cannot_expose_other_binding_or_change_input(connection, binding, digest, error):
    conn, cursor = connection
    cursor.fetchone.return_value = _row()
    with pytest.raises(error):
        db.BossVisualUsageDB.reserve("tenant", binding, "req", digest, "zhipu", "model", {})
    conn.commit.assert_not_called()


@pytest.mark.parametrize("balance", [Decimal("0"), Decimal("-1"), None])
def test_no_balance_never_reserves_model_call(connection, balance):
    _, cursor = connection
    cursor.fetchone.side_effect = [None, {"credit_balance": balance}]
    with pytest.raises(db.BossVisualInsufficientCreditError):
        db.BossVisualUsageDB.reserve("tenant", "binding", "req", "digest", "zhipu", "model", {})
    assert not _writes(cursor, "INSERT")


def test_reserved_request_can_resume_but_only_dispatch_once(connection):
    _, cursor = connection
    cursor.fetchone.side_effect = [_row(), {"credit_balance": Decimal("1")}, {"id": 42}]
    assert db.BossVisualUsageDB.dispatch("tenant", "binding", "req") is True
    cursor.fetchone.side_effect = None
    cursor.fetchone.return_value = _row("dispatched")
    assert db.BossVisualUsageDB.dispatch("tenant", "binding", "req") is False
    assert len(_writes(cursor, "SET status = 'dispatched'")) == 1


def test_reserved_retry_checks_balance_again_before_dispatch(connection):
    _, cursor = connection
    cursor.fetchone.side_effect = [_row(), {"credit_balance": Decimal("0")}]
    with pytest.raises(db.BossVisualInsufficientCreditError):
        db.BossVisualUsageDB.dispatch("tenant", "binding", "req")
    assert not _writes(cursor, "SET status = 'dispatched'")


def test_unknown_request_never_gets_a_second_dispatch(connection):
    _, cursor = connection
    cursor.fetchone.return_value = _row("model_unknown")
    assert db.BossVisualUsageDB.dispatch("tenant", "binding", "req") is False
    assert not _writes(cursor, "UPDATE client_usage_logs")


def test_settlement_retains_precise_cost_and_enforces_frozen_fee_once(connection):
    conn, cursor = connection
    cursor.fetchone.side_effect = [_row("dispatched"), {"credit_balance": Decimal("-0.01")}]
    original = {"diagnosis": None, "diagnosis_error": "invalid_model_output",
                "receipt": {"model": "GLM-5.3-Flash"}}
    result = db.BossVisualUsageDB.settle(
        "tenant", "binding", "req", {"prompt_tokens": 10, "completion_tokens": 2},
        Decimal("0.000222"), Decimal("0.01"), original,
    )
    assert result["diagnosis_error"] == "invalid_model_output"
    assert result["receipt"]["token_cost_yuan"] == "0.000222"
    assert result["receipt"]["credit_cost"] == "0.01"
    assert result["receipt"]["factor"] == 20
    assert result["receipt"]["balance_after"] == "-0.01"
    assert "factor" not in original["receipt"]
    assert _writes(cursor, "UPDATE tenants")[0].args[1] == (Decimal("0.01"), "tenant")
    stored = json.loads(_writes(cursor, "SET status = 'completed'")[0].args[1][6])
    assert stored["input_digest"] == "digest"
    assert stored["price_snapshot"] == {"input": "1.00"}
    assert stored["token_cost_yuan"] == "0.000222"
    conn.commit.assert_called_once()


def test_duplicate_settlement_returns_first_receipt_without_debit(connection):
    _, cursor = connection
    row = _row("completed")
    row["detail"] = json.dumps({"response": {"receipt": {"credit_cost": "0.50"}}})
    cursor.fetchone.return_value = row
    result = db.BossVisualUsageDB.settle("tenant", "binding", "req", {}, Decimal("9"), Decimal("450"), {})
    assert result == {"receipt": {"credit_cost": "0.50"}}
    assert not _writes(cursor, "UPDATE client_usage_logs")


def test_get_only_reads_current_tenant_without_balance_gate(connection):
    _, cursor = connection
    cursor.fetchone.return_value = _row("completed")
    assert db.BossVisualUsageDB.get("tenant", "req")["status"] == "completed"
    assert cursor.execute.call_args.args[1] == ("tenant", "boss.visual:req")
    assert not _writes(cursor, "FROM tenants")


def test_mark_unknown_never_reverts_dispatch_or_completed_to_reserved(connection):
    _, cursor = connection
    cursor.fetchone.return_value = _row("dispatched")
    db.BossVisualUsageDB.mark_unknown("tenant", "binding", "req")
    update = _writes(cursor, "UPDATE client_usage_logs")[0]
    assert "status = 'model_unknown'" in update.args[0]
    assert "AND status = 'dispatched'" in update.args[0]


def test_other_binding_cannot_settle_a_known_request(connection):
    _, cursor = connection
    cursor.fetchone.return_value = _row("dispatched", binding_id="other")
    with pytest.raises(PermissionError):
        db.BossVisualUsageDB.settle("tenant", "binding", "req", {}, Decimal("0"), Decimal("0"), {})
    assert not _writes(cursor, "UPDATE tenants")


@pytest.mark.parametrize("status", ["reserved", "model_unknown"])
def test_settlement_requires_a_known_dispatched_response(connection, status):
    _, cursor = connection
    cursor.fetchone.return_value = _row(status)
    with pytest.raises(db.BossVisualConflictError):
        db.BossVisualUsageDB.settle("tenant", "binding", "req", {}, Decimal("0.01"), Decimal("0.50"), {})
    assert not _writes(cursor, "UPDATE tenants")


@pytest.mark.parametrize("raw,fee", [("NaN", "0.01"), ("0.01", "-1"), ("0.01", "0.001")])
def test_invalid_final_fee_is_rejected_before_touching_ledger(connection, raw, fee):
    conn, _ = connection
    with pytest.raises(ValueError):
        db.BossVisualUsageDB.settle("tenant", "binding", "req", {}, Decimal(raw), Decimal(fee), {})
    conn.cursor.assert_not_called()



def test_reserved_new_snapshot_survives_a_different_server_policy(connection, monkeypatch):
    _, cursor = connection
    cursor.fetchone.return_value = _row()
    monkeypatch.setattr(db, "BOSS_VISUAL_BILLING_FACTOR", 50)
    result = db.BossVisualUsageDB.reserve("tenant", "binding", "req", "digest", "zhipu", "other", {})
    assert result["detail"]["billing_factor"] == 20
    assert not _writes(cursor, "INSERT")


def test_legacy_reserved_factor_is_materialized_as_50_when_dispatched(connection):
    _, cursor = connection
    row = _row()
    detail = json.loads(row["detail"])
    detail.pop("billing_factor")
    row["detail"] = json.dumps(detail)
    cursor.fetchone.side_effect = [row, {"credit_balance": Decimal("1")}, {"id": 42}]
    assert db.BossVisualUsageDB.dispatch("tenant", "binding", "req")
    update = _writes(cursor, "SET status = 'dispatched'")[0]
    assert json.loads(update.args[1][0])["billing_factor"] == 50


def test_legacy_in_flight_settlement_retains_50_without_new_policy_repricing(connection):
    _, cursor = connection
    row = _row("dispatched")
    detail = json.loads(row["detail"])
    detail.pop("billing_factor")
    row["detail"] = json.dumps(detail)
    cursor.fetchone.side_effect = [row, {"credit_balance": Decimal("0.50")}]
    result = db.BossVisualUsageDB.settle("tenant", "binding", "req", {}, Decimal("0.01"), Decimal("0.50"), {})
    assert result["receipt"]["factor"] == 50
    assert result["receipt"]["credit_cost"] == "0.50"


def test_wrong_factor_fee_is_refused_before_debit(connection):
    _, cursor = connection
    cursor.fetchone.return_value = _row("dispatched")
    with pytest.raises(ValueError, match="冻结费率"):
        db.BossVisualUsageDB.settle("tenant", "binding", "req", {}, Decimal("0.01"), Decimal("0.50"), {})
    assert not _writes(cursor, "UPDATE tenants")


def test_zero_actual_cost_remains_zero_and_does_not_invalidate_tenant_cache(connection, monkeypatch):
    _, cursor = connection
    invalidate = MagicMock()
    monkeypatch.setattr(db, "invalidate_tenant_cache", invalidate)
    cursor.fetchone.side_effect = [_row("dispatched"), {"credit_balance": Decimal("1.00")}]
    result = db.BossVisualUsageDB.settle("tenant", "binding", "req", {}, Decimal("0"), Decimal("0"), {})
    assert result["receipt"]["credit_cost"] == "0.00"
    assert result["receipt"]["factor"] == 20
    invalidate.assert_not_called()
