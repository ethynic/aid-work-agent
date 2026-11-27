"""Batch requests use the signed desktop lane, with a durable receipt per contact."""

import json
import secrets
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import Workbook

from src.config.settings import settings
from src.local_tools import repository, weixin_friends
from src.local_tools.api import router
from src.local_tools.security import sha256_hex
from src.saas.middleware import TenantContextMiddleware
from tests.fixtures import runtime_plugin_registry

pytestmark = pytest.mark.integration
FRIENDS = "/api/local-tools/weixin-agent/friends"
RUNTIME = "/api/local-tools/runtime"


def _web_headers(actor):
    return {"Authorization": f"Bearer {actor.token}", "X-Tenant-Id": actor.tenant_id}


def test_first_excel_sheet_preserves_text_identifier():
    book = Workbook()
    book.active.append(["手机号或微信号", "称呼"])
    book.active.append(["0001234", "小夏"])
    book.create_sheet().append(["不能代替首表"])
    buffer = BytesIO()
    book.save(buffer)
    _, contacts = weixin_friends.parse_file("演示.xlsx", buffer.getvalue())
    assert [(row.identifier, row.name) for row in contacts] == [("0001234", "小夏")]


def test_friend_batch_requires_signed_device_and_classifies_only_confirmed_receipts(
    actors, service_database, tmp_path, monkeypatch,
):
    owner = actors["a"]
    headers = _web_headers(owner)
    provider, tool = weixin_friends.PROVIDER, weixin_friends.TOOL
    monkeypatch.setattr(runtime_plugin_registry, "PROVIDER", provider)
    sign = runtime_plugin_registry.signed_registry(tmp_path)
    definition = runtime_plugin_registry.definition("identifier", name=tool)
    definition["annotations"] = {"readOnlyHint": False}
    bundle = sign(definition)
    digest = bundle["envelope"]["manifest_digest"]
    monkeypatch.setattr(settings.deployment, "private_cloud", False)
    monkeypatch.setattr(settings.agent_runner, "plugin_trust_roots", str(tmp_path / "trust-roots.json"))
    monkeypatch.setattr(weixin_friends, "check_tenant_credit_blocked", lambda _: None)
    app = FastAPI()
    app.add_middleware(TenantContextMiddleware)
    app.include_router(router)
    client = TestClient(app)
    device_token = secrets.token_urlsafe(32)
    device_headers = {"Authorization": f"Bearer {device_token}"}
    device = repository.create_device(owner.tenant_id, owner.user_id, sha256_hex(device_token))
    device_id = str(device["id"])
    source = (
        "称呼,手机号或微信号,验证语\n"
        "安一,wx_test_a,本行验证语\n韩二,wx_test_b,\n"
        "重复行,wx_test_a,\n未填,,\n本次不发,wx_test_c,\n"
    ).encode("utf-8-sig")
    claim_path = f"{RUNTIME}/claim?wait=0"
    capabilities = {"capabilities": ["tool_declarations_v1"],
                    "declared_tools": {provider: [tool]}, "plugin_manifest_digests": {provider: digest}}

    def preview():
        return client.post(f"{FRIENDS}/preview", headers=headers, files={"file": ("试用.csv", source, "text/csv")})

    def claim():
        return client.post(claim_path, headers=device_headers, json={"plugin_manifest_digests": {provider: digest}})

    def runtime_path(claimed, command):
        return f"{RUNTIME}/invocations/{claimed['invocation_id']}/{command}"

    try:
        assert repository.select_device(owner.tenant_id, owner.user_id, device_id)
        response = preview()
        assert response.status_code == 200, response.text
        payload = response.json()
        assert [row.pop("exclusion") for row in payload["contacts"]] == [None, None, "DUPLICATE", "MISSING_IDENTIFIER", None]
        payload["contacts"][-1]["selected"] = False
        payload.update(device_id=device_id, request_id=str(uuid4()), verify_message="批次验证语")
        heartbeat = client.post(f"{RUNTIME}/heartbeat", headers=device_headers, json={"capabilities": capabilities})
        assert heartbeat.json()["missing_plugin_manifests"] == [provider]
        rejected = client.post(f"{FRIENDS}/batches", headers=headers, json=payload)
        assert rejected.status_code == 409 and rejected.json()["detail"]["code"] == "WEIXIN_FRIEND_PLUGIN_UNAVAILABLE"
        assert client.post(f"{RUNTIME}/heartbeat", headers=device_headers, json={
            "capabilities": capabilities, "plugin_manifests": {provider: bundle},
        }).status_code == 200

        def create():
            return client.post(f"{FRIENDS}/batches", headers=headers, json=payload)

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = [job.result() for job in (pool.submit(create), pool.submit(create))]
        assert all(item.status_code == 200 for item in responses), [item.text for item in responses]
        batch_path = f"{FRIENDS}/batches/{responses[0].json()['id']}"
        assert responses[0].json()["id"] == responses[1].json()["id"]
        assert responses[0].json()["counts"] == {"queued": 2, "skipped": 3}
        assert "批次验证语" not in responses[0].text and "本行验证语" not in responses[0].text
        assert client.get(batch_path, headers=_web_headers(actors["a_other"])).status_code == 404
        assert client.get(batch_path, headers=_web_headers(actors["b"])).status_code == 404

        assert client.post(claim_path, headers=device_headers).json().get("invocation_id") is None
        a = claim().json()
        assert a["tool_name"] == tool
        assert a["arguments"] == {"identifier": "wx_test_a", "verify_message": "本行验证语"}
        a_report = {"claim_token": a["claim_token"], "success": True, "effect": "applied", "code": "OK",
                    "data": {"status": "request_sent", "unexpected": "never-store-this"}}
        before_started = client.post(runtime_path(a, "result"), headers=device_headers, json=a_report)
        assert before_started.json()["state"] == "claimed"
        assert client.post(runtime_path(a, "started"), headers=device_headers,
                           json={"claim_token": a["claim_token"]}).status_code == 200
        result = client.post(runtime_path(a, "result"), headers=device_headers, json=a_report)
        assert result.json()["state"] == "succeeded"
        saved = repository.get_invocation(a["invocation_id"], owner.tenant_id)
        assert "never-store-this" not in json.dumps(saved, default=str)
        assert "verify_message" not in saved["arguments_json"]

        b = claim().json()
        assert b["arguments"] == {"identifier": "wx_test_b", "verify_message": "批次验证语"}
        assert client.post(runtime_path(b, "started"), headers=device_headers,
                           json={"claim_token": b["claim_token"]}).status_code == 200
        service_database.rows(
            "UPDATE local_tool_invocations SET lease_expires_at=NOW()-INTERVAL '1 second' WHERE id=%s", (b["invocation_id"],),
        )
        assert client.post(runtime_path(b, "started"), headers=device_headers,
                           json={"claim_token": b["claim_token"]}).status_code == 404
        assert claim().json().get("invocation_id") is None
        late_inconsistent_receipt = client.post(runtime_path(b, "result"), headers=device_headers, json={
            **a_report, "claim_token": b["claim_token"], "effect": "none",
        })
        assert late_inconsistent_receipt.json()["state"] == "unknown"
        result = client.get(batch_path, headers=headers).json()
        assert result["counts"] == {"request_sent": 1, "unknown": 1, "skipped": 3}
        assert result["contacts"][0]["submitted_at"]
        assert result["contacts"][1]["submitted_at"] is None
        assert result["contacts"][-1]["reason"] == "NOT_SELECTED"

        preview_again = preview().json()["contacts"]
        assert [row["exclusion"] for row in preview_again[:2]] == ["PREVIOUS_REQUEST", "PREVIOUS_UNKNOWN"]
        repeat = client.post(f"{FRIENDS}/batches", headers=headers, json={**payload, "request_id": str(uuid4())})
        assert repeat.json()["counts"] == {"skipped": 5}
        assert claim().json().get("invocation_id") is None
        history = client.get(f"{FRIENDS}/batches", headers=headers).json()
        assert len(history["batches"]) == 2
        assert all("contacts" not in row for row in history["batches"])
        assert len(client.get(f"{FRIENDS}/batches", headers=_web_headers(actors["a_other"])).json()["batches"]) == 0

        reconfigured = sign({**definition, "description": "new signed schema"})
        c_request = {**payload, "request_id": str(uuid4()), "contacts": [{"identifier": "wx_test_c", "selected": True}]}
        c_batch = client.post(f"{FRIENDS}/batches", headers=headers, json=c_request).json()
        assert c_batch["counts"] == {"queued": 1}
        heartbeat = client.post(f"{RUNTIME}/heartbeat", headers=device_headers, json={
            "capabilities": {**capabilities, "plugin_manifest_digests": {provider: reconfigured["envelope"]["manifest_digest"]}},
            "plugin_manifests": {provider: reconfigured},
        })
        assert heartbeat.status_code == 200
        assert claim().json().get("invocation_id") is None
        expired = client.get(f"{FRIENDS}/batches/{c_batch['id']}", headers=headers).json()
        assert expired["contacts"][0]["reason"] == repository.PLUGIN_CHANGED_CODE
    finally:
        service_database.rows("DELETE FROM bs_weixin_friend_batches WHERE device_id=%s", (device_id,))
        service_database.rows(
            "DELETE FROM local_tool_events WHERE invocation_id IN (SELECT id FROM local_tool_invocations WHERE device_id=%s)",
            (device_id,),
        )
        service_database.rows("DELETE FROM local_tool_invocations WHERE device_id=%s", (device_id,))
        service_database.rows("DELETE FROM local_tool_devices WHERE id=%s", (device_id,))
