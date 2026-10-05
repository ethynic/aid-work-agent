"""Real immutable receipts and real price-table snapshots, without provider IO."""
from decimal import Decimal
import json
import uuid

import pytest

from src.core.agent_engine.contracts import CheckpointFailure
from src.db.database import get_db_connection
from src.services.agent_runner.ownership import Attempt, LeaseLost
from src.services.agent_runner.usage_pricing import grouped_cost
from src.services.agent_runner.usage_repository import UsageRepository

from .test_storage import attempt, storage

pytestmark = pytest.mark.integration


@pytest.fixture
def prices(service_database, monkeypatch):
    # Freeze real settings through supported configuration, not fake a resolver.
    for name in ("USAGE_FACTOR", "EMBEDDING_USAGE_FACTOR", "ASR_USAGE_FACTOR"):
        monkeypatch.setenv(name, "100")
    with get_db_connection() as connection:
        cursor = connection.cursor()
        cursor.execute("SELECT current_database() AS name")
        assert cursor.fetchone()["name"] == service_database.name
    model = "runner-price-" + uuid.uuid4().hex
    tiered = model + "-tiered"
    service_database.rows("""INSERT INTO token_cost_prices(model_name,input_price_per_m,output_price_per_m,
        cached_input_price_per_m,embedding_price_per_m,asr_price_per_call) VALUES (%s,1,2,0.2,1,0.001)""", (model,))
    service_database.rows("INSERT INTO token_cost_prices(model_name,tiered_pricing) VALUES (%s,%s::jsonb)",
        (tiered, json.dumps([{"max_input": 100, "input_per_m": 10, "cached_input_per_m": 2, "output_per_m": 20},
                            {"max_input": 200, "input_per_m": 20, "cached_input_per_m": 4, "output_per_m": 40}])))
    try:
        yield model, tiered
    finally:
        service_database.rows("DELETE FROM token_cost_prices WHERE model_name=ANY(%s)", ([model, tiered],))


@pytest.fixture
def ledger(storage, actors, service_database, prices):
    repository, execution, submit = storage
    row, principal = submit(actors["a"])
    owned = execution.acquire("receipt-owner", 30)
    usage = UsageRepository(service_database.connect)
    return repository, execution, usage, owned, principal, prices


def started(ledger, *, call=None, model=None, **kwargs):
    _, _, usage, owned, _, prices = ledger
    return usage.start(attempt(owned), call_id=call or uuid.uuid4().hex, execution_id="root-execution",
                       provider="qwen", model=model or prices[0], **kwargs)


def observed(ledger, *, fact=None, purpose="llm", **kwargs):
    receipt = started(ledger, purpose=purpose, **kwargs)
    usage, owned = ledger[2], ledger[3]
    return usage.observe(attempt(owned), receipt["receipt_id"], fact or {"prompt_tokens": 10, "completion_tokens": 0},
                         provider="qwen", model=receipt["model"], request_id="fixture-" + receipt["call_id"])


def test_call_start_and_fact_replay_do_not_create_another_receipt(ledger, service_database):
    receipt = started(ledger, call="unique-call")
    usage, owned = ledger[2], ledger[3]
    with pytest.raises(CheckpointFailure, match="USAGE_CALL_ALREADY_DISPATCHED"):
        started(ledger, call="unique-call")
    fact = {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}
    first = usage.observe(attempt(owned), receipt["receipt_id"], fact, provider="qwen", model=receipt["model"], request_id="actual-remote-id")
    again = usage.observe(attempt(owned), receipt["receipt_id"], dict(reversed(list(fact.items()))),
                          provider="qwen", model=receipt["model"], request_id="actual-remote-id")
    assert first == again and first["phase"] == "observed"
    rows = service_database.rows("SELECT * FROM agent_runner_usage_receipts WHERE runner_id=%s", (owned["runner_id"],))
    assert len(rows) == 1 and rows[0]["observed_at"] == first["observed_at"]
    assert ledger[0].get(owned["runner_id"])["revision"] == owned["revision"]


