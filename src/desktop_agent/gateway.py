"""Remote Tool Gateway using the existing ToolRegistry and ToolExecutor."""
from __future__ import annotations

import asyncio
import copy
import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

from src.tools.base import ExecutionTarget
from src.tools.executor import ToolExecutor
from src.tools.registry import ToolRegistry

from .protocol import digest, negotiate_version
from .security import AuthorizationTicketError, AuthorizationTicketSigner, TicketBinding


class GatewayError(ValueError):
    pass


_SENSITIVE_MARKERS = ("apikey", "authorization", "password", "prompt", "secret", "token")


def _is_sensitive_key(key: Any) -> bool:
    normalized = "".join(character for character in str(key).lower() if character.isalnum())
    return any(marker in normalized for marker in _SENSITIVE_MARKERS)


def _public_result(value: Any) -> Any:
    """Defense in depth: Desktop never receives common secret/prompt fields."""
    if isinstance(value, dict):
        return {key: ("[REDACTED]" if _is_sensitive_key(key) else _public_result(item)) for key, item in value.items() if not str(key).startswith("_")}
    if isinstance(value, list): return [_public_result(item) for item in value]
    return value


@dataclass
class InvocationRecord:
    tenant_id: str
    user_id: str
    request_digest: str
    response: dict[str, Any] | None
    events: list[dict[str, Any]] = field(default_factory=list)
    status: str = "pending"


class InMemoryGatewayStore:
    """Thread-safe reference store; production repository can preserve this interface."""
    def __init__(self):
        self._lock = threading.Lock()
        self._by_key: dict[tuple[str, str], InvocationRecord] = {}
        self._consumed: set[str] = set()

    def get(self, tenant_id: str, key: str) -> InvocationRecord | None:
        with self._lock: return copy.deepcopy(self._by_key.get((tenant_id, key)))

    def save(self, tenant_id: str, key: str, record: InvocationRecord) -> None:
        with self._lock: self._by_key[(tenant_id, key)] = copy.deepcopy(record)

    def reserve(self, tenant_id: str, key: str, record: InvocationRecord) -> InvocationRecord | None:
        with self._lock:
            existing = self._by_key.get((tenant_id, key))
            if existing is not None: return copy.deepcopy(existing)
            self._by_key[(tenant_id, key)] = copy.deepcopy(record)
            return None

    def claim(self, ticket_id: str, tenant_id: str, key: str, record: InvocationRecord) -> InvocationRecord | None:
        with self._lock:
            existing = self._by_key.get((tenant_id, key))
            if existing is not None: return copy.deepcopy(existing)
            if ticket_id in self._consumed: raise AuthorizationTicketError("Authorization ticket already consumed")
            self._consumed.add(ticket_id)
            self._by_key[(tenant_id, key)] = copy.deepcopy(record)
            return None

    def begin(self, tenant_id: str, key: str, user_id: str) -> str:
        with self._lock:
            record = self._by_key.get((tenant_id, key))
            if not record or record.user_id != user_id: return "missing"
            if record.status != "pending": return record.status
            record.status = "running"
            record.events.append({"seq": 2, "type": "running", "at": datetime.now(timezone.utc).isoformat()})
            return "running"

    def cancel(self, tenant_id: str, key: str, user_id: str) -> str:
        with self._lock:
            record = self._by_key.get((tenant_id, key))
            if not record or record.user_id != user_id: return "missing"
            if record.status == "pending":
                record.status = "cancelled"
                record.events.append({"seq": 2, "type": "cancelled", "at": datetime.now(timezone.utc).isoformat()})
                return "cancelled"
            return record.status

    def consume_ticket(self, ticket_id: str) -> None:
        with self._lock:
            if ticket_id in self._consumed: raise AuthorizationTicketError("Authorization ticket already consumed")
            self._consumed.add(ticket_id)


