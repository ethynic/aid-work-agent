"""Finalizer terminal exit for finalizing rows whose transaction keeps failing.

Covers the 2026-10-30 retry storm incident (docs/incidents/
runner-finalize-orphan-retry-storm-20261030.md): a finalizing row used to be
re-leased forever because interrupt is a no-op for it and every finalize retry
raised the same deterministic error.
"""
import pytest

from src.core.agent_engine.contracts import CheckpointFailure
from src.services.agent_runner.execution_repository import ExecutionRepository, released_by
from src.services.agent_runner.finalizer import RunnerFinalizer
from src.services.agent_runner.usage_repository import UsageRepository

from .test_storage import attempt, storage

pytestmark = pytest.mark.integration


def finalizing_runner(storage, actors, service_database, *, with_receipt=False):
    """Submit, acquire and stage a completed finalization intent."""
    repository, execution, submit = storage
    row, _ = submit(actors["a"])
    owned = execution.acquire("finalize-abandon-owner", 30)
    assert owned["runner_id"] == row["runner_id"]
    receipt = None
    if with_receipt:
        usage = UsageRepository(service_database.connect)
        receipt = usage.start(attempt(owned), call_id="call-abandon-1", execution_id="exec-abandon-1")
    result = {"status": "completed", "output": "完成输出", "images": [], "messages": []}
    execution.stage_finalization(attempt(owned), owned["revision"], {}, result, {})
    finalizer = RunnerFinalizer(service_database.connect)
    return repository, execution, finalizer, row, owned, receipt


def test_deterministic_ownership_failure_abandons_immediately(storage, actors, service_database):
    repository, execution, finalizer, row, owned, _ = finalizing_runner(storage, actors, service_database)
    service_database.rows("DELETE FROM chat_sessions WHERE session_id=%s", (actors["a"].session_id,))
    settled = finalizer.finalize_failed(attempt(owned), CheckpointFailure("FINALIZE_SESSION_OWNER_CHANGED"))
    assert settled is not None
    stored = repository.get(row["runner_id"])
    assert stored["status"] == "failed"
    assert stored["attempt"] == owned["attempt"]
    assert stored["result"]["status"] == "failed"
    assert stored["result"]["error_code"] == "FINALIZE_SESSION_OWNER_CHANGED"
    assert stored["result"]["credit_cost"] == 0.0
    assert stored["settlement_status"] == "settled"
    assert stored["worker_id"] is None and stored["lease_until"] is None
    assert stored["checkpoint"]["finalize_abandoned"] == "FINALIZE_SESSION_OWNER_CHANGED"
    assert service_database.rows(
        "SELECT 1 FROM agent_runner_session_claims WHERE owner_runner_id=%s", (row["runner_id"],)) == []


@pytest.mark.parametrize("error_code", ["RECORD_OWNER_MISMATCH", "BILLING_TENANT_NOT_FOUND", "USAGE_COST_REGRESSION"])
def test_deterministic_settlement_failures_also_abandon_immediately(
        storage, actors, service_database, error_code):
    repository, execution, finalizer, row, owned, _ = finalizing_runner(storage, actors, service_database)
    settled = finalizer.finalize_failed(attempt(owned), CheckpointFailure(error_code))
    assert settled is not None
    stored = repository.get(row["runner_id"])
    assert stored["status"] == "failed"
    assert stored["checkpoint"]["finalize_abandoned"] == error_code


def test_transient_failure_retries_then_abandons_on_fifth(storage, actors, service_database):
    repository, execution, finalizer, row, owned, _ = finalizing_runner(storage, actors, service_database)
    error = CheckpointFailure("TRANSIENT_STORAGE_GLITCH")
    for expected in (1, 2, 3, 4):
        settled = finalizer.finalize_failed(attempt(owned), error)
        assert settled is not None and settled["status"] == "finalizing"
        stored = repository.get(row["runner_id"])
        assert stored["status"] == "finalizing"
        assert stored["checkpoint"]["finalize_failures"] == expected
        assert stored["checkpoint"]["last_finalize_error"] == "TRANSIENT_STORAGE_GLITCH"
        assert service_database.rows(
            "SELECT 1 FROM agent_runner_session_claims WHERE owner_runner_id=%s", (row["runner_id"],))
    settled = finalizer.finalize_failed(attempt(owned), error)
    assert settled is not None
    stored = repository.get(row["runner_id"])
    assert stored["status"] == "failed"
    assert stored["checkpoint"]["finalize_abandoned"] == "TRANSIENT_STORAGE_GLITCH"


def test_abandoned_release_proof_satisfies_heartbeat(storage, actors, service_database):
    repository, execution, finalizer, row, owned, receipt = finalizing_runner(
        storage, actors, service_database, with_receipt=True)
    assert receipt is not None
    settled = finalizer.finalize_failed(attempt(owned), CheckpointFailure("FINALIZE_SESSION_OWNER_CHANGED"))
    assert settled is not None
    receipts = service_database.rows(
        "SELECT applied,applied_at,record_id FROM agent_runner_usage_receipts WHERE receipt_id=%s",
        (receipt["receipt_id"],))
    assert receipts[0]["applied"] is True and receipts[0]["applied_at"] is not None
    assert receipts[0]["record_id"] is None
    stored = repository.get(row["runner_id"])
    assert released_by(stored, attempt(owned)) is True
    assert execution.heartbeat(attempt(owned), 30)["closed"] is True


def test_finalize_failed_without_authority_has_no_side_effects(storage, actors, service_database):
    repository, execution, finalizer, row, owned, _ = finalizing_runner(storage, actors, service_database)
    stale = attempt(owned)
    service_database.rows("UPDATE agent_runners SET lease_until=clock_timestamp()-INTERVAL '1 second' WHERE runner_id=%s",
                          (row["runner_id"],))
    replacement = execution.acquire("replacement-owner", 30)
    assert replacement["runner_id"] == row["runner_id"]
    settled = finalizer.finalize_failed(stale, CheckpointFailure("FINALIZE_SESSION_OWNER_CHANGED"))
    assert settled is None
    stored = repository.get(row["runner_id"])
    assert stored["status"] == "finalizing"
    assert "finalize_abandoned" not in stored["checkpoint"]
    assert "finalize_failures" not in stored["checkpoint"]
