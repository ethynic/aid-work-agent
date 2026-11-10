"""Paired WeCom calibration uses device identity and a fixed model request."""

import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import client_routes
from src.local_tools import api, repository
from src.local_tools.security import sha256_hex

pytestmark = pytest.mark.unit

DEVICE = {"id": "device-1", "tenant_id": "paired-tenant", "user_id": "paired-user"}
IMAGE = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\nimage").decode()


@pytest.fixture
def rig(monkeypatch):
    monkeypatch.setattr(repository, "get_device_by_token_hash",
                        lambda token_hash: DEVICE if token_hash == sha256_hex("paired-token") else None)
    monkeypatch.setattr(api.TenantDB, "get_by_id",
                        staticmethod(lambda tenant_id: {"status": "active", "credit_balance": 100}
                                     if tenant_id == DEVICE["tenant_id"] else None))
    monkeypatch.setattr(api.UserDB, "get_by_id",
                        staticmethod(lambda user_id: {"status": "active", "tenant_id": DEVICE["tenant_id"]}
                                     if user_id == DEVICE["user_id"] else None))
    chat = AsyncMock(return_value={"content": '{"divider_x":400,"title_x":440,"title_y":38,"input_x":620,"input_y":710}',
                                   "usage": {"prompt_tokens": 100, "completion_tokens": 30}})
    gateway = SimpleNamespace(chat=chat, get_model_name=lambda: "GLM-5.3-Flash",
                              get_provider_name=lambda: "zhipu")
    models = []

    def model_gateway(model):
        models.append(model)
        return gateway

    monkeypatch.setattr(client_routes, "_get_model_gateway", model_gateway)
    billed = []

    def record(**kwargs):
        billed.append(kwargs)
        return {"raw_credit_cost": 0.01, "credit_cost": 0.1, "balance_after": 99.9}

    monkeypatch.setattr(api.ClientUsageLogDB, "record_llm_usage", staticmethod(record))
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, chat=chat, models=models, billed=billed)


def post(rig, body, token="paired-token"):
    return rig.client.post("/api/local-tools/runtime/wecom/calibrate", json=body,
                           headers={"Authorization": "Bearer " + token})


def test_calibration_uses_paired_tenant_fixed_prompt_and_original_billing(rig):
    response = post(rig, {"image": IMAGE})
    assert response.status_code == 200
    assert response.json()["content"].startswith('{"divider_x":')
    assert rig.models == ["GLM-5.3-Flash"]
    kwargs = rig.chat.await_args.kwargs
    assert kwargs["temperature"] == 0 and kwargs["reasoning_effort"] == "max"
    assert kwargs["messages"][0]["role"] == "system"
    assert kwargs["messages"][1]["content"][1]["image_url"]["url"] == IMAGE
    assert len(rig.billed) == 1
    assert rig.billed[0]["tenant_id"] == "paired-tenant"
    assert rig.billed[0]["binding_id"] == "wecom-local-runtime"
    assert rig.billed[0]["stage"] == "wecom-title-coordinate"
    assert rig.billed[0]["detail"] == {"device_id": "device-1"}
    assert rig.billed[0]["usage"] == {"prompt_tokens": 100, "completion_tokens": 30}


def test_missing_or_revoked_device_cannot_use_model_or_bill(rig):
    assert rig.client.post("/api/local-tools/runtime/wecom/calibrate", json={"image": IMAGE}).status_code == 401
    assert post(rig, {"image": IMAGE}, token="revoked-token").status_code == 401
    assert rig.models == [] and rig.billed == []


def test_request_cannot_supply_tenant_model_or_remote_image(rig):
    assert post(rig, {"image": IMAGE, "tenant_id": "other", "model": "other"}).status_code == 422
    assert post(rig, {"image": "https://example.invalid/screenshot.png"}).status_code == 400
    assert post(rig, {"image": "data:image/png;base64,Zm9v"}).status_code == 400
    assert rig.models == [] and rig.billed == []


def test_inactive_tenant_cannot_use_model(rig, monkeypatch):
    monkeypatch.setattr(api.TenantDB, "get_by_id", staticmethod(lambda tenant_id: {"status": "disabled"}))
    assert post(rig, {"image": IMAGE}).status_code == 403
    assert rig.models == [] and rig.billed == []
