"""Freeze real prices per call; aggregate using existing billing rounding boundaries."""

from collections import defaultdict
from decimal import Decimal
import json
import math
import copy
from src.config.settings import create_settings
from src.db.models import TokenCostPriceDB
from src.services.billing import (BILLING_FALLBACK_MODEL, calculate_credit_cost_with_breakdown,
    calculate_llm_credit_cost_with_breakdown, calculate_embedding_credit_cost_with_breakdown,
    calculate_asr_credit_cost_with_breakdown)
from .contracts import canonical_json


def accumulate_cost(receipts, previous=None):
    """Bounded pricing groups over new immutable receipts; round cumulative cost once.

    Independent skill calls retain their existing per-call rounding. Their IDs
    stay in the receipt ledger, while their display/credit base is folded here.
    Main tiered calls accumulate the same unrounded weighted components used by
    calculate_llm_credit_cost_with_breakdown, rather than rounding each poll.
    """
    state = copy.deepcopy(previous) if previous else {'version':1,'groups':{},'independent':{},
        'totals':{'prompt_tokens':0,'completion_tokens':0,'cached_input_tokens':0,
            'total_token_count':0,'embedding_tokens':0,'asr_calls':0}}
    if state.get('version') != 1:
        raise ValueError('USAGE_ACCUMULATOR_VERSION_UNSUPPORTED')
    for receipt in receipts:
        if receipt['phase'] != 'observed' or receipt['owner']=='local_reference':
            continue
        usage = receipt['usage']
        owner,model,boundary = receipt['owner'],receipt['model'],receipt['billing_boundary']
        prompt,completion = int(usage.get('prompt_tokens',0)),int(usage.get('completion_tokens',0))
        totals = state['totals']
        if owner=='llm':
            totals['prompt_tokens'] += prompt
            totals['completion_tokens'] += completion
            totals['cached_input_tokens'] += int(usage.get('cached_tokens',0))
            totals['total_token_count'] += prompt+completion
        elif owner=='embedding':
            totals['embedding_tokens'] += int(usage.get('tokens',0))
        elif owner=='asr':
            totals['asr_calls'] += int(usage.get('calls',0))
        if boundary.startswith('skill-call:') or receipt['price_snapshot'].get('covered_cost'):
            key = canonical_json([owner,receipt['provider'],model,'independent'])
            entry = state['independent'].setdefault(key,{'owner':owner,'provider':receipt['provider'],
                'model':model,'billing_boundary':'independent-calls','credit':'0','calls':0,'detail':{}})
            cost,_,_ = grouped_cost([receipt])
            entry['credit'] = str(Decimal(entry['credit'])+cost)
            entry['calls'] += 1
            continue
        key = canonical_json([owner,receipt['provider'],model,boundary,receipt['price_snapshot']])
        entry = state['groups'].setdefault(key,{'owner':owner,'provider':receipt['provider'],'model':model,
            'billing_boundary':boundary,'snapshot':receipt['price_snapshot'],'usage':{},'weighted':{}})
        for name,value in usage.items():
            entry['usage'][name] = entry['usage'].get(name,0)+int(value)
        tiers = entry['snapshot']['price'].get('tiered_pricing')
        if owner=='llm' and tiers:
            from src.services.billing import _pick_tier
            tier = _pick_tier(tiers,prompt)
            cached,creation = int(usage.get('cached_tokens',0)),int(usage.get('cache_creation_tokens',0))
            cached_price = tier.get('cached_input_per_m')
            weights = (prompt-cached-creation,cached,creation,completion) if cached_price is not None else (prompt,0,0,completion)
            prices = (float(tier.get('input_per_m') or 0),float(cached_price or 0),
                float(tier.get('input_per_m') or 0)*1.25,float(tier.get('output_per_m') or 0))
            for name,weight,price in zip(('input','cached','creation','output'),weights,prices):
                entry['weighted'][name] = entry['weighted'].get(name,0.0)+weight*price
                entry['weighted'][name+'_tokens'] = entry['weighted'].get(name+'_tokens',0)+weight
    total,breakdown = Decimal('0'),[]
    for entry in state['groups'].values():
        snapshot,owner = entry['snapshot'],entry['owner']
        usage,model = entry['usage'],entry['model']
        kwargs = {'model':model,'usage_factor_override':snapshot['usage_factor'],'price_snapshot':snapshot['price']}
        if owner=='llm' and snapshot['price'].get('tiered_pricing') and model:
            weighted = entry['weighted']
            raw = sum(weighted.get(name,0.0) for name in ('input','cached','creation','output'))/1_000_000
            cost = max(math.ceil(raw*snapshot['usage_factor']*100)/100,0.0)
            detail = {'tiered':True,'prompt_tokens':usage.get('prompt_tokens',0),
                'completion_tokens':usage.get('completion_tokens',0),'cached_input_tokens':usage.get('cached_tokens',0),
                'cache_creation_input_tokens':usage.get('cache_creation_tokens',0),
                'non_cached_input_tokens':weighted.get('input_tokens',0),'usage_factor':snapshot['usage_factor'],
                'unit_prices':{name:weighted.get(part,0)/weighted[part+'_tokens'] if weighted.get(part+'_tokens') else None
                    for name,part in (('input_per_m','input'),('cached_input_per_m','cached'),
                        ('cache_creation_input_per_m','creation'),('output_per_m','output'))},
                'credits':{name:round(weighted.get(part,0)/1_000_000*snapshot['usage_factor'],6)
                    for name,part in (('non_cached_input','input'),('cached_input','cached'),
                        ('cache_creation_input','creation'),('output','output'))}}
        elif owner=='llm':
            cost,detail = calculate_llm_credit_cost_with_breakdown([usage],**kwargs)
        elif owner=='embedding':
            cost,detail = calculate_embedding_credit_cost_with_breakdown(usage.get('tokens',0),**kwargs)
        else:
            cost,detail = calculate_asr_credit_cost_with_breakdown(usage.get('calls',0),**kwargs)
        total += Decimal(str(cost))
        breakdown.append({**{key:entry[key] for key in ('owner','provider','model','billing_boundary')},'credit':cost,'detail':detail})
    for entry in state['independent'].values():
        total += Decimal(entry['credit'])
        breakdown.append({**entry,'credit':float(entry['credit'])})
    return total,state['totals'],{'groups':breakdown,'runner_accumulator':state}


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
