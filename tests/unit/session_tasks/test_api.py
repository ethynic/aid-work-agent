"""API 层轻量用例（C1 二轮补缺）：envelope、Idempotency-Key 重放/冲突、设备认证。

用独立 FastAPI 实例挂载本模块 router（不启动整个 src.main）；认证与租户上下文
通过 monkeypatch 注入，专注本模块 API 语义，不测全局中间件。
"""

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.session_tasks import api as session_tasks_api
from tests.unit.session_tasks.conftest import build_draft_payload


@pytest.fixture()
def client(monkeypatch, tenant_id):
    app = FastAPI()
    app.include_router(session_tasks_api.router)
    app.include_router(session_tasks_api.device_router)

    @app.middleware("http")
    async def _inject_tenant(request, call_next):  # noqa: ANN202
        tid = request.headers.get("X-Tenant-Id")
        if tid:
            request.state.tenant_id = tid
        return await call_next(request)

    monkeypatch.setattr(session_tasks_api, "_get_current_user_sync", lambda request: {"user_id": "user-1"})
    with TestClient(app) as tc:
        yield tc


class TestUserRootApi:
    def test_create_and_idempotent_replay(self, client, tenant_id, verified_binding):
        body = build_draft_payload(verified_binding)
        key = f"idem-{uuid.uuid4().hex[:8]}"
        first = client.post(
            "/api/session-tasks", json=body, headers={"Idempotency-Key": key, "X-Tenant-Id": tenant_id}
        )
        assert first.status_code == 201
        assert first.json()["success"] is True
        task_id = first.json()["data"]["task_id"]

        replay = client.post(
            "/api/session-tasks", json=body, headers={"Idempotency-Key": key, "X-Tenant-Id": tenant_id}
        )
        assert replay.status_code == 200
        assert replay.json()["data"]["task_id"] == task_id  # 同 key 同请求返回原资源

        conflict = client.post(
            "/api/session-tasks", json={**body, "spec": {**body["spec"], "goal": "另一个目标"}},
            headers={"Idempotency-Key": key, "X-Tenant-Id": tenant_id},
        )
        assert conflict.status_code == 409
        assert conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"  # 同 key 异请求体

    def test_create_requires_idempotency_key(self, client, tenant_id, verified_binding):
        resp = client.post("/api/session-tasks", json=build_draft_payload(verified_binding),
                           headers={"X-Tenant-Id": tenant_id})
        assert resp.status_code == 400

    def test_list_and_detail(self, client, tenant_id, verified_binding):
        body = build_draft_payload(verified_binding)
        created = client.post("/api/session-tasks", json=body,
                              headers={"Idempotency-Key": f"l-{uuid.uuid4().hex[:8]}", "X-Tenant-Id": tenant_id})
        task_id = created.json()["data"]["task_id"]
        listing = client.get("/api/session-tasks", headers={"X-Tenant-Id": tenant_id})
        assert listing.status_code == 200 and listing.json()["data"]["total"] >= 1
        detail = client.get(f"/api/session-tasks/{task_id}", headers={"X-Tenant-Id": tenant_id})
        assert detail.status_code == 200
        assert detail.json()["data"]["spec"] is None  # 草稿任务：已发布 spec 为空
        assert detail.json()["data"]["draft_spec"]["goal"]  # 草稿内容经授权服务端解密返回
        # 列表不带正文
        listed_keys = set(listing.json()["data"]["items"][0].keys())
        assert "draft_digest" not in listed_keys

    def test_missing_tenant_context_400(self, client):
        resp = client.get("/api/session-tasks")
        assert resp.status_code == 400


class TestDeviceRootApi:
    def test_claim_without_device_token_401(self, client):
        resp = client.post("/api/local-tools/runtime/session-tasks/claim", json={"runtime_instance_id": "rt-1"})
        assert resp.status_code == 401

    def test_claim_with_invalid_token_401(self, client):
        resp = client.post(
            "/api/local-tools/runtime/session-tasks/claim",
            json={"runtime_instance_id": "rt-1"},
            headers={"Authorization": "Bearer not-a-real-token"},
        )
        assert resp.status_code == 401
