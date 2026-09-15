import copy

import pytest

from scripts.edge_session_metrics import STAGES, compare, distribution, summarize


def trace(step=10, n=30):
    return {"mode": "fake", "conditions": {"fixture": "same"}, "samples": [
        {"outcome": "verified", "timestamps": {key: i * step for i, key in enumerate(STAGES)},
         "peer_wait_ms": 50000, "model_calls": 1, "ocr_calls": 2, "ocr_cold_starts": 0}
        for _ in range(n)]}


def test_percentiles_and_customer_wait_excluded():
    assert distribution([1, 2, 3, 4])["p50"] == 2.5
    result = compare(trace(), trace(7))
    assert result["comparison"] == "PASS"
    assert result["a9_gate"] == "BLOCKED"
    assert result["candidate"]["visible_to_verified_ms"]["p50"] == 49
    assert result["candidate"]["peer_wait_ms"]["p50"] == 50000


def test_failure_prefix_preserved_and_regression_blocks():
    candidate = trace(1, 31)
    candidate["samples"][-1].update(outcome="unknown", timestamps={STAGES[0]: 0})
    result = compare(trace(), candidate)
    assert "unknown_regression" in result["reasons"]
    assert result["candidate"]["outcomes"]["unknown"] == 1
    assert result["candidate"]["segments_ms"]["submitted->verified"]["n"] == 30


def test_insufficient_or_different_conditions_never_pass():
    assert "insufficient_samples" in compare(trace(n=29), trace(1))["reasons"]
    candidate = trace(1)
    candidate["mode"] = "real"
    assert "conditions_mismatch" in compare(trace(), candidate)["reasons"]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, True, "10", 10**400, 1e308])
def test_bad_measurements_rejected(value):
    candidate = trace()
    candidate["samples"][0]["timestamps"]["detected"] = value
    with pytest.raises(ValueError):
        summarize(candidate)


def test_missing_or_reversed_stage_rejected():
    candidate = trace()
    del candidate["samples"][0]["timestamps"]["authorized"]
    with pytest.raises(ValueError):
        summarize(candidate)
    candidate = trace()
    candidate["samples"][0]["timestamps"]["detected"] = 50
    with pytest.raises(ValueError):
        summarize(candidate)


def test_zero_baseline_and_extra_payload_rejected():
    assert compare(trace(0), trace(0))["comparison"] == "BLOCKED"
    candidate = copy.deepcopy(trace())
    candidate["samples"][0]["message"] = "not accepted"
    with pytest.raises(ValueError):
        summarize(candidate)
