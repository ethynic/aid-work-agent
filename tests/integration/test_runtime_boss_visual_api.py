"""配对设备视觉入口：真实鉴权依赖/请求解析/视觉服务，DB与模型边界隔离。"""

import base64
import io
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from src.api import client_routes
from src.local_tools import api, security
from src.services import boss_visual_service as visual

pytestmark = [pytest.mark.integration, pytest.mark.api]
PATH = "/api/local-tools/runtime/boss/visual-diagnose"
CLIENT_PATH = "/api/client/v1/boss/visual-diagnose"


@pytest.fixture(scope="session", autouse=True)
def init_db_pool():
    """This API contract suite replaces every persistence boundary; never connect a default DB."""
    yield


def payload(version=2):
    image = io.BytesIO()
    Image.new("RGB", (12, 8), "white").save(image, format="PNG")
    body = {"api_version": version, "request_id": str(uuid4()),
            "context": {"mode": "standalone"},
            "observation": {"image_base64": base64.b64encode(image.getvalue()).decode(),
                            "width": 12, "height": 8, "space": "page_image_px"}}
    if version == 1:
        body["operation"] = {"kind": "overlay", "goal": "关闭阻挡"}
    else:
        body.update(purpose="diagnose", available_actions=["dismiss_click"],
                    operation={"name": "boss_filter", "goal": "完成筛选", "expected": "筛选数量为2"},
                    execution={"stage": "postcheck", "input_status": "attempted", "attempts_used": 2,
                               "ordinary_retries_used": 1},
                    verification={"status": "not_satisfied", "evidence": ["数量仍为0"]})
        body["observation"].update(captured_at="2026-10-20T12:00:00+08:00", after_attempt=2)
    return body


@pytest.fixture
def harness(monkeypatch):
    rows, reservations, settlements = {}, [], []
    device = {"tenant_id": "tenant-a", "id": str(uuid4()), "user_id": "user-a", "status": "active"}
    devices = {security.sha256_hex("device-token"): device}
    users = {"user-a": {"tenant_id": "tenant-a", "status": "active"},
             "user-b": {"tenant_id": "tenant-a", "status": "active"}}
    tenants = {"tenant-a": {"status": "active"}, "tenant-b": {"status": "active"}}

    class Ledger:
        @staticmethod
        def get(tenant, rid):
            return rows.get((tenant, rid))

        @staticmethod
        def reserve(tenant, bid, rid, digest, provider, model, price, **kwargs):
            reservations.append((tenant, bid, kwargs))
            detail = {"input_digest": digest, "price_snapshot": price, "billing_factor": 20}
            if "caller_detail" in kwargs:
                detail["caller"] = kwargs["caller_detail"]
            return rows.setdefault((tenant, rid), {"binding_id": bid, "status": "reserved",
                "provider": provider, "model": model, "detail": detail})

        @staticmethod
        def dispatch(tenant, bid, rid):
            row = rows[tenant, rid]
            assert row["binding_id"] == bid
            if row["status"] != "reserved":
                return False
            row["status"] = "dispatched"
            return True

        @staticmethod
        def mark_unknown(tenant, bid, rid):
            row = rows[tenant, rid]
            assert row["binding_id"] == bid
            row["status"] = "model_unknown"

        @staticmethod
        def settle(tenant, bid, rid, usage, cost, credits, response):
            row = rows[tenant, rid]
            assert row["binding_id"] == bid
            settlements.append((tenant, bid, credits))
            response["receipt"].update(token_cost_yuan=str(cost), credit_cost=str(credits),
                                       factor=visual.get_boss_visual_billing_factor(row["detail"]))
            row["detail"]["response"], row["status"] = response, "completed"
            return response

    monkeypatch.setattr(api.repository, "get_device_by_token_hash", lambda digest: devices.get(digest))
    monkeypatch.setattr(api.UserDB, "get_by_id", users.get)
    monkeypatch.setattr(api.TenantDB, "get_by_id", tenants.get)
    monkeypatch.setattr(visual, "BossVisualUsageDB", Ledger)
    monkeypatch.setattr(visual.TokenCostPriceDB, "get_by_model_name", lambda _: {
        "input_price_per_m": "0.8", "output_price_per_m": "2.8"})
    diagnosis = {"page_summary": "推广遮挡", "goal_state": "not_satisfied", "reason": "页面被遮挡",
                 "obstruction": "present",
                 "proposed_action": None, "missing_evidence": []}
    gateway = AsyncMock(return_value={"content": json.dumps(diagnosis),
        "usage": {"prompt_tokens": 9000, "completion_tokens": 1000}})
    monkeypatch.setattr(visual.llm_gateway, "chat_direct", gateway)
    binding = SimpleNamespace(tenant_id="tenant-a", binding_id="old-binding")
    monkeypatch.setattr(client_routes, "verify_client_token", lambda token: binding if token == "client-token" else None)
    app = FastAPI()
    app.include_router(api.router)
    app.include_router(client_routes.router)
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, app=app, rows=rows, device=device, users=users, tenants=tenants,
            devices=devices, gateway=gateway, reservations=reservations, settlements=settlements, binding=binding)


