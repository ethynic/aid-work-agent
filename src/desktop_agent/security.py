"""Fail-closed PDP authorization tickets for Desktop execution PEPs."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .protocol import canonical_json, digest


class AuthorizationTicketError(ValueError):
    pass


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


@dataclass(frozen=True)
class TicketBinding:
    tenant_id: str
    user_id: str
    task_id: str
    execution_id: str
    action_id: str
    policy_decision_id: str
    policy_revision: str
    target: str
    tool_name: str
    schema_version: str
    schema_digest: str
    arguments: Mapping[str, Any]

    @property
    def args_digest(self) -> str:
        return digest(self.arguments)

    @property
    def action_digest(self) -> str:
        return digest({"action_id": self.action_id, "tool_name": self.tool_name, "target": self.target, "schema_digest": self.schema_digest, "args_digest": self.args_digest})


class AuthorizationTicketSigner:
    def __init__(self, secret: str, ttl_seconds: int = 120, clock: Callable[[], float] = time.time):
        if len(secret.encode("utf-8")) < 32:
            raise AuthorizationTicketError("DESKTOP_AGENT_AUTHORIZATION_SECRET must be at least 32 bytes")
        self._secret = secret.encode("utf-8")
        self._ttl = ttl_seconds
        self._clock = clock

    def issue(self, binding: TicketBinding) -> str:
        now = int(self._clock())
        claims = {**binding.__dict__, "ticket_id": f"ticket_{uuid.uuid4().hex}", "args_digest": binding.args_digest, "action_digest": binding.action_digest, "issued_at": now, "expires_at": now + self._ttl}
        claims.pop("arguments")
        payload = _b64(canonical_json(claims))
        signature = _b64(hmac.new(self._secret, payload.encode("ascii"), hashlib.sha256).digest())
        return f"d1.{payload}.{signature}"

    def verify(self, ticket: str, expected: TicketBinding) -> dict[str, Any]:
        try:
            prefix, payload, supplied = ticket.split(".")
            if prefix != "d1": raise ValueError("wrong prefix")
            actual = _b64(hmac.new(self._secret, payload.encode("ascii"), hashlib.sha256).digest())
            if not hmac.compare_digest(actual, supplied): raise AuthorizationTicketError("Authorization ticket signature is invalid")
            claims = json.loads(_unb64(payload))
        except AuthorizationTicketError:
            raise
        except Exception as exc:
            raise AuthorizationTicketError("Malformed authorization ticket") from exc
        if int(claims.get("expires_at", 0)) <= int(self._clock()):
            raise AuthorizationTicketError("Authorization ticket expired")
        expected_values = {**expected.__dict__, "args_digest": expected.args_digest, "action_digest": expected.action_digest}
        expected_values.pop("arguments")
        for field, value in expected_values.items():
            if claims.get(field) != value:
                raise AuthorizationTicketError(f"Authorization ticket binding mismatch: {field}")
        return claims
