"""Byte-bounded admission and capped serialization for durable runner persistence.

Entry helpers reject one oversized request/control intent (HTTP 413) before any
row is written. Capped dumps serialize a checkpoint/public snapshot once and
reuse the same string as the SQL parameter, so the per-boundary cost stays one
serialization pass: the length check rides on the payload that is persisted.
"""

import json

from src.config.settings import settings
from .contracts import RunnerError, canonical_json


def _limits():
    return settings.agent_runner.limits


def intent_bytes(intent) -> int:
    return len(canonical_json(intent).encode("utf-8"))


def assert_intent_within_request_limit(intent, *, code):
    """Admission gate for a single request/control input (queue backpressure).

    The serialized intent covers text plus inline attachment payloads, which is
    the durable footprint an unbounded input would impose on every later row.
    """
    limit = _limits().request_bytes
    size = intent_bytes(intent)
    if size > limit:
        raise RunnerError(code, 413)
    return size


def capped_checkpoint_dumps(value) -> str:
    """Serialize a checkpoint once; an oversized result is an explicit failure."""
    from src.core.agent_engine.contracts import CheckpointFailure

    payload = json.dumps(value, ensure_ascii=False)
    if len(payload.encode("utf-8")) > _limits().checkpoint_bytes:
        raise CheckpointFailure("CHECKPOINT_TOO_LARGE")
    return payload


def capped_snapshot_dumps(value) -> str:
    """Serialize a public snapshot once; an oversized result is an explicit failure."""
    from src.core.agent_engine.contracts import CheckpointFailure

    payload = json.dumps(value, ensure_ascii=False)
    if len(payload.encode("utf-8")) > _limits().snapshot_bytes:
        raise CheckpointFailure("SNAPSHOT_TOO_LARGE")
    return payload