def post(h, body, token="device-token", path=PATH, **kwargs):
    return h.client.post(path, json=body, headers={"Authorization": f"Bearer {token}", **kwargs})


@pytest.mark.parametrize("version", [1, 2])
def test_paired_identity_diagnoses_and_retrieves_once_at_20_without_client_binding(harness, version):
    h, body = harness, payload(version)
    if version == 1:
        h.gateway.return_value["content"] = json.dumps({"page_summary": "推广遮挡", "verdict": "not_satisfied",
            "reason": "页面被遮挡", "proposed_action": None})
    first = post(h, body, **{"X-Tenant-Id": "tenant-b"})
    assert first.status_code == 200
    assert first.json()["receipt"]["credit_cost"] == "0.20"
    assert first.json()["receipt"]["factor"] == 20
    assert first.json()["diagnosis_error"] is None
    assert post(h, body).json() == first.json()
    assert post(h, {"api_version": version, "request_id": body["request_id"], "retrieve_only": True}).json() == first.json()
    assert h.reservations == [("tenant-a", f"runtime-device:{h.device['id']}", {"caller_detail": {
        "kind": "runtime_device", "device_id": h.device["id"], "user_id": "user-a"}})]
    assert len(h.settlements) == 1
    h.gateway.assert_awaited_once()


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("user_tenant", [None, "tenant-b"])
def test_platform_admin_pairing_keeps_device_tenant_and_charges_once(harness, version, user_tenant):
    """Tenant-context pairing permits global admins; vision must use that same authority."""
    h, body = harness, payload(version)
    h.users["user-a"].update(role="platform_admin", tenant_id=user_tenant)
    if version == 1:
        h.gateway.return_value["content"] = json.dumps({"page_summary": "推广遮挡", "verdict": "not_satisfied",
            "reason": "页面被遮挡", "proposed_action": None})
    first = post(h, body, **{"X-Tenant-Id": "tenant-b"})
    assert first.status_code == 200
    assert first.json()["receipt"]["factor"] == 20
    assert first.json()["receipt"]["credit_cost"] == "0.20"
    assert post(h, body).json() == first.json()
    assert post(h, {"api_version": version, "request_id": body["request_id"], "retrieve_only": True}).json() == first.json()
    assert h.reservations == [("tenant-a", f"runtime-device:{h.device['id']}", {"caller_detail": {
        "kind": "runtime_device", "device_id": h.device["id"], "user_id": "user-a"}})]
    assert list(h.rows) == [("tenant-a", body["request_id"])]
    assert len(h.settlements) == 1
    h.gateway.assert_awaited_once()


@pytest.mark.parametrize("kind", ["user_missing", "suspended", "deactivated", "tenant_missing", "tenant_suspended", "role_revoked"])
def test_platform_admin_exception_cannot_bypass_live_user_or_tenant_checks(harness, kind):
    h = harness
    h.users["user-a"].update(role="platform_admin", tenant_id=None)
    if kind == "user_missing":
        h.users.clear()
    elif kind in {"suspended", "deactivated"}:
        h.users["user-a"]["status"] = kind
    elif kind == "tenant_missing":
        h.tenants.clear()
    elif kind == "tenant_suspended":
        h.tenants["tenant-a"]["status"] = "suspended"
    else:
        h.users["user-a"]["role"] = "user"
    assert post(h, payload()).status_code == 403
    h.gateway.assert_not_called()
    assert not h.reservations