@pytest.mark.parametrize("change", ["usage", "request_id", "provider", "model", "attempt", "runner"])
def test_original_receipt_authority_and_observed_fact_are_immutable(ledger, change, service_database):
    receipt = observed(ledger)
    usage, owned = ledger[2], ledger[3]
    authority = attempt(owned)
    fact = receipt["usage"]
    provider, model, request_id = "qwen", receipt["model"], receipt["provider_request_id"]
    if change == "usage":
        fact = {"prompt_tokens": 11, "completion_tokens": 0}
    elif change == "request_id":
        request_id = "different-provider-request"
    elif change == "provider":
        provider = "different-provider"
    elif change == "model":
        model = "different-model"
    elif change == "attempt":
        authority = Attempt(owned["runner_id"], owned["worker_id"], owned["attempt"] + 1)
    else:
        authority = Attempt("foreign-runner", owned["worker_id"], owned["attempt"])
    with pytest.raises(CheckpointFailure):
        usage.observe(authority, receipt["receipt_id"], fact, provider=provider, model=model, request_id=request_id)
    stored = service_database.rows("SELECT usage,fact_digest,provider_request_id FROM agent_runner_usage_receipts WHERE receipt_id=%s", (receipt["receipt_id"],))[0]
    assert stored["usage"] == receipt["usage"] and stored["fact_digest"] == receipt["fact_digest"]
    assert stored["provider_request_id"] == receipt["provider_request_id"]


@pytest.mark.parametrize("owner,fact", [("llm", {"total_tokens": 7}), ("llm", {"prompt_tokens": 7}),
                                       ("embedding", {"total_tokens": 7}), ("asr", {"total_tokens": 7})])
def test_partial_provider_facts_never_become_observed_free_usage(ledger, service_database, owner, fact):
    receipt = started(ledger, owner=owner)
    usage, owned = ledger[2], ledger[3]
    with pytest.raises(CheckpointFailure, match="PROVIDER_USAGE_NOT_REPORTED"):
        usage.observe(attempt(owned), receipt["receipt_id"], fact)
    row = service_database.rows("SELECT phase,usage,fact_digest FROM agent_runner_usage_receipts WHERE receipt_id=%s", (receipt["receipt_id"],))[0]
    assert row["phase"] == "started" and row["usage"] is None and row["fact_digest"] is None


@pytest.mark.parametrize("fact,code", [({"prompt_tokens": -1, "completion_tokens": 0}, "PROVIDER_USAGE_INVALID"),
                                      ({"prompt_tokens": True, "completion_tokens": 0}, "PROVIDER_USAGE_INVALID"),
                                      ({"prompt_tokens": 1, "completion_tokens": 0, "cached_tokens": 2}, "PROVIDER_CACHE_USAGE_INVALID")])
def test_invalid_usage_cannot_mutate_a_started_receipt(ledger, service_database, fact, code):
    receipt = started(ledger)
    with pytest.raises(CheckpointFailure, match=code):
        ledger[2].observe(attempt(ledger[3]), receipt["receipt_id"], fact)
    assert service_database.rows("SELECT phase FROM agent_runner_usage_receipts WHERE receipt_id=%s", (receipt["receipt_id"],))[0]["phase"] == "started"


def test_late_observation_after_lease_loss_preserves_execution_state_and_cannot_dispatch(ledger, service_database):
    repository, execution, usage, owned, _, _ = ledger
    receipt = started(ledger)
    usage.uncertain(attempt(owned), receipt["receipt_id"])
    service_database.rows("UPDATE agent_runners SET lease_until=clock_timestamp()-INTERVAL '1 second' WHERE runner_id=%s", (owned["runner_id"],))
    execution.reap_expired()
    before = repository.get(owned["runner_id"])
    late = usage.observe(attempt(owned), receipt["receipt_id"], {"prompt_tokens": 10, "completion_tokens": 2})
    after = repository.get(owned["runner_id"])
    assert late["phase"] == "observed" and after["settlement_status"] == "pending"
    for field in ("status", "attempt", "worker_id", "lease_until", "revision", "checkpoint", "result"):
        assert after[field] == before[field]
    with pytest.raises(LeaseLost):
        execution.assert_dispatch(attempt(owned))
    with pytest.raises(LeaseLost):
        started(ledger, call="late-cannot-dispatch")


def test_main_and_background_share_rounding_but_skill_calls_have_own_boundaries(ledger):
    main = observed(ledger, purpose="llm")
    summary = observed(ledger, purpose="compression")
    main_cost, totals, detail = grouped_cost([main, summary])
    assert main_cost == Decimal("0.01") and totals["prompt_tokens"] == 20
    assert len(detail["groups"]) == 1
    skill_a = observed(ledger, call="skill-a", boundary="skill-call:skill-a", purpose="skill")
    skill_b = observed(ledger, call="skill-b", boundary="skill-call:skill-b", purpose="skill")
    skill_cost, _, detail = grouped_cost([skill_a, skill_b])
    assert skill_cost == Decimal("0.02") and len(detail["groups"]) == 2
    with pytest.raises(CheckpointFailure, match="SKILL_BILLING_BOUNDARY_MISMATCH"):
        started(ledger, call="skill-c", boundary="skill-call:skill-a")
    with pytest.raises(ValueError, match="SKILL_BILLING_BOUNDARY_NOT_UNIQUE"):
        grouped_cost([skill_a, skill_a])


