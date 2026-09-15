"""Regression coverage for durable billing and pricing recovery."""
from tests.unit.session_tasks._review_c3_recovery_paths import (
    gate_env, clean_probe_ledger,
    test_billing_failure_does_not_discard_model_result,
    test_one_failed_billing_does_not_block_other_recoveries,
    test_concurrent_pricing_freezes_one_amount,
    test_missing_reservation_does_not_mark_failed_pricing_settled,
)


def test_zero_model_charge_settles_without_inventing_fee(tenant_id, device_row, verified_binding, monkeypatch):
    from decimal import Decimal
    from tests.unit.session_tasks._review_c3_billing_atomic import setup_decision, response
    from src.session_tasks import decisions as d
    from src.services import billing
    from src.db.database import get_db_connection
    task, decision = setup_decision(tenant_id, device_row, verified_binding)
    monkeypatch.setattr(billing, "calculate_credit_cost_with_breakdown", lambda *a, **k: (Decimal("0"), {}))
    d.run_decision_tick(model_call=lambda *a: response())
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("""SELECT a.state, a.credit_cost, r.settled_amount, cr.credit_cost AS billed
                     FROM session_task_decision_attempts a
                     JOIN session_task_cost_reservations r ON r.tenant_id=a.tenant_id AND r.ref_key=a.attempt_ref
                     JOIN chat_records cr ON cr.tenant_id=a.tenant_id AND cr.billing_ref=a.billing_key
                     WHERE a.tenant_id=%s AND a.attempt_ref=%s""", (tenant_id, decision["decision_id"]))
        row = dict(c.fetchone())
    assert row["state"] == "settled"
    assert row["credit_cost"] == row["settled_amount"] == row["billed"] == 0
