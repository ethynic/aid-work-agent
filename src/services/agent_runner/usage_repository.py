"""Immutable provider facts; late observations never grant execution authority."""

import hashlib
import json
import uuid
from src.core.agent_engine.contracts import CheckpointFailure
from src.db.database import get_db_connection
from .contracts import canonical_json
from .ownership import lock_runner
from .event_repository import EventRepository
from .usage_pricing import freeze_price


def receipt_row(row):
    if row is None:
        return None
    result = dict(row)
    for key in ("usage", "price_snapshot"):
        if isinstance(result.get(key), str):
            result[key] = json.loads(result[key])
    return result


class UsageRepository:
    def __init__(self, connection_factory=get_db_connection, price_resolver=freeze_price):
        self.connection_factory, self.price_resolver = connection_factory, price_resolver

    def start(self, attempt, *, call_id, execution_id, tool_call_id=None, owner="llm", purpose="llm",
              boundary="main", provider=None, model=None, domain_authorization=None):
        validate_start(call_id,owner,purpose,boundary,domain_authorization)
        snapshot = self.price_resolver(owner, model, boundary)
        with self.connection_factory() as conn:
            result = self.start_in_tx(conn.cursor(), attempt, call_id=call_id,
                execution_id=execution_id, tool_call_id=tool_call_id, owner=owner,
                purpose=purpose, boundary=boundary, provider=provider, model=model,
                price_snapshot=snapshot, domain_authorization=domain_authorization)
            conn.commit()
            return result

    def start_in_tx(self, cursor, attempt, *, call_id, execution_id, price_snapshot,
                    tool_call_id=None, owner='llm', purpose='llm', boundary='main',
                    provider=None, model=None, domain_authorization=None):
        """Caller owns the root transaction; pricing is frozen before entering it."""
        validate_start(call_id,owner,purpose,boundary,domain_authorization)
        snapshot = price_snapshot
        runner = lock_runner(cursor, attempt.runner_id, attempt, dispatch=True)
        if domain_authorization is not None:
            if owner!='llm':
                raise CheckpointFailure('DOMAIN_MODEL_PURPOSE_INVALID')
            from .domain_usage import assert_physical_authorization
            purpose = assert_physical_authorization(cursor,runner,attempt,domain_authorization,
                execution_id=execution_id,tool_call_id=tool_call_id,purpose=purpose,call_id=call_id,provider=provider,model=model)
        cursor.execute("""INSERT INTO agent_runner_usage_receipts
            (receipt_id,runner_id,tenant_id,scope_key,call_id,execution_id,tool_call_id,authorized_attempt,
             owner,purpose,billing_boundary,provider,model,price_snapshot)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
            ON CONFLICT(runner_id,call_id) DO NOTHING RETURNING *""",
            ("receipt_" + uuid.uuid4().hex, attempt.runner_id, runner["tenant_id"], runner["scope_key"],
             call_id, execution_id, tool_call_id, attempt.number, owner, purpose, boundary,
             provider, model, json.dumps(snapshot, ensure_ascii=False)))
        result = receipt_row(cursor.fetchone())
        if result is None:
            # Reusing a call identity is never permission to repeat its remote
            # operation. Recovery queries this original fact instead.
            raise CheckpointFailure("USAGE_CALL_ALREADY_DISPATCHED")
        return result

    def observe(self, attempt, receipt_id, usage, *, provider=None, model=None, request_id=None):
        usage=normalize_fact(usage)
        with self.connection_factory() as conn:
            result = self.observe_in_tx(conn.cursor(), attempt, receipt_id, usage,
                provider=provider, model=model, request_id=request_id)
            conn.commit()
            return result

    def observe_in_tx(self, cursor, attempt, receipt_id, usage, *, provider=None, model=None, request_id=None):
        usage = normalize_fact(usage)
        runner = lock_runner(cursor, attempt.runner_id)
        cursor.execute("SELECT * FROM agent_runner_usage_receipts WHERE receipt_id=%s AND runner_id=%s FOR UPDATE",
                       (receipt_id, attempt.runner_id))
        receipt = receipt_row(cursor.fetchone())
        if not receipt or receipt["authorized_attempt"] != attempt.number:
            raise CheckpointFailure("USAGE_RECEIPT_NOT_AUTHORIZED")
        required = {'llm': {'prompt_tokens','completion_tokens'}, 'embedding': {'tokens'},
                    'asr': {'calls'}, 'local_reference': set()}.get(receipt['owner'])
        if required is None or not required.issubset(usage):
            raise CheckpointFailure('PROVIDER_USAGE_NOT_REPORTED')
        if receipt["tenant_id"] != runner["tenant_id"] or receipt["scope_key"] != runner["scope_key"]:
            raise CheckpointFailure("USAGE_RECEIPT_SCOPE_MISMATCH")
        if provider is not None and provider != receipt["provider"] or model is not None and model != receipt["model"]:
            raise CheckpointFailure("USAGE_PROVIDER_MISMATCH")
        fact = {"usage": usage, "provider": receipt["provider"], "model": receipt["model"], "request_id": request_id or ""}
        digest = hashlib.sha256(canonical_json(fact).encode()).hexdigest()
        if receipt["phase"] == "observed":
            if receipt["fact_digest"] != digest:
                raise CheckpointFailure("USAGE_FACT_CONFLICT")
            return receipt
        if receipt["phase"] not in ("started", "unknown"):
            raise CheckpointFailure("USAGE_RECEIPT_CLOSED")
        cursor.execute("""UPDATE agent_runner_usage_receipts SET phase='observed',usage=%s::jsonb,
            provider_request_id=%s,fact_digest=%s,observed_at=clock_timestamp()
            WHERE receipt_id=%s RETURNING *""", (json.dumps(usage), request_id or "", digest, receipt_id))
        result = receipt_row(cursor.fetchone())
        cursor.execute("""UPDATE agent_runners SET settlement_status='pending',
            view_revision=view_revision+CASE WHEN settlement_status<>'pending' THEN 1 ELSE 0 END
            WHERE runner_id=%s""", (attempt.runner_id,))
        EventRepository.notify_in_tx(cursor,runner)
        return result

    def uncertain(self, attempt, receipt_id):
        with self.connection_factory() as conn:
            self.uncertain_in_tx(conn.cursor(), attempt, receipt_id)
            conn.commit()

    @staticmethod
    def uncertain_in_tx(cursor, attempt, receipt_id):
        lock_runner(cursor, attempt.runner_id)
        cursor.execute("""UPDATE agent_runner_usage_receipts SET phase='unknown'
            WHERE runner_id=%s AND receipt_id=%s AND authorized_attempt=%s AND phase='started'""",
            (attempt.runner_id, receipt_id, attempt.number))

    @staticmethod
    def facts_in_tx(cursor, runner_id):
        cursor.execute("SELECT * FROM agent_runner_usage_receipts WHERE runner_id=%s ORDER BY receipt_id FOR UPDATE", (runner_id,))
        return [receipt_row(row) for row in cursor.fetchall()]


