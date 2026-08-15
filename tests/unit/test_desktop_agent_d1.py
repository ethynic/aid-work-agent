import asyncio

import pytest

from src.desktop_agent.gateway import GatewayError, InMemoryGatewayStore, RemoteToolGateway
from src.desktop_agent import protocol
from src.desktop_agent.protocol import ProtocolVersionError, digest, negotiate_version
from src.desktop_agent.security import AuthorizationTicketError, AuthorizationTicketSigner
from src.desktop_agent.turn import AgentTurnService
from src.tools.base import BaseTool
from src.tools.executor import ToolExecutor
from src.tools.registry import ToolRegistry


SECRET = "desktop-d1-test-secret-that-is-at-least-32-bytes"


class EchoTool(BaseTool):
    name = "test_echo"
    description = "Test-only echo tool"
    parameters_schema = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}

    async def execute(self, **kwargs):
        return {"success": True, "echo": kwargs["text"], "tenant": kwargs["_trusted_tenant_id"]}


class ScriptedBackend:
    async def next_outcome(self, turn_input, correlation, tenant_id, user_id):
        if turn_input["type"] == "tool_result":
            return {"type": "final", "message": f"completed: {turn_input['result']['echo']}"}
        return {"type": "remote_tool_call", "tool_name": "test_echo", "arguments": {"text": turn_input["content"]}}


def correlation():
    return {"task_id": "task-1", "session_ref": "session-1", "execution_id": "exec-1", "attempt_id": "attempt-1", "action_id": "action-1", "invocation_id": "invoke-1", "artifact_id": "artifact-none", "evidence_stream_id": "evidence-1", "release_id": "release-1", "policy_decision_id": "policy-1"}


def build(clock=lambda: 1000):
    registry = ToolRegistry(); registry.register(EchoTool())
    signer = AuthorizationTicketSigner(SECRET, ttl_seconds=60, clock=clock)
    gateway = RemoteToolGateway(registry, ToolExecutor(registry), signer, {"test_echo"}, InMemoryGatewayStore())
    return gateway, AgentTurnService(ScriptedBackend(), gateway, "policy-r1")


def next_request(key="next-key-0001", turn_input=None, versions=None):
    return {"supported_protocol_versions": versions or ["1.0"], "idempotency_key": key, "correlation": correlation(), "input": turn_input or {"type": "user_message", "content": "hello"}}


def invoke_request(outcome, key="invoke-key-0001"):
    return {"supported_protocol_versions": ["1.0"], "idempotency_key": key, "correlation": correlation(), "tool_name": outcome["tool_name"], "target": outcome["target"], "schema_version": outcome["schema_version"], "schema_digest": outcome["schema_digest"], "arguments": outcome["arguments"], "policy_revision": "policy-r1", "authorization_ticket": outcome["authorization_ticket"]}


def test_vertical_next_remote_result_final_and_duplicate():
    async def scenario():
        gateway, turns = build()
        first = await turns.next(next_request(), "tenant-1", "user-1")
        assert await turns.next(next_request(), "tenant-1", "user-1") == first
        changed_next = next_request(); changed_next["input"] = {"type": "user_message", "content": "changed"}
        with pytest.raises(ValueError, match="Idempotency"): await turns.next(changed_next, "tenant-1", "user-1")
        assert first["outcome"]["type"] == "remote_tool_call"
        invoked = await gateway.invoke(invoke_request(first["outcome"]), "tenant-1", "user-1")
        assert invoked["result"] == {"success": True, "echo": "hello", "tenant": "tenant-1"}
        assert await gateway.invoke(invoke_request(first["outcome"]), "tenant-1", "user-1") == invoked
        final = await turns.next(next_request("next-key-0002", {"type": "tool_result", "invocation_id": "invoke-1", "result": invoked["result"]}), "tenant-1", "user-1")
        assert final["outcome"] == {"type": "final", "message": "completed: hello"}
    asyncio.run(scenario())


def test_ticket_tamper_expiry_argument_target_and_claim_token_fail_closed():
    async def scenario():
        gateway, turns = build()
        outcome = (await turns.next(next_request(), "tenant-1", "user-1"))["outcome"]
        for mutate in (
            lambda req: req.update(authorization_ticket=req["authorization_ticket"] + "x"),
            lambda req: req["arguments"].update(text="substituted"),
            lambda req: req.update(target="local"),
            lambda req: req.update(authorization_ticket="claim-not-authorization"),
        ):
            req = invoke_request(outcome, f"negative-{id(mutate)}")
            req["arguments"] = dict(req["arguments"]); mutate(req)
            with pytest.raises((AuthorizationTicketError, GatewayError)): await gateway.invoke(req, "tenant-1", "user-1")
        expired_gateway, expired_turns = build(clock=lambda: 1000)
        expired = (await expired_turns.next(next_request("expired-next"), "tenant-1", "user-1"))["outcome"]
        expired_gateway.signer._clock = lambda: 1061
        with pytest.raises(AuthorizationTicketError, match="expired"): await expired_gateway.invoke(invoke_request(expired, "expired-invoke"), "tenant-1", "user-1")
    asyncio.run(scenario())


