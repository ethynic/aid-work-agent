"""契约 2.20 Phase 3 集成测试：声明即调度授权——claim 行级放行端到端（真实 DB，不可用时 skip）

覆盖（对应裁决：已上报声明的设备以 declared_tools 放行；未上报声明的旧设备沿
既有静态目录回退，行为不变）：
- 声明设备：声明内工具照常领取（串行与 parallel_only 两条路径）
- 声明设备：声明外工具（清单有声明无的内部命令 wecom_chat_search）拒绝 TOOL_NOT_ALLOWED
- 声明设备：子集声明下目录内但未声明的工具拒绝（声明为准），NULL provider 行沿目录放行
- 旧设备（无声明上报）：声明内工具照常领取；目录外工具照常拒绝（回退目录）

运行：./scripts/dev_test.sh --isolated-db tests/integration/test_tool_declaration_claim.py -p no:cacheprovider -q
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from src.local_tools.security import generate_claim_token, sha256_hex

pytestmark = pytest.mark.integration

WECOM_PROVIDER_KEY = "wecom"
# 与 CLI buildToolDeclaration 白名单同一集合（12 个受信工具）
WECOM_DECLARED = [
    "wecom_probe", "wecom_message_send", "wecom_send_status", "wecom_send_image",
    "wecom_send_file", "wecom_unread_list", "wecom_read_session", "wecom_read_sessions",
    "wecom_session_directory", "wecom_group_poll", "wecom_config_doctor", "wecom_config_calibrate",
]
WECOM_PARALLEL_TOOLS = {"wecom": ["wecom_read_session", "wecom_read_sessions"]}


@pytest.fixture(scope="module")
def client():
    from src.main import app
    return TestClient(app)


@pytest.fixture(scope="module")
def tenants():
    from src.saas.db.tenant_db import TenantDB

    created = []
    for _ in range(1):
        code = f"T{uuid.uuid4().hex[:6].upper()}"
        tenant = TenantDB.create(
            company_name=f"声明领取测试-{code}",
            tenant_code=code,
            contact_name="测试",
            contact_phone="13800000000",
        )
        if not tenant:
            pytest.skip("无法创建测试租户（DB 不可用）")
        created.append(tenant["tenant_id"])

    yield created

    from src.db.database import get_db_connection
    for tenant_id in created:
        TenantDB.delete(tenant_id)
        try:
            with get_db_connection() as conn:
                cur = conn.cursor()
                for table in ("local_tool_events", "local_tool_invocations",
                              "local_tool_devices", "local_tool_pairing_tickets"):
                    cur.execute(f"DELETE FROM {table} WHERE tenant_id = %s", (tenant_id,))
                cur.execute("DELETE FROM tokens WHERE user_id IN (SELECT user_id FROM users WHERE tenant_id = %s)", (tenant_id,))
                cur.execute("DELETE FROM users WHERE tenant_id = %s", (tenant_id,))
                cur.execute("DELETE FROM tenants WHERE tenant_id = %s", (tenant_id,))
                conn.commit()
        except Exception:
            pass


@pytest.fixture(scope="module")
def users(tenants):
    from src.api.auth import generate_token
    from src.db.models import UserDB

    result = []
    for tenant_id in tenants:
        phone = f"199{uuid.uuid4().int % 10**8:08d}"
        user = UserDB.create(phone=phone, username="声明领取测试用户", tenant_id=tenant_id)
        if not user:
            pytest.skip("无法创建测试用户（DB 不可用）")
        result.append({**user, "token": generate_token(user["user_id"])})
    return result


def _pair_device(client, user, name, capabilities):
    resp = client.post("/api/local-tools/pairing-tickets",
                       headers={"Authorization": f"Bearer {user['token']}"})
    assert resp.status_code == 200, resp.text
    resp = client.post(
        "/api/local-tools/runtime/pair",
        json={"code": resp.json()["code"], "name": name, "platform": "windows",
              "runtime_version": "1.0.0", "capabilities": capabilities,
              "machine_fingerprint": "fp-" + uuid.uuid4().hex[:8]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    device_id, device_token = body["device_id"], body["device_token"]
    resp = client.post("/api/local-tools/runtime/heartbeat",
                       json={"runtime_version": "1.0.0", "capabilities": capabilities},
                       headers={"Authorization": f"Bearer {device_token}"})
    assert resp.status_code == 200, resp.text
    return {"device_id": device_id, "device_token": device_token}


@pytest.fixture(scope="module")
def users_devices(client, users):
    """四台设备：全量声明、子集声明（模拟未升级完的目录/声明差集）、
    新插件声明（目录外 provider）、旧设备（无声明）"""
    user = users[0]
    declared = _pair_device(client, user, "声明设备", {
        "providers": [WECOM_PROVIDER_KEY],
        "capabilities": ["local_v2", "tool_declarations_v1"],
        "declared_tools": {"wecom": WECOM_DECLARED},
        "parallel_tools": WECOM_PARALLEL_TOOLS,
    })
    subset = _pair_device(client, user, "子集声明设备", {
        "providers": [WECOM_PROVIDER_KEY, "ai.aidwork.wecom"],
        "provider_id": "ai.aidwork.wecom",
        "capabilities": ["local_v2", "tool_declarations_v1"],
        "declared_tools": {"wecom": ["wecom_probe", "wecom_message_send"]},
    })
    new_plugin = _pair_device(client, user, "新插件声明设备", {
        "providers": ["resume-tool"],
        "capabilities": ["local_v2", "tool_declarations_v1", "runtime_execution_receipt_v1_1"],
        "declared_tools": {"resume-tool": ["resume_parse"]},
    })
    legacy = _pair_device(client, user, "旧设备", {
        "providers": [WECOM_PROVIDER_KEY],
        "capabilities": ["local_v2"],
    })
    return {"user": user, "declared": declared, "subset": subset,
            "new_plugin": new_plugin, "legacy": legacy}


@pytest.fixture(autouse=True)
def _isolated_invocation_queue(users):
    yield
    from src.db.database import get_db_connection
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM local_tool_events WHERE tenant_id = %s", (users[0]["tenant_id"],))
        cur.execute("DELETE FROM local_tool_invocations WHERE tenant_id = %s", (users[0]["tenant_id"],))
        conn.commit()


def _create(user, device_id, tool_name, provider_key=WECOM_PROVIDER_KEY):
    from src.local_tools import repository
    return repository.create_invocation(
        user["tenant_id"], user["user_id"], device_id, tool_name, {},
        provider_key=provider_key,
    )


def _row_state(invocation_id):
    from src.db.database import get_db_connection
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT state, error_code FROM local_tool_invocations WHERE id = %s", (invocation_id,))
        return cur.fetchone()


class TestDeclarationClaimEndToEnd:
    def test_declared_device_claims_declared_tool(self, client, users_devices):
        """声明设备领取声明内工具（串行路径）"""
        device = users_devices["declared"]
        user = users_devices["user"]
        inv = _create(user, device["device_id"], "wecom_message_send")
        resp = client.post("/api/local-tools/runtime/claim?wait=1",
                           headers={"Authorization": f"Bearer {device['device_token']}"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["invocation_id"] == inv
        assert body["provider"] == WECOM_PROVIDER_KEY

    def test_declared_device_claims_declared_tool_via_parallel_lane(self, client, users_devices):
        """声明设备并行领取（parallel_only=true）共用同一放行判定"""
        device = users_devices["declared"]
        user = users_devices["user"]
        inv = _create(user, device["device_id"], "wecom_read_session")
        resp = client.post("/api/local-tools/runtime/claim?wait=1&parallel_only=true",
                           headers={"Authorization": f"Bearer {device['device_token']}"})
        assert resp.status_code == 200
        assert resp.json()["invocation_id"] == inv

    def test_declared_device_rejects_undeclared_internal_tool(self, client, users_devices):
        """声明设备拒绝未声明工具：内部命令 wecom_chat_search（清单有、声明无）→ TOOL_NOT_ALLOWED 终态"""
        device = users_devices["declared"]
        user = users_devices["user"]
        inv = _create(user, device["device_id"], "wecom_chat_search")
        resp = client.post("/api/local-tools/runtime/claim?wait=1",
                           headers={"Authorization": f"Bearer {device['device_token']}"})
        assert resp.status_code == 200
        assert resp.json()["invocation"] is None
        row = _row_state(inv)
        assert row["state"] == "failed"
        assert row["error_code"] == "TOOL_NOT_ALLOWED"

    def test_subset_declaration_governs_over_catalog(self, client, users_devices):
        """子集声明设备：目录内但未声明的工具拒绝（声明为准），NULL provider 行沿目录放行"""
        device = users_devices["subset"]
        user = users_devices["user"]
        inv = _create(user, device["device_id"], "wecom_read_session")
        resp = client.post("/api/local-tools/runtime/claim?wait=1",
                           headers={"Authorization": f"Bearer {device['device_token']}"})
        assert resp.status_code == 200
        assert resp.json()["invocation"] is None
        row = _row_state(inv)
        assert row["state"] == "failed" and row["error_code"] == "TOOL_NOT_ALLOWED", \
            "wecom_read_session 在静态目录内但未声明：声明设备以声明为准拒绝"

        null_inv = _create(user, device["device_id"], "wecom_read_session", provider_key=None)
        resp = client.post("/api/local-tools/runtime/claim?wait=1",
                           headers={"Authorization": f"Bearer {device['device_token']}"})
        assert resp.status_code == 200
        assert resp.json()["invocation_id"] == null_inv, "NULL provider 行（旧聊天链路）仍走目录判定放行"

    def test_new_plugin_provider_claimable_from_declaration(self, client, users_devices):
        """新插件正式接入：目录外 provider（resume-tool）作业经声明驱动可领取（修复轮 3b）"""
        device = users_devices["new_plugin"]
        user = users_devices["user"]
        inv = _create(user, device["device_id"], "resume_parse", provider_key="resume-tool")
        resp = client.post("/api/local-tools/runtime/claim?wait=1",
                           headers={"Authorization": f"Bearer {device['device_token']}"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["invocation_id"] == inv, "声明驱动的目录外 provider 作业可领取"
        assert body["provider"] == "resume-tool"
        assert body["receipt_version"] == "1.1"

        headers = {"Authorization": f"Bearer {device['device_token']}"}
        data = {"receipt_version": "1.1", "step": "validate_document", "known_effect": "none",
                "reason": {"code": "UNREADABLE", "message": "无法读取文件"}, "business": "keep"}
        result = client.post(f"/api/local-tools/runtime/invocations/{inv}/result",
            headers=headers, json={"claim_token": body["claim_token"], "success": False,
                                  "code": "UNREADABLE", "effect": "none", "data": data})
        assert result.status_code == 200, result.text
        from src.local_tools.repository import get_invocation
        persisted = get_invocation(inv, user["tenant_id"])
        assert persisted["state"] == "failed" and persisted["result_json"]["data"] == data

    def test_declared_plugin_screenshot_stays_in_its_original_execution(self, client, users_devices, tmp_path, monkeypatch):
        import asyncio
        import base64
        import json
        from io import BytesIO
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from PIL import Image

        from src.core.storage import resolve_delivery_file_info
        from src.local_tools import repository, result_images
        from src.services.agent_runner.runtime.local_tools import LocalToolAdapter
        from src.tools.context import ToolExecutionContext, tool_execution_scope
        from src.tools.image.image_read_tool import ImageReadTool

        user, device = users_devices["user"], users_devices["new_plugin"]
        tenant, user_id = user["tenant_id"], user["user_id"]
        session, execution, call = ("receipt-session", "receipt-execution", "receipt-call")
        inv = repository.create_invocation(tenant, user_id, device["device_id"], "resume_parse", {},
            session_id=session, provider_key="resume-tool", business_kind="agent_runner",
            business_ref={"runner_id": execution, "execution_id": execution, "tool_call_id": call,
                          "branch": "base", "ordinal": 0}, dedupe_key="receipt-media-test")
        headers = {"Authorization": f"Bearer {device['device_token']}"}
        claim = client.post("/api/local-tools/runtime/claim?wait=0", headers=headers).json()
        assert claim["invocation_id"] == inv and claim["receipt_version"] == "1.1"

        content = BytesIO()
        Image.new("RGB", (2, 2), color="white").save(content, format="PNG")
        encoded = base64.b64encode(content.getvalue()).decode("ascii")
        data = {"receipt_version": "1.1", "known_effect": "none", "business": "keep",
                "screenshot": {"type": "image", "mimeType": "image/png", "data": encoded}}
        answer = client.post(f"/api/local-tools/runtime/invocations/{inv}/result",
            headers=headers, json={"claim_token": claim["claim_token"], "success": False,
                                  "code": "UNREADABLE", "effect": "none", "data": data})
        assert answer.status_code == 200, answer.text
        stored = repository.get_invocation(inv, tenant)["result_json"]
        # A domain adapter may retain only its business summary.
        result = {"data": {"business": "keep"}, "success": False, "effect": "none", "invocation_id": inv}

        monkeypatch.setattr("src.core.storage.configured_storage_root", lambda: tmp_path)
        registry = AsyncMock(side_effect=AssertionError("receipt image must not be registered"))
        stage = AsyncMock(side_effect=AssertionError("receipt image must not be staged as a file"))
        monkeypatch.setattr(result_images, "get_image_registry", registry)
        monkeypatch.setattr(result_images, "stage_result_image", stage)
        monkeypatch.setattr("src.core.image_asset.get_image_registry", registry)

        context = ToolExecutionContext(tenant_id=tenant, user_id=user_id, session_id=session,
            agent_execution_id=execution, tool_call_id=call)

        async def project(context):
            executor = SimpleNamespace(registry=SimpleNamespace(get_tool=lambda name: None),
                execute=AsyncMock(return_value=result))
            adapter = LocalToolAdapter(session_id=session, execution_id=context.agent_execution_id,
                subagent_config=None, llm=None, tool_executor=executor)
            return [payload async for kind, payload in adapter._run_local_required_tool(
                "resume_parse", {}, tenant, context.user_id, context=context) if kind == "result"][0]

        public = asyncio.run(project(context))
        ref = public["data"]["screenshot"]["image_ref"]
        assert public["data"]["business"] == "keep" and public["data"]["known_effect"] == "none"
        assert encoded not in json.dumps(public) and "images" not in public
        assert public["data"]["screenshot"] == {"image_ref": ref}
        assert repository.get_invocation(inv, tenant)["result_json"] == stored

        assert resolve_delivery_file_info(ref) is None
        with tool_execution_scope(context):
            assert resolve_delivery_file_info(ref) is None
        for suffix in ("", "/download"):
            assert client.get(f"/api/files/{ref}{suffix}").status_code == 404
            assert client.get(f"/api/files/{ref}{suffix}",
                headers={"Authorization": f"Bearer {user['token']}"}).status_code == 404

        other = context.derive(agent_execution_id="other-execution")
        assert "screenshot" not in asyncio.run(project(other))["data"]
        assert "screenshot" not in asyncio.run(project(context.derive(user_id="another-user")))["data"]

        reader = ImageReadTool()
        monkeypatch.setattr(reader, "_resolve_model", lambda _: ("fixture", "visual"))
        vision = AsyncMock(return_value="fixture page")
        monkeypatch.setattr(reader, "_call_vl", vision)

        async def read(context):
            with tool_execution_scope(context.derive(tool_call_id="new-image-read-call")):
                return await reader.execute(image=ref, question="fixture screenshot")

        for foreign in (other, context.derive(user_id="another-user"),
                        context.derive(session_id="another-session"), context.derive(tenant_id="another-tenant")):
            assert asyncio.run(read(foreign))["success"] is False
        vision.assert_not_awaited()
        evidence = asyncio.run(read(context))
        assert evidence["answer"] == "fixture page" and "file_path" not in evidence
        assert vision.await_args.args[-1] == "data:image/png;base64," + encoded
        registry.assert_not_called()
        stage.assert_not_called()
        assert list(tmp_path.iterdir()) == []

    def test_legacy_device_falls_back_to_catalog(self, client, users_devices):
        """旧设备（无声明上报）：声明内工具照常领取；目录外工具照常拒绝——行为不变"""
        device = users_devices["legacy"]
        user = users_devices["user"]
        inv = _create(user, device["device_id"], "wecom_message_send")
        resp = client.post("/api/local-tools/runtime/claim?wait=1",
                           headers={"Authorization": f"Bearer {device['device_token']}"})
        assert resp.status_code == 200
        assert resp.json()["invocation_id"] == inv and "receipt_version" not in resp.json()

        inv2 = _create(user, device["device_id"], "wecom_chat_search")
        resp = client.post("/api/local-tools/runtime/claim?wait=1",
                           headers={"Authorization": f"Bearer {device['device_token']}"})
        assert resp.status_code == 200
        assert resp.json()["invocation"] is None
        row = _row_state(inv2)
        assert row["state"] == "failed" and row["error_code"] == "TOOL_NOT_ALLOWED", \
            "旧设备目录外工具沿静态目录拒绝（与升级前行为一致）"
