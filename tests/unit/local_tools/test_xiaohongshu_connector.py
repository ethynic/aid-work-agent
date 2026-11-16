from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.local_tools import api, catalog, repository


@pytest.fixture
def rig(monkeypatch):
    app = FastAPI()
    app.include_router(api.router)
    enqueued = []
    rows = {}
    device = {
        "id": str(uuid4()), "name": "我的电脑", "selected": True, "status": "active",
        "last_seen_at": datetime.now(timezone.utc),
        "capabilities_json": {"providers": ["xiaohongshu"], "capabilities": [
            "xiaohongshu_login", "xiaohongshu_open",
        ]},
    }

    @app.middleware("http")
    async def tenant_context(request, call_next):
        request.state.tenant_id = request.headers.get("X-Test-Tenant", "tenant-a")
        return await call_next(request)

    def authenticated_user(request):
        user_id = request.headers.get("X-Test-User")
        return {"user_id": user_id} if user_id else None

    def enqueue(self, **data):
        enqueued.append(data)
        action_id = str(uuid4())
        rows[(action_id, data["tenant_id"])] = {"id": action_id, "state": "queued", **data}
        return {"id": action_id}

    monkeypatch.setattr(api, "get_current_user", authenticated_user)
    monkeypatch.setattr(repository, "list_devices", lambda tenant_id, user_id: [device])
    monkeypatch.setattr(api.LocalInvocationService, "enqueue", enqueue)
    monkeypatch.setattr(api.LocalInvocationService, "get", lambda self, action_id, tenant_id: rows.get((action_id, tenant_id)))
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, headers={"X-Test-User": "user-a"}, device=device, enqueued=enqueued, rows=rows)


def start(rig, action="login", headers=None, **params):
    response = rig.client.post("/api/local-tools/xiaohongshu/actions",
                               json={"action": action, "device_id": rig.device["id"], **params},
                               headers=headers if headers is not None else rig.headers)
    return response


def test_device_scope_is_derived_on_server_and_same_scope_reuses_profile(rig):
    assert start(rig).status_code == start(rig, "open").status_code == 202
    assert start(rig, headers={"X-Test-User": "user-b"}).status_code == 202
    assert start(rig, headers={**rig.headers, "X-Test-Tenant": "tenant-b"}).status_code == 202
    rig.device["id"] = str(uuid4())
    assert start(rig).status_code == 202
    keys = [entry["arguments"]["profile_key"] for entry in rig.enqueued]
    assert keys[0] == keys[1] and len(set(keys)) == 4
    assert all(len(key) == 64 for key in keys)
    assert all(entry["provider_key"] == "xiaohongshu" and entry.get("session_id") is None
               for entry in rig.enqueued)
    assert [entry["tool_name"] for entry in rig.enqueued[:2]] == ["xiaohongshu_login", "xiaohongshu_open"]


def test_unavailable_or_different_device_cannot_enqueue(rig):
    assert start(rig, headers={}).status_code == 401
    assert start(rig, profile_key="injected", url="https://example.invalid").status_code == 422
    assert start(rig, device_id=str(uuid4())).status_code == 409
    rig.device["last_seen_at"] -= timedelta(minutes=1)
    assert start(rig).status_code == 409
    rig.device["last_seen_at"] += timedelta(minutes=1)
    rig.device["capabilities_json"] = {"provider_id": "ai.aidwork.boss-recruiting"}
    assert start(rig).status_code == 409
    assert not rig.enqueued
    assert catalog.is_tool_allowed("boss-recruiting", "boss_goto")
    assert not catalog.agent_tools("xiaohongshu")


def test_only_owner_receives_safe_status_not_raw_browser_result(rig):
    action = start(rig).json()["action_id"]
    row = rig.rows[(action, "tenant-a")]
    path = f"/api/local-tools/xiaohongshu/actions/{action}"
    assert rig.client.get(path, headers=rig.headers).json()["status"] == "pending"
    row["deadline_at"] -= timedelta(minutes=1)
    assert rig.client.get(path, headers=rig.headers).json()["code"] == "XHS_QUEUE_TIMEOUT"
    row.update(state="succeeded", result_json={"success": True,
               "data": {"schema_version": 1, "status": "qr_ready", "secret": "must-not-project"}})
    assert rig.client.get(path, headers=rig.headers).json() == {"success": True, "status": "qr_ready"}
    for headers in ({"X-Test-User": "user-b"}, {**rig.headers, "X-Test-Tenant": "tenant-b"}):
        assert rig.client.get(path, headers=headers).status_code == 404
    row["business_kind"] = "wecom_config"
    assert rig.client.get(path, headers=rig.headers).status_code == 404