def test_schema_and_version_compatibility_and_idempotency_conflict():
    assert negotiate_version(["1.1", "1.0"]) == "1.0"
    with pytest.raises(ProtocolVersionError): negotiate_version(["2.0"])
    async def scenario():
        gateway, turns = build()
        outcome = (await turns.next(next_request(), "tenant-1", "user-1"))["outcome"]
        bad = invoke_request(outcome); bad["schema_digest"] = digest({"different": True})
        with pytest.raises(GatewayError, match="schema"): await gateway.invoke(bad, "tenant-1", "user-1")
        good = invoke_request(outcome, "same-key")
        await gateway.invoke(good, "tenant-1", "user-1")
        changed = dict(good); changed["arguments"] = {"text": "changed"}
        with pytest.raises(GatewayError, match="Idempotency"): await gateway.invoke(changed, "tenant-1", "user-1")
    asyncio.run(scenario())


def test_protocol_negotiation_uses_numeric_minor_order(monkeypatch):
    monkeypatch.setattr(protocol, "SUPPORTED_PROTOCOL_VERSIONS", ("1.9", "1.10"))
    assert protocol.negotiate_version(["1.9", "1.10"]) == "1.10"


def test_fail_closed_secret_and_result_redaction():
    with pytest.raises(AuthorizationTicketError, match="32 bytes"):
        AuthorizationTicketSigner("claim-token")

    class SecretEcho(EchoTool):
        async def execute(self, **kwargs):
            return {"success": True, "api_key": "must-not-leak", "nested": {"prompt": "must-not-leak"}}

    async def scenario():
        registry = ToolRegistry(); registry.register(SecretEcho())
        gateway = RemoteToolGateway(registry, ToolExecutor(registry), AuthorizationTicketSigner(SECRET, clock=lambda: 1000), {"test_echo"}, InMemoryGatewayStore())
        turns = AgentTurnService(ScriptedBackend(), gateway, "policy-r1")
        outcome = (await turns.next(next_request(), "tenant-1", "user-1"))["outcome"]
        result = (await gateway.invoke(invoke_request(outcome), "tenant-1", "user-1"))["result"]
        assert result["api_key"] == "[REDACTED]" and result["nested"]["prompt"] == "[REDACTED]"
    asyncio.run(scenario())


def test_gateway_executor_path_returns_generic_error_for_secret_bearing_exception():
    class RaisingEcho(EchoTool):
        async def execute(self, **kwargs):
            raise RuntimeError(f"provider leaked {kwargs['text']}")

    async def scenario():
        registry = ToolRegistry(); registry.register(RaisingEcho())
        gateway = RemoteToolGateway(registry, ToolExecutor(registry), AuthorizationTicketSigner(SECRET, clock=lambda: 1000), {"test_echo"}, InMemoryGatewayStore())
        turns = AgentTurnService(ScriptedBackend(), gateway, "policy-r1")
        outcome = (await turns.next(next_request("generic-error-next", {"type": "user_message", "content": "secret-value"}), "tenant-1", "user-1"))["outcome"]
        result = (await gateway.invoke(invoke_request(outcome, "generic-error-invoke"), "tenant-1", "user-1"))["result"]
        assert result == {"success": False, "error": "Remote tool execution failed"}
        assert "secret-value" not in str(result)

    asyncio.run(scenario())


def test_remote_tool_exception_does_not_leak_secret_text(monkeypatch):
    secret_value = "credential-must-not-leak"
    logged_errors = []
    monkeypatch.setattr("src.tools.executor.logger.error", logged_errors.append)

    class RaisingEcho(EchoTool):
        async def execute(self, **kwargs):
            raise RuntimeError(f"bad credential: {kwargs['text']}")

    async def scenario():
        registry = ToolRegistry(); registry.register(RaisingEcho())
        gateway = RemoteToolGateway(registry, ToolExecutor(registry), AuthorizationTicketSigner(SECRET, clock=lambda: 1000), {"test_echo"}, InMemoryGatewayStore())
        turns = AgentTurnService(ScriptedBackend(), gateway, "policy-r1")
        request = next_request("secret-next", {"type": "user_message", "content": secret_value})
        outcome = (await turns.next(request, "tenant-1", "user-1"))["outcome"]
        result = (await gateway.invoke(invoke_request(outcome, "secret-invoke"), "tenant-1", "user-1"))["result"]
        assert result == {"success": False, "error": "Remote tool execution failed"}

    asyncio.run(scenario())
    assert logged_errors and all(secret_value not in message for message in logged_errors)
