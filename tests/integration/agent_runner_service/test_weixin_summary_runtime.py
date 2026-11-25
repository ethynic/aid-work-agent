"""A consented WeChat summary uses one signed device claim and persists only its summary."""
from concurrent.futures import ThreadPoolExecutor
import json
import secrets

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.config.settings import settings
from src.core.cache_utils import invalidate_tenant_cache
from src.local_tools import repository, weixin_summary
from src.local_tools.api import router
from src.local_tools.security import sha256_hex
from src.saas.middleware import TenantContextMiddleware
from tests.fixtures import runtime_plugin_registry


pytestmark = pytest.mark.integration
RUNS = "/api/local-tools/weixin-summary/runs"
RUNTIME = "/api/local-tools/runtime"


def _web_headers(actor):
    return {"Authorization": f"Bearer {actor.token}", "X-Tenant-Id": actor.tenant_id}


def test_summary_grant_is_bound_to_started_signed_run(actors, service_database, tmp_path, monkeypatch):
    owner = actors["a"]
    provider = "weixin"
    tool = weixin_summary.TOOL
    monkeypatch.setattr(runtime_plugin_registry, "PROVIDER", provider)
    sign = runtime_plugin_registry.signed_registry(tmp_path)
    bundle = sign(runtime_plugin_registry.definition("target_name", name=tool))
    digest = bundle["envelope"]["manifest_digest"]
    monkeypatch.setattr(settings.deployment, "private_cloud", False)
    monkeypatch.setattr(settings.agent_runner, "plugin_trust_roots", str(tmp_path / "trust-roots.json"))

    message = "synthetic source message 5ffc92, never included in the result"
    summary = "下周确认验收安排"
    model_calls = []

    async def model(messages, tenant_id, user_id):
        model_calls.append((messages, tenant_id, user_id))
        return summary

    monkeypatch.setattr(weixin_summary, "summarize", model)
    app = FastAPI()
    app.add_middleware(TenantContextMiddleware)
    app.include_router(router)
    client = TestClient(app)
    device_token = secrets.token_urlsafe(32)
    device_headers = {"Authorization": f"Bearer {device_token}"}
    local_device = repository.create_device(owner.tenant_id, owner.user_id, sha256_hex(device_token))
    device_id = str(local_device["id"])
    other_device = repository.create_device(owner.tenant_id, owner.user_id, sha256_hex(secrets.token_urlsafe(32)))
    other_id = str(other_device["id"])
    request_body = {"target_name": "项目组测试群", "device_id": device_id, "confirmed": True}
    try:
        assert repository.select_device(owner.tenant_id, owner.user_id, device_id)
        response = client.post(f"{RUNTIME}/heartbeat", headers=device_headers, json={
            "capabilities": {
                "capabilities": ["tool_declarations_v1"],
                "declared_tools": {provider: [tool]},
                "plugin_manifest_digests": {provider: digest},
            },
        })
        assert response.status_code == 200
        assert response.json()["missing_plugin_manifests"] == [provider]
        response = client.post(RUNS, headers=_web_headers(owner), json=request_body)
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "WEIXIN_SUMMARY_PLUGIN_UNAVAILABLE"

        response = client.post(f"{RUNTIME}/heartbeat", headers=device_headers, json={
            "capabilities": {
                "capabilities": ["tool_declarations_v1"],
                "declared_tools": {provider: [tool]},
                "plugin_manifest_digests": {provider: digest},
            },
            "plugin_manifests": {provider: bundle},
        })
        assert response.status_code == 200
        assert "missing_plugin_manifests" not in response.json()

        assert repository.select_device(owner.tenant_id, owner.user_id, other_id)
        response = client.post(RUNS, headers=_web_headers(owner), json=request_body)
        assert response.status_code == 409 and response.json()["detail"]["code"] == "DEVICE_NOT_SELECTED"
        assert repository.select_device(owner.tenant_id, owner.user_id, device_id)
        response = client.post(RUNS, headers=_web_headers(owner), json={**request_body, "confirmed": False})
        assert response.status_code in (400, 422)
        response = client.post(RUNS, headers=_web_headers(owner), json=request_body)
        assert response.status_code == 200, response.text
        queued = response.json()
        run = f"{RUNS}/{queued['id']}"
        assert queued["state"] == "queued"
        for label in ("a_other", "b"):
            assert client.get(run, headers=_web_headers(actors[label])).status_code == 404

        claim_path = f"{RUNTIME}/claim?wait=0"
        assert client.post(claim_path, headers=device_headers).json().get("invocation") is None
        claimed = client.post(claim_path, headers=device_headers, json={
            "plugin_manifest_digests": {provider: digest},
        }).json()
        assert claimed["invocation_id"] == queued["id"]
        assert claimed["tool_name"] == tool
        assert claimed["arguments"]["target_name"] == request_body["target_name"]
        assert claimed["arguments"]["business_date"] == queued["business_date"]
        grant = claimed["arguments"]["grant"]
        assert grant not in response.text
        assert grant not in client.get(run, headers=_web_headers(owner)).text
        upload_path = f"{RUNTIME}/weixin-summary"
        upload_headers = {"Authorization": f"Bearer {grant}"}
        upload = {"tenant_id": owner.tenant_id, "business_date": queued["business_date"], "messages": [message]}
        assert client.post(upload_path, headers=upload_headers, json=upload).status_code == 403
        invocation = f"{RUNTIME}/invocations/{queued['id']}"
        claim_token = {"claim_token": claimed["claim_token"]}
        assert client.post(f"{invocation}/started", headers=device_headers, json=claim_token).status_code == 200
        assert client.post(upload_path, headers=upload_headers,
                           json={**upload, "tenant_id": actors["b"].tenant_id}).status_code == 403

        def attempt():
            return client.post(upload_path, headers=upload_headers, json=upload)

        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = [work.result() for work in (executor.submit(attempt), executor.submit(attempt))]
        assert sorted(item.status_code for item in responses) == [200, 403]
        assert model_calls == [([message], owner.tenant_id, owner.user_id)]

        response = client.post(f"{invocation}/result", headers=device_headers, json={
            **claim_token, "success": True, "code": "OK", "effect": "none",
            "data": {
                "summary": summary, "message_count": 1, "partial": True,
                "messages": [message],
            },
        })
        assert response.status_code == 200 and response.json()["state"] == "succeeded"
        result = client.get(run, headers=_web_headers(owner)).json()
        assert result["target_name"] == request_body["target_name"]
        assert result["summary"] == summary and result["partial"] and result["message_count"] == 1
        stored = repository.get_invocation(queued["id"], owner.tenant_id)
        assert message not in json.dumps(stored, default=str)
        assert grant not in json.dumps(stored, default=str)

        def set_balance(balance):
            service_database.rows("UPDATE tenants SET credit_balance=%s WHERE tenant_id=%s", (balance, owner.tenant_id))
            invalidate_tenant_cache(owner.tenant_id)

        set_balance(0)
        blocked = client.post(RUNS, headers=_web_headers(owner), json=request_body)
        assert blocked.status_code == 403 and blocked.json()["detail"]["code"] == "NO_CREDIT"
        set_balance(1000)
        queued_again = client.post(RUNS, headers=_web_headers(owner), json=request_body).json()
        claimed_again = client.post(claim_path, headers=device_headers, json={
            "plugin_manifest_digests": {provider: digest},
        }).json()
        assert claimed_again["invocation_id"] == queued_again["id"]
        assert client.post(
            f"{RUNTIME}/invocations/{queued_again['id']}/started", headers=device_headers,
            json={"claim_token": claimed_again["claim_token"]},
        ).status_code == 200
        set_balance(0)
        depleted = client.post(upload_path, headers={
            "Authorization": "Bearer " + claimed_again["arguments"]["grant"],
        }, json={**upload, "business_date": queued_again["business_date"]})
        assert depleted.status_code == 403 and depleted.json()["code"] == "NO_CREDIT"
        assert len(model_calls) == 1
    finally:
        service_database.rows(
            "DELETE FROM local_tool_events WHERE invocation_id IN (SELECT id FROM local_tool_invocations WHERE device_id=%s)",
            (device_id,),
        )
        service_database.rows("DELETE FROM local_tool_invocations WHERE device_id=%s", (device_id,))
        service_database.rows("DELETE FROM local_tool_devices WHERE id IN (%s,%s)", (device_id, other_id))