def validate_start(call_id,owner,purpose,boundary,domain_authorization):
    if boundary.startswith("skill-call:") and boundary != "skill-call:" + call_id:
        raise CheckpointFailure("SKILL_BILLING_BOUNDARY_MISMATCH")
    covered = boundary.startswith('covered:resume-recognition:')
    if (covered or purpose=='resume_recognition_covered') and (boundary!='covered:resume-recognition:'+call_id
            or owner!='llm' or purpose!='resume_recognition_covered' or domain_authorization is None):
        raise CheckpointFailure('COVERED_USAGE_OWNER_REQUIRED')

def normalize_fact(usage):
    if not isinstance(usage, dict):
        raise CheckpointFailure("PROVIDER_USAGE_NOT_REPORTED")
    aliases = {"input_tokens": "prompt_tokens", "output_tokens": "completion_tokens",
               "cached_input_tokens": "cached_tokens"}
    allowed = {"prompt_tokens", "completion_tokens", "cached_tokens", "cache_creation_tokens", "total_tokens", "tokens", "calls"}
    result = {}
    for key, value in usage.items():
        key = aliases.get(key, key)
        if key not in allowed:
            continue
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise CheckpointFailure("PROVIDER_USAGE_INVALID")
        result[key] = value
    if not result:
        raise CheckpointFailure("PROVIDER_USAGE_NOT_REPORTED")
    prompt = result.get("prompt_tokens", 0)
    if result.get("cached_tokens", 0) + result.get("cache_creation_tokens", 0) > prompt:
        raise CheckpointFailure("PROVIDER_CACHE_USAGE_INVALID")
    return result
