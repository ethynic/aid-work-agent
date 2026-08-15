"""Agent Turn orchestration seam; it does not expose prompts, provider keys or policy internals."""
from __future__ import annotations

import copy
import asyncio
import inspect
import json
import threading
from typing import Any, Mapping, Protocol

from .gateway import RemoteToolGateway
from .protocol import digest, negotiate_version
from .security import TicketBinding


class AgentTurnBackend(Protocol):
    async def next_outcome(self, turn_input: Mapping[str, Any], correlation: Mapping[str, str], tenant_id: str, user_id: str) -> dict[str, Any]: ...


class ExistingAgentBackend:
    """Pause/resume adapter over the real Agent model loop; Web/channel defaults are unchanged."""
    def __init__(self, deferred_tool_names: set[str], agent_provider=None, persist_messages=None, session_lookup=None):
        self.deferred_tool_names = set(deferred_tool_names)
        self._agent_provider = agent_provider
        self._persist_messages = persist_messages
        self._session_lookup = session_lookup

    async def _require_owned_session(self, session_ref: str, tenant_id: str, user_id: str) -> None:
        if self._session_lookup:
            session = self._session_lookup(session_ref)
            if inspect.isawaitable(session): session = await session
        else:
            from src.db.models import SessionDB
            session = await asyncio.to_thread(SessionDB.get_by_id, session_ref)
        if not session or str(session.get("tenant_id") or "") != tenant_id or str(session.get("user_id") or "") != user_id:
            # Do not reveal whether another user's session exists.
            raise ValueError("Desktop session not found or not owned by authenticated identity")

    def _agent(self, session_ref: str, tenant_id: str):
        if self._agent_provider:
            return self._agent_provider(session_ref, tenant_id)
        from src.core.agent_router import agent_router
        return agent_router.get_agent(None, session_ref, tenant_id=tenant_id)

    async def _persist(self, session_ref: str, turn_input: Mapping[str, Any], tool_messages: list[dict[str, Any]], final_text: str | None = None) -> None:
        batch = []
        if turn_input.get("type") == "user_message": batch.append({"role": "user", "content": str(turn_input.get("content") or ""), "metadata": {}})
        for message in tool_messages:
            if message.get("role") == "assistant":
                metadata = {"tool_calls": message.get("tool_calls") or []}
                if message.get("reasoning_content"): metadata["reasoning_content"] = message["reasoning_content"]
                batch.append({"role": "assistant", "content": str(message.get("content") or ""), "metadata": metadata})
            elif message.get("role") == "tool":
                content = message.get("content")
                batch.append({"role": "tool", "content": json.dumps(content, ensure_ascii=False, default=str) if isinstance(content, (dict, list)) else str(content or ""), "metadata": {"tool_call_id": message.get("tool_call_id") or ""}})
        if final_text is not None: batch.append({"role": "assistant", "content": final_text, "metadata": {}})
        if not batch: return
        if self._persist_messages:
            result = self._persist_messages(session_ref, batch)
            if inspect.isawaitable(result): await result
            return
        from src.db.models import MessageDB
        created = await asyncio.to_thread(MessageDB.create_batch_transactional, session_ref, batch)
        if created is None: raise RuntimeError("DESKTOP_TURN_PERSIST_FAILED")

    async def next_outcome(self, turn_input: Mapping[str, Any], correlation: Mapping[str, str], tenant_id: str, user_id: str) -> dict[str, Any]:
        from src.models.user import User
        await self._require_owned_session(correlation["session_ref"], tenant_id, user_id)
        agent = self._agent(correlation["session_ref"], tenant_id)
        user = User(user_id=user_id, name=user_id)
        if turn_input.get("type") == "user_message":
            events = agent.process_message(
                str(turn_input.get("content", "")),
                correlation["session_ref"],
                user=user,
                _defer_tool_names=self.deferred_tool_names,
                _deferred_tool_call_id=correlation["invocation_id"],
            )
        elif turn_input.get("type") == "tool_result":
            events = agent.continue_tool_call(
                session_id=correlation["session_ref"],
                tool_call_id=str(turn_input.get("invocation_id") or ""),
                result=dict(turn_input.get("result") or {}),
                user=user,
                defer_tool_names=self.deferred_tool_names,
                deferred_tool_call_id=correlation["invocation_id"],
            )
        else:
            return {"type": "clarification", "question": "Unsupported turn input type."}
        response_parts: list[str] = []
        clarification = None
        tool_messages: list[dict[str, Any]] = []
        async for event in events:
            event_type = event.get("type")
            if event_type == "tool_messages":
                tool_messages.extend(event.get("messages") or [])
                continue
            if event_type == "desktop_remote_tool_call":
                await self._persist(correlation["session_ref"], turn_input, tool_messages)
                return {"type": "remote_tool_call", "tool_name": event["toolName"], "arguments": event.get("toolArgs") or {}}
            if event_type == "clarification": clarification = event.get("question")
            elif event_type == "response": response_parts.append(str(event.get("data") or ""))
        final_text = "".join(response_parts)
        await self._persist(correlation["session_ref"], turn_input, tool_messages, final_text)
        if clarification: return {"type": "clarification", "question": str(clarification)}
        return {"type": "final", "message": final_text}


class InMemoryTurnStore:
    def __init__(self): self._lock, self._items = threading.Lock(), {}
    def get(self, tenant_id: str, key: str):
        with self._lock: return copy.deepcopy(self._items.get((tenant_id, key)))
    def save(self, tenant_id: str, user_id: str, key: str, request_digest: str, response: dict[str, Any]):
        with self._lock: self._items[(tenant_id, key)] = (user_id, request_digest, copy.deepcopy(response))
    def reserve(self, tenant_id: str, user_id: str, key: str, request_digest: str):
        with self._lock:
            existing = self._items.get((tenant_id, key))
            if existing is not None: return copy.deepcopy(existing)
            self._items[(tenant_id, key)] = (user_id, request_digest, None)
            return None
    def abandon(self, tenant_id: str, user_id: str, key: str, request_digest: str):
        with self._lock:
            if self._items.get((tenant_id, key)) == (user_id, request_digest, None):
                del self._items[(tenant_id, key)]