class PostgresGatewayStore:
    """Cross-worker idempotency, one-time ticket consumption and audit event storage."""
    def get(self, tenant_id: str, key: str) -> InvocationRecord | None:
        from src.db.database import get_db_connection
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT tenant_id,user_id,request_digest,response_json,events_json,status FROM desktop_remote_tool_invocations WHERE tenant_id=%s AND idempotency_key=%s", (tenant_id, key))
            row = cur.fetchone()
        if not row: return None
        return InvocationRecord(row["tenant_id"], row["user_id"], row["request_digest"], row["response_json"], row["events_json"], row["status"])

    def reserve(self, tenant_id: str, key: str, record: InvocationRecord) -> InvocationRecord | None:
        from src.db.database import get_db_connection
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""INSERT INTO desktop_remote_tool_invocations
                (tenant_id,user_id,idempotency_key,request_digest,response_json,events_json,status)
                VALUES (%s,%s,%s,%s,NULL,%s::jsonb,'pending')
                ON CONFLICT (tenant_id,idempotency_key) DO NOTHING RETURNING id""",
                (tenant_id, record.user_id, key, record.request_digest, json.dumps(record.events)))
            inserted = cur.fetchone()
            if inserted:
                conn.commit()
                return None
            cur.execute("SELECT tenant_id,user_id,request_digest,response_json,events_json,status FROM desktop_remote_tool_invocations WHERE tenant_id=%s AND idempotency_key=%s", (tenant_id, key))
            row = cur.fetchone()
            conn.commit()
        if not row: raise GatewayError("Idempotency reservation could not be read")
        return InvocationRecord(row["tenant_id"], row["user_id"], row["request_digest"], row["response_json"], row["events_json"], row["status"])

    def claim(self, ticket_id: str, tenant_id: str, key: str, record: InvocationRecord) -> InvocationRecord | None:
        """Atomically reserve the invocation and consume its one-use ticket."""
        from src.db.database import get_db_connection
        try:
            with get_db_connection() as conn:
                cur = conn.cursor()
                cur.execute("""INSERT INTO desktop_remote_tool_invocations
                    (tenant_id,user_id,idempotency_key,request_digest,response_json,events_json,status)
                    VALUES (%s,%s,%s,%s,NULL,%s::jsonb,'pending')
                    ON CONFLICT (tenant_id,idempotency_key) DO NOTHING RETURNING id""",
                    (tenant_id, record.user_id, key, record.request_digest, json.dumps(record.events)))
                if cur.fetchone():
                    cur.execute("INSERT INTO desktop_authorization_ticket_consumptions (ticket_id) VALUES (%s)", (ticket_id,))
                    conn.commit()
                    return None
                cur.execute("SELECT tenant_id,user_id,request_digest,response_json,events_json,status FROM desktop_remote_tool_invocations WHERE tenant_id=%s AND idempotency_key=%s", (tenant_id, key))
                row = cur.fetchone()
                conn.commit()
        except Exception as exc:
            if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
                raise AuthorizationTicketError("Authorization ticket already consumed") from exc
            raise
        if not row: raise GatewayError("Idempotency reservation could not be read")
        return InvocationRecord(row["tenant_id"], row["user_id"], row["request_digest"], row["response_json"], row["events_json"], row["status"])

    def begin(self, tenant_id: str, key: str, user_id: str) -> str:
        event = {"seq": 2, "type": "running", "at": datetime.now(timezone.utc).isoformat()}
        from src.db.database import get_db_connection
        with get_db_connection() as conn:
            cur = conn.cursor(); cur.execute("""UPDATE desktop_remote_tool_invocations SET status='running',events_json=events_json || %s::jsonb
                WHERE tenant_id=%s AND idempotency_key=%s AND user_id=%s AND status='pending' RETURNING status""", (json.dumps([event]), tenant_id, key, user_id)); row = cur.fetchone()
            if not row: cur.execute("SELECT status FROM desktop_remote_tool_invocations WHERE tenant_id=%s AND idempotency_key=%s AND user_id=%s", (tenant_id, key, user_id)); row = cur.fetchone()
            conn.commit()
        return row["status"] if row else "missing"

    def cancel(self, tenant_id: str, key: str, user_id: str) -> str:
        event = {"seq": 2, "type": "cancelled", "at": datetime.now(timezone.utc).isoformat()}
        from src.db.database import get_db_connection
        with get_db_connection() as conn:
            cur = conn.cursor(); cur.execute("""UPDATE desktop_remote_tool_invocations SET status='cancelled',events_json=events_json || %s::jsonb
                WHERE tenant_id=%s AND idempotency_key=%s AND user_id=%s AND status='pending' RETURNING status""", (json.dumps([event]), tenant_id, key, user_id)); row = cur.fetchone()
            if not row: cur.execute("SELECT status FROM desktop_remote_tool_invocations WHERE tenant_id=%s AND idempotency_key=%s AND user_id=%s", (tenant_id, key, user_id)); row = cur.fetchone()
            conn.commit()
        return row["status"] if row else "missing"

    def save(self, tenant_id: str, key: str, record: InvocationRecord) -> None:
        from src.db.database import get_db_connection
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""UPDATE desktop_remote_tool_invocations
                SET response_json=%s::jsonb,events_json=%s::jsonb,status='completed'
                WHERE tenant_id=%s AND idempotency_key=%s AND user_id=%s AND request_digest=%s AND status='running'""",
                (json.dumps(record.response), json.dumps(record.events), tenant_id, key, record.user_id, record.request_digest))
            if cur.rowcount != 1: raise GatewayError("Idempotency reservation completion failed")
            conn.commit()

    def consume_ticket(self, ticket_id: str) -> None:
        from src.db.database import get_db_connection
        try:
            with get_db_connection() as conn:
                cur = conn.cursor()
                cur.execute("INSERT INTO desktop_authorization_ticket_consumptions (ticket_id) VALUES (%s)", (ticket_id,))
                conn.commit()
        except Exception as exc:
            if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
                raise AuthorizationTicketError("Authorization ticket already consumed") from exc
            raise


