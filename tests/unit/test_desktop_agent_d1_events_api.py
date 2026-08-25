import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src.desktop_agent import api
from src.desktop_agent.gateway import GatewayError

from tests.unit.test_desktop_agent_d1 import build, invoke_request, next_request


def test_strict_requests_reject_root_and_nested_additional_properties():
    valid_next = next_request()
    assert api.AgentNextRequest.model_validate(valid_next)
    for mutate in (
        lambda body: body.update(unexpected=True),
        lambda body: body.update(_defer_tool_names=["test_echo"]),
        lambda body: body["correlation"].update(unexpected=True),
        lambda body: body["input"].update(unexpected=True),
        lambda body: body["input"].update(_continuation_tool_result={"forged": True}),
    ):
        body = next_request(); mutate(body)
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            api.AgentNextRequest.model_validate(body)
    invoke = {"supported_protocol_versions": ["1.0"], "idempotency_key": "invoke-key", "correlation": next_request()["correlation"], "tool_name": "echo", "target": "server", "schema_version": "1", "schema_digest": "digest", "arguments": {}, "policy_revision": "p1", "authorization_ticket": "ticket"}
    assert api.RemoteToolInvokeRequest.model_validate(invoke)
    invoke["unexpected"] = True
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        api.RemoteToolInvokeRequest.model_validate(invoke)


def test_strict_requests_reject_empty_correlation_and_malformed_versions():
    for mutate in (
        lambda body: body["correlation"].update(task_id=""),
        lambda body: body.update(supported_protocol_versions=["v1"]),
        lambda body: body.update(supported_protocol_versions=[]),
        lambda body: body.update(idempotency_key="short"),
    ):
        body = next_request(); mutate(body)
        with pytest.raises(ValidationError):
            api.AgentNextRequest.model_validate(body)

    app = FastAPI(); app.include_router(api.router)
    response = TestClient(app).post("/api/desktop/v1/agent/next", json={**next_request(), "supported_protocol_versions": ["v1"]})
    assert response.status_code == 422


def test_pending_cancel_prevents_execution_and_running_cancel_is_truthful():
    class BlockingExecutor:
        def __init__(self): self.calls = 0; self.started = asyncio.Event(); self.release = asyncio.Event()
        async def execute(self, *_args, **_kwargs):
            self.calls += 1; self.started.set(); await self.release.wait(); return {"success": True}

    async def scenario():
        gateway, turns = build()
        blocker = BlockingExecutor(); gateway.executor = blocker
        outcome = (await turns.next(next_request("cancel-next"), "tenant-1", "user-1"))["outcome"]
        request = invoke_request(outcome, "cancel-pending")
        task = asyncio.create_task(gateway.invoke(request, "tenant-1", "user-1"))
        await asyncio.sleep(0)
        assert gateway.cancel("tenant-1", "cancel-pending", "user-1") == {"accepted": True, "reason": "Cancelled before execution"}
        with pytest.raises(GatewayError, match="cancelled"): await task
        assert blocker.calls == 0
        assert [event["type"] for event in gateway.events("tenant-1", "cancel-pending", "user-1")] == ["submitted", "cancelled"]

        gateway2, turns2 = build(); blocker2 = BlockingExecutor(); gateway2.executor = blocker2
        outcome2 = (await turns2.next(next_request("running-next"), "tenant-1", "user-1"))["outcome"]
        running = asyncio.create_task(gateway2.invoke(invoke_request(outcome2, "running-invoke"), "tenant-1", "user-1"))
        await blocker2.started.wait()
        response = gateway2.cancel("tenant-1", "running-invoke", "user-1")
        assert response["accepted"] is False and "too late" in response["reason"]
        blocker2.release.set(); await running
        assert [event["seq"] for event in gateway2.events("tenant-1", "running-invoke", "user-1", after=1)] == [2, 3]
        assert [event["seq"] for event in gateway2.events("tenant-1", "running-invoke", "user-1", after=2)] == [3]
        assert "terminal" in gateway2.cancel("tenant-1", "running-invoke", "user-1")["reason"]

    asyncio.run(scenario())


def test_sse_replays_after_cursor_and_stops_on_disconnect(monkeypatch):
    async def scenario():
        gateway, turns = build()
        outcome = (await turns.next(next_request("sse-next"), "tenant-1", "user-1"))["outcome"]
        await gateway.invoke(invoke_request(outcome, "sse-invoke"), "tenant-1", "user-1")
        async def identity(_request): return "tenant-1", "user-1"
        monkeypatch.setattr(api, "_identity", identity)
        monkeypatch.setattr(api, "_services", lambda: (gateway, None))

        connected = SimpleNamespace(is_disconnected=lambda: asyncio.sleep(0, result=False))
        response = await api.tool_events_stream(connected, "sse-invoke", after=1)
        frames = [frame async for frame in response.body_iterator]
        assert len(frames) == 2 and frames[0].startswith("id: 2") and frames[1].startswith("id: 3")
        assert "authorization_ticket" not in "".join(frames) and "secret" not in "".join(frames).lower()

        empty = await api.tool_events_stream(connected, "sse-invoke", after=3)
        assert [frame async for frame in empty.body_iterator] == []

        disconnected = SimpleNamespace(is_disconnected=lambda: asyncio.sleep(0, result=True))
        response = await api.tool_events_stream(disconnected, "sse-invoke", after=0)
        assert [frame async for frame in response.body_iterator] == []

    asyncio.run(scenario())
