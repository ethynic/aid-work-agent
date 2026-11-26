"""Runtime device authentication, fixed-purpose WeChat vision, and tenant billing."""

import base64
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from src.api import client_routes
from src.local_tools import api, security, weixin_visual

pytestmark = [pytest.mark.integration, pytest.mark.api]
PATH = "/api/local-tools/runtime/weixin/visual-locate"


@pytest.fixture(scope="session", autouse=True)
def init_db_pool():
    yield


@pytest.fixture
def visual(monkeypatch):
    device = {"id": "device-a", "tenant_id": "tenant-a", "user_id": "user-a"}
    user = {"tenant_id": "tenant-a", "status": "active"}
    tenant = {"status": "active", "credit_balance": 100}
    monkeypatch.setattr(api.repository, "get_device_by_token_hash",
                        lambda digest: device if digest == security.sha256_hex("runtime-test") else None)
    monkeypatch.setattr(api.UserDB, "get_by_id", lambda user_id: user if user_id == "user-a" else None)
    monkeypatch.setattr(api.TenantDB, "get_by_id", lambda tenant_id: tenant if tenant_id == "tenant-a" else None)
    model = SimpleNamespace(
        chat=AsyncMock(return_value={"content": '{"plus":{"x":1,"y":2}}',
                                     "usage": {"prompt_tokens": 42, "completion_tokens": 7}}),
        get_model_name=lambda: "GLM-5.3-Flash",
        get_provider_name=lambda: "zhipu",
    )
    gateway = Mock(return_value=model)
    ledger = Mock(return_value={"raw_credit_cost": 0.1, "credit_cost": 1, "balance_after": 99})
    monkeypatch.setattr(client_routes, "_get_model_gateway", gateway)
    monkeypatch.setattr(weixin_visual.ClientUsageLogDB, "record_llm_usage", ledger)
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, gateway=gateway, model=model, ledger=ledger, user=user, tenant=tenant)


def image_url():
    image = io.BytesIO()
    Image.new("RGB", (12, 8), "white").save(image, format="PNG")
    return "data:image/png;base64," + base64.b64encode(image.getvalue()).decode("ascii")


def post(visual, *, token="runtime-test", **overrides):
    return visual.client.post(PATH, json={"step": "plus", "image_url": image_url(), **overrides},
                              headers={"Authorization": f"Bearer {token}"})


def test_paired_device_calls_fixed_glm_and_bills_its_tenant_without_client_binding(visual):
    response = post(visual)
    assert response.status_code == 200
    assert response.json()["content"] == '{"plus":{"x":1,"y":2}}'
    visual.gateway.assert_called_once_with("GLM-5.3-Flash")
    visual.model.chat.assert_awaited_once()
    call = visual.model.chat.await_args.kwargs
    assert "Find the + button" in call["messages"][1]["content"][1]["text"]
    assert call["reasoning_effort"] == "max"
    assert call["messages"][1]["content"][0]["image_url"]["url"] == image_url()
    visual.ledger.assert_called_once_with(
        tenant_id="tenant-a", binding_id="weixin-local-runtime", model="GLM-5.3-Flash",
        provider="zhipu", usage={"prompt_tokens": 42, "completion_tokens": 7},
        stage="weixin-vision-weixin-driver-add-friend",
        detail={"device_id": "device-a", "user_id": "user-a", "step": "plus"},
    )


def test_bad_credentials_owner_balance_and_unsupported_payload_never_call_model(visual):
    assert post(visual, token="client-binding-token").status_code == 401
    assert post(visual, step="arbitrary_chat").status_code == 422
    assert post(visual, image_url="http://localhost/image.png").status_code == 422
    assert post(visual, image_url="data:image/png;base64,bm90LXBuZw==").status_code == 422
    assert post(visual, tenant_id="another-tenant", model="another-model").status_code == 422
    visual.user["tenant_id"] = "another-tenant"
    assert post(visual).status_code == 403
    visual.user["tenant_id"] = "tenant-a"
    visual.tenant["status"] = "disabled"
    assert post(visual).status_code == 403
    visual.tenant.update(status="active", credit_balance=0)
    assert post(visual).status_code == 402
    visual.gateway.assert_not_called()
    visual.model.chat.assert_not_awaited()
    visual.ledger.assert_not_called()


def test_model_error_never_echoes_visual_input_or_bills(visual):
    visual.model.chat.side_effect = RuntimeError("private-input-placeholder")
    response = post(visual)
    assert response.status_code == 502
    assert "private-input-placeholder" not in response.text
    visual.ledger.assert_not_called()