def test_price_and_factor_snapshot_survive_table_and_configuration_changes(ledger, service_database, monkeypatch):
    first = observed(ledger)
    second = observed(ledger)
    model = ledger[5][0]
    service_database.rows("UPDATE token_cost_prices SET input_price_per_m=100 WHERE model_name=%s", (model,))
    monkeypatch.setenv("USAGE_FACTOR", "200")
    third = observed(ledger)
    assert first["price_snapshot"]["usage_factor"] == second["price_snapshot"]["usage_factor"] == 100
    assert third["price_snapshot"]["usage_factor"] == 200
    cost, _, detail = grouped_cost([first, second, third])
    assert cost == Decimal("0.21") and len(detail["groups"]) == 2
    # Re-read durable old receipts after repricing, not an in-memory assertion.
    old = service_database.rows("SELECT price_snapshot FROM agent_runner_usage_receipts WHERE receipt_id=%s", (first["receipt_id"],))[0]
    assert old["price_snapshot"] == first["price_snapshot"]


def test_tiered_model_prices_each_actual_request_before_group_rounding(ledger):
    model = ledger[5][1]
    first = observed(ledger, model=model, fact={"prompt_tokens": 80, "completion_tokens": 0})
    second = observed(ledger, model=model, fact={"prompt_tokens": 150, "completion_tokens": 0})
    cost, totals, detail = grouped_cost([first, second])
    assert cost == Decimal("0.38") and totals["prompt_tokens"] == 230
    assert len(detail["groups"]) == 1


def test_embedding_asr_and_local_reference_have_distinct_billing_owners(ledger):
    embedding_a = observed(ledger, owner="embedding", fact={"tokens": 10})
    embedding_b = observed(ledger, owner="embedding", fact={"tokens": 10})
    asr = observed(ledger, owner="asr", fact={"calls": 2})
    local = observed(ledger, owner="local_reference", fact={"prompt_tokens": 500000, "completion_tokens": 500000})
    cost, totals, detail = grouped_cost([embedding_a, embedding_b, asr, local])
    assert cost == Decimal("0.21")
    assert totals["embedding_tokens"] == 20 and totals["asr_calls"] == 2
    assert totals["total_token_count"] == 0 and len(detail["groups"]) == 2


def test_unpriced_main_is_zero_but_skill_retains_legacy_fallback_price(ledger):
    missing = "missing-runner-price-" + uuid.uuid4().hex
    main = observed(ledger, model=missing)
    skill = observed(ledger, call="fallback-skill", model=missing, boundary="skill-call:fallback-skill")
    assert main["price_snapshot"]["price"] == {}
    assert skill["price_snapshot"]["price"]["price_model"] == "deepseek-flash"
    assert grouped_cost([main])[0] == Decimal("0")
    assert grouped_cost([skill])[0] > 0


def test_real_foreign_tenant_attempt_cannot_observe_another_runners_receipt(ledger, storage, actors, service_database):
    receipt = started(ledger)
    foreign, _ = storage[2](actors["b"])
    foreign_owned = storage[1].acquire("foreign-tenant-worker", 30)
    assert foreign_owned["runner_id"] == foreign["runner_id"]
    assert foreign_owned["tenant_id"] != ledger[3]["tenant_id"]
    with pytest.raises(CheckpointFailure, match="USAGE_RECEIPT_NOT_AUTHORIZED"):
        ledger[2].observe(attempt(foreign_owned), receipt["receipt_id"], {"prompt_tokens": 10, "completion_tokens": 0})
    rows = service_database.rows("SELECT phase,usage FROM agent_runner_usage_receipts WHERE receipt_id=%s", (receipt["receipt_id"],))
    assert rows == [{"phase": "started", "usage": None}]


def test_parent_and_child_actual_models_are_priced_in_separate_groups(ledger):
    main = observed(ledger)
    usage, owned = ledger[2], ledger[3]
    child_model = ledger[5][1]
    child_start = usage.start(attempt(owned), call_id="child-model-call", execution_id="child-execution",
                              purpose="child", provider="qwen", model=child_model)
    child = usage.observe(attempt(owned), child_start["receipt_id"], {"prompt_tokens": 80, "completion_tokens": 0})
    cost, totals, detail = grouped_cost([main, child])
    assert cost == Decimal("0.09") and totals["prompt_tokens"] == 90
    assert {group["model"] for group in detail["groups"]} == {main["model"], child_model}
