"""Scoped waiting context and deterministic redelegation preparation."""

import json
import uuid
from src.core.redis_client import redis_client
from src.core.agent_engine.contracts import ToolCall, ToolFact


class ClarificationStore:
    def __init__(self, store=None, authorize_legacy=None):
        self.store = store or redis_client
        self.authorize_legacy = authorize_legacy

    def _key(self, identity):
        scope = json.dumps([identity.tenant_id, identity.session_kind, identity.session_id, identity.user_id], separators=(",", ":"))
        return self.store.make_key("pending_clarification", scope)

    def save(self, identity, data):
        key = self._key(identity)
        self.store.hset(key, "data", data)
        self.store.expire(key, 3600)

    def read(self, identity):
        pending = self.store.hget(self._key(identity), "data") or None
        if pending or self.authorize_legacy is None:
            return pending
        old_key = self.store.make_key("pending_clarification", identity.session_id)
        legacy = self.store.hgetall(old_key) or None
        if not legacy:
            return None
        # Only compatibility ingress opts in, and it must independently verify
        # ownership before promoting an old globally keyed waiting context.
        self.authorize_legacy(identity)
        for field in ("tenant_id", "user_id"):
            if field in legacy and legacy[field] != getattr(identity, field):
                raise PermissionError("CLARIFICATION_ACCESS_DENIED")
        self.save(identity, legacy)
        self.store.delete(old_key)
        return legacy

    def clear(self, identity):
        self.store.delete(self._key(identity))


class ClarificationCoordinator:
    def __init__(self, store):
        self.store = store

    def prepare_reply(self, state, text):
        pending = self.store.read(state.identity)
        if not pending:
            return False
        task = (f"{pending['task_description']}\n\n[补充信息]\n"
                f"在执行过程中需要确认以下问题：{pending['question']}\n用户补充回答：{text}")
        call = ToolCall(f"clarification_{uuid.uuid4().hex}", "delegate_to_subagent", {
            "subagent_name": pending["subagent_name"], "task_description": task,
            "context_needed": None})
        state.messages.append({"role": "assistant", "content": "", "tool_calls": [call.message_call()]})
        state.pending = [call]
        state.tools[call.id] = ToolFact(call)
        # Presentation adapter consumes this explicit task-specific fact, not the
        # kernel. It preserves the old supplemental-answer direct-return behavior.
        state.resources["clarification_reply_call_id"] = call.id
        return True
