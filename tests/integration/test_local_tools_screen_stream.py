"""Runtime 桌面实时画面服务端集成测试（真实 PostgreSQL）

契约 2.25、设计 runtime-screen-stream-design.md 第 3、5 节、计划 Phase 1。覆盖：
- 创建会话：成功响应、同设备二次创建 409、离线/不支持/他人设备/功能未启用的拒绝、并发创建只成功一个
- 全流程信令：prepared → start → offer/answer/candidate → connected → streaming，状态与判定结果落库，
  after_seq 游标、两个方向互不可见
- 心跳：pending 带通知、streaming 每次带并刷新 runtime_seen_at、prepared 不带、结束后 30 秒内带 ended
- 结束：网页结束接口幂等并清空信令、Runtime 报告 same_machine 后 verdict=same
- 超时收敛：六种情况，用 SQL 把时间戳改早后经读取接口触发
- 归属与隔离：另一租户、另一用户、另一设备的令牌访问 → 404
- 旧设备对照：心跳响应与设备列表除新增字段外逐字段不变
- 入参校验：信令方向、state、reason、payload 形态与大小、信令条数上限

时间戳一律在库里改早、用库里的 NOW() 比较，不靠 sleep，不用 Python 时间减库时间。
"""

import threading
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

PROVIDER_ID = "ai.aidwork.boss-recruiting"
SCREEN_CAPABILITY = "screen_stream_v1"
STUN = ["stun:stun.example.test:3478"]
SESSION_COLUMNS = [
    "id", "tenant_id", "user_id", "device_id", "state", "end_reason", "verdict", "path", "signals",
    "created_at", "connected_at", "started_at", "ended_at", "viewer_seen_at", "runtime_seen_at",
]


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
                for table in ("local_tool_screen_sessions", "local_tool_events", "local_tool_invocations",
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
        user = UserDB.create(phone=phone, username=f"实时画面测试用户{i}", tenant_id=tenant_id)
        if not user:
            pytest.skip("无法创建测试用户（DB 不可用）")
        token = generate_token(user["user_id"])
        result.append({"user_id": user["user_id"], "tenant_id": tenant_id, "token": token})
    return result


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _pair_device(client, user, name="测试设备", screen=True):
    """走接口配对，返回 (device_id, device_token)；screen=False 表示不支持实时画面的旧版本 Runtime"""
    resp = client.post("/api/local-tools/pairing-tickets", headers=_auth(user["token"]))
    assert resp.status_code == 200, resp.text
    capabilities = {"provider_id": PROVIDER_ID}
    if screen:
        capabilities["capabilities"] = ["local_v2", SCREEN_CAPABILITY]
    resp = client.post(
        "/api/local-tools/runtime/pair",
        json={
            "code": resp.json()["code"],
            "name": name,
            "platform": "windows",
            "runtime_version": "2.0.0" if screen else "1.0.0",
            "capabilities": capabilities,
            "machine_fingerprint": "fp-" + uuid.uuid4().hex[:8],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return body["device_id"], body["device_token"]


def _online_device(client, user, name="测试设备", screen=True):
    """配对并心跳一次，使设备在线"""
    device_id, token = _pair_device(client, user, name, screen)
    resp = client.post("/api/local-tools/runtime/heartbeat", json={}, headers=_auth(token))
    assert resp.status_code == 200, resp.text
    return device_id, token


def _heartbeat(client, token):
    resp = client.post("/api/local-tools/runtime/heartbeat", json={}, headers=_auth(token))
    assert resp.status_code == 200, resp.text
    return resp.json()


def _create(client, user, device_id):
    return client.post(f"/api/local-tools/devices/{device_id}/screen-sessions", headers=_auth(user["token"]))


def _new_session(client, user, device_id):
    resp = _create(client, user, device_id)
    assert resp.status_code == 200, resp.text
    return resp.json()["session"]["id"]


def _path(device_id, session_id, suffix=""):
    return f"/api/local-tools/devices/{device_id}/screen-sessions/{session_id}{suffix}"


def _read(client, user, device_id, session_id, after_seq=None):
    params = {} if after_seq is None else {"after_seq": after_seq}
    return client.get(_path(device_id, session_id), params=params, headers=_auth(user["token"]))


def _web_signal(client, user, device_id, session_id, signal_type, payload=None):
    return client.post(_path(device_id, session_id, "/signals"),
                       json={"type": signal_type, "payload": {} if payload is None else payload},
                       headers=_auth(user["token"]))


def _web_end(client, user, device_id, session_id, body=None):
    return client.post(_path(device_id, session_id, "/end"), json=body, headers=_auth(user["token"]))


def _runtime_read(client, token, session_id, after_seq=0):
    return client.post("/api/local-tools/runtime/screen/session",
                       json={"session_id": session_id, "after_seq": after_seq}, headers=_auth(token))


def _runtime_signal(client, token, session_id, signal_type, payload=None):
    return client.post("/api/local-tools/runtime/screen/signal",
                       json={"session_id": session_id, "type": signal_type,
                             "payload": {} if payload is None else payload},
                       headers=_auth(token))


def _ok_web(client, user, device_id, session_id, signal_type, payload=None):
    resp = _web_signal(client, user, device_id, session_id, signal_type, payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _ok_runtime(client, token, session_id, signal_type, payload=None):
    resp = _runtime_signal(client, token, session_id, signal_type, payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _reach(client, user, device_id, token, session_id, state):
    """按正常顺序把会话推进到指定状态"""
    if state == "pending":
        return
    _ok_runtime(client, token, session_id, "prepared")
    if state == "prepared":
        return
    _ok_web(client, user, device_id, session_id, "start")
    if state == "connecting":
        return
    _ok_runtime(client, token, session_id, "status", {"state": "connected"})
    if state == "verifying":
        return
    _ok_runtime(client, token, session_id, "status", {"state": "streaming", "path": "lan"})


def _sql(statement, params=()):
    """直接读写测试库（本轮临时库）；SELECT 返回字典列表"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(statement, params)
        rows = [dict(r) for r in cur.fetchall()] if cur.description else []
        conn.commit()
        return rows


def _db_session(session_id):
    rows = _sql("SELECT * FROM local_tool_screen_sessions WHERE id = %s", (session_id,))
    assert len(rows) == 1
    return rows[0]


def _age(session_id, column, seconds):
    """把会话行里的一个时间戳改早若干秒（列名只允许固定取值）"""
    assert column in {"created_at", "viewer_seen_at", "runtime_seen_at", "ended_at"}
    _sql(f"UPDATE local_tool_screen_sessions SET {column} = NOW() - INTERVAL '{int(seconds)} seconds' WHERE id = %s",
         (session_id,))


def _age_device(device_id, seconds):
    _sql("UPDATE local_tool_devices SET last_seen_at = NOW() - INTERVAL '%s seconds' WHERE id = %s",
         (int(seconds), device_id))


def _is_fresh(session_id, column):
    """该时间戳是否在最近 5 秒内（全部用库时钟比较）"""
    assert column in {"viewer_seen_at", "runtime_seen_at"}
    return _sql(f"SELECT {column} > NOW() - INTERVAL '5 seconds' AS fresh "
                f"FROM local_tool_screen_sessions WHERE id = %s", (session_id,))[0]["fresh"]


def _stun_urls():
    from src.config.settings import settings
    return list(settings.screen_stream.stun_urls)


def _another_user(tenant_id, username):
    """同一租户下再建一个用户（同租户的「别人」）"""
    from src.api.auth import generate_token
    from src.db.models import UserDB

    phone = f"199{uuid.uuid4().int % 10**8:08d}"
    user = UserDB.create(phone=phone, username=username, tenant_id=tenant_id)
    assert user, "无法创建测试用户"
    return {"user_id": user["user_id"], "tenant_id": tenant_id, "token": generate_token(user["user_id"])}


def _run_together(workers):
    """多线程在同一时刻一起发起请求，返回各线程的结果"""
    barrier = threading.Barrier(len(workers))
    results = []

    def run(worker):
        barrier.wait(timeout=30)
        results.append(worker())

    threads = [threading.Thread(target=run, args=(worker,)) for worker in workers]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not any(t.is_alive() for t in threads)
    return results


def _detail(resp):
    return resp.json()["detail"]


# ---------------------------------------------------------------------------
# 创建会话
# ---------------------------------------------------------------------------


class TestCreateSession:
    def test_create_returns_pending_session_with_stun_urls(self, client, users):
        from src.config.settings import settings

        user = users[0]
        device_id, _ = _online_device(client, user, "创建会话机")
        with pytest.MonkeyPatch.context() as patcher:
            patcher.setattr(settings.screen_stream, "stun_urls", list(STUN))
            resp = _create(client, user, device_id)
        assert resp.status_code == 200, resp.text
        session_id = resp.json()["session"]["id"]
        uuid.UUID(session_id)
        assert resp.json() == {"success": True, "session": {"id": session_id, "state": "pending", "stun_urls": STUN}}
        row = _db_session(session_id)
        assert (row["tenant_id"], row["user_id"]) == (user["tenant_id"], user["user_id"])
        assert row["device_id"] == uuid.UUID(device_id)
        assert (row["state"], row["end_reason"], row["verdict"], row["path"]) == ("pending", None, None, None)
        assert (row["signals"], row["connected_at"], row["started_at"], row["ended_at"], row["runtime_seen_at"]) == (
            [], None, None, None, None)
        assert _is_fresh(session_id, "viewer_seen_at") is True

    def test_stun_urls_default_to_config(self, client, users):
        user = users[0]
        device_id, token = _online_device(client, user, "配置透传机")
        session_id = _new_session(client, user, device_id)
        assert _create(client, user, device_id).status_code == 409  # 同设备已有会话
        assert _runtime_read(client, token, session_id).json()["session"]["stun_urls"] == _stun_urls()

    def test_second_session_on_same_device_is_409(self, client, users):
        user = users[0]
        device_id, _ = _online_device(client, user, "重复创建机")
        first = _new_session(client, user, device_id)
        resp = _create(client, user, device_id)
        assert resp.status_code == 409
        assert _detail(resp) == {"error": "已有窗口正在查看这台电脑", "code": "SCREEN_SESSION_ACTIVE", "debug": ""}
        assert _db_session(first)["state"] == "pending"
        assert _sql("SELECT COUNT(*) AS n FROM local_tool_screen_sessions WHERE device_id = %s", (device_id,))[0]["n"] == 1

    def test_active_session_in_any_state_blocks_creation(self, client, users):
        user = users[0]
        device_id, token = _online_device(client, user, "占用中设备")
        session_id = _new_session(client, user, device_id)
        for state in ("pending", "prepared", "connecting", "verifying", "streaming"):
            _reach(client, user, device_id, token, session_id, state)
            assert _db_session(session_id)["state"] == state
            resp = _create(client, user, device_id)
            assert resp.status_code == 409 and _detail(resp)["code"] == "SCREEN_SESSION_ACTIVE"

    def test_offline_device_is_409(self, client, users):
        user = users[0]
        device_id, _ = _pair_device(client, user, "离线机")
        _age_device(device_id, 31)
        resp = _create(client, user, device_id)
        assert resp.status_code == 409
        assert _detail(resp) == {"error": "Runtime 电脑不在线", "code": "DEVICE_OFFLINE", "debug": ""}
        # 从未上报过在线时间也按离线
        _sql("UPDATE local_tool_devices SET last_seen_at = NULL WHERE id = %s", (device_id,))
        resp = _create(client, user, device_id)
        assert resp.status_code == 409 and _detail(resp)["code"] == "DEVICE_OFFLINE"
        assert _sql("SELECT COUNT(*) AS n FROM local_tool_screen_sessions WHERE device_id = %s", (device_id,))[0]["n"] == 0

    def test_device_at_online_threshold_is_still_online(self, client, users):
        user = users[0]
        device_id, _ = _online_device(client, user, "临界在线机")
        _age_device(device_id, 29)
        assert _create(client, user, device_id).status_code == 200

    def test_runtime_without_capability_is_409(self, client, users):
        user = users[0]
        device_id, _ = _online_device(client, user, "旧版Runtime", screen=False)
        resp = _create(client, user, device_id)
        assert resp.status_code == 409
        assert _detail(resp) == {"error": "当前 Runtime 版本不支持实时画面，请升级 Runtime",
                                 "code": "SCREEN_STREAM_UNSUPPORTED", "debug": ""}

    def test_unknown_or_foreign_device_is_404(self, client, users):
        user_a, user_b = users
        device_id, _ = _online_device(client, user_a, "归属校验机")
        other_user = _another_user(user_a["tenant_id"], "同租户另一个用户")
        not_found = {"error": "设备不存在", "code": "DEVICE_NOT_FOUND", "debug": ""}
        for caller, target in ((user_a, str(uuid.uuid4())), (user_b, device_id), (other_user, device_id)):
            resp = _create(client, caller, target)
            assert resp.status_code == 404 and _detail(resp) == not_found
        for bad in ("not-a-uuid", "0"):
            resp = client.post(f"/api/local-tools/devices/{bad}/screen-sessions", headers=_auth(user_a["token"]))
            assert resp.status_code == 404 and _detail(resp) == not_found

    def test_revoked_device_is_404(self, client, users):
        user = users[0]
        device_id, _ = _online_device(client, user, "被撤销机")
        resp = client.delete(f"/api/local-tools/devices/{device_id}", headers=_auth(user["token"]))
        assert resp.status_code == 200, resp.text
        resp = _create(client, user, device_id)
        assert resp.status_code == 404 and _detail(resp)["code"] == "DEVICE_NOT_FOUND"

    def test_concurrent_create_allows_exactly_one_session(self, client, users):
        """多线程同时为同一设备创建会话：只有一个成功，其余 409"""
        user = users[0]
        device_id, _ = _online_device(client, user, "并发创建机")
        from src.main import app

        workers_count = 8
        workers = [lambda: _create(TestClient(app), user, device_id) for _ in range(workers_count)]
        results = _run_together(workers)
        created = [resp.json()["session"]["id"] for resp in results if resp.status_code == 200]
        rejected = [resp for resp in results if resp.status_code != 200]
        assert len(created) == 1
        assert len(rejected) == workers_count - 1
        assert all(_detail(resp)["code"] == "SCREEN_SESSION_ACTIVE" for resp in rejected)
        rows = _sql("SELECT id, state FROM local_tool_screen_sessions WHERE device_id = %s", (device_id,))
        assert [(str(row["id"]), row["state"]) for row in rows] == [(created[0], "pending")]

    def test_creation_does_not_disturb_the_device_heartbeat(self, client, users):
        """创建会话只短暂锁设备行：之后设备心跳照常成功、设备保持在线"""
        user = users[0]
        device_id, token = _online_device(client, user, "心跳共存机")
        session_id = _new_session(client, user, device_id)
        for _ in range(3):
            body = _heartbeat(client, token)
            assert body["success"] is True
            assert body["screen_session"] == {"id": session_id, "state": "pending"}


# ---------------------------------------------------------------------------
# 功能未启用
# ---------------------------------------------------------------------------


class TestFeatureDisabled:
    def test_all_six_endpoints_return_503(self, client, users):
        from src.config.settings import settings

        user = users[0]
        device_id, token = _online_device(client, user, "功能关闭机")
        session_id = _new_session(client, user, device_id)
        with pytest.MonkeyPatch.context() as patcher:
            patcher.setattr(settings.screen_stream, "enabled", False)
            disabled = {"error": "实时画面功能未启用", "code": "SCREEN_STREAM_DISABLED", "debug": ""}
            calls = [
                _create(client, user, device_id),
                _read(client, user, device_id, session_id),
                _web_signal(client, user, device_id, session_id, "start"),
                _web_end(client, user, device_id, session_id),
                _runtime_read(client, token, session_id),
                _runtime_signal(client, token, session_id, "prepared"),
            ]
            assert [resp.status_code for resp in calls] == [503] * 6
            assert all(_detail(resp) == disabled for resp in calls)
            # 关闭功能不改任何会话数据
            assert _db_session(session_id)["state"] == "pending"
            # 心跳照常成功，两个画面字段都没有；设备列表按不支持显示
            body = _heartbeat(client, token)
            assert set(body) == {"success", "selected", "server_time", "parallel_claim"}
            assert body["success"] is True and body["parallel_claim"] is True
            devices = client.get("/api/local-tools/devices", headers=_auth(user["token"])).json()["devices"]
            item = next(d for d in devices if d["device_id"] == device_id)
            assert item["screen_stream"] is False

    def test_enabled_again_recovers(self, client, users):
        from src.config.settings import settings

        user = users[0]
        device_id, token = _online_device(client, user, "功能恢复机")
        with pytest.MonkeyPatch.context() as patcher:
            patcher.setattr(settings.screen_stream, "enabled", False)
            assert _create(client, user, device_id).status_code == 503
        session_id = _new_session(client, user, device_id)
        assert _heartbeat(client, token)["screen_session"] == {"id": session_id, "state": "pending"}


# ---------------------------------------------------------------------------
# 全流程信令
# ---------------------------------------------------------------------------


class TestSignalingFlow:
    def test_full_flow_states_signals_and_audit_columns(self, client, users):
        from src.config.settings import settings

        user = users[0]
        device_id, token = _online_device(client, user, "全流程机")
        with pytest.MonkeyPatch.context() as patcher:
            patcher.setattr(settings.screen_stream, "stun_urls", list(STUN))
            session_id = _new_session(client, user, device_id)

            # Runtime 取会话：pending、带 STUN 列表、还没有网页信令
            resp = _runtime_read(client, token, session_id)
            assert resp.status_code == 200, resp.text
            assert resp.json() == {"success": True, "signals": [],
                                   "session": {"id": session_id, "state": "pending", "end_reason": None,
                                               "stun_urls": STUN}}
            assert _db_session(session_id)["state"] == "pending"
            assert _is_fresh(session_id, "runtime_seen_at") is True

            # Runtime 已应答
            assert _ok_runtime(client, token, session_id, "prepared") == {"success": True, "seq": 1}
            assert _db_session(session_id)["state"] == "prepared"

            # 网页读到 prepared（只含 Runtime 方向、seq 大于游标的信令）
            resp = _read(client, user, device_id, session_id)
            assert resp.status_code == 200, resp.text
            assert resp.json() == {"success": True,
                                   "session": {"id": session_id, "state": "prepared", "end_reason": None},
                                   "signals": [{"seq": 1, "type": "prepared", "payload": {}}]}
            assert _read(client, user, device_id, session_id, after_seq=1).json()["signals"] == []
            assert _read(client, user, device_id, session_id, after_seq=0).json()["signals"] == [
                {"seq": 1, "type": "prepared", "payload": {}}]

            # 网页已准备好
            assert _ok_web(client, user, device_id, session_id, "start") == {"success": True, "seq": 2}
            assert _db_session(session_id)["state"] == "connecting"
            assert _runtime_read(client, token, session_id, after_seq=1).json()["signals"] == [
                {"seq": 2, "type": "start", "payload": {}}]

            # 建连信令双向转交：两个方向共用一个序号计数，互相看不到对方的信令
            offer = {"sdp": "v=0\r\no=- 1 1 IN IP4 0.0.0.0\r\n"}
            answer = {"sdp": "v=0\r\no=- 2 1 IN IP4 0.0.0.0\r\n"}
            rt_candidate = {"candidate": "candidate:1 1 udp 1 192.168.1.20 50000 typ host",
                            "sdp_mid": "0", "sdp_mline_index": 0}
            web_candidate = {"candidate": "candidate:1 1 udp 1 192.168.1.30 50001 typ host",
                             "sdp_mid": "0", "sdp_mline_index": 0}
            assert _ok_runtime(client, token, session_id, "offer", offer)["seq"] == 3
            assert _ok_web(client, user, device_id, session_id, "answer", answer)["seq"] == 4
            assert _ok_web(client, user, device_id, session_id, "candidate", web_candidate)["seq"] == 5
            assert _ok_runtime(client, token, session_id, "candidate", rt_candidate)["seq"] == 6
            assert _read(client, user, device_id, session_id, after_seq=1).json()["signals"] == [
                {"seq": 3, "type": "offer", "payload": offer},
                {"seq": 6, "type": "candidate", "payload": rt_candidate},
            ]
            assert _runtime_read(client, token, session_id, after_seq=2).json()["signals"] == [
                {"seq": 4, "type": "answer", "payload": answer},
                {"seq": 5, "type": "candidate", "payload": web_candidate},
            ]
            assert _db_session(session_id)["state"] == "connecting"

            # 连接已建立（仍无画面）
            _ok_runtime(client, token, session_id, "status", {"state": "connected"})
            row = _db_session(session_id)
            assert (row["state"], row["started_at"], row["verdict"], row["path"]) == ("verifying", None, None, None)
            assert row["connected_at"] is not None

            # 开始出画面，带上实际线路
            _ok_runtime(client, token, session_id, "status", {"state": "streaming", "path": "lan"})
            row = _db_session(session_id)
            assert (row["state"], row["verdict"], row["path"]) == ("streaming", "other", "lan")
            assert row["started_at"] is not None
            assert row["connected_at"] <= row["started_at"]

            # 锁屏、降画质等状态只转交，不改会话状态
            for state in ("locked", "degraded", "restored", "unlocked"):
                _ok_runtime(client, token, session_id, "status", {"state": state})
            assert _db_session(session_id)["state"] == "streaming"
            resp = _read(client, user, device_id, session_id, after_seq=6)
            assert resp.json()["session"] == {"id": session_id, "state": "streaming", "end_reason": None}
            assert [(s["seq"], s["type"], s["payload"]) for s in resp.json()["signals"]] == [
                (7, "status", {"state": "connected"}),
                (8, "status", {"state": "streaming", "path": "lan"}),
                (9, "status", {"state": "locked"}),
                (10, "status", {"state": "degraded"}),
                (11, "status", {"state": "restored"}),
                (12, "status", {"state": "unlocked"}),
            ]

            # 库里的信令：序号连续，方向与发送方一致
            row = _db_session(session_id)
            assert [s["seq"] for s in row["signals"]] == list(range(1, 13))
            assert [s["dir"] for s in row["signals"]] == [
                "rt", "web", "rt", "web", "web", "rt", "rt", "rt", "rt", "rt", "rt", "rt"]

    def test_p2p_path_and_missing_path(self, client, users):
        user = users[0]
        device_id, token = _online_device(client, user, "线路记录机")
        first = _new_session(client, user, device_id)
        _reach(client, user, device_id, token, first, "streaming")
        assert _db_session(first)["path"] == "lan"

        _ok_runtime(client, token, first, "end", {"reason": "runtime_stopped"})
        second = _new_session(client, user, device_id)
        _reach(client, user, device_id, token, second, "connecting")
        _ok_runtime(client, token, second, "status", {"state": "streaming", "path": "p2p"})
        assert _db_session(second)["path"] == "p2p"

        _ok_runtime(client, token, second, "end", {"reason": "runtime_stopped"})
        third = _new_session(client, user, device_id)
        _reach(client, user, device_id, token, third, "connecting")
        _ok_runtime(client, token, third, "status", {"state": "streaming"})
        row = _db_session(third)
        assert (row["state"], row["verdict"], row["path"]) == ("streaming", "other", None)

    def test_out_of_order_progress_is_accepted(self, client, users):
        """服务端不校验信令顺序：从 connecting 直接到 streaming 也接受并写相应字段"""
        user = users[0]
        device_id, token = _online_device(client, user, "跳步机")
        session_id = _new_session(client, user, device_id)
        _reach(client, user, device_id, token, session_id, "connecting")
        _ok_runtime(client, token, session_id, "status", {"state": "streaming", "path": "p2p"})
        row = _db_session(session_id)
        assert (row["state"], row["verdict"], row["path"]) == ("streaming", "other", "p2p")
        assert row["started_at"] is not None and row["connected_at"] is None

    def test_reading_refreshes_only_the_caller_seen_time(self, client, users):
        user = users[0]
        device_id, token = _online_device(client, user, "访问时间机")
        session_id = _new_session(client, user, device_id)
        _reach(client, user, device_id, token, session_id, "prepared")
        _age(session_id, "viewer_seen_at", 20)
        _age(session_id, "runtime_seen_at", 20)

        assert _read(client, user, device_id, session_id).status_code == 200
        assert _is_fresh(session_id, "viewer_seen_at") is True
        assert _is_fresh(session_id, "runtime_seen_at") is False

        assert _runtime_read(client, token, session_id).status_code == 200
        assert _is_fresh(session_id, "runtime_seen_at") is True


# ---------------------------------------------------------------------------
# 心跳通知与续期
# ---------------------------------------------------------------------------


class TestHeartbeatNotice:
    def test_pending_session_is_announced_until_runtime_answers(self, client, users):
        user = users[0]
        device_id, token = _online_device(client, user, "心跳通知机")
        session_id = _new_session(client, user, device_id)
        for _ in range(2):
            body = _heartbeat(client, token)
            assert body["screen_stream"] == {"version": 1}
            assert body["screen_session"] == {"id": session_id, "state": "pending"}
            assert set(body) == {"success", "selected", "server_time", "parallel_claim",
                                 "screen_stream", "screen_session"}
        # Runtime 已应答后不再经心跳通知：这时 Runtime 自己在轮询
        _reach(client, user, device_id, token, session_id, "prepared")
        body = _heartbeat(client, token)
        assert "screen_session" not in body
        assert body["screen_stream"] == {"version": 1}

    def test_handshake_states_are_not_announced(self, client, users):
        user = users[0]
        device_id, token = _online_device(client, user, "建连阶段机")
        session_id = _new_session(client, user, device_id)
        _reach(client, user, device_id, token, session_id, "connecting")
        assert "screen_session" not in _heartbeat(client, token)
        _reach(client, user, device_id, token, session_id, "verifying")
        assert "screen_session" not in _heartbeat(client, token)

    def test_streaming_session_is_announced_on_every_heartbeat_and_renews(self, client, users):
        user = users[0]
        device_id, token = _online_device(client, user, "续期机")
        session_id = _new_session(client, user, device_id)
        _reach(client, user, device_id, token, session_id, "streaming")
        for _ in range(3):
            _age(session_id, "runtime_seen_at", 20)
            assert _is_fresh(session_id, "runtime_seen_at") is False
            body = _heartbeat(client, token)
            assert body["screen_session"] == {"id": session_id, "state": "streaming"}
            assert _is_fresh(session_id, "runtime_seen_at") is True
        assert _db_session(session_id)["state"] == "streaming"

    def test_ended_session_is_announced_for_30_seconds_after_streaming(self, client, users):
        user = users[0]
        device_id, token = _online_device(client, user, "结束通知机")
        session_id = _new_session(client, user, device_id)
        _reach(client, user, device_id, token, session_id, "streaming")
        assert _web_end(client, user, device_id, session_id).status_code == 200
        assert _heartbeat(client, token)["screen_session"] == {"id": session_id, "state": "ended"}
        _age(session_id, "ended_at", 29)
        assert _heartbeat(client, token)["screen_session"] == {"id": session_id, "state": "ended"}
        _age(session_id, "ended_at", 31)
        assert "screen_session" not in _heartbeat(client, token)

    def test_ended_session_that_never_streamed_is_not_announced(self, client, users):
        """开始传画面之前结束的会话不经心跳通知：Runtime 在轮询里自己会看到"""
        user = users[0]
        device_id, token = _online_device(client, user, "未出画面结束机")
        session_id = _new_session(client, user, device_id)
        _reach(client, user, device_id, token, session_id, "connecting")
        _ok_runtime(client, token, session_id, "end", {"reason": "connect_failed"})
        assert _db_session(session_id)["state"] == "ended"
        assert "screen_session" not in _heartbeat(client, token)

    def test_notice_gives_way_to_a_new_session(self, client, users):
        """旧的已结束会话还在 30 秒通知期内时，新会话的 pending 通知优先"""
        user = users[0]
        device_id, token = _online_device(client, user, "新旧会话机")
        old = _new_session(client, user, device_id)
        _reach(client, user, device_id, token, old, "streaming")
        _ok_runtime(client, token, old, "end", {"reason": "runtime_stopped"})
        assert _heartbeat(client, token)["screen_session"] == {"id": old, "state": "ended"}
        new = _new_session(client, user, device_id)
        assert _heartbeat(client, token)["screen_session"] == {"id": new, "state": "pending"}

    def test_screen_query_failure_or_timeout_leaves_heartbeat_healthy(self, client, users):
        """故障注入：画面会话查询报错或超时，心跳仍成功且不带 screen_session"""
        import asyncio

        from src.local_tools import api as local_tools_api
        from src.local_tools import screen_stream

        user = users[0]
        device_id, token = _online_device(client, user, "故障注入机")
        session_id = _new_session(client, user, device_id)

        def _boom(*args, **kwargs):
            raise RuntimeError("injected screen query failure")

        def _slow(*args, **kwargs):
            time.sleep(2)
            return None

        original = screen_stream.heartbeat_notice
        try:
            screen_stream.heartbeat_notice = _boom
            resp = client.post("/api/local-tools/runtime/heartbeat", json={}, headers=_auth(token))
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert set(body) == {"success", "selected", "server_time", "parallel_claim", "screen_stream"}
            assert body["success"] is True and body["screen_stream"] == {"version": 1}

            # 计量处理函数在 0.5 秒限时后返回：直接调用路由协程，在事件循环内部计时。
            # 不用 TestClient 的请求耗时：它在请求结束后关闭事件循环时，会等默认线程池里
            # 仍在睡眠的那条线程（实测约 2 秒），那是测试客户端的收尾行为，不是服务端的返回时间。
            screen_stream.heartbeat_notice = _slow
            device = {"id": uuid.UUID(device_id), "tenant_id": user["tenant_id"], "user_id": user["user_id"]}

            async def call_heartbeat():
                started = time.perf_counter()
                result = await local_tools_api.runtime_heartbeat(local_tools_api.HeartbeatRequest(), device=device)
                return result, time.perf_counter() - started

            body, elapsed = asyncio.run(call_heartbeat())
            assert elapsed < 1.0
            assert body["success"] is True and body["screen_stream"] == {"version": 1}
            assert "screen_session" not in body
        finally:
            screen_stream.heartbeat_notice = original
        # 查询恢复后通知照常带回；会话没有因为查询失败被改动
        assert _heartbeat(client, token)["screen_session"] == {"id": session_id, "state": "pending"}
        assert _db_session(session_id)["state"] == "pending"

    def test_device_is_updated_before_the_screen_query(self, client, users):
        """先按现状更新设备，再查画面会话：查询看到的是刚更新的在线时间，不会把 streaming 会话误判为掉线"""
        user = users[0]
        device_id, token = _online_device(client, user, "先更新后查询机")
        session_id = _new_session(client, user, device_id)
        _reach(client, user, device_id, token, session_id, "streaming")
        _age_device(device_id, 300)
        assert _heartbeat(client, token)["screen_session"] == {"id": session_id, "state": "streaming"}
        assert _db_session(session_id)["state"] == "streaming"


# ---------------------------------------------------------------------------
# 结束
# ---------------------------------------------------------------------------


class TestEnding:
    def test_viewer_end_is_idempotent_and_clears_signals(self, client, users):
        user = users[0]
        device_id, token = _online_device(client, user, "网页结束机")
        session_id = _new_session(client, user, device_id)
        _reach(client, user, device_id, token, session_id, "streaming")
        assert len(_db_session(session_id)["signals"]) == 4

        resp = _web_end(client, user, device_id, session_id, body={"reason": "用户关闭了窗口"})
        assert resp.status_code == 200, resp.text
        expected = {"success": True, "session": {"id": session_id, "state": "ended", "end_reason": "viewer_closed"}}
        assert resp.json() == expected
        row = _db_session(session_id)
        assert (row["state"], row["end_reason"], row["signals"], row["verdict"]) == ("ended", "viewer_closed", [], "other")
        assert row["ended_at"] is not None
        ended_at = row["ended_at"]

        # 重复结束（不带请求体）返回同样结果，不改任何数据
        again = _web_end(client, user, device_id, session_id)
        assert again.status_code == 200 and again.json() == expected
        assert _db_session(session_id)["ended_at"] == ended_at

        # 结束后：读取返回 ended，非 end 信令 409，end 信令两个方向都仍返回成功
        resp = _read(client, user, device_id, session_id)
        assert resp.json() == {"success": True, "signals": [],
                               "session": {"id": session_id, "state": "ended", "end_reason": "viewer_closed"}}
        rejected = _web_signal(client, user, device_id, session_id, "candidate", {"candidate": "x"})
        assert rejected.status_code == 409
        assert _detail(rejected) == {"error": "会话已结束", "code": "SCREEN_SESSION_ENDED", "debug": ""}
        assert _web_signal(client, user, device_id, session_id, "end", {"reason": "again"}).status_code == 200
        assert _runtime_signal(client, token, session_id, "end", {"reason": "runtime_stopped"}).status_code == 200
        assert _runtime_signal(client, token, session_id, "prepared").status_code == 409
        assert _db_session(session_id)["end_reason"] == "viewer_closed"

    def test_viewer_end_before_runtime_answers(self, client, users):
        user = users[0]
        device_id, token = _online_device(client, user, "提前结束机")
        session_id = _new_session(client, user, device_id)
        assert _web_end(client, user, device_id, session_id).status_code == 200
        assert _db_session(session_id)["end_reason"] == "viewer_closed"
        # Runtime 之后来取会话：看到已结束，不能再应答
        resp = _runtime_read(client, token, session_id)
        assert resp.status_code == 200, resp.text
        assert resp.json()["session"] == {"id": session_id, "state": "ended", "end_reason": "viewer_closed",
                                          "stun_urls": _stun_urls()}
        assert _runtime_signal(client, token, session_id, "prepared").status_code == 409

    def test_web_end_signal_ends_session_like_the_end_endpoint(self, client, users):
        user = users[0]
        device_id, token = _online_device(client, user, "信令结束机")
        session_id = _new_session(client, user, device_id)
        _reach(client, user, device_id, token, session_id, "prepared")
        resp = _web_signal(client, user, device_id, session_id, "end", {"reason": "closed"})
        assert resp.status_code == 200, resp.text
        # end 不占信令存储，没有新信令，所以 seq 为空
        assert resp.json() == {"success": True, "seq": None}
        row = _db_session(session_id)
        assert (row["state"], row["end_reason"], row["signals"]) == ("ended", "viewer_closed", [])
        assert _create(client, user, device_id).status_code == 200

    @pytest.mark.parametrize(("reason", "verdict"), [
        ("same_machine", "same"),
        ("machine_check_unknown", "unknown"),
        ("runtime_stopped", None),
        ("runtime_rejected", None),
        ("connect_failed", None),
        ("renewal_timeout", None),
        ("resource_busy", None),
    ])
    def test_runtime_end_records_reason_and_verdict(self, client, users, reason, verdict):
        user = users[0]
        device_id, token = _online_device(client, user, f"Runtime结束-{reason}")
        session_id = _new_session(client, user, device_id)
        _reach(client, user, device_id, token, session_id, "connecting")
        resp = _runtime_signal(client, token, session_id, "end", {"reason": reason})
        assert resp.status_code == 200, resp.text
        row = _db_session(session_id)
        assert (row["state"], row["end_reason"], row["verdict"], row["signals"]) == ("ended", reason, verdict, [])
        assert row["ended_at"] is not None
        assert _read(client, user, device_id, session_id).json()["session"] == {
            "id": session_id, "state": "ended", "end_reason": reason}

    def test_verdict_follows_the_runtime_report(self, client, users):
        """报告 streaming 记 other；以 same_machine 结束记 same；以 machine_check_unknown 结束记 unknown"""
        user = users[0]
        device_id, token = _online_device(client, user, "判定结果机")
        session_id = _new_session(client, user, device_id)
        _reach(client, user, device_id, token, session_id, "streaming")
        assert _db_session(session_id)["verdict"] == "other"

        _ok_runtime(client, token, session_id, "end", {"reason": "same_machine"})
        assert _db_session(session_id)["verdict"] == "same"

        second = _new_session(client, user, device_id)
        _reach(client, user, device_id, token, second, "prepared")
        _ok_runtime(client, token, second, "end", {"reason": "machine_check_unknown"})
        assert _db_session(second)["verdict"] == "unknown"

    def test_runtime_end_of_streaming_session_is_announced_as_ended(self, client, users):
        user = users[0]
        device_id, token = _online_device(client, user, "Runtime停止通知机")
        session_id = _new_session(client, user, device_id)
        _reach(client, user, device_id, token, session_id, "streaming")
        _ok_runtime(client, token, session_id, "end", {"reason": "resource_busy"})
        assert _heartbeat(client, token)["screen_session"] == {"id": session_id, "state": "ended"}
        resp = _read(client, user, device_id, session_id)
        assert resp.json() == {"success": True, "signals": [],
                               "session": {"id": session_id, "state": "ended", "end_reason": "resource_busy"}}

    def test_concurrent_end_from_both_sides(self, client, users):
        """两端同时结束：会话只结束一次，end_reason 是先到的那一方，另一方幂等成功"""
        user = users[0]
        device_id, token = _online_device(client, user, "两端同时结束机")
        session_id = _new_session(client, user, device_id)
        _reach(client, user, device_id, token, session_id, "streaming")
        from src.main import app

        results = _run_together([
            lambda: _web_end(TestClient(app), user, device_id, session_id).status_code,
            lambda: _runtime_signal(TestClient(app), token, session_id, "end", {"reason": "runtime_stopped"}).status_code,
        ])
        assert sorted(results) == [200, 200]
        row = _db_session(session_id)
        assert row["state"] == "ended"
        assert row["end_reason"] in {"viewer_closed", "runtime_stopped"}
        assert row["signals"] == []


# ---------------------------------------------------------------------------
# 超时收敛
# ---------------------------------------------------------------------------


class TestTimeoutConvergence:
    @pytest.mark.parametrize(("state", "anchor", "seconds", "reason"), [
        ("pending", "created_at", 31, "device_offline"),
        ("prepared", "viewer_seen_at", 61, "viewer_closed"),
        ("connecting", "runtime_seen_at", 31, "connect_failed"),
        ("verifying", "runtime_seen_at", 16, "machine_check_unknown"),
        ("streaming", "device", 31, "runtime_offline"),
        ("streaming", "viewer_seen_at", 31, "viewer_closed"),
    ])
    def test_session_past_limit_is_ended_when_read(self, client, users, state, anchor, seconds, reason):
        user = users[0]
        device_id, token = _online_device(client, user, f"超时-{state}-{anchor}")
        session_id = _new_session(client, user, device_id)
        _reach(client, user, device_id, token, session_id, state)

        def shift(value):
            if anchor == "device":
                _age_device(device_id, value)
            else:
                _age(session_id, anchor, value)

        # 时限之内：读一次仍然活着（读本身会刷新访问方的时间，所以每次判定前都重新改早）
        shift(seconds - 2)
        resp = _read(client, user, device_id, session_id)
        assert resp.status_code == 200, resp.text
        assert resp.json()["session"] == {"id": session_id, "state": state, "end_reason": None}
        assert _db_session(session_id)["state"] == state

        # 超过时限：读取时顺带收敛，信令清空
        shift(seconds)
        resp = _read(client, user, device_id, session_id)
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"success": True, "signals": [],
                               "session": {"id": session_id, "state": "ended", "end_reason": reason}}
        row = _db_session(session_id)
        assert (row["state"], row["end_reason"], row["signals"]) == ("ended", reason, [])
        assert row["ended_at"] is not None

    def test_viewer_can_recreate_after_pending_session_timed_out(self, client, users):
        user = users[0]
        device_id, _ = _online_device(client, user, "pending超时重建机")
        stale = _new_session(client, user, device_id)
        _age(stale, "created_at", 31)
        resp = _create(client, user, device_id)
        assert resp.status_code == 200, resp.text
        fresh = resp.json()["session"]["id"]
        assert fresh != stale
        assert (_db_session(stale)["state"], _db_session(stale)["end_reason"]) == ("ended", "device_offline")
        assert _db_session(fresh)["state"] == "pending"

    def test_stale_streaming_session_does_not_block_recreation(self, client, users):
        """传画面中网页已离开的旧会话，在下一次创建时被收敛，不挡新会话"""
        user = users[0]
        device_id, token = _online_device(client, user, "streaming超时重建机")
        stale = _new_session(client, user, device_id)
        _reach(client, user, device_id, token, stale, "streaming")
        _age(stale, "viewer_seen_at", 31)
        resp = _create(client, user, device_id)
        assert resp.status_code == 200, resp.text
        assert (_db_session(stale)["state"], _db_session(stale)["end_reason"]) == ("ended", "viewer_closed")

    def test_runtime_read_converges_a_session_whose_viewer_is_gone(self, client, users):
        user = users[0]
        device_id, token = _online_device(client, user, "Runtime读到收敛机")
        session_id = _new_session(client, user, device_id)
        _reach(client, user, device_id, token, session_id, "prepared")
        _age(session_id, "viewer_seen_at", 61)
        resp = _runtime_read(client, token, session_id)
        assert resp.json()["session"] == {"id": session_id, "state": "ended", "end_reason": "viewer_closed",
                                          "stun_urls": _stun_urls()}
        assert _runtime_signal(client, token, session_id, "offer", {"sdp": "v=0"}).status_code == 409

    def test_rejected_signal_still_persists_the_timeout_convergence(self, client, users):
        """超时的会话先被一条信令碰到：信令被拒，但收敛已经落库，之后没人再读也不会一直挂着未结束"""
        user = users[0]
        device_id, token = _online_device(client, user, "信令触发收敛机")
        session_id = _new_session(client, user, device_id)
        _reach(client, user, device_id, token, session_id, "connecting")
        _age(session_id, "runtime_seen_at", 31)
        resp = _runtime_signal(client, token, session_id, "offer", {"sdp": "v=0"})
        assert resp.status_code == 409 and _detail(resp)["code"] == "SCREEN_SESSION_ENDED"
        row = _db_session(session_id)
        assert (row["state"], row["end_reason"], row["signals"]) == ("ended", "connect_failed", [])
        assert row["ended_at"] is not None

    def test_heartbeat_converges_streaming_session_whose_viewer_is_gone(self, client, users):
        user = users[0]
        device_id, token = _online_device(client, user, "心跳收敛机")
        session_id = _new_session(client, user, device_id)
        _reach(client, user, device_id, token, session_id, "streaming")
        _age(session_id, "viewer_seen_at", 31)
        assert _heartbeat(client, token)["screen_session"] == {"id": session_id, "state": "ended"}
        row = _db_session(session_id)
        assert (row["state"], row["end_reason"], row["signals"]) == ("ended", "viewer_closed", [])

    def test_heartbeat_converges_unanswered_pending_session(self, client, users):
        user = users[0]
        device_id, token = _online_device(client, user, "pending心跳收敛机")
        session_id = _new_session(client, user, device_id)
        _age(session_id, "created_at", 31)
        assert _heartbeat(client, token)["screen_session"] == {"id": session_id, "state": "ended"}
        assert (_db_session(session_id)["state"], _db_session(session_id)["end_reason"]) == ("ended", "device_offline")
        # 开始传画面之前结束的会话只通知这一次，之后不再出现
        assert "screen_session" not in _heartbeat(client, token)

    def test_streaming_device_offline_wins_over_viewer(self, client, users):
        user = users[0]
        device_id, token = _online_device(client, user, "双超时机")
        session_id = _new_session(client, user, device_id)
        _reach(client, user, device_id, token, session_id, "streaming")
        _age(session_id, "viewer_seen_at", 120)
        _age_device(device_id, 60)
        assert _read(client, user, device_id, session_id).json()["session"]["end_reason"] == "runtime_offline"

    def test_viewer_polling_and_device_heartbeat_keep_a_streaming_session_alive(self, client, users):
        user = users[0]
        device_id, token = _online_device(client, user, "保活机")
        session_id = _new_session(client, user, device_id)
        _reach(client, user, device_id, token, session_id, "streaming")
        for _ in range(3):
            _age(session_id, "viewer_seen_at", 20)
            _age(session_id, "runtime_seen_at", 20)
            _age_device(device_id, 20)
            assert _read(client, user, device_id, session_id).json()["session"]["state"] == "streaming"
            assert _heartbeat(client, token)["screen_session"] == {"id": session_id, "state": "streaming"}
        assert _db_session(session_id)["state"] == "streaming"


# ---------------------------------------------------------------------------
# 归属、隔离与校验
# ---------------------------------------------------------------------------


class TestOwnershipAndValidation:
    def test_runtime_cannot_access_another_devices_session(self, client, users):
        user = users[0]
        device_a, _ = _online_device(client, user, "会话归属A")
        device_b, token_b = _online_device(client, user, "会话归属B")
        session_id = _new_session(client, user, device_a)
        not_found = {"error": "会话不存在或无权访问", "code": "SCREEN_SESSION_NOT_FOUND", "debug": ""}
        read = _runtime_read(client, token_b, session_id)
        signal = _runtime_signal(client, token_b, session_id, "prepared")
        end = _runtime_signal(client, token_b, session_id, "end", {"reason": "runtime_stopped"})
        assert (read.status_code, signal.status_code, end.status_code) == (404, 404, 404)
        assert _detail(read) == _detail(signal) == _detail(end) == not_found
        assert _db_session(session_id)["state"] == "pending"

    def test_runtime_of_another_tenant_cannot_access_the_session(self, client, users):
        user_a, user_b = users
        device_a, _ = _online_device(client, user_a, "跨租户A机")
        _, token_b = _online_device(client, user_b, "跨租户B机")
        session_id = _new_session(client, user_a, device_a)
        resp = _runtime_read(client, token_b, session_id)
        assert resp.status_code == 404 and _detail(resp)["code"] == "SCREEN_SESSION_NOT_FOUND"

    def test_viewer_routes_hide_sessions_of_others(self, client, users):
        user_a, user_b = users
        device_a, _ = _online_device(client, user_a, "隔离A机")
        device_b, _ = _online_device(client, user_a, "隔离B机")
        session_id = _new_session(client, user_a, device_a)
        other_user = _another_user(user_a["tenant_id"], "隔离校验用户")
        not_found = {"error": "会话不存在或无权访问", "code": "SCREEN_SESSION_NOT_FOUND", "debug": ""}
        # 另一租户、同租户另一用户、本人但路径里的设备不是会话的设备
        for caller, path_device in ((user_b, device_a), (other_user, device_a), (user_a, device_b)):
            read = _read(client, caller, path_device, session_id)
            signal = _web_signal(client, caller, path_device, session_id, "start")
            end = _web_end(client, caller, path_device, session_id)
            assert (read.status_code, signal.status_code, end.status_code) == (404, 404, 404)
            assert _detail(read) == _detail(signal) == _detail(end) == not_found
        assert _db_session(session_id)["state"] == "pending"

    def test_unknown_session_is_404_and_malformed_session_id_is_rejected(self, client, users):
        user = users[0]
        device_id, token = _online_device(client, user, "会话编号校验机")
        unknown = str(uuid.uuid4())
        not_found = {"error": "会话不存在或无权访问", "code": "SCREEN_SESSION_NOT_FOUND", "debug": ""}
        for bad in (unknown, "not-a-uuid"):
            read = _read(client, user, device_id, bad)
            assert read.status_code == 404 and _detail(read) == not_found
        for bad in (unknown, "not-a-uuid"):
            # 设备侧的会话编号在请求体里：形态非法由模型校验拦下（422），查不到是 404
            expected = 404 if bad == unknown else 422
            assert _runtime_read(client, token, bad).status_code == expected
            assert _runtime_signal(client, token, bad, "prepared").status_code == expected

    def test_signal_types_are_limited_by_direction(self, client, users):
        user = users[0]
        device_id, token = _online_device(client, user, "信令方向机")
        session_id = _new_session(client, user, device_id)
        for body in ({"type": "offer", "payload": {}}, {"type": "status", "payload": {"state": "streaming"}},
                     {"type": "prepared", "payload": {}}, {"type": "unknown", "payload": {}}):
            resp = client.post(_path(device_id, session_id, "/signals"), json=body, headers=_auth(user["token"]))
            assert resp.status_code == 422, body
        for body in ({"session_id": session_id, "type": "start", "payload": {}},
                     {"session_id": session_id, "type": "answer", "payload": {"sdp": "v=0"}}):
            resp = client.post("/api/local-tools/runtime/screen/signal", json=body, headers=_auth(token))
            assert resp.status_code == 422, body
        assert _db_session(session_id)["signals"] == []

    def test_status_and_end_payload_enums(self, client, users):
        user = users[0]
        device_id, token = _online_device(client, user, "枚举校验机")
        session_id = _new_session(client, user, device_id)
        for payload in ({"state": "bogus"}, {}, {"state": "streaming", "path": "wan"}, {"state": "Streaming"}):
            assert _runtime_signal(client, token, session_id, "status", payload).status_code == 422, payload
        for payload in ({"reason": "viewer_closed"}, {"reason": "device_offline"}, {"reason": "runtime_offline"},
                        {"reason": "nope"}, {}):
            assert _runtime_signal(client, token, session_id, "end", payload).status_code == 422, payload
        assert _db_session(session_id)["signals"] == []

    def test_payload_must_be_an_object_within_16kb(self, client, users):
        user = users[0]
        device_id, token = _online_device(client, user, "payload校验机")
        session_id = _new_session(client, user, device_id)
        for payload in ([], "sdp", 1):
            assert _web_signal(client, user, device_id, session_id, "answer", payload).status_code == 422
            assert _runtime_signal(client, token, session_id, "offer", payload).status_code == 422
        missing = client.post(_path(device_id, session_id, "/signals"), json={"type": "answer"},
                              headers=_auth(user["token"]))
        assert missing.status_code == 422
        assert _web_signal(client, user, device_id, session_id, "answer", {"sdp": "x" * 16000}).status_code == 200
        assert _web_signal(client, user, device_id, session_id, "answer", {"sdp": "x" * 17000}).status_code == 422
        # 上限按序列化后的字节数算：6000 个汉字是 18000 字节
        assert _runtime_signal(client, token, session_id, "offer", {"sdp": "画" * 6000}).status_code == 422
        assert _runtime_signal(client, token, session_id, "offer", {"sdp": "画" * 5000}).status_code == 200
        assert [s["seq"] for s in _db_session(session_id)["signals"]] == [1, 2]

    @pytest.mark.parametrize("token_text", ["NaN", "Infinity", "-Infinity"])
    def test_payload_with_non_finite_number_never_reaches_the_database(self, client, users, token_text):
        """Python 的 JSON 解析接受 NaN / Infinity / -Infinity，PostgreSQL 的 json 却不接受。
        放行到写库那一步的话，数据库的报错会带出一段信令文本（进 500 的 debug 和全局异常日志）。

        这里固定三件事：不写库；响应里不出现信令内容；控制台和文件日志不带信令内容。
        最后一项的断言查的是全局兜底记下的那条 ERROR 日志的消息正文和顶层异常的文本；
        控制台和文件日志目前只写消息正文（格式里没有异常堆栈），写的就是这部分。

        已知限制，本阶段接受（主控者决定不在本阶段处理，因为需要改全局兜底或错误日志的写入）：
        错误日志库那一路会带异常链。全局兜底记下的 ValueError 是在处理框架的 RequestValidationError
        时抛出的，src/core/error_log_sink.py 用 traceback.format_exception 写 log_error.traceback
        时会把整条链打印出来，而 RequestValidationError 的文本里有出错的输入。所以被拒请求自己的
        payload 会进错误日志库的堆栈列；对方发来的信令不在这条链里。本用例不检查这一路。

        这时接口返回的不是 422：FastAPI 默认的校验错误响应会把出错的 payload 原样带回，Starlette
        渲染它时同样不允许 NaN，于是落到全局兜底的 500。这里不对状态码下结论。
        """
        from loguru import logger

        marker = "SIGNAL-TEXT-MARKER"
        user = users[0]
        device_id, device_token = _online_device(client, user, f"非有限数-{token_text}")
        session_id = _new_session(client, user, device_id)
        json_headers = {"Content-Type": "application/json"}
        records = []
        handler = logger.add(lambda message: records.append(message.record), level="ERROR")
        try:
            # 这类请求在真实应用里会走到全局兜底；默认的 TestClient 会把服务端异常直接抛给测试
            watcher = TestClient(client.app, raise_server_exceptions=False)
            web = watcher.post(_path(device_id, session_id, "/signals"),
                               content='{"type": "answer", "payload": {"sdp": "%s", "x": %s}}' % (marker, token_text),
                               headers={**_auth(user["token"]), **json_headers})
            runtime = watcher.post("/api/local-tools/runtime/screen/signal",
                                   content='{"session_id": "%s", "type": "offer", "payload": {"sdp": "%s", "x": %s}}'
                                           % (session_id, marker, token_text),
                                   headers={**_auth(device_token), **json_headers})
        finally:
            logger.remove(handler)
        assert web.status_code != 200 and runtime.status_code != 200
        assert marker not in web.text and marker not in runtime.text
        assert _db_session(session_id)["signals"] == []
        logged = "\n".join(record["message"] + (str(record["exception"].value) if record["exception"] else "")
                           for record in records)
        assert marker not in logged

    def test_after_seq_validation(self, client, users):
        user = users[0]
        device_id, token = _online_device(client, user, "游标校验机")
        session_id = _new_session(client, user, device_id)
        for bad in ("-1", "abc", "1.5"):
            assert _read(client, user, device_id, session_id, after_seq=bad).status_code == 422
        for bad in (-1, "abc"):
            resp = client.post("/api/local-tools/runtime/screen/session",
                               json={"session_id": session_id, "after_seq": bad}, headers=_auth(token))
            assert resp.status_code == 422
        assert _read(client, user, device_id, session_id, after_seq=0).status_code == 200
        assert _runtime_read(client, token, session_id).status_code == 200

    def test_signal_limit_rejects_further_signals_but_allows_end(self, client, users):
        user = users[0]
        device_id, token = _online_device(client, user, "信令上限机")
        session_id = _new_session(client, user, device_id)
        _reach(client, user, device_id, token, session_id, "connecting")
        _sql("""UPDATE local_tool_screen_sessions SET signals = (
                   SELECT jsonb_agg(jsonb_build_object('seq', n, 'dir', 'web', 'type', 'candidate',
                                                      'payload', '{}'::jsonb) ORDER BY n)
                   FROM generate_series(1, %s) AS n)
               WHERE id = %s""", (128, session_id))
        limit = {"error": "信令条数超过上限", "code": "SCREEN_SIGNAL_LIMIT", "debug": ""}
        resp = _runtime_signal(client, token, session_id, "candidate", {"candidate": "one too many"})
        assert resp.status_code == 409 and _detail(resp) == limit
        assert len(_db_session(session_id)["signals"]) == 128
        assert _web_signal(client, user, device_id, session_id, "candidate", {}).status_code == 409
        # end 不占信令存储：满额后仍可结束
        assert _runtime_signal(client, token, session_id, "end", {"reason": "connect_failed"}).status_code == 200
        assert _db_session(session_id)["signals"] == []

    def test_reaching_the_limit_by_real_signals(self, client, users):
        """逐条真实追加到第 128 条：序号连续，下一条被拒"""
        user = users[0]
        device_id, token = _online_device(client, user, "逐条到上限机")
        session_id = _new_session(client, user, device_id)
        seqs = [_ok_runtime(client, token, session_id, "candidate", {"n": n})["seq"] for n in range(127)]
        assert seqs == list(range(1, 128))
        # 第 128 条由网页发出：两个方向共用一个计数
        assert _ok_web(client, user, device_id, session_id, "candidate", {"n": 128})["seq"] == 128
        assert _runtime_signal(client, token, session_id, "candidate", {"n": 129}).status_code == 409
        assert _web_signal(client, user, device_id, session_id, "start").status_code == 409
        stored = _db_session(session_id)["signals"]
        assert [s["seq"] for s in stored] == list(range(1, 129))


# ---------------------------------------------------------------------------
# 旧设备对照
# ---------------------------------------------------------------------------


class TestLegacyDeviceCompatibility:
    def test_heartbeat_keys_are_unchanged_plus_screen_stream(self, client, users):
        """没有画面能力、没有会话的设备：心跳响应的键恰好是原四个加 screen_stream"""
        user = users[0]
        _, token = _online_device(client, user, "旧设备对照", screen=False)
        body = _heartbeat(client, token)
        assert set(body) == {"success", "selected", "server_time", "parallel_claim", "screen_stream"}
        assert body["success"] is True
        assert body["selected"] in (True, False)
        assert body["parallel_claim"] is True
        assert isinstance(body["server_time"], str)
        assert body["screen_stream"] == {"version": 1}

    def test_device_list_items_are_unchanged_plus_screen_stream(self, client, users):
        """设备列表每一项是原有的九个键加 screen_stream，不下发能力原始内容"""
        user = users[0]
        legacy_id, _ = _online_device(client, user, "旧设备列表对照", screen=False)
        capable_id, _ = _online_device(client, user, "新设备列表对照")
        resp = client.get("/api/local-tools/devices", headers=_auth(user["token"]))
        assert resp.status_code == 200
        assert set(resp.json()) == {"success", "devices"}
        items = {d["device_id"]: d for d in resp.json()["devices"]}
        original = {"device_id", "name", "platform", "runtime_version", "selected", "status", "online",
                    "last_seen_at", "created_at"}
        assert set(items[legacy_id]) == original | {"screen_stream"}
        assert set(items[capable_id]) == original | {"screen_stream"}
        assert items[legacy_id]["screen_stream"] is False
        assert items[capable_id]["screen_stream"] is True
        assert items[legacy_id]["runtime_version"] == "1.0.0" and items[legacy_id]["online"] is True
        assert items[legacy_id]["status"] == "active"
        for item in items.values():
            dumped = str(item)
            assert SCREEN_CAPABILITY not in dumped and "provider_id" not in dumped and "capabilities" not in dumped

    def test_screen_stream_flag_does_not_depend_on_online_state(self, client, users):
        user = users[0]
        offline_id, _ = _pair_device(client, user, "离线但有能力")
        _age_device(offline_id, 300)
        devices = client.get("/api/local-tools/devices", headers=_auth(user["token"])).json()["devices"]
        item = next(d for d in devices if d["device_id"] == offline_id)
        assert (item["online"], item["screen_stream"]) == (False, True)

    def test_device_token_cannot_call_viewer_routes(self, client, users):
        user = users[0]
        device_id, token = _online_device(client, user, "令牌隔离机")
        session_id = _new_session(client, user, device_id)
        assert client.post(f"/api/local-tools/devices/{device_id}/screen-sessions",
                           headers=_auth(token)).status_code == 401
        assert client.get(_path(device_id, session_id), headers=_auth(token)).status_code == 401

    def test_viewer_token_cannot_call_runtime_routes(self, client, users):
        user = users[0]
        device_id, _ = _online_device(client, user, "令牌隔离机B")
        session_id = _new_session(client, user, device_id)
        resp = client.post("/api/local-tools/runtime/screen/session", json={"session_id": session_id},
                           headers=_auth(user["token"]))
        assert resp.status_code == 401
        assert _db_session(session_id)["state"] == "pending"

    def test_anonymous_calls_are_rejected(self, client, users):
        user = users[0]
        device_id, _ = _online_device(client, user, "匿名访问机")
        session_id = _new_session(client, user, device_id)
        assert client.post(f"/api/local-tools/devices/{device_id}/screen-sessions").status_code == 401
        assert client.get(_path(device_id, session_id)).status_code == 401
        assert client.post("/api/local-tools/runtime/screen/session", json={"session_id": session_id}).status_code == 401
        assert client.post("/api/local-tools/runtime/screen/signal",
                           json={"session_id": session_id, "type": "prepared", "payload": {}}).status_code == 401
        assert _db_session(session_id)["state"] == "pending"

    def test_session_table_has_only_the_designed_columns(self, client, users):
        """会话表只有设计 5.5 列出的字段：没有来源地址、凭据或画面内容列"""
        user = users[0]
        device_id, token = _online_device(client, user, "表结构对照机")
        session_id = _new_session(client, user, device_id)
        _reach(client, user, device_id, token, session_id, "streaming")
        columns = _sql("""SELECT column_name FROM information_schema.columns
                          WHERE table_schema = 'public' AND table_name = 'local_tool_screen_sessions'
                          ORDER BY ordinal_position""")
        assert [c["column_name"] for c in columns] == SESSION_COLUMNS
        assert set(_db_session(session_id)) == set(SESSION_COLUMNS)


# ---------------------------------------------------------------------------
# 建表脚本：全量与增量内容相同，并且可以重复执行
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[2]
TABLE = "local_tool_screen_sessions"


def _structure():
    """会话表的列与索引快照"""
    return (
        _sql("""SELECT column_name, data_type, column_default, is_nullable FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = %s ORDER BY ordinal_position""", (TABLE,)),
        _sql("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = %s ORDER BY indexname", (TABLE,)),
    )


class TestSchemaScripts:
    """项目规范：加表必须同时改 deploy/init-postgres.sql 和 deploy/db_update.yaml，内容相同、SQL 幂等"""

    def _scripts(self):
        from src.db.database import _load_db_update_blocks, _split_sql_statements

        blocks = [block for block in _load_db_update_blocks(ROOT / "deploy" / "db_update.yaml")
                  if TABLE in block["statements"]]
        assert len(blocks) == 1
        full = _split_sql_statements((ROOT / "deploy" / "init-postgres.sql").read_text(encoding="utf-8"))
        return blocks[0], _split_sql_statements(blocks[0]["statements"]), [s for s in full if TABLE in s]

    def test_incremental_block_equals_the_full_init_script(self):
        block, incremental, full = self._scripts()
        assert incremental == full
        assert len(incremental) == 3  # 建表 + 两个索引
        assert [statement.split()[0] for statement in incremental] == ["CREATE", "CREATE", "CREATE"]
        assert all(" IF NOT EXISTS " in statement for statement in incremental)
        assert block["datetime"] > "2026-11-24 14:30:00"  # 晚于此前的最后一块
        assert "外键" not in incremental[0] and "REFERENCES" not in incremental[0]
        # 除主键外没有 NOT NULL
        assert incremental[0].count("NOT NULL") == 1

    def test_ddl_replay_leaves_the_structure_unchanged(self):
        _, incremental, _ = self._scripts()
        before = _structure()
        assert len(before[0]) == len(SESSION_COLUMNS) and len(before[1]) == 3  # 主键 + 两个索引
        for statement in incremental:
            _sql(statement)
        assert _structure() == before

    def test_session_queries_run_on_the_indexes(self):
        """每次心跳都会跑的查询和创建会话时的占用检查都走索引，不做全表扫描"""
        from src.local_tools import screen_stream

        plans = [
            _plan(screen_stream._NOTICE_SQL, (str(uuid.uuid4()),) * 2 + (30,)),
            _plan(screen_stream._ACTIVE_SESSIONS_SQL, (str(uuid.uuid4()),)),
            _plan(screen_stream._LOCK_SESSION_SQL.format(where=screen_stream.runtime_scope("t", str(uuid.uuid4()))[0]),
                  (str(uuid.uuid4()), "t", str(uuid.uuid4()))),
        ]
        indexes = ("idx_lt_screen_sessions_device_created", "idx_lt_screen_sessions_active",
                   "local_tool_screen_sessions_pkey")
        for text in plans:
            # 具体选哪个索引由规划器按统计信息决定，这里只守住「能用上索引、不做全表扫描」
            assert "Seq Scan on local_tool_screen_sessions" not in text
            assert any(index in text for index in indexes)


def _plan(statement, params):
    """在关闭顺序扫描的会话里看执行计划：规划器能用上哪个索引（测试库里数据很少，默认会选顺序扫描）"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SET LOCAL enable_seqscan = off")
        cur.execute("EXPLAIN " + statement, params)
        return "\n".join(row["QUERY PLAN"] for row in cur.fetchall())
