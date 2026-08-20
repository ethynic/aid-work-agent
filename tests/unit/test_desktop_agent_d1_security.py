import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.desktop_agent import api
from src.desktop_agent.gateway import GatewayError
from src.desktop_agent.security import AuthorizationTicketError
from src.desktop_agent.turn import AgentTurnService, InMemoryTurnStore
from src.tools.base import ExecutionTarget
from src.tools.executor import ToolExecutor
from src.tools.registry import ToolRegistry

from tests.unit.test_desktop_agent_d1 import EchoTool, ScriptedBackend, SECRET, build, invoke_request, next_request
from src.desktop_agent.gateway import InMemoryGatewayStore, RemoteToolGateway
from src.desktop_agent.security import AuthorizationTicketSigner


def test_ticket_is_bound_to_all_security_relevant_fields_and_identity():
    async def scenario():
        gateway, turns = build()
        outcome = (await turns.next(next_request(), "tenant-1", "user-1"))["outcome"]
        mutations = (
            lambda request: request["correlation"].update(action_id="action-other"),
            lambda request: request.update(policy_revision="policy-other"),
            lambda request: request.update(schema_version="2"),
            lambda request: request.update(schema_digest="0" * 64),
            lambda request: request.update(tool_name="other_tool"),
            lambda request: request.update(target="local"),
            lambda request: request["arguments"].update(text="substituted"),
        )
        for index, mutate in enumerate(mutations):
            request = deepcopy(invoke_request(outcome, f"bound-field-{index}"))
            mutate(request)
            with pytest.raises((AuthorizationTicketError, GatewayError)):
                await gateway.invoke(request, "tenant-1", "user-1")

        with pytest.raises(AuthorizationTicketError, match="tenant_id"):
            await gateway.invoke(invoke_request(outcome, "cross-tenant"), "tenant-2", "user-1")
        with pytest.raises(AuthorizationTicketError, match="user_id"):
            await gateway.invoke(invoke_request(outcome, "cross-user"), "tenant-1", "user-2")

        request = invoke_request(outcome, "first-consumption")
        await gateway.invoke(request, "tenant-1", "user-1")
        replay = invoke_request(outcome, "fresh-key-replay")
        with pytest.raises(AuthorizationTicketError, match="consumed"):
            await gateway.invoke(replay, "tenant-1", "user-1")
        assert gateway.store.get("tenant-1", "fresh-key-replay") is None

        reserved = invoke_request(outcome, "reserved-argument")
        reserved["arguments"] = {**reserved["arguments"], "_trusted_tenant_id": "tenant-2"}
        with pytest.raises(GatewayError, match="Reserved"):
            await gateway.invoke(reserved, "tenant-1", "user-1")

    asyncio.run(scenario())


def test_d1_identity_rejects_non_admin_tenant_override(monkeypatch):
    request = SimpleNamespace(state=SimpleNamespace(tenant_id="tenant-2"))
    monkeypatch.setattr("src.saas.db.tenant_db.TenantDB.get_by_id", lambda tenant_id: {"tenant_id": tenant_id, "status": "active"})
    monkeypatch.setattr(api, "get_current_user", lambda _request: {"user_id": "user-1", "tenant_id": "tenant-1", "role": "member"})
    with pytest.raises(HTTPException) as caught:
        asyncio.run(api._identity(request))
    assert caught.value.status_code == 403

    monkeypatch.setattr(api, "get_current_user", lambda _request: {"user_id": "admin-1", "tenant_id": "platform", "role": "platform_admin"})
    assert asyncio.run(api._identity(request)) == ("tenant-2", "admin-1")

    monkeypatch.setattr("src.saas.db.tenant_db.TenantDB.get_by_id", lambda _tenant_id: {"status": "suspended"})
    with pytest.raises(HTTPException) as suspended:
        asyncio.run(api._identity(request))
    assert suspended.value.status_code == 403