class RemoteToolGateway:
    def __init__(self, registry: ToolRegistry, executor: ToolExecutor, signer: AuthorizationTicketSigner, allowed_tools: set[str], store: InMemoryGatewayStore | PostgresGatewayStore | None = None):
        self.registry, self.executor, self.signer = registry, executor, signer
        self.allowed_tools, self.store = set(allowed_tools), store or InMemoryGatewayStore()

    def catalog(self) -> list[dict[str, Any]]:
        result = []
        for name in sorted(self.allowed_tools):
            tool = self.registry.get_tool(name)
            if not tool or tool.execution_target not in {ExecutionTarget.SERVER, ExecutionTarget.EITHER}: continue
            schema = tool._get_parameters_schema()
            result.append({"tool_name": name, "description": tool.description, "target": "server", "schema_version": "1", "schema_digest": digest(schema), "input_schema": schema})
        return result

    async def invoke(self, body: Mapping[str, Any], tenant_id: str, user_id: str) -> dict[str, Any]:
        version = negotiate_version(body["supported_protocol_versions"])
        request_digest = digest({k: v for k, v in body.items() if k != "authorization_ticket"})
        existing = self.store.get(tenant_id, body["idempotency_key"])
        if existing:
            if existing.user_id != user_id or existing.request_digest != request_digest: raise GatewayError("Idempotency key reused with different request")
            if existing.response is None: raise GatewayError("Invocation is already in progress or status unknown")
            return existing.response
        tool_name = body["tool_name"]
        if any(str(key).startswith("_") for key in body["arguments"]): raise GatewayError("Reserved trusted arguments are not accepted from clients")
        descriptor = next((item for item in self.catalog() if item["tool_name"] == tool_name), None)
        if not descriptor: raise GatewayError("Tool is not permitted for Desktop Remote Gateway")
        if body["target"] != "server" or body["schema_version"] != descriptor["schema_version"] or body["schema_digest"] != descriptor["schema_digest"]:
            raise GatewayError("Tool target or schema is incompatible")
        correlation = body["correlation"]
        binding = TicketBinding(tenant_id, user_id, correlation["task_id"], correlation["execution_id"], correlation["action_id"], correlation["policy_decision_id"], body["policy_revision"], "server", tool_name, body["schema_version"], body["schema_digest"], body["arguments"])
        claims = self.signer.verify(body["authorization_ticket"], binding)
        submitted = {"seq": 1, "type": "submitted", "at": datetime.now(timezone.utc).isoformat()}
        pending = InvocationRecord(tenant_id, user_id, request_digest, None, [submitted], "pending")
        raced = self.store.claim(claims["ticket_id"], tenant_id, body["idempotency_key"], pending)
        if raced:
            if raced.user_id != user_id or raced.request_digest != request_digest: raise GatewayError("Idempotency key reused with different request")
            if raced.response is None: raise GatewayError("Invocation is already in progress or status unknown")
            return raced.response
        # Give a concurrent cancel request a deterministic reservation window.
        await asyncio.sleep(0)
        state = self.store.begin(tenant_id, body["idempotency_key"], user_id)
        if state == "cancelled": raise GatewayError("Invocation was cancelled before execution")
        if state != "running": raise GatewayError(f"Invocation cannot start from state: {state}")
        parameters = dict(body["arguments"])
        parameters.update({"_trusted_tenant_id": tenant_id, "_trusted_user_id": user_id, "_agent_execution_id": correlation["execution_id"], "_tool_call_id": correlation["invocation_id"]})
        result = _public_result(await self.executor.execute(tool_name, parameters, user_permissions=[tool_name], redact_parameter_logs=True))
        response = {"protocol_version": version, "correlation": correlation, "status": "completed", "result": result}
        events = [submitted, {"seq": 2, "type": "running", "at": datetime.now(timezone.utc).isoformat()}, {"seq": 3, "type": "completed", "at": datetime.now(timezone.utc).isoformat(), "success": bool(result.get("success"))}]
        self.store.save(tenant_id, body["idempotency_key"], InvocationRecord(tenant_id, user_id, request_digest, response, events, "completed"))
        return response

    def events(self, tenant_id: str, idempotency_key: str, user_id: str, after: int = 0) -> list[dict[str, Any]]:
        record = self.store.get(tenant_id, idempotency_key)
        if not record or record.user_id != user_id: raise GatewayError("Invocation not found")
        return [event for event in record.events if int(event.get("seq", 0)) > after]

    def cancel(self, tenant_id: str, idempotency_key: str, user_id: str) -> dict[str, Any]:
        state = self.store.cancel(tenant_id, idempotency_key, user_id)
        if state == "missing": raise GatewayError("Invocation not found")
        if state == "cancelled": return {"accepted": True, "reason": "Cancelled before execution"}
        if state == "running": return {"accepted": False, "reason": "Execution already running; cancellation is too late"}
        return {"accepted": False, "reason": f"Invocation already terminal: {state}"}
