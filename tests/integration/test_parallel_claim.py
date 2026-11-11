"""契约 2.20 Phase 2 集成测试：parallel_only 领取过滤（真实 DB，不可用时 skip）

覆盖（对应设计「服务端排队，Runtime 领取能开工的任务」）：
- parallel_only 领到并行工具作业
- 跳过队头串行作业且不消耗它（串行原地等待）
- provider_key NULL 旧聊天链路作业永不并行可领
- 缺省参数行为与旧版一致（FIFO 不变）
- 心跳响应携带 parallel_claim=True
- 既有调用方回归锚：session_task 道定向领取不受并行参数影响

运行：./scripts/dev_test.sh --isolated-db tests/integration/test_parallel_claim.py -p no:cacheprovider -q
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from src.local_tools.security import generate_claim_token, sha256_hex

pytestmark = pytest.mark.integration

WECOM_PROVIDER_KEY = "wecom"
PARALLEL_TOOLS = {"wecom": ["wecom_read_session", "wecom_read_sessions"]}


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
            company_name=f"并行领取测试-{code}",
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
        user = UserDB.create(phone=phone, username="并行领取测试用户", tenant_id=tenant_id)
        if not user:
            pytest.skip("无法创建测试用户（DB 不可用）")
        result.append({**user, "token": generate_token(user["user_id"])})
    return result


@pytest.fixture(scope="module")
def parallel_device(client, users):
    """已配对且心跳上报 parallel_tools 的设备（能力对齐新版 Runtime：providers + parallel_tools）"""
    from src.db.database import get_db_connection

    user = users[0]
    resp = client.post("/api/local-tools/pairing-tickets", headers={"Authorization": f"Bearer {user['token']}"})
    assert resp.status_code == 200, resp.text
    code = resp.json()["code"]
    resp = client.post(
        "/api/local-tools/runtime/pair",
        json={
            "code": code,
            "name": "并行领取测试设备",
            "platform": "windows",
            "runtime_version": "1.0.0",
            "capabilities": {"providers": [WECOM_PROVIDER_KEY], "parallel_tools": PARALLEL_TOOLS},
            "machine_fingerprint": "fp-" + uuid.uuid4().hex[:8],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    device_id, device_token = body["device_id"], body["device_token"]

    # Web 端选定 + 心跳带能力（capabilities_json 落库）
    resp = client.post(f"/api/local-tools/devices/{device_id}/select",
                       headers={"Authorization": f"Bearer {user['token']}"})
    assert resp.status_code == 200, resp.text
    resp = client.post("/api/local-tools/runtime/heartbeat",
                       json={"runtime_version": "1.0.0",
                             "capabilities": {"providers": [WECOM_PROVIDER_KEY], "parallel_tools": PARALLEL_TOOLS}},
                       headers={"Authorization": f"Bearer {device_token}"})
    assert resp.status_code == 200, resp.text

    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT capabilities_json FROM local_tool_devices WHERE id = %s", (device_id,))
        caps = cur.fetchone()["capabilities_json"]
    assert caps.get("parallel_tools") == PARALLEL_TOOLS
    return {"device_id": device_id, "device_token": device_token, "tenant_id": user["tenant_id"], "user_id": user["user_id"]}


@pytest.fixture(autouse=True)
def _isolated_invocation_queue(users):
    """模块级设备/租户共享同一条 FIFO 队列：用例间清理 invocation，防止上一用例
    「串行原地等待」遗留的 queued 行被下一用例的缺省领取按 FIFO 消费（真实 PG 复现）"""
    yield
    from src.db.database import get_db_connection
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM local_tool_events WHERE tenant_id = %s", (users[0]["tenant_id"],))
        cur.execute("DELETE FROM local_tool_invocations WHERE tenant_id = %s", (users[0]["tenant_id"],))
        conn.commit()


def _create(user, device_id, tool_name, provider_key=None, execution_lane="standard"):
    from src.local_tools import repository
    return repository.create_invocation(
        user["tenant_id"], user["user_id"], device_id, tool_name, {},
        provider_key=provider_key, execution_lane=execution_lane,
    )


def _claim(user, device_id, parallel_only=False, parallel_tools=None, **kwargs):
    from src.local_tools import repository
    token = generate_claim_token()
    return repository.claim_next(
        device_id, user["tenant_id"], sha256_hex(token), 60,
        provider_keys=[WECOM_PROVIDER_KEY],
        parallel_only=parallel_only, parallel_tools=parallel_tools, **kwargs,
    )


class TestParallelClaimRepository:
    def test_parallel_only_claims_parallel_tool_job(self, users, parallel_device):
        """parallel_only 领到并行工具作业（provider_key + tool_name 命中设备上报映射）"""
        inv = _create(users[0], parallel_device["device_id"], "wecom_read_session", provider_key=WECOM_PROVIDER_KEY)
        claimed = _claim(users[0], parallel_device["device_id"], parallel_only=True, parallel_tools=PARALLEL_TOOLS)
        assert claimed is not None
        assert str(claimed["id"]) == inv
        assert claimed["state"] == "claimed"

    def test_parallel_only_skips_serial_head_without_consuming(self, users, parallel_device):
        """队头串行作业原地等待：parallel_only 跳过它领取后面的并行作业，串行作业不被消耗"""
        serial = _create(users[0], parallel_device["device_id"], "wecom_message_send", provider_key=WECOM_PROVIDER_KEY)
        parallel_inv = _create(users[0], parallel_device["device_id"], "wecom_read_sessions", provider_key=WECOM_PROVIDER_KEY)
        claimed = _claim(users[0], parallel_device["device_id"], parallel_only=True, parallel_tools=PARALLEL_TOOLS)
        assert str(claimed["id"]) == parallel_inv

        from src.db.database import get_db_connection
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT state FROM local_tool_invocations WHERE id = %s", (serial,))
            assert cur.fetchone()["state"] == "queued", "队头串行作业必须原地保持 queued"

    def test_null_provider_key_never_parallel_claimable(self, users, parallel_device):
        """provider_key NULL（旧聊天链路作业）永不并行可领；缺省参数可领（旧行为）"""
        inv = _create(users[0], parallel_device["device_id"], "boss_goto", provider_key=None)
        assert _claim(users[0], parallel_device["device_id"], parallel_only=True, parallel_tools=PARALLEL_TOOLS) is None
        claimed = _claim(users[0], parallel_device["device_id"])
        assert str(claimed["id"]) == inv

    def test_default_args_fifo_unchanged(self, users, parallel_device):
        """缺省参数（不传 parallel_only）：FIFO 顺序与旧版一致，串行队头先领"""
        first = _create(users[0], parallel_device["device_id"], "wecom_message_send", provider_key=WECOM_PROVIDER_KEY)
        _create(users[0], parallel_device["device_id"], "wecom_read_session", provider_key=WECOM_PROVIDER_KEY)
        claimed = _claim(users[0], parallel_device["device_id"])
        assert str(claimed["id"]) == first

    def test_session_lane_directed_claim_regression(self, users, parallel_device):
        """既有调用方回归锚：session_task 道定向领取在并行参数引入后行为不变"""
        from src.local_tools import repository
        inv = _create(users[0], parallel_device["device_id"], "wecom_read_session",
                      provider_key=WECOM_PROVIDER_KEY, execution_lane="session_task")
        # 通用 claim（含并行模式）都领不到 session 道作业
        assert _claim(users[0], parallel_device["device_id"], parallel_only=True, parallel_tools=PARALLEL_TOOLS) is None
        # 定向领取照常
        claimed = repository.claim_next(
            parallel_device["device_id"], users[0]["tenant_id"], sha256_hex(generate_claim_token()), 60,
            provider_keys=[WECOM_PROVIDER_KEY], execution_lane="session_task", expected_invocation_id=inv,
        )
        assert claimed is not None and str(claimed["id"]) == inv


class TestParallelClaimApi:
    def test_heartbeat_response_has_parallel_claim(self, client, parallel_device):
        """心跳响应携带 parallel_claim=True（支持协商标记）"""
        resp = client.post("/api/local-tools/runtime/heartbeat",
                           json={"runtime_version": "1.0.0"},
                           headers={"Authorization": f"Bearer {parallel_device['device_token']}"})
        assert resp.status_code == 200
        assert resp.json()["parallel_claim"] is True

    def test_claim_endpoint_parallel_only(self, client, users, parallel_device):
        """端到端：claim?parallel_only=true 只领并行作业；串行作业留队"""
        serial = _create(users[0], parallel_device["device_id"], "wecom_message_send", provider_key=WECOM_PROVIDER_KEY)
        parallel_inv = _create(users[0], parallel_device["device_id"], "wecom_read_session", provider_key=WECOM_PROVIDER_KEY)
        runtime = {"Authorization": f"Bearer {parallel_device['device_token']}"}

        resp = client.post("/api/local-tools/runtime/claim?wait=1&parallel_only=true", headers=runtime)
        assert resp.status_code == 200
        body = resp.json()
        assert body["invocation_id"] == parallel_inv

        resp = client.post("/api/local-tools/runtime/claim?wait=1", headers=runtime)
        assert resp.status_code == 200
        assert resp.json()["invocation_id"] == serial

    def test_claim_endpoint_parallel_only_without_capability(self, client, users):
        """设备未上报 parallel_tools 时 parallel_only=true 领不到任何作业（不抛 500）"""
        user = users[0]
        resp = client.post("/api/local-tools/pairing-tickets", headers={"Authorization": f"Bearer {user['token']}"})
        code = resp.json()["code"]
        resp = client.post(
            "/api/local-tools/runtime/pair",
            json={"code": code, "name": "无并行能力设备", "platform": "windows", "runtime_version": "1.0.0",
                  "capabilities": {"providers": [WECOM_PROVIDER_KEY]},
                  "machine_fingerprint": "fp-" + uuid.uuid4().hex[:8]},
        )
        assert resp.status_code == 200
        device_token = resp.json()["device_token"]
        device_id = resp.json()["device_id"]
        client.post(f"/api/local-tools/devices/{device_id}/select",
                    headers={"Authorization": f"Bearer {user['token']}"})

        _create(user, device_id, "wecom_read_session", provider_key=WECOM_PROVIDER_KEY)
        resp = client.post("/api/local-tools/runtime/claim?wait=1&parallel_only=true",
                           headers={"Authorization": f"Bearer {device_token}"})
        assert resp.status_code == 200
        assert resp.json()["invocation"] is None