class PostgresTurnStore:
    def get(self, tenant_id: str, key: str):
        from src.db.database import get_db_connection
        with get_db_connection() as conn:
            cur = conn.cursor(); cur.execute("SELECT user_id,request_digest,response_json FROM desktop_agent_turn_requests WHERE tenant_id=%s AND idempotency_key=%s", (tenant_id, key)); row = cur.fetchone()
        return (row["user_id"], row["request_digest"], row["response_json"]) if row else None
    def reserve(self, tenant_id: str, user_id: str, key: str, request_digest: str):
        from src.db.database import get_db_connection
        with get_db_connection() as conn:
            cur = conn.cursor(); cur.execute("""INSERT INTO desktop_agent_turn_requests (tenant_id,user_id,idempotency_key,request_digest,response_json,status)
                VALUES (%s,%s,%s,%s,NULL,'pending') ON CONFLICT (tenant_id,idempotency_key) DO NOTHING RETURNING id""", (tenant_id, user_id, key, request_digest))
            inserted = cur.fetchone()
            if inserted:
                conn.commit()
                return None
            cur.execute("SELECT user_id,request_digest,response_json FROM desktop_agent_turn_requests WHERE tenant_id=%s AND idempotency_key=%s", (tenant_id, key))
            row = cur.fetchone(); conn.commit()
        if not row: raise ValueError("Idempotency reservation could not be read")
        return (row["user_id"], row["request_digest"], row["response_json"])
    def save(self, tenant_id: str, user_id: str, key: str, request_digest: str, response: dict[str, Any]):
        from src.db.database import get_db_connection
        with get_db_connection() as conn:
            cur = conn.cursor(); cur.execute("""UPDATE desktop_agent_turn_requests SET response_json=%s::jsonb,status='completed'
                WHERE tenant_id=%s AND user_id=%s AND idempotency_key=%s AND request_digest=%s AND status='pending'""", (json.dumps(response), tenant_id, user_id, key, request_digest))
            if cur.rowcount != 1: raise ValueError("Idempotency reservation completion failed")
            conn.commit()
    def abandon(self, tenant_id: str, user_id: str, key: str, request_digest: str):
        from src.db.database import get_db_connection
        with get_db_connection() as conn:
            cur = conn.cursor(); cur.execute("""DELETE FROM desktop_agent_turn_requests
                WHERE tenant_id=%s AND user_id=%s AND idempotency_key=%s AND request_digest=%s
                AND status='pending' AND response_json IS NULL""", (tenant_id, user_id, key, request_digest))
            conn.commit()


class AgentTurnService:
    def __init__(self, backend: AgentTurnBackend, gateway: RemoteToolGateway, policy_revision: str, store=None):
        self.backend, self.gateway, self.policy_revision = backend, gateway, policy_revision
        self.store = store or InMemoryTurnStore()

    async def next(self, body: Mapping[str, Any], tenant_id: str, user_id: str) -> dict[str, Any]:
        version = negotiate_version(body["supported_protocol_versions"])
        key = body["idempotency_key"]
        request_digest = digest(body)
        existing = self.store.get(tenant_id, key)
        if existing:
            old_user, old_digest, old_response = existing
            if old_user != user_id or old_digest != request_digest: raise ValueError("Idempotency key reused with different Agent Turn request")
            if old_response is None: raise ValueError("Agent Turn is already in progress or status unknown")
            return copy.deepcopy(old_response)
        raced = self.store.reserve(tenant_id, user_id, key, request_digest)
        if raced:
            old_user, old_digest, old_response = raced
            if old_user != user_id or old_digest != request_digest: raise ValueError("Idempotency key reused with different Agent Turn request")
            if old_response is None: raise ValueError("Agent Turn is already in progress or status unknown")
            return copy.deepcopy(old_response)
        correlation = body["correlation"]
        try:
            outcome = await self.backend.next_outcome(body["input"], correlation, tenant_id, user_id)
            if outcome.get("type") == "remote_tool_call":
                descriptor = next((item for item in self.gateway.catalog() if item["tool_name"] == outcome.get("tool_name")), None)
                if not descriptor: raise ValueError("Agent selected a tool not permitted by Remote Tool Gateway")
                arguments = outcome.get("arguments", {})
                outcome.update({"target": "server", "schema_version": descriptor["schema_version"], "schema_digest": descriptor["schema_digest"]})
                binding = TicketBinding(tenant_id, user_id, correlation["task_id"], correlation["execution_id"], correlation["action_id"], correlation["policy_decision_id"], self.policy_revision, "server", outcome["tool_name"], outcome["schema_version"], outcome["schema_digest"], arguments)
                outcome["authorization_ticket"] = self.gateway.signer.issue(binding)
            response = {"protocol_version": version, "correlation": correlation, "outcome": outcome}
            self.store.save(tenant_id, user_id, key, request_digest, response)
            return response
        except BaseException:
            # A failed model/persistence attempt must not strand a permanent
            # pending reservation. Conditional ownership prevents deleting a
            # completed or replacement request.
            try:
                self.store.abandon(tenant_id, user_id, key, request_digest)
            except Exception:
                # Preserve the original model/persistence failure. A database
                # outage may also make best-effort reservation cleanup fail.
                pass
            raise
