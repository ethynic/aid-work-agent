"""本地工具 API 集成测试（真实 DB，不可用时 skip）

覆盖（对应 m03-implementation-spec.md §5）：
- 全链路：ticket → pair → heartbeat → create_invocation → claim → started
  → progress×2 → result → 重复 result 幂等
- 配对码单次使用 / 过期不可消费
- 撤销设备后 token 失效（401）
- 租户隔离：另一 tenant 设备 claim 不到本 tenant invocation；Web API 看不到别的 tenant 设备
- 并发 claim：多线程同时 claim 无重复领取（SKIP LOCKED）
- 取消：request_cancel 后 progress 返回 cancel=true
- 跨连接可见性：未提交不可见、提交后可见（模拟多 worker）
"""

import threading
import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

PROVIDER_ID = "ai.aidwork.boss-recruiting"


@pytest.fixture(scope="module")
def client():
    from src.main import app
    return TestClient(app)


@pytest.fixture(scope="module")
def tenants():
    """两个临时租户，测试后物理清理"""
    from src.saas.db.tenant_db import TenantDB

    created = []
    for _ in range(2):
        code = f"T{uuid.uuid4().hex[:6].upper()}"
        tenant = TenantDB.create(
            company_name=f"本地工具测试-{code}",
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
    """每个租户一个用户 + 一个登录 token"""
    from src.api.auth import generate_token
    from src.db.models import UserDB

    result = []
    for i, tenant_id in enumerate(tenants):
        phone = f"199{uuid.uuid4().int % 10**8:08d}"
        user = UserDB.create(phone=phone, username=f"本地工具测试用户{i}", tenant_id=tenant_id)
        if not user:
            pytest.skip("无法创建测试用户（DB 不可用）")
        token = generate_token(user["user_id"])
        result.append({"user_id": user["user_id"], "tenant_id": tenant_id, "token": token})
    return result


def _auth(user):
    return {"Authorization": f"Bearer {user['token']}"}


def _pair_device(client, user, name="测试设备"):
    """走完整 Web→Runtime 配对，返回 (device_id, device_token)"""
    resp = client.post("/api/local-tools/pairing-tickets", headers=_auth(user))
    assert resp.status_code == 200, resp.text
    code = resp.json()["code"]

    resp = client.post(
        "/api/local-tools/runtime/pair",
        json={
            "code": code,
            "name": name,
            "platform": "windows",
            "runtime_version": "1.0.0",
            "capabilities": {"provider_id": PROVIDER_ID},
            "machine_fingerprint": "fp-" + uuid.uuid4().hex[:8],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return body["device_id"], body["device_token"]


class TestFullFlow:
    def test_full_invocation_lifecycle(self, client, users):
        """全链路：配对 → 心跳 → 选定 → claim → started → progress×2 → result → 幂等"""
        user = users[0]
        device_id, device_token = _pair_device(client, user)
        runtime = {"Authorization": f"Bearer {device_token}"}

        # 心跳：未选定时 selected=False
        resp = client.post("/api/local-tools/runtime/heartbeat",
                           json={"runtime_version": "1.0.0"}, headers=runtime)
        assert resp.status_code == 200, resp.text
        assert resp.json()["selected"] is False

        # Web 设备列表：在线、未选定
        resp = client.get("/api/local-tools/devices", headers=_auth(user))
        assert resp.status_code == 200
        devices = resp.json()["devices"]
        assert len(devices) == 1
        assert devices[0]["device_id"] == device_id
        assert devices[0]["online"] is True

        # 选定设备
        resp = client.post(f"/api/local-tools/devices/{device_id}/select", headers=_auth(user))
        assert resp.status_code == 200, resp.text
        resp = client.post("/api/local-tools/runtime/heartbeat", json={}, headers=runtime)
        assert resp.json()["selected"] is True

        # 直接调 repository 创建 invocation（M0.5 由路由层创建）
        from src.local_tools import repository
        invocation_id = repository.create_invocation(
            user["tenant_id"], user["user_id"], device_id, "boss_goto", {"url": "https://example.com"}
        )

        # claim 领取
        resp = client.post("/api/local-tools/runtime/claim?wait=2", headers=runtime)
        assert resp.status_code == 200, resp.text
        claim = resp.json()
        assert claim["invocation_id"] == invocation_id
        assert claim["tool_name"] == "boss_goto"
        assert claim["arguments"] == {"url": "https://example.com"}
        assert claim["provider"] == "boss-recruiting"
        claim_token = claim["claim_token"]

        # claim 超时（队列已空）→ invocation: null
        resp = client.post("/api/local-tools/runtime/claim?wait=1", headers=runtime)
        assert resp.status_code == 200
        assert resp.json()["invocation"] is None

        # started
        resp = client.post(f"/api/local-tools/runtime/invocations/{invocation_id}/started",
                           json={"claim_token": claim_token}, headers=runtime)
        assert resp.status_code == 200, resp.text
        assert resp.json()["state"] == "running"

        # progress ×2：seq 递增、cancel=False
        for i, expected_seq in enumerate((1, 2)):
            resp = client.post(
                f"/api/local-tools/runtime/invocations/{invocation_id}/progress",
                json={"claim_token": claim_token, "stage": "s", "current": i + 1,
                      "total": 2, "message": f"步骤{i + 1}"},
                headers=runtime,
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["seq"] == expected_seq
            assert body["cancel"] is False

        # result：succeeded
        resp = client.post(
            f"/api/local-tools/runtime/invocations/{invocation_id}/result",
            json={"claim_token": claim_token, "success": True, "effect": "applied",
                  "data": {"ok": True}},
            headers=runtime,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["state"] == "succeeded"
        assert body["effect"] == "applied"

        # 重复 result：幂等返回原状态
        resp = client.post(
            f"/api/local-tools/runtime/invocations/{invocation_id}/result",
            json={"claim_token": claim_token, "success": False, "code": "X"},
            headers=runtime,
        )
        assert resp.status_code == 200
        assert resp.json()["state"] == "succeeded"

    def test_progress_message_truncated(self, client, users):
        """进度文案服务端截断 500 字符"""
        user = users[0]
        device_id, device_token = _pair_device(client, user)
        runtime = {"Authorization": f"Bearer {device_token}"}

        from src.local_tools import repository
        from src.db.database import get_db_connection
        invocation_id = repository.create_invocation(
            user["tenant_id"], user["user_id"], device_id, "boss_goto", {}
        )
        resp = client.post("/api/local-tools/runtime/claim?wait=2", headers=runtime)
        claim_token = resp.json()["claim_token"]

        long_msg = "长" * 1000
        resp = client.post(
            f"/api/local-tools/runtime/invocations/{invocation_id}/progress",
            json={"claim_token": claim_token, "message": long_msg},
            headers=runtime,
        )
        assert resp.status_code == 200, resp.text

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT message FROM local_tool_events WHERE invocation_id = %s",
                        (invocation_id,))
            row = cur.fetchone()
        assert len(row["message"]) == 500


class TestPairingSecurity:
    def test_code_single_use(self, client, users):
        """配对码单次使用：第二次 pair 返回 400"""
        user = users[0]
        resp = client.post("/api/local-tools/pairing-tickets", headers=_auth(user))
        code = resp.json()["code"]

        payload = {"code": code, "capabilities": {"provider_id": PROVIDER_ID}}
        resp = client.post("/api/local-tools/runtime/pair", json=payload)
        assert resp.status_code == 200
        resp = client.post("/api/local-tools/runtime/pair", json=payload)
        assert resp.status_code == 400

    def test_expired_code_rejected(self, client, users):
        """过期配对码不可消费"""
        user = users[0]
        resp = client.post("/api/local-tools/pairing-tickets", headers=_auth(user))
        code = resp.json()["code"]

        from src.db.database import get_db_connection
        from src.local_tools.security import sha256_hex
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE local_tool_pairing_tickets SET expires_at = %s WHERE code_hash = %s",
                (datetime.now() - timedelta(seconds=1), sha256_hex(code)),
            )
            conn.commit()

        resp = client.post("/api/local-tools/runtime/pair",
                           json={"code": code, "capabilities": {"provider_id": PROVIDER_ID}})
        assert resp.status_code == 400

    def test_revoked_device_token_invalid(self, client, users):
        """撤销设备后 device token 立即失效（401）"""
        user = users[0]
        device_id, device_token = _pair_device(client, user)
        runtime = {"Authorization": f"Bearer {device_token}"}

        resp = client.delete(f"/api/local-tools/devices/{device_id}", headers=_auth(user))
        assert resp.status_code == 200, resp.text

        resp = client.post("/api/local-tools/runtime/heartbeat", json={}, headers=runtime)
        assert resp.status_code == 401

    def test_device_token_cannot_call_web_api(self, client, users):
        """设备 token 不能调 Web 用户 API（token 体系隔离）"""
        user = users[0]
        _, device_token = _pair_device(client, user)
        resp = client.get("/api/local-tools/devices",
                          headers={"Authorization": f"Bearer {device_token}"})
        assert resp.status_code == 401

    def test_web_api_requires_auth(self, client):
        """Web API 无 token → 401"""
        resp = client.post("/api/local-tools/pairing-tickets")
        assert resp.status_code == 401


class TestTenantIsolation:
    def test_cross_tenant_claim_blocked(self, client, users):
        """另一 tenant 的设备 claim 不到本 tenant 的 invocation"""
        user_a, user_b = users[0], users[1]
        device_a, _ = _pair_device(client, user_a, "A机")
        _, token_b = _pair_device(client, user_b, "B机")

        from src.local_tools import repository
        invocation_id = repository.create_invocation(
            user_a["tenant_id"], user_a["user_id"], device_a, "boss_goto", {}
        )

        # B 设备 claim（A 的 tenant 有 queued invocation，但 B 不属于该 tenant）
        resp = client.post("/api/local-tools/runtime/claim?wait=1",
                           headers={"Authorization": f"Bearer {token_b}"})
        assert resp.status_code == 200
        assert resp.json()["invocation"] is None

        # A 设备能 claim 到（验证 invocation 确实可领取，排除误报）
        # 注意：这里不再实际 claim，避免影响其他用例——直接查库确认仍 queued
        from src.db.database import get_db_connection
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT state FROM local_tool_invocations WHERE id = %s", (invocation_id,))
            assert cur.fetchone()["state"] == "queued"

    def test_web_api_device_list_isolated(self, client, users):
        """Web API 看不到别的 tenant 设备"""
        user_a, user_b = users[0], users[1]
        device_a, _ = _pair_device(client, user_a, "A隔离机")

        resp = client.get("/api/local-tools/devices", headers=_auth(user_b))
        assert resp.status_code == 200
        ids = [d["device_id"] for d in resp.json()["devices"]]
        assert device_a not in ids

        # B 用户不能选定/撤销 A 的设备
        resp = client.post(f"/api/local-tools/devices/{device_a}/select", headers=_auth(user_b))
        assert resp.status_code == 404
        resp = client.delete(f"/api/local-tools/devices/{device_a}", headers=_auth(user_b))
        assert resp.status_code == 404


class TestConcurrentClaim:
    def test_no_duplicate_claim(self, client, users):
        """多线程同时 claim 同一设备的多条 invocation：无重复领取（SKIP LOCKED）"""
        user = users[0]
        device_id, _ = _pair_device(client, user)

        from src.local_tools import repository
        from src.local_tools.security import generate_claim_token, sha256_hex

        n = 6
        ids = [
            repository.create_invocation(
                user["tenant_id"], user["user_id"], device_id, "boss_goto", {"i": i}
            )
            for i in range(n)
        ]

        claimed = []
        lock = threading.Lock()

        def worker():
            while True:
                token = generate_claim_token()
                inv = repository.claim_next(device_id, user["tenant_id"], sha256_hex(token), 60)
                if not inv:
                    return
                with lock:
                    claimed.append(str(inv["id"]))

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert len(claimed) == n
        assert len(set(claimed)) == n  # 无重复领取
        assert set(claimed) == set(ids)


class TestCancel:
    def test_queued_cancel_goes_to_terminal_cancelled(self, client, users):
        """queued invocation 被取消：直接落终态 cancelled，且此后 claim 领取不到"""
        user = users[0]
        device_id, device_token = _pair_device(client, user)
        runtime = {"Authorization": f"Bearer {device_token}"}

        from src.local_tools import repository
        from src.db.database import get_db_connection
        invocation_id = repository.create_invocation(
            user["tenant_id"], user["user_id"], device_id, "boss_goto", {}
        )

        ok = repository.request_cancel(invocation_id, user["tenant_id"])
        assert ok is True

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT state, effect FROM local_tool_invocations WHERE id = %s",
                        (invocation_id,))
            row = cur.fetchone()
        assert row["state"] == "cancelled"
        assert row["effect"] == "none"

        # 已终态，claim 领取不到
        resp = client.post("/api/local-tools/runtime/claim?wait=1", headers=runtime)
        assert resp.status_code == 200
        assert resp.json()["invocation"] is None

    def test_cancel_flag_via_progress(self, client, users):
        """request_cancel 后 progress 返回 cancel=true"""
        user = users[0]
        device_id, device_token = _pair_device(client, user)
        runtime = {"Authorization": f"Bearer {device_token}"}

        from src.local_tools import repository
        invocation_id = repository.create_invocation(
            user["tenant_id"], user["user_id"], device_id, "boss_goto", {}
        )
        resp = client.post("/api/local-tools/runtime/claim?wait=2", headers=runtime)
        claim_token = resp.json()["claim_token"]

        ok = repository.request_cancel(invocation_id, user["tenant_id"])
        assert ok is True

        resp = client.post(
            f"/api/local-tools/runtime/invocations/{invocation_id}/progress",
            json={"claim_token": claim_token, "message": "进行中"},
            headers=runtime,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["cancel"] is True


class TestCrossConnectionVisibility:
    def test_uncommitted_invisible_committed_visible(self, users):
        """跨连接可见性（模拟多 worker）：未提交对其他连接不可见，提交后可见"""
        from src.db.database import get_db_connection

        tenant_id = users[0]["tenant_id"]
        invocation_id = str(uuid.uuid4())

        with get_db_connection() as conn_a:
            cur_a = conn_a.cursor()
            cur_a.execute(
                """
                INSERT INTO local_tool_invocations
                    (id, tenant_id, user_id, device_id, tool_name, arguments_json)
                VALUES (%s, %s, %s, %s, %s, '{}'::jsonb)
                """,
                (invocation_id, tenant_id, users[0]["user_id"], str(uuid.uuid4()), "boss_goto"),
            )
            # 未提交：另一个连接读不到
            with get_db_connection() as conn_b:
                cur_b = conn_b.cursor()
                cur_b.execute("SELECT 1 FROM local_tool_invocations WHERE id = %s", (invocation_id,))
                assert cur_b.fetchone() is None

            conn_a.commit()

            # 提交后：另一个连接可见
            with get_db_connection() as conn_b:
                cur_b = conn_b.cursor()
                cur_b.execute("SELECT state FROM local_tool_invocations WHERE id = %s", (invocation_id,))
                assert cur_b.fetchone()["state"] == "queued"

        # 清理
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM local_tool_invocations WHERE id = %s", (invocation_id,))
            conn.commit()


class TestExpireStaleClaims:
    def test_lease_expired_becomes_unknown(self, client, users):
        """租约过期的 claimed invocation 被 expire_stale_claims 置为 unknown"""
        user = users[0]
        device_id, device_token = _pair_device(client, user)
        runtime = {"Authorization": f"Bearer {device_token}"}

        from src.local_tools import repository
        from src.db.database import get_db_connection
        invocation_id = repository.create_invocation(
            user["tenant_id"], user["user_id"], device_id, "boss_goto", {}
        )
        resp = client.post("/api/local-tools/runtime/claim?wait=2", headers=runtime)
        assert resp.json().get("invocation_id") == invocation_id

        # 手动把租约改到过去
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE local_tool_invocations SET lease_expires_at = %s WHERE id = %s",
                (datetime.now() - timedelta(seconds=1), invocation_id),
            )
            conn.commit()

        expired = repository.expire_stale_claims()
        assert expired >= 1

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT state, effect FROM local_tool_invocations WHERE id = %s",
                        (invocation_id,))
            row = cur.fetchone()
        assert row["state"] == "unknown"
        assert row["effect"] == "unknown"