@pytest.mark.parametrize("role", ["user", "tenant_admin", None])
@pytest.mark.parametrize("user_tenant", [None, "tenant-b"])
def test_non_platform_users_cannot_use_an_unrelated_device_tenant(harness, role, user_tenant):
    h = harness
    h.users["user-a"].update(role=role, tenant_id=user_tenant)
    assert post(h, payload(), **{"X-Tenant-Id": "tenant-a"}).status_code == 403
    h.gateway.assert_not_called()
    assert not h.reservations


@pytest.mark.parametrize("token", ["", "wrong", "client-token"])
def test_only_active_paired_device_can_enter_and_never_falls_back_to_client_token(harness, token):
    assert post(harness, payload(), token).status_code == 401
    harness.gateway.assert_not_called()
    assert not harness.reservations


def test_revoke_applies_to_receipt_retrieval_too(harness):
    body = payload()
    assert post(harness, body).status_code == 200
    harness.devices.clear()
    assert post(harness, {"api_version": 2, "request_id": body["request_id"], "retrieve_only": True}).status_code == 401
    harness.gateway.assert_awaited_once()


@pytest.mark.parametrize("mutation", ["device", "user", "tenant"])
def test_cannot_take_over_another_device_user_or_tenant_request(harness, mutation):
    h, body = harness, payload()
    assert post(h, body).status_code == 200
    if mutation == "device":
        h.device["id"] = str(uuid4())
    elif mutation == "user":
        h.device["user_id"] = "user-b"
    else:
        h.device["tenant_id"] = "tenant-b"
        h.users["user-a"]["tenant_id"] = "tenant-b"
    result = post(h, {"api_version": 2, "request_id": body["request_id"], "retrieve_only": True})
    assert result.status_code == (404 if mutation == "tenant" else 403)
    h.gateway.assert_awaited_once()


@pytest.mark.parametrize("from_client", [False, True])
def test_legacy_and_device_caller_namespaces_cannot_read_or_dispatch_each_others_receipts(harness, from_client):
    h, body = harness, payload()
    source = ("client-token", CLIENT_PATH) if from_client else ("device-token", PATH)
    dest = ("device-token", PATH) if from_client else ("client-token", CLIENT_PATH)
    assert post(h, body, *source).status_code == 200
    for status in ["completed", "reserved", "dispatched", "model_unknown"]:
        h.rows["tenant-a", body["request_id"]]["status"] = status
        assert post(h, body, *dest).status_code == 403
        assert post(h, {"api_version": 2, "request_id": body["request_id"], "retrieve_only": True}, *dest).status_code == 403
    h.gateway.assert_awaited_once()


@pytest.mark.parametrize("kind", ["tenant_missing", "tenant_suspended", "user_missing", "user_suspended", "user_moved"])
def test_invalid_current_ownership_rejects_before_model_and_ledger(harness, kind):
    h = harness
    if kind == "tenant_missing":
        h.tenants.clear()
    elif kind == "tenant_suspended":
        h.tenants["tenant-a"]["status"] = "suspended"
    elif kind == "user_missing":
        h.users.clear()
    elif kind == "user_suspended":
        h.users["user-a"]["status"] = "suspended"
    else:
        h.users["user-a"]["tenant_id"] = "tenant-b"
    assert post(h, payload()).status_code == 403
    h.gateway.assert_not_called()
    assert not h.reservations


@pytest.mark.parametrize("field", ["tenant_id", "user_id", "device_id", "binding_id", "token"])
def test_body_cannot_override_authenticated_identity(harness, field):
    body = payload()
    body[field] = "forged"
    assert post(harness, body).status_code == 422
    harness.gateway.assert_not_called()


def test_device_dispatched_retrieval_preserves_retry_protocol_without_new_provider_call(harness):
    h, body = harness, payload()
    assert post(h, body).status_code == 200
    h.rows["tenant-a", body["request_id"]]["status"] = "dispatched"
    result = post(h, {"api_version": 2, "request_id": body["request_id"], "retrieve_only": True})
    assert result.status_code == 202
    assert result.headers["Retry-After"] == "2"
    assert result.json()["state"] == "dispatched"
    h.gateway.assert_awaited_once()


def test_device_unknown_is_never_automatically_dispatched_again(harness):
    h, body = harness, payload()
    h.gateway.side_effect = RuntimeError("isolated provider failure")
    assert post(h, body).status_code == 502
    assert post(h, body).status_code == 409
    assert h.rows["tenant-a", body["request_id"]]["status"] == "model_unknown"
    h.gateway.assert_awaited_once()
    assert not h.settlements
