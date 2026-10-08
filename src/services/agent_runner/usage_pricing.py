"""Freeze real prices per call; aggregate using existing billing rounding boundaries."""

from collections import defaultdict
from decimal import Decimal
import json
from src.config.settings import create_settings
from src.db.models import TokenCostPriceDB
from src.services.billing import (BILLING_FALLBACK_MODEL, calculate_credit_cost_with_breakdown,
    calculate_llm_credit_cost_with_breakdown, calculate_embedding_credit_cost_with_breakdown,
    calculate_asr_credit_cost_with_breakdown)
from .contracts import canonical_json


def freeze_price(owner, model, boundary):
    if owner=='llm' and boundary.startswith('covered:resume-recognition:'):
        return {'version':1,'price':{},'usage_factor':100,'covered_cost':'resume_recognition'}
    price = TokenCostPriceDB.get_by_model_name(model) if model else None
    price_model = model
    if not price and boundary.startswith("skill-call:") and model:
        price = TokenCostPriceDB.get_by_model_name(BILLING_FALLBACK_MODEL)
        price_model = BILLING_FALLBACK_MODEL
    fields = ("input_price_per_m", "output_price_per_m", "cached_input_price_per_m",
              "tiered_pricing", "embedding_price_per_m", "asr_price_per_call")
    price = {key: price.get(key) for key in fields} if price else {}
    price = json.loads(json.dumps(price, default=str))
    if price:
        price["price_model"] = price_model
    billing = create_settings().billing
    factor_name = {"embedding": "embedding_usage_factor", "asr": "asr_usage_factor"}.get(owner, "usage_factor")
    return {"version": 1, "price": price, "usage_factor": getattr(billing, factor_name, 100) or 100}


def grouped_cost(receipts):
    """Pure calculation: snapshots/factors supplied, no price/settings lookup.

    Main and inline background share a boundary; legacy independently billed
    skill calls retain independent boundaries. Round once per pricing group.
    """
    groups = defaultdict(list)
    totals = {"prompt_tokens": 0, "completion_tokens": 0, "cached_input_tokens": 0,
              "total_token_count": 0, "embedding_tokens": 0, "asr_calls": 0}
    for receipt in receipts:
        if receipt["phase"] != "observed" or receipt["owner"] == "local_reference":
            continue
        usage = receipt["usage"]
        key = (receipt["owner"], receipt["provider"], receipt["model"], receipt["billing_boundary"],
               canonical_json(receipt["price_snapshot"]))
        groups[key].append(usage)
        if receipt["owner"] == "llm":
            prompt = int(usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0)
            completion = int(usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0)
            totals["prompt_tokens"] += prompt
            totals["completion_tokens"] += completion
            totals["cached_input_tokens"] += int(usage.get("cached_tokens", usage.get("cached_input_tokens", 0)) or 0)
            totals["total_token_count"] += prompt + completion
        elif receipt["owner"] == "embedding":
            totals["embedding_tokens"] += int(usage.get("tokens", 0) or 0)
        elif receipt["owner"] == "asr":
            totals["asr_calls"] += int(usage.get("calls", 0) or 0)
    total = Decimal("0")
    breakdown = []
    for (owner, provider, model, boundary, serialized), rounds in groups.items():
        snapshot = json.loads(serialized)
        kwargs = {"model": model, "usage_factor_override": snapshot["usage_factor"], "price_snapshot": snapshot["price"]}
        if owner == "llm" and snapshot.get('covered_cost')=='resume_recognition':
            cost, detail = 0, {'covered_cost':'resume_recognition'}
        elif owner == "llm":
            if boundary.startswith("skill-call:"):
                if len(rounds) != 1:
                    raise ValueError("SKILL_BILLING_BOUNDARY_NOT_UNIQUE")
                # start() binds this boundary to one immutable actual call id.
                usage = rounds[0]
                cost, detail = calculate_credit_cost_with_breakdown(
                    prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
                    completion_tokens=int(usage.get("completion_tokens", 0) or 0),
                    cached_input_tokens=int(usage.get("cached_tokens", 0) or 0),
                    cache_creation_input_tokens=int(usage.get("cache_creation_tokens", 0) or 0), **kwargs)
            else:
                cost, detail = calculate_llm_credit_cost_with_breakdown(rounds, **kwargs)
        elif owner == "embedding":
            cost, detail = calculate_embedding_credit_cost_with_breakdown(sum(int(r.get("tokens", 0) or 0) for r in rounds), **kwargs)
        else:
            cost, detail = calculate_asr_credit_cost_with_breakdown(sum(int(r.get("calls", 0) or 0) for r in rounds), **kwargs)
        total += Decimal(str(cost))
        breakdown.append({"owner": owner, "provider": provider, "model": model,
                          "billing_boundary": boundary, "credit": cost, "detail": detail})
    return total, totals, {"groups": breakdown}