def test_shared_stores_reserve_before_side_effects():
    class BlockingBackend:
        def __init__(self):
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.calls = 0

        async def next_outcome(self, *_args):
            self.calls += 1
            self.started.set()
            await self.release.wait()
            return {"type": "final", "message": "done"}

    async def scenario():
        gateway, _ = build()
        backend = BlockingBackend()
        shared = InMemoryTurnStore()
        worker_a = AgentTurnService(backend, gateway, "policy-r1", shared)
        worker_b = AgentTurnService(backend, gateway, "policy-r1", shared)
        request = next_request("cross-worker-key")
        first = asyncio.create_task(worker_a.next(request, "tenant-1", "user-1"))
        await backend.started.wait()
        with pytest.raises(ValueError, match="progress"):
            await worker_b.next(request, "tenant-1", "user-1")
        assert backend.calls == 1
        backend.release.set()
        assert (await first)["outcome"]["message"] == "done"
        assert await worker_b.next(request, "tenant-1", "user-1") == await worker_a.next(request, "tenant-1", "user-1")

    asyncio.run(scenario())


def test_failed_turn_abandons_pending_reservation_for_safe_retry():
    class FailingBackend:
        async def next_outcome(self, *_args):
            raise RuntimeError("provider unavailable")

    async def scenario():
        gateway, _ = build()
        store = InMemoryTurnStore()
        service = AgentTurnService(FailingBackend(), gateway, "policy-r1", store)
        request = next_request("retry-after-failure")
        with pytest.raises(RuntimeError, match="provider unavailable"):
            await service.next(request, "tenant-1", "user-1")
        assert store.get("tenant-1", "retry-after-failure") is None

    asyncio.run(scenario())


def test_shared_gateway_store_reserves_before_tool_side_effect():
    class BlockingExecutor:
        def __init__(self):
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.calls = 0

        async def execute(
            self, _name, _parameters, user_permissions, *,
            redact_parameter_logs=False, context=None,
        ):
            assert user_permissions == ["test_echo"]
            assert redact_parameter_logs is True
            assert context.tenant_id == "tenant-1"
            assert context.user_id == "user-1"
            self.calls += 1
            self.started.set()
            await self.release.wait()
            return {"success": True}

    async def scenario():
        gateway, turns = build()
        executor = BlockingExecutor()
        gateway.executor = executor
        outcome = (await turns.next(next_request("gateway-concurrent-next"), "tenant-1", "user-1"))["outcome"]
        request = invoke_request(outcome, "gateway-concurrent-invoke")
        first = asyncio.create_task(gateway.invoke(request, "tenant-1", "user-1"))
        await executor.started.wait()
        with pytest.raises(GatewayError, match="progress"):
            await gateway.invoke(request, "tenant-1", "user-1")
        assert executor.calls == 1
        executor.release.set()
        assert (await first)["status"] == "completed"
        assert await gateway.invoke(request, "tenant-1", "user-1") == await first

    asyncio.run(scenario())


def test_catalog_allowlist_target_and_event_ownership():
    class LocalOnlyEcho(EchoTool):
        name = "local_echo"
        execution_target = ExecutionTarget.LOCAL_REQUIRED

    registry = ToolRegistry()
    registry.register(EchoTool())
    registry.register(LocalOnlyEcho())
    gateway = RemoteToolGateway(
        registry,
        ToolExecutor(registry),
        AuthorizationTicketSigner(SECRET, clock=lambda: 1000),
        {"test_echo", "local_echo", "missing_tool"},
        InMemoryGatewayStore(),
    )
    assert [item["tool_name"] for item in gateway.catalog()] == ["test_echo"]

    async def scenario():
        turns = AgentTurnService(ScriptedBackend(), gateway, "policy-r1")
        outcome = (await turns.next(next_request("events-next"), "tenant-1", "user-1"))["outcome"]
        await gateway.invoke(invoke_request(outcome, "events-invoke"), "tenant-1", "user-1")
        assert [event["type"] for event in gateway.events("tenant-1", "events-invoke", "user-1")] == ["submitted", "running", "completed"]
        with pytest.raises(GatewayError, match="not found"):
            gateway.events("tenant-1", "events-invoke", "user-2")
        with pytest.raises(GatewayError, match="not found"):
            gateway.events("tenant-2", "events-invoke", "user-1")

    asyncio.run(scenario())
