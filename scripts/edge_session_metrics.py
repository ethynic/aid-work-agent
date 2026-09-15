"""Offline C5 trace summarizer; never connects to a device or service.

Input: {mode: fake|real, conditions: {...}, samples: [{outcome, timestamps,
peer_wait_ms, model_calls, ocr_calls, ocr_cold_starts}]}. Timestamps are elapsed
milliseconds on ONE monotonic clock. No message text or identity is accepted.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import Counter
from pathlib import Path

STAGES = ("peer_message_visible", "detected", "batch_ready", "decision_ready",
          "lock_acquired", "authorized", "submitted", "verified")
OUTCOMES = {"verified", "failed", "unknown", "coverage_gap"}


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 2**53 - 1 or not math.isfinite(value):
        raise ValueError("expected finite nonnegative number")
    return value


def distribution(values):
    if not values:
        return {"n": 0, "p50": None, "p95": None, "max": None}
    values = sorted(values)
    return {"n": len(values), "p50": statistics.median(values),
            "p95": values[math.ceil(.95 * len(values)) - 1], "max": max(values)}


def summarize(trace):
    if set(trace) != {"mode", "conditions", "samples"} or trace["mode"] not in {"fake", "real"}:
        raise ValueError("invalid trace envelope")
    if not isinstance(trace["conditions"], dict) or not trace["conditions"]:
        raise ValueError("conditions must identify hardware/model/provider/replay/settings")
    if not isinstance(trace["samples"], list) or not trace["samples"]:
        raise ValueError("samples required")
    segments = {f"{a}->{b}": [] for a, b in zip(STAGES, STAGES[1:])}
    outcomes, counters = Counter(), Counter()
    totals, waits = [], []
    for sample in trace["samples"]:
        if set(sample) != {"outcome", "timestamps", "peer_wait_ms", "model_calls", "ocr_calls", "ocr_cold_starts"}:
            raise ValueError("invalid sample fields")
        outcome = sample["outcome"]
        if outcome not in OUTCOMES:
            raise ValueError("invalid outcome")
        outcomes[outcome] += 1
        times = sample["timestamps"]
        if not isinstance(times, dict) or not times or set(times) - set(STAGES):
            raise ValueError("invalid timestamps")
        # Failed samples retain their measured prefix, never invented end times.
        keys = list(STAGES[:len(times)])
        if set(times) != set(keys) or (outcome == "verified" and len(times) != len(STAGES)):
            raise ValueError("timestamps must be a contiguous stage prefix")
        values = [number(times[k]) for k in keys]
        if values != sorted(values):
            raise ValueError("timestamps must use one monotonic clock")
        for a, b in zip(keys, keys[1:]):
            segments[f"{a}->{b}"].append(times[b] - times[a])
        # Customer wait precedes peer_message_visible and is reported separately.
        waits.append(number(sample["peer_wait_ms"]))
        for key in ("model_calls", "ocr_calls", "ocr_cold_starts"):
            count = number(sample[key])
            if int(count) != count:
                raise ValueError("call counts must be integers")
            counters[key] += count
        if outcome == "verified":
            totals.append(times["verified"] - times["peer_message_visible"])
    return {"mode": trace["mode"], "samples": len(trace["samples"]),
            "outcomes": {key: outcomes[key] for key in sorted(OUTCOMES)},
            "segments_ms": {key: distribution(value) for key, value in segments.items()},
            "visible_to_verified_ms": distribution(totals),
            "peer_wait_ms": distribution(waits), "calls": dict(counters)}


def compare(baseline, candidate):
    before, after = summarize(baseline), summarize(candidate)
    reasons = []
    if baseline["mode"] != candidate["mode"] or baseline["conditions"] != candidate["conditions"]:
        reasons.append("conditions_mismatch")
    if min(before["samples"], after["samples"], before["visible_to_verified_ms"]["n"], after["visible_to_verified_ms"]["n"]) < 30:
        reasons.append("insufficient_samples")
    for outcome in OUTCOMES - {"verified"}:
        if after["outcomes"][outcome] / after["samples"] > before["outcomes"][outcome] / before["samples"]:
            reasons.append(f"{outcome}_regression")
    old, new = before["visible_to_verified_ms"]["p50"], after["visible_to_verified_ms"]["p50"]
    reduction = None if old is None or old <= 0 or new is None else 1 - new / old
    if reduction is None or reduction < .30 - 1e-12:
        reasons.append("reduction_below_30_percent")
    # This is a trace comparison, not proof of A9's no-LLM-polling/OCR/lock gates.
    return {"baseline": before, "candidate": after, "p50_reduction": reduction,
            "comparison": "BLOCKED" if reasons else "PASS", "reasons": reasons,
            "a9_gate": "BLOCKED", "note": "Trace comparison alone cannot approve rollout; independent correctness, polling, OCR and real-device evidence required."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    args = parser.parse_args()
    try:
        result = compare(json.loads(args.baseline.read_text(encoding="utf-8")),
                         json.loads(args.candidate.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError, KeyError):
        parser.exit(2, "Invalid trace input; no input contents were logged.\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["comparison"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
