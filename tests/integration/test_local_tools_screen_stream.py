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
- 占用闸与连接池：画面请求只用画面专用连接池，不占业务连接池；闸满时立即 503；
  画面这一路被行锁挡住时不占住连接；占用闸的 503 跨域时可读
- 画面连接的复用：第一次用到画面才创建画面连接池并一次建满，之后心跳和两端轮询复用这几条连接；
  不支持画面的旧 Runtime 心跳不查画面会话，也就不拿名额、不建连接

时间戳一律在库里改早、用库里的 NOW() 比较，不靠 sleep，不用 Python 时间减库时间。
"""

import asyncio
import contextvars
import json
import subprocess
import sys
import textwrap
import threading
import time
import uuid
from collections import Counter
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

PROVIDER_ID = "ai.aidwork.boss-recruiting"
SCREEN_CAPABILITY = "screen_stream_v1"
STUN = ["stun:stun.example.test:3478"]
BUSY = {"error": "实时画面暂时繁忙，请稍后重试", "code": "SCREEN_STREAM_BUSY", "debug": ""}
# 画面连接池建连时用的 application_name：在 pg_stat_activity 里按它认出画面连接
SCREEN_APPLICATION_NAME = "aid-work-agent-screen"
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


def _business_pool():
    """业务连接池（psycopg2 连接池对象）"""
    from src.db import database

    return database.get_postgres_pool()


def _screen_pool():
    """画面专用连接池（psycopg2 连接池对象）"""
    from src.db import database

    return database.get_screen_pool()


def _checked_out(pool):
    """连接池里正被借用的连接数。读的是 psycopg2 连接池的私有属性 _used，只在测试里用来观察占用"""
    with pool._lock:
        return len(pool._used)


def _in_screen_scope():
    """借连接的那一刻是否处于画面作用域"""
    from src.db import database

    return database.in_screen_scope()


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
# 占用闸与连接池：观察用的探针、风暴
# ---------------------------------------------------------------------------

# 风暴的规模：CORE_DEVICES 台不支持画面的旧 Runtime 照常工作，SCREEN_SESSIONS 场画面建连在轮询，
# 每 500 毫秒一轮，共 STORM_ROUNDS 轮
CORE_DEVICES = 8
SCREEN_SESSIONS = 10
STORM_ROUNDS = 12

_request_kind = contextvars.ContextVar("screen_stream_test_request_kind", default="直接调用")


@contextmanager
def _as(kind):
    """这一段里发出的请求是画面请求（"screen"）还是核心请求（"core"），借连接的记录据此区分。
    ContextVar 的值随上下文复制到处理这个请求的线程里"""
    token = _request_kind.set(kind)
    try:
        yield
    finally:
        _request_kind.reset(token)


class _BorrowLog:
    """记下测试期间每一次借连接，只观察、不改变行为。

    borrows 每一项是 (请求类型, 连接池, 借的时候是否在画面作用域里, 调用方函数名, 是否在主线程上)。
    peak 是各连接池「同时借出的连接数」的峰值：连接数只在借出时增加，所以每次借出后看一眼不会漏掉峰值
    """

    def __init__(self):
        from src.db import database

        self._database = database
        self._original = database.get_pooled_connection
        self.borrows = []
        self.peak = {}

    def __enter__(self):
        self._database.get_pooled_connection = self._spy
        return self

    def __exit__(self, *_):
        self._database.get_pooled_connection = self._original

    def _spy(self, max_retries=2, pool=None):
        conn = self._original(max_retries=max_retries, pool=pool)
        used = self._database._pg_connection_pool if pool is None else pool
        if used is not None:
            self.borrows.append((_request_kind.get(), used, _in_screen_scope(), _borrower(),
                                 threading.current_thread() is threading.main_thread()))
            self.peak[used] = max(self.peak.get(used, 0), _checked_out(used))
        return conn


# 借连接这一路上的包装函数：不把它们记成调用方
_BORROW_WRAPPERS = ("_spy", "get_db_connection", "_session_transaction", "_connection_permit")


def _borrower():
    """借连接的调用方函数名：从 _spy 往上找，跳过包装函数（get_db_connection、画面事务那两层上下文管理器，
    以及 contextlib 自己的几层），停在业务代码里"""
    frame = sys._getframe(1)
    while frame.f_globals.get("__name__") == "contextlib" or frame.f_code.co_name in _BORROW_WRAPPERS:
        frame = frame.f_back
    return frame.f_code.co_name


def _peak(log, pool):
    return log.peak.get(pool, 0)


def _step(step, resp, ok):
    """核心请求一步的结果：(步骤名, 是否成功, 失败时的状态码和响应)"""
    return step, ok, "" if ok else f"{resp.status_code} {resp.text[:160]}"


async def _heartbeat_step(live, token):
    """一次设备心跳，返回这一步的结果"""
    with _as("core"):
        resp = await live.post("/api/local-tools/runtime/heartbeat", json={}, headers=_auth(token))
    return [_step("心跳", resp, resp.status_code == 200 and resp.json().get("success") is True)]


async def _invocation_steps(live, user, device_id, token):
    """一台设备顺序走完一次完整调用：创建、领取、started、回执，同一时刻只发一个请求，返回各步的结果"""
    from src.local_tools import repository

    with _as("core"):
        try:
            invocation_id = await asyncio.to_thread(
                repository.create_invocation, user["tenant_id"], user["user_id"], device_id,
                "boss_goto", {"url": "https://example.com"})
        except Exception as error:  # 例如创建调用时连接池已耗尽（PoolError）
            return [("创建调用", False, f"{type(error).__name__}: {error}")]
        claim = await live.post("/api/local-tools/runtime/claim?wait=2", headers=_auth(token))
        steps = [_step("接单", claim, claim.status_code == 200 and claim.json().get("invocation_id") == invocation_id)]
        if not steps[0][1]:
            return steps
        claim_token = claim.json()["claim_token"]
        invocation = f"/api/local-tools/runtime/invocations/{invocation_id}"
        started = await live.post(f"{invocation}/started", json={"claim_token": claim_token}, headers=_auth(token))
        steps.append(_step("started", started, started.status_code == 200 and started.json().get("state") == "running"))
        result = await live.post(
            f"{invocation}/result",
            json={"claim_token": claim_token, "success": True, "effect": "applied", "data": {"ok": True}},
            headers=_auth(token))
        steps.append(_step("回执", result, result.status_code == 200 and result.json().get("state") == "succeeded"))
        return steps


async def _screen_poll(live, method, url, headers, **kwargs):
    """一次画面轮询，返回 (状态码, 被拒时的 detail)"""
    with _as("screen"):
        resp = await live.request(method, url, headers=headers, **kwargs)
    return resp.status_code, (None if resp.status_code == 200 else resp.json().get("detail"))


async def _storm(live, sessions, user_headers, core_round):
    """每 500 毫秒一轮、共 STORM_ROUNDS 轮。每一轮：core_round() 给出的核心请求协程，加上每场会话的
    网页轮询和 Runtime 轮询，全部同时发出。返回 (核心请求各步的结果, 画面各状态码的次数, 画面被拒的原因)。

    请求用挂在同一个事件循环上的异步客户端，原因见 TestBlockedSessionRow
    """
    core_results, statuses, errors = [], Counter(), []
    for _ in range(STORM_ROUNDS):
        started = time.perf_counter()
        polls = [poll for device_id, token, session_id in sessions for poll in (
            _screen_poll(live, "GET", _path(device_id, session_id), user_headers),
            _screen_poll(live, "POST", "/api/local-tools/runtime/screen/session", _auth(token),
                          json={"session_id": session_id, "after_seq": 0}))]
        results = await asyncio.gather(*core_round(), *polls)
        core = results[:len(results) - len(polls)]
        core_results.extend(step for device in core for step in device)
        for status, error in results[len(results) - len(polls):]:
            statuses[status] += 1
            if error is not None:
                errors.append(error)
        await asyncio.sleep(max(0.0, 0.5 - (time.perf_counter() - started)))
    return core_results, statuses, errors


def _storm_once(client, sessions, user_headers, make_core_round):
    """跑一遍风暴。make_core_round(live) 给出「每一轮的核心请求协程」的函数"""

    async def run():
        import httpx

        transport = httpx.ASGITransport(app=client.app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as live:
            return await _storm(live, sessions, user_headers, make_core_round(live))

    return asyncio.run(run())


def _legacy_runtimes(client, user, count=CORE_DEVICES):
    """不支持画面的旧版本 Runtime，都在线"""
    return [_online_device(client, user, f"不支持画面的旧 Runtime {index}", screen=False) for index in range(count)]


def _connecting_sessions(client, user, count=SCREEN_SESSIONS):
    """count 台支持画面的设备各有一场会话处于 connecting：网页和 Runtime 两端都在轮询"""
    sessions = []
    for index in range(count):
        device_id, token = _online_device(client, user, f"同时建连 {index}")
        sessions.append((device_id, token, _new_session(client, user, device_id)))
    for device_id, token, session_id in sessions:
        _ok_runtime(client, token, session_id, "prepared")
    for device_id, token, session_id in sessions:
        _ok_web(client, user, device_id, session_id, "start")
    return sessions


def _take_gate():
    """把占用闸的名额全部借出来（借了不还），返回借到的个数"""
    from src.local_tools import screen_stream

    taken = 0
    while screen_stream.acquire_permit():
        taken += 1
    return taken


def _give_back(count):
    from src.local_tools import screen_stream

    for _ in range(count):
        screen_stream.release_permit()


class _GateSpy:
    """包住真的占用闸，数一数有多少次尝试拿名额、其中多少次没拿到"""

    def __init__(self, real):
        self._real = real
        self.attempts = 0
        self.refused = 0

    def acquire(self, blocking=True):
        self.attempts += 1
        acquired = self._real.acquire(blocking=blocking)
        self.refused += not acquired
        return acquired

    def release(self):
        self._real.release()


def _screen_backends():
    """画面连接池在 PostgreSQL 那一侧的连接：backend 进程号列表"""
    rows = _sql("SELECT pid FROM pg_stat_activity "
                "WHERE datname = current_database() AND application_name = %s ORDER BY pid",
                (SCREEN_APPLICATION_NAME,))
    return [row["pid"] for row in rows]


@contextmanager
def _screen_borrows():
    """记下测试期间每一次借画面连接池的连接，用的是哪一条数据库连接（backend 进程号）"""
    from src.db import database

    borrowed, original = [], database.get_pooled_connection

    def spy(max_retries=2, pool=None):
        conn = original(max_retries=max_retries, pool=pool)
        if pool is not None:  # 指定池来借的只有画面连接池（get_db_connection 在画面作用域里才指定池）
            borrowed.append(conn.info.backend_pid)
        return conn

    database.get_pooled_connection = spy
    try:
        yield borrowed
    finally:
        database.get_pooled_connection = original


def _run_in_fresh_process(code):
    """在一个新的 Python 进程里跑一段代码（pytest 进程里连接池早就建好了，查不了「刚启动」的状态），
    返回它用 print("PROBE ...") 打印出来的 JSON"""
    result = subprocess.run([sys.executable, "-c", textwrap.dedent(code)], cwd=ROOT, capture_output=True,
                            text=True, encoding="utf-8", timeout=120)
    assert result.returncode == 0, result.stderr[-3000:]
    return json.loads(next(line[len("PROBE "):] for line in result.stdout.splitlines()
                           if line.startswith("PROBE ")))


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
        """多线程同时为同一设备创建会话：最终只创建出一个会话。

        精确断言：200 恰好一次，数据库里恰好一行，就是那次创建出来的 pending 会话。
        过了占用闸的请求照常查库，除了成功的那个都得到 409 SCREEN_SESSION_ACTIVE；闸满时让出的请求
        不查库，得到 503 SCREEN_STREAM_BUSY（下一次重试）。除此之外不能有别的结果。

        409 和 503 各有几个不确定，只能给上下界。闸从空开始，先到的请求只要不超过名额数（缺省 3 个，
        等于画面连接池的上限）就一定过得去，所以 409 不少于「名额数减 1」、503 不多于「8 减名额数」；
        其余请求如果赶在某个名额归还之后才到达闸，也能过去并得到 409，所以 503 也可能更少"""
        from collections import Counter

        from src.local_tools import screen_stream
        user = users[0]
        device_id, _ = _online_device(client, user, "并发创建机")
        from src.main import app

        workers_count = 8
        workers = [lambda: _create(TestClient(app), user, device_id) for _ in range(workers_count)]
        results = _run_together(workers)

        # 只创建出一个会话
        created = [resp.json()["session"]["id"] for resp in results if resp.status_code == 200]
        assert len(created) == 1
        rows = _sql("SELECT id, state, tenant_id, user_id FROM local_tool_screen_sessions WHERE device_id = %s",
                    (device_id,))
        assert len(rows) == 1
        assert (str(rows[0]["id"]), rows[0]["state"], rows[0]["tenant_id"], rows[0]["user_id"]) == (
            created[0], "pending", user["tenant_id"], user["user_id"])

        expected = {
            "SCREEN_SESSION_ACTIVE": (409, "已有窗口正在查看这台电脑"),
            "SCREEN_STREAM_BUSY": (503, "实时画面暂时繁忙，请稍后重试"),
        }
        codes = Counter()
        for resp in results:
            if resp.status_code == 200:
                continue
            detail = _detail(resp)
            status, message = expected[detail["code"]]  # 出现别的错误码时这里直接 KeyError
            assert (resp.status_code, detail["error"], detail["debug"]) == (status, message, "")
            codes[detail["code"]] += 1
        assert codes["SCREEN_SESSION_ACTIVE"] + codes["SCREEN_STREAM_BUSY"] == workers_count - 1
        passed_gate = min(screen_stream.gate_capacity(), workers_count)
        assert codes["SCREEN_SESSION_ACTIVE"] >= passed_gate - 1
        assert codes["SCREEN_STREAM_BUSY"] <= workers_count - passed_gate

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


class TestStunUrlsConfiguration:
    def test_only_stun_addresses_are_handed_out(self, client, users):
        """共同契约 2.25：只下发 STUN 服务器地址。配置里混进 TURN 地址（可能带凭据）或不带协议头的条目时，
        配置模型把它们丢掉，下发地址的两个接口只返回 STUN 地址，顺序不变"""
        from src.config.settings import ScreenStreamConfig, settings

        user = users[0]
        device_id, token = _online_device(client, user, "配置校验机")
        mixed = [
            "turn:demo-user:demo-pass@relay.example.com:3478",
            "stun:stun.example.net:3478",
            "relay.example.com",
            "stuns:stuns.example.net",
            "turns:relay.example.com:5349",
        ]
        with pytest.MonkeyPatch.context() as patcher:
            patcher.setattr(settings, "screen_stream", ScreenStreamConfig(stun_urls=mixed))
            created = _create(client, user, device_id)
            assert created.status_code == 200, created.text
            session_id = created.json()["session"]["id"]
            runtime = _runtime_read(client, token, session_id)
            assert runtime.status_code == 200, runtime.text
        assert created.json()["session"]["stun_urls"] == ["stun:stun.example.net:3478", "stuns:stuns.example.net"]
        assert runtime.json()["session"]["stun_urls"] == ["stun:stun.example.net:3478", "stuns:stuns.example.net"]


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
            # 与真实鉴权给出的一样，带着设备已保存的能力：这台设备支持画面，所以心跳会去查画面会话
            device = {"id": uuid.UUID(device_id), "tenant_id": user["tenant_id"], "user_id": user["user_id"],
                      "capabilities_json": {"capabilities": [SCREEN_CAPABILITY]}}

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
# 画面这一路被行锁挡住时，不占住连接
# ---------------------------------------------------------------------------


class TestBlockedSessionRow:
    """画面这一路被行锁挡住时，不能占住连接、拖累别的设备（设计 4.4）。

    另一条连接把一条 streaming 会话行锁住、不提交。心跳里的续期 UPDATE 要等这把锁：应用层 0.5 秒后
    放弃等待时，线程里的 SQL 还在等，连接也不归还，连续心跳几次就把画面连接池占满，画面请求和心跳里的
    画面查询都会报 connection pool exhausted。所以画面这一路的 SQL 在数据库层有自己的等待上限，到点由
    PostgreSQL 取消语句、归还连接；网页和 Runtime 的轮询等锁超时得到 503，下一次轮询再试。
    画面这一路只用画面专用连接池，业务连接池始终不受影响。
    """

    def test_wait_limits_apply_only_inside_the_transaction(self):
        """等待上限的语句在真实数据库上成立：事务内生效（会话路径等锁 1 秒；心跳路径等锁和整条语句各 400 毫秒，
        且不动整条语句的时限），事务结束后恢复这条连接原来的值。
        mock 连接不解析 SQL，语句写法对不对只能在这里验证"""
        from src.db.database import get_db_connection
        from src.local_tools import screen_stream

        def limits(cursor):
            cursor.execute("SHOW lock_timeout")
            lock_timeout = cursor.fetchone()["lock_timeout"]
            cursor.execute("SHOW statement_timeout")
            return lock_timeout, cursor.fetchone()["statement_timeout"]

        with get_db_connection() as conn:
            cursor = conn.cursor()
            original = limits(cursor)
            assert original[0] == "0"

            cursor.execute(screen_stream._SESSION_LOCK_TIMEOUT_SQL)
            assert limits(cursor) == ("1s", original[1])
            conn.rollback()

            cursor.execute(screen_stream._HEARTBEAT_LOCK_TIMEOUT_SQL)
            cursor.execute(screen_stream._HEARTBEAT_STATEMENT_TIMEOUT_SQL)
            assert limits(cursor) == ("400ms", "400ms")
            conn.rollback()

            assert limits(cursor) == original

    @pytest.mark.slow
    def test_blocked_session_row_does_not_drain_the_connection_pool(self, client, users):
        import asyncio
        import os

        import httpx
        import psycopg2
        from loguru import logger

        from src.db import database
        from src.local_tools import api as local_tools_api
        from src.local_tools import repository, screen_stream

        dsn = os.environ.get("DATABASE_URL", "")
        if not dsn:
            pytest.skip("需要显式的测试库连接")
        user = users[0]
        user_headers = _auth(user["token"])
        device_a, token_a = _online_device(client, user, "会话被锁的设备")
        device_b, token_b = _online_device(client, user, "另一台设备")
        session_id = _new_session(client, user, device_a)
        _reach(client, user, device_a, token_a, session_id, "streaming")
        # 网页读一次，刷新它的访问时间（免得被当成网页已离开）；再把续期时间改早，
        # 锁释放之后「续期照常落库」才看得出
        assert _read(client, user, device_a, session_id).status_code == 200
        _age(session_id, "runtime_seen_at", 20)

        # 这条连接不走连接池：锁本身不占池里的连接
        blocker = psycopg2.connect(dsn, connect_timeout=10)
        try:
            blocker_cursor = blocker.cursor()
            blocker_cursor.execute("SELECT id FROM local_tool_screen_sessions WHERE id = %s FOR UPDATE", (session_id,))
            assert blocker_cursor.fetchone() is not None

            async def while_row_is_locked():
                """行锁占着的时候把被挡住的阶段跑完。

                这里不用 TestClient：它在每个请求结束时会关掉事件循环，并等线程池里还在跑的线程结束，
                也就是那条在等行锁的数据库查询。于是被挡住的画面查询表现为一次心跳 30 秒才返回（语句超时），
                连接池根本来不及被占满。真实服务（uvicorn）不等这种线程：处理函数一返回响应就送达，
                查询留在后台占着连接。所以改用挂在同一个事件循环上的异步客户端，和真实服务一致。
                无论成败，退出前先释放行锁：否则等锁的线程不会结束，事件循环也收不了尾。
                """
                records = []
                handler = logger.add(lambda message: records.append(message.record), level="WARNING")
                try:
                    transport = httpx.ASGITransport(app=client.app, raise_app_exceptions=False)
                    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as live:
                        business_pool, screen_pool = _business_pool(), _screen_pool()
                        heartbeat = "/api/local-tools/runtime/heartbeat"

                        def checked_out():
                            """两个连接池里正被借用的连接数：(业务连接池, 画面连接池)"""
                            return _checked_out(business_pool), _checked_out(screen_pool)

                        with _BorrowLog() as log:
                            # 请求都串行发出时，这两个数在请求之间应当回到基线
                            baseline = checked_out()
                            # 这台设备连续心跳「连接池上限 + 2」次，每一轮另一台设备也心跳一次
                            checked_out_after_heartbeat = []
                            for attempt in range(database.DB_POOL_MAX + 2):
                                started_at = time.perf_counter()
                                with _as("core"):
                                    screen = await live.post(heartbeat, json={}, headers=_auth(token_a))
                                    screen_elapsed = time.perf_counter() - started_at
                                    checked_out_after_heartbeat.append(checked_out())
                                    other = await live.post(heartbeat, json={}, headers=_auth(token_b))
                                assert other.status_code == 200, f"第 {attempt + 1} 轮：另一台设备的心跳被拖垮 {other.text}"
                                assert other.json()["success"] is True
                                assert screen.status_code == 200, f"第 {attempt + 1} 轮：这台设备的心跳失败 {screen.text}"
                                assert screen.json()["success"] is True and "screen_session" not in screen.json()
                                assert screen_elapsed < 2.0, f"第 {attempt + 1} 轮：这台设备的心跳 {screen_elapsed:.2f} 秒才返回"
                            # 每次心跳返回的那一刻，被挡住的画面查询占着的连接（在画面连接池里）已经归还，业务连接池也没被占。
                            # 如果是应用层 0.5 秒放弃等待、线程还在等锁，连接就不会回来，这个数会一轮比一轮大
                            assert checked_out_after_heartbeat == [baseline] * (database.DB_POOL_MAX + 2)
                            # 数据库取消语句按「查询超时」告警，不算失败；另一台设备没有画面查询的告警
                            screen_warnings = [record for record in records if "画面会话查询" in record["message"]]
                            assert all("画面会话查询超时" in record["message"] for record in screen_warnings)
                            assert len([record for record in screen_warnings
                                        if f"device={device_a}" in record["message"]]) == database.DB_POOL_MAX + 2
                            assert not [record for record in screen_warnings if f"device={device_b}" in record["message"]]

                            # 取消语句的是 PostgreSQL：锁还占着时直接查，抛的是数据库到点取消的异常。
                            # 两个上限都是 400 毫秒，谁先到点由数据库决定：等锁超时是 55P03，语句超时是 57014
                            with pytest.raises((psycopg2.errors.LockNotAvailable, psycopg2.errors.QueryCanceled)) as caught:
                                await asyncio.to_thread(screen_stream.heartbeat_notice, device_a,
                                                        device_offline_seconds=local_tools_api.ONLINE_THRESHOLD_SECONDS)
                            assert caught.value.pgcode in ("55P03", "57014")
                            assert checked_out() == baseline

                            # 另一台设备照常走完一次完整调用：创建、领取、started、提交结果
                            with _as("core"):
                                invocation_id = repository.create_invocation(
                                    user["tenant_id"], user["user_id"], device_b, "boss_goto", {"url": "https://example.com"})
                                claim = await live.post("/api/local-tools/runtime/claim?wait=2", headers=_auth(token_b))
                                assert claim.status_code == 200, claim.text
                                assert claim.json()["invocation_id"] == invocation_id
                                claim_token = claim.json()["claim_token"]
                                invocation = f"/api/local-tools/runtime/invocations/{invocation_id}"
                                started = await live.post(f"{invocation}/started", json={"claim_token": claim_token},
                                                          headers=_auth(token_b))
                                assert started.status_code == 200 and started.json()["state"] == "running", started.text
                                result = await live.post(
                                    f"{invocation}/result",
                                    json={"claim_token": claim_token, "success": True, "effect": "applied",
                                          "data": {"ok": True}}, headers=_auth(token_b))
                                assert result.status_code == 200 and result.json()["state"] == "succeeded", result.text
                            assert checked_out() == baseline

                            # 被锁住的这条会话：读写都及时返回 503 SCREEN_STREAM_BUSY，而不是挂着等锁
                            session_path = _path(device_a, session_id)
                            candidate = {"candidate": "candidate:1 1 udp 1 192.0.2.1 50000 typ host"}
                            blocked_requests = [
                                ("网页读取", lambda: live.get(session_path, headers=user_headers)),
                                ("Runtime 读取", lambda: live.post("/api/local-tools/runtime/screen/session",
                                                                    json={"session_id": session_id, "after_seq": 0},
                                                                    headers=_auth(token_a))),
                                ("网页追加信令", lambda: live.post(f"{session_path}/signals",
                                                                    json={"type": "candidate", "payload": candidate},
                                                                    headers=user_headers)),
                                ("Runtime 追加信令", lambda: live.post(
                                    "/api/local-tools/runtime/screen/signal",
                                    json={"session_id": session_id, "type": "candidate", "payload": candidate},
                                    headers=_auth(token_a))),
                                ("网页创建会话", lambda: live.post(
                                    f"/api/local-tools/devices/{device_a}/screen-sessions", headers=user_headers)),
                            ]
                            for label, send in blocked_requests:
                                started_at = time.perf_counter()
                                with _as("screen"):
                                    resp = await send()
                                elapsed = time.perf_counter() - started_at
                                assert resp.status_code == 503, f"{label}：{resp.status_code} {resp.text[:200]}"
                                assert _detail(resp) == BUSY, f"{label}：{resp.text[:200]}"
                                assert elapsed < 3.0, f"{label}：{elapsed:.2f} 秒才返回"
                                assert checked_out() == baseline, f"{label}：请求返回时还有连接被占着"
                            # 被挡住的这段时间里续期没有落库，会话也没有被改动
                            assert _is_fresh(session_id, "runtime_seen_at") is False
                            assert _db_session(session_id)["state"] == "streaming"

                        # 业务连接池始终不受影响：这一路被挡住的查询（包括心跳里的）全部借自画面连接池，
                        # 也就是画面作用域里的每一次借出都来自画面连接池。期间核心请求一次只来一个，
                        # 业务连接池同时借出的连接最多 1 个
                        in_scope = [borrow for borrow in log.borrows if borrow[2]]
                        assert in_scope, "被挡住的画面查询没有借过连接，用例没有测到"
                        assert all(borrow[1] is screen_pool for borrow in in_scope), \
                            f"画面作用域里有 {sum(borrow[1] is business_pool for borrow in in_scope)} 次借的是业务连接池"
                        assert _peak(log, business_pool) <= baseline[0] + 1
                finally:
                    logger.remove(handler)
                    blocker.rollback()  # 释放行锁

            asyncio.run(while_row_is_locked())
            # 锁释放之后：这台设备的心跳重新带会话通知，续期照常落库
            body = _heartbeat(client, token_a)
            assert body["screen_session"] == {"id": session_id, "state": "streaming"}
            assert _is_fresh(session_id, "runtime_seen_at") is True
            assert _db_session(session_id)["state"] == "streaming"
        finally:
            blocker.close()


# ---------------------------------------------------------------------------
# 占用闸：画面请求只用画面专用连接池，画面繁忙时立即 503
# ---------------------------------------------------------------------------


class TestScreenRequestGate:
    """画面请求同时处理的数量有上限（等于画面连接池的连接数上限），画面繁忙时由画面请求让出，不拖累核心请求。

    10 场建连同时开始，网页和 Runtime 两端每 500 毫秒同时轮询一次，一批就是 20 个请求同时到，
    比测试进程的画面连接池（缺省 3 个连接）多得多。所以画面请求在进入租户中间件之前先过一道占用闸，
    拿不到名额的请求立即得到 503（下一次轮询重试；心跳这次不带会话通知）。过了闸的请求连同租户
    中间件的查库只用画面连接池，业务连接池留给心跳、接单、续租、回执这些核心请求。
    """

    @pytest.mark.slow
    def test_core_requests_survive_a_screen_polling_storm(self, client, users):
        from src.db import database
        from src.local_tools import screen_stream

        user = users[0]
        user_headers = _auth(user["token"])
        sessions = _connecting_sessions(client, user)
        assert all(_db_session(session_id)["state"] == "connecting" for _, _, session_id in sessions)

        # 另一台设备：不支持画面的旧 Runtime，同一时间里照常心跳、接单、回执
        core_device, core_token = _online_device(client, user, "不支持画面的旧 Runtime", screen=False)
        listed = client.get("/api/local-tools/devices", headers=user_headers).json()["devices"]
        assert next(item for item in listed if item["device_id"] == core_device)["screen_stream"] is False

        def make_core_round(live):
            return lambda: [_heartbeat_step(live, core_token), _invocation_steps(live, user, core_device, core_token)]

        with _BorrowLog() as log:
            core, screen_statuses, screen_errors = _storm_once(client, sessions, user_headers, make_core_round)
        business_pool, screen_pool = _business_pool(), _screen_pool()

        # 1. 另一台设备的心跳和完整调用不受画面请求影响
        beats = [(ok, detail) for step, ok, detail in core if step == "心跳"]
        assert len(beats) == STORM_ROUNDS and all(ok for ok, _ in beats), \
            f"另一台设备的心跳被画面请求拖垮：{[detail for ok, detail in beats if not ok]}"
        calls = [(ok, detail) for step, ok, detail in core if step != "心跳"]
        assert [step for step, _, _ in core if step != "心跳"] == ["接单", "started", "回执"] * STORM_ROUNDS \
            and all(ok for ok, _ in calls), \
            f"另一台设备的完整调用被画面请求拖垮：{[detail for ok, detail in calls if not ok]}"
        # 2. 画面轮询只可能是成功或「画面繁忙」，不会是 500；并且确实繁忙过
        assert set(screen_statuses) <= {200, 503}, f"画面轮询出现了 200 和 503 以外的状态码：{dict(screen_statuses)}"
        assert all(detail == BUSY for detail in screen_errors), f"画面轮询被拒的原因不对：{screen_errors[:3]}"
        assert set(screen_statuses) == {200, 503}, f"这轮风暴没有造成画面繁忙，用例没有测到闸：{dict(screen_statuses)}"
        assert sum(screen_statuses.values()) == STORM_ROUNDS * 2 * len(sessions)
        # 3. 画面轮询没有从业务连接池借过连接：画面请求的每一次借出都在画面作用域里、都来自画面连接池；
        #    业务连接池的每一次借出都不在画面请求里。闸的容量等于画面连接池的上限，画面连接池不会被借空
        screen_borrows = [borrow for borrow in log.borrows if borrow[0] == "screen"]
        assert screen_borrows
        assert all(borrow[1] is screen_pool and borrow[2] for borrow in screen_borrows), \
            f"画面请求从业务连接池借了 {sum(borrow[1] is business_pool for borrow in screen_borrows)} 次连接"
        assert all(borrow[0] != "screen" and not borrow[2] for borrow in log.borrows if borrow[1] is business_pool)
        gate = screen_stream.gate_capacity()
        assert gate == max(1, database.SCREEN_DB_POOL_MAX)
        assert _peak(log, screen_pool) <= gate

        print(f"\n[风暴] 另一台设备：心跳 {sum(ok for ok, _ in beats)}/{STORM_ROUNDS}，完整调用 {sum(ok for ok, _ in calls)}/{STORM_ROUNDS * 3} 步；"
              f"画面 {dict(screen_statuses)}；业务连接池借出峰值 {_peak(log, business_pool)}，"
              f"画面连接池借出峰值 {_peak(log, screen_pool)}/{screen_pool.maxconn}")

        # 轮询停下来之后：随便挑一场会话把建连走完，心跳续期正常
        device_id, token, session_id = sessions[0]
        _ok_runtime(client, token, session_id, "status", {"state": "connected"})
        _ok_runtime(client, token, session_id, "status", {"state": "streaming", "path": "lan"})
        _age(session_id, "runtime_seen_at", 20)
        body = _heartbeat(client, token)
        assert body["screen_session"] == {"id": session_id, "state": "streaming"}
        assert _is_fresh(session_id, "runtime_seen_at") is True
        assert _db_session(session_id)["state"] == "streaming"


# ---------------------------------------------------------------------------
# 对照回归：画面限流不挤占核心请求原有的连接容量
# ---------------------------------------------------------------------------


class TestBusinessPoolStaysWithCoreRequests:
    """画面限流不能挤占核心请求原有的连接容量。

    只让画面请求「少占」业务连接池不够：只要它还能从业务连接池借连接，核心请求可用的容量就一定变少。
    所以画面请求（连同路由之前租户中间件的那次查库）只用画面专用连接池，占用闸的名额数等于这个池的
    上限，业务连接池的容量全部留给核心请求。对照组是 CORE_DEVICES 台不支持画面的旧 Runtime：有画面轮询
    和没有画面轮询各跑一遍，两遍里核心请求都必须全部成功。业务连接池上限是 10（tests/integration/conftest.py）。
    """

    @staticmethod
    def _assert_all_succeeded(label, core):
        failures = [f"{step}：{detail}" for step, ok, detail in core if not ok]
        assert failures == [], f"{label}：{len(failures)} 步核心请求失败：{failures[:3]}"

    @pytest.mark.slow
    def test_heartbeats_only(self, client, users):
        """只心跳：8 台设备每轮同时各心跳一次，共 96 次。有画面轮询和没有画面轮询各跑一遍，两遍都全部成功"""
        user = users[0]
        devices = _legacy_runtimes(client, user)
        sessions = _connecting_sessions(client, user)
        expected = STORM_ROUNDS * CORE_DEVICES
        business_pool, screen_pool = _business_pool(), _screen_pool()

        def make_core_round(live):
            return lambda: [_heartbeat_step(live, token) for _, token in devices]

        with _BorrowLog() as quiet_log:
            quiet, _, _ = _storm_once(client, (), _auth(user["token"]), make_core_round)
        with _BorrowLog() as busy_log:
            busy, screen_statuses, screen_errors = _storm_once(client, sessions, _auth(user["token"]), make_core_round)

        quiet_ok = sum(ok for _, ok, _ in quiet)
        busy_ok = sum(ok for _, ok, _ in busy)
        print(f"\n[对照回归 1a] 没有画面轮询：心跳 {quiet_ok}/{expected}，业务连接池借出峰值 {_peak(quiet_log, business_pool)}；"
              f"有画面轮询：心跳 {busy_ok}/{expected}，画面 {dict(screen_statuses)}，"
              f"业务连接池峰值 {_peak(busy_log, business_pool)}，画面连接池峰值 {_peak(busy_log, screen_pool)}")
        for label, core in (("没有画面轮询", quiet), ("有画面轮询", busy)):
            assert [step for step, _, _ in core] == ["心跳"] * expected
            self._assert_all_succeeded(label, core)
        # 有画面轮询时画面确实繁忙过
        assert set(screen_statuses) == {200, 503} and screen_errors == [BUSY] * len(screen_errors)
        # 业务连接池里只有核心心跳在借：同时借出的连接数不超过同时在线的核心设备数。
        # 画面请求都在画面连接池里，同时借出的连接数不超过画面连接池的上限
        assert _peak(quiet_log, business_pool) <= CORE_DEVICES
        assert _peak(busy_log, business_pool) <= CORE_DEVICES
        assert _peak(busy_log, screen_pool) <= screen_pool.maxconn

    @pytest.mark.slow
    def test_heartbeat_claim_started_and_result_in_sequence(self, client, users):
        """心跳、接单、started、回执：每台设备在一轮里顺序走完这一条链（同一时刻只发一个请求），8 台之间同时。
        有画面轮询和没有画面轮询各跑一遍，两遍全部成功，结果一致"""
        user = users[0]
        devices = _legacy_runtimes(client, user)
        sessions = _connecting_sessions(client, user)
        expected = STORM_ROUNDS * CORE_DEVICES
        business_pool, screen_pool = _business_pool(), _screen_pool()

        def make_core_round(live):
            async def chain(device_id, token):
                return await _heartbeat_step(live, token) + await _invocation_steps(live, user, device_id, token)

            return lambda: [chain(device_id, token) for device_id, token in devices]

        with _BorrowLog() as quiet_log:
            quiet, _, _ = _storm_once(client, (), _auth(user["token"]), make_core_round)
        with _BorrowLog() as busy_log:
            busy, screen_statuses, screen_errors = _storm_once(client, sessions, _auth(user["token"]), make_core_round)

        def summary(core):
            counts = Counter({step: 0 for step in ("心跳", "接单", "started", "回执")})
            counts.update(step for step, ok, _ in core if ok)
            return dict(counts)

        print(f"\n[对照回归 1b] 没有画面轮询：{summary(quiet)}，业务连接池借出峰值 {_peak(quiet_log, business_pool)}；"
              f"有画面轮询：{summary(busy)}，画面 {dict(screen_statuses)}，"
              f"业务连接池峰值 {_peak(busy_log, business_pool)}，画面连接池峰值 {_peak(busy_log, screen_pool)}")
        for label, core in (("没有画面轮询", quiet), ("有画面轮询", busy)):
            self._assert_all_succeeded(label, core)
            assert summary(core) == {"心跳": expected, "接单": expected, "started": expected, "回执": expected}, \
                f"{label}：核心请求没有全部走完：{summary(core)}"
        assert set(screen_statuses) == {200, 503} and screen_errors == [BUSY] * len(screen_errors)
        assert _peak(quiet_log, business_pool) <= CORE_DEVICES
        assert _peak(busy_log, business_pool) <= CORE_DEVICES
        assert _peak(busy_log, screen_pool) <= screen_pool.maxconn

    @pytest.mark.slow
    def test_screen_requests_only_use_the_screen_pool(self, client, users):
        """确定性证据：轮询期间业务连接池的每一次借出都不在画面请求里，画面请求只使用画面连接池；
        画面请求的状态只能是 200 或 503"""
        user = users[0]
        devices = _legacy_runtimes(client, user)
        sessions = _connecting_sessions(client, user)
        business_pool, screen_pool = _business_pool(), _screen_pool()

        def make_core_round(live):
            return lambda: [_heartbeat_step(live, token) for _, token in devices]

        with _BorrowLog() as log:
            core, screen_statuses, screen_errors = _storm_once(client, sessions, _auth(user["token"]), make_core_round)
        screen_borrows = [borrow for borrow in log.borrows if borrow[0] == "screen"]
        business_borrows = [borrow for borrow in log.borrows if borrow[1] is business_pool]
        from_business = [borrow for borrow in screen_borrows if borrow[1] is business_pool]
        print(f"\n[对照回归 1c] 核心心跳 {sum(ok for _, ok, _ in core)}/{STORM_ROUNDS * CORE_DEVICES}；"
              f"画面请求借连接 {len(screen_borrows)} 次，其中借业务连接池 {len(from_business)} 次；"
              f"画面 {dict(screen_statuses)}；业务连接池借出峰值 {_peak(log, business_pool)}，"
              f"画面连接池借出峰值 {_peak(log, screen_pool)}")
        # 1. 画面请求的状态只能是 200 或 503
        assert set(screen_statuses) <= {200, 503}, f"画面请求出现了 200 和 503 以外的状态码：{dict(screen_statuses)}"
        assert screen_errors == [BUSY] * len(screen_errors)
        # 2. 画面请求只使用画面连接池：画面请求确实处理过，并且每一次都借自画面连接池、借的时候都在画面作用域里
        assert screen_borrows
        assert from_business == [], f"画面请求从业务连接池借了 {len(from_business)} 次连接"
        assert all(borrow[1] is screen_pool and borrow[2] for borrow in screen_borrows)
        # 3. 业务连接池的每一次借出都不在画面请求里：借的时候不在画面作用域里，请求类型也不是画面请求
        assert business_borrows
        assert all(borrow[0] != "screen" and not borrow[2] for borrow in business_borrows)
        # 4. 核心请求全部成功
        self._assert_all_succeeded("有画面轮询", core)

    def test_scope_reaches_every_thread_that_borrows_for_a_screen_request(self, client, users):
        """画面作用域跟着请求进了线程。一次画面请求里有三处借连接：路由之前租户中间件的查库在事件循环线程上，
        设备鉴权是同步依赖（FastAPI 放在线程池里执行），画面函数经 asyncio.to_thread 执行；
        三处借连接时都还在画面作用域里，都借自画面连接池。对照：心跳里的画面查询也走画面连接池，
        心跳自己的设备更新走业务连接池"""
        user = users[0]
        device_id, token = _online_device(client, user, "作用域继承机")
        session_id = _new_session(client, user, device_id)

        async def read():
            import httpx

            transport = httpx.ASGITransport(app=client.app, raise_app_exceptions=False)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as live:
                with _as("screen"):
                    runtime = await live.post("/api/local-tools/runtime/screen/session",
                                              json={"session_id": session_id, "after_seq": 0}, headers=_auth(token))
                    web = await live.get(_path(device_id, session_id), headers=_auth(user["token"]))
                with _as("core"):
                    heartbeat = await live.post("/api/local-tools/runtime/heartbeat", json={}, headers=_auth(token))
                return runtime, web, heartbeat

        with _BorrowLog() as log:
            runtime, web, heartbeat = asyncio.run(read())
        assert (runtime.status_code, web.status_code, heartbeat.status_code) == (200, 200, 200), \
            (runtime.text, web.text, heartbeat.text)
        screen_pool = _screen_pool()
        screen = [borrow for borrow in log.borrows if borrow[0] == "screen"]
        core = [borrow for borrow in log.borrows if borrow[0] == "core"]
        assert all(borrow[1] is screen_pool and borrow[2] for borrow in screen)
        # 设备鉴权（Runtime 取会话那一次）：在线程池里执行，调用方是 get_device_by_token_hash
        device_auth = [borrow for borrow in screen if borrow[3] == "get_device_by_token_hash"]
        assert len(device_auth) == 1 and device_auth[0][4] is False
        # 画面函数（两次读取）：经 asyncio.to_thread 执行
        loads = [borrow for borrow in screen if borrow[3] == "load_session"]
        assert len(loads) == 2 and all(borrow[4] is False for borrow in loads)
        # 租户中间件的查库：在事件循环线程（主线程）上
        on_main = [borrow for borrow in screen if borrow[4]]
        assert on_main and all(borrow[3] in ("verify_token", "_authenticate_user") for borrow in on_main)
        # 对照：心跳里的画面查询自己进画面作用域、走画面连接池；心跳的设备更新走业务连接池、不在画面作用域里
        assert any(borrow[3] == "heartbeat_notice" and borrow[1] is screen_pool and borrow[2] for borrow in core)
        device_updates = [borrow for borrow in core if borrow[3] != "heartbeat_notice"]
        assert device_updates and all(borrow[1] is not screen_pool and not borrow[2] for borrow in device_updates)


# ---------------------------------------------------------------------------
# 占用闸的 503 跨域时可读
# ---------------------------------------------------------------------------


class TestBusyResponseCors:
    def test_busy_response_carries_cors_headers_only_for_requests_with_an_allowed_origin(self, client):
        """占用闸挂在全局 CORS 中间件外面，它回的 503 不经过那一层；main.py 给拒绝响应单独包了一层
        CORS，参数与全局那一层相同。所以跨域部署时网页读得到这个 503：带允许的 Origin 时带
        access-control-allow-origin，不带 Origin 时不加"""
        from starlette.middleware.cors import CORSMiddleware

        from src.local_tools import screen_stream
        from src.main import app

        # 只断言相对顺序：占用闸在租户中间件外面，全局 CORS 在里面（以后别的中间件加进来不影响这条用例）
        names = [middleware.cls.__name__ for middleware in app.user_middleware]
        assert names.count("ScreenStreamGateMiddleware") == names.count("TenantContextMiddleware") == names.count("CORSMiddleware") == 1
        assert (names.index("ScreenStreamGateMiddleware") < names.index("TenantContextMiddleware")
                < names.index("CORSMiddleware")), names
        gate = app.user_middleware[names.index("ScreenStreamGateMiddleware")]
        cors = app.user_middleware[names.index("CORSMiddleware")]
        assert gate.cls is screen_stream.ScreenStreamGateMiddleware and cors.cls is CORSMiddleware
        origins = cors.kwargs["allow_origins"]
        # 全局那一层是请求到来时才按 kwargs 构造的，这里按同样的 kwargs 造一个出来比较构造后的参数
        reference = CORSMiddleware(None, **cors.kwargs)
        busy_cors = gate.kwargs["busy_app"]
        assert (busy_cors.allow_origins, busy_cors.allow_credentials, busy_cors.allow_methods,
                busy_cors.allow_headers) == (reference.allow_origins, reference.allow_credentials,
                                             reference.allow_methods, reference.allow_headers)

        taken = _take_gate()
        try:
            path = "/api/local-tools/runtime/screen/session"
            with_origin = client.post(path, json={"session_id": str(uuid.uuid4()), "after_seq": 0},
                                      headers={"Origin": origins[0]})
            without_origin = client.post(path, json={"session_id": str(uuid.uuid4()), "after_seq": 0})
            disallowed = client.post(path, json={"session_id": str(uuid.uuid4()), "after_seq": 0},
                                     headers={"Origin": "http://not-allowed.example"})
        finally:
            _give_back(taken)
        for resp in (with_origin, without_origin, disallowed):
            assert resp.status_code == 503 and resp.json() == {"detail": BUSY}, resp.text
        assert with_origin.headers["access-control-allow-origin"] == origins[0]
        assert "access-control-allow-origin" not in without_origin.headers
        assert "access-control-allow-origin" not in disallowed.headers


# ---------------------------------------------------------------------------
# 画面连接的复用
# ---------------------------------------------------------------------------


class TestScreenConnectionsAreReused:
    """画面连接池里的连接要复用：第一次用到画面时才创建、一次建满，之后心跳和两端轮询用的都是这几条，不会关了再建。

    psycopg2 2.9 的连接池只在「空闲连接数小于 minconn」时才保留归还的连接，否则直接关闭；所以画面连接池的
    minconn 必须等于 maxconn。minconn 取 0 的话每用完一次就关一条，下一次又要重新做数据库连接握手。
    连接数和 backend 进程号直接查 PostgreSQL 的 pg_stat_activity。
    """

    def test_a_fresh_process_holds_no_screen_connection_until_screen_is_used(self):
        """新进程：初始化连接池不创建画面连接池，数据库里没有画面连接；第一次在画面作用域里借连接时才创建，
        并一次建满 SCREEN_DB_POOL_MAX 条"""
        from src.db import database

        seen = _run_in_fresh_process(f"""
            import json
            from src.db import database

            def backends():
                with database.get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT pid FROM pg_stat_activity WHERE datname = current_database() "
                                   "AND application_name = %s ORDER BY pid", ("{SCREEN_APPLICATION_NAME}",))
                    return [row["pid"] for row in cursor.fetchall()]

            database.init_postgres_pool()
            # 这个数据库里别的进程（比如正在跑的 pytest 进程）已经建好的画面连接不算在新进程头上
            others = set(backends())
            seen = {{"pool_created_by_init": database.get_screen_pool() is not None,
                     "after_init": [pid for pid in backends() if pid not in others]}}
            with database.screen_connection_scope():
                with database.get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT pg_backend_pid() AS pid")
                    seen["first_borrow"] = cursor.fetchone()["pid"]
            seen["after_first_use"] = [pid for pid in backends() if pid not in others]
            print("PROBE", json.dumps(seen))
            database.close_postgres_pool()
        """)
        print(f"\n[连接复用·新进程] {seen}")
        assert seen["pool_created_by_init"] is False
        assert seen["after_init"] == []
        assert len(seen["after_first_use"]) == database.SCREEN_DB_POOL_MAX
        assert seen["first_borrow"] in seen["after_first_use"]

    def test_screen_connections_are_reused_not_reopened(self, client, users):
        """第一次用到画面后，画面连接池在数据库一侧始终是 SCREEN_DB_POOL_MAX 条连接；之后连续的心跳、网页轮询、
        Runtime 轮询借到的都是这几条（backend 进程号不变）"""
        from contextlib import ExitStack

        from src.db import database

        user = users[0]
        device_id, token = _online_device(client, user, "连接复用机")
        session_id = _new_session(client, user, device_id)  # pending：每次心跳都查画面会话通知
        rounds = 12
        with _screen_borrows() as borrowed:
            assert _heartbeat(client, token)["screen_session"] == {"id": session_id, "state": "pending"}
            after_first = _screen_backends()
            for _ in range(rounds):
                assert _heartbeat(client, token)["screen_session"] == {"id": session_id, "state": "pending"}
                assert _read(client, user, device_id, session_id).status_code == 200
                assert _runtime_read(client, token, session_id).status_code == 200
            after_rounds = _screen_backends()
            # 同时借出上限这么多条：池里的每一条连接都用上一遍，记下它们的 backend 进程号
            held = []
            with database.screen_connection_scope(), ExitStack() as stack:
                for _ in range(database.SCREEN_DB_POOL_MAX):
                    conn = stack.enter_context(database.get_db_connection())
                    conn.execute("SELECT pg_backend_pid() AS pid")
                    held.append(conn.fetchone()["pid"])
        after_all = _screen_backends()
        print(f"\n[连接复用] 第一次心跳后 {len(after_first)} 条 {after_first}；之后 {rounds} 轮（每轮心跳、网页轮询、"
              f"Runtime 轮询各一次）借画面连接 {len(borrowed)} 次，用到 {len(set(borrowed))} 个不同的 backend 进程号；"
              f"同时借出 {len(held)} 条时用的是 {sorted(held)}；结束时 {len(after_all)} 条 {after_all}")
        assert len(after_first) == database.SCREEN_DB_POOL_MAX, (
            f"第一次用到画面后，画面连接池在数据库一侧应当有 {database.SCREEN_DB_POOL_MAX} 条连接，"
            f"实际 {len(after_first)} 条")
        assert sorted(held) == after_first
        assert borrowed and set(borrowed) <= set(after_first), "借到的连接里有不在画面连接池里的：有连接被关掉后重新建了"
        assert after_rounds == after_first
        assert after_all == after_first

    def test_peer_reads_committed_signal_after_an_idle_screen_connection_dies(self, client, users):
        """空闲连接断开后，画面请求借出时丢弃坏连接，对端仍能读到已确认的信令。"""
        user = users[0]
        device_id, token = _online_device(client, user, "画面连接断开")
        session_id = _new_session(client, user, device_id)
        _reach(client, user, device_id, token, session_id, "connecting")
        payload = {"candidate": "candidate:1 1 udp 1 192.0.2.1 50000 typ host"}

        with _screen_borrows() as borrowed:
            ack = _ok_runtime(client, token, session_id, "candidate", payload)
        backend = borrowed[-1]
        # 只终止当前隔离库中刚由该测试使用、现已归还的画面连接。
        stopped = _sql(
            "SELECT pg_terminate_backend(pid, 2000) AS stopped FROM pg_stat_activity "
            "WHERE pid = %s AND datname = current_database() AND application_name = %s",
            (backend, SCREEN_APPLICATION_NAME),
        )
        assert stopped == [{"stopped": True}]

        with _screen_borrows() as borrowed:
            read = _read(client, user, device_id, session_id, after_seq=ack["seq"] - 1)
        assert read.status_code == 200, read.text
        assert read.json()["signals"] == [{"seq": ack["seq"], "type": "candidate", "payload": payload}]
        assert borrowed and backend not in borrowed


# ---------------------------------------------------------------------------
# 不支持画面的旧 Runtime：心跳不查画面会话
# ---------------------------------------------------------------------------


class TestLegacyRuntimeSkipsScreenQuery:
    """不支持画面的旧 Runtime 不可能有画面会话（创建会话时就被拒绝）：心跳不为它查会话，也就不拿名额、不建连接。
    响应里仍然带 screen_stream 标记（旧设备对照要求除新增字段外不变）。"""

    def test_heartbeats_take_no_permit_and_borrow_no_screen_connection(self, client, users, monkeypatch):
        from src.local_tools import screen_stream

        user = users[0]
        _, token = _online_device(client, user, "旧 Runtime 不查画面", screen=False)
        gate = _GateSpy(screen_stream._connection_gate())
        monkeypatch.setattr(screen_stream, "_gate", gate)
        with _screen_borrows() as borrowed:
            for _ in range(12):
                body = _heartbeat(client, token)
                assert "screen_stream" in body and "screen_session" not in body
        print(f"\n[旧 Runtime 跳过] 12 次心跳：尝试拿名额 {gate.attempts} 次，借画面连接 {len(borrowed)} 次"
              f"（{len(set(borrowed))} 个不同的数据库连接）")
        assert (gate.attempts, borrowed) == (0, [])

    def test_heartbeats_do_not_create_the_screen_pool(self, client, users):
        """还没用到画面（画面连接池没创建）时来心跳：旧设备的心跳不会让它被创建，数据库里也没有画面连接"""
        from src.db import database

        user = users[0]
        _, token = _online_device(client, user, "旧 Runtime 不建连接", screen=False)
        database.close_screen_pool()  # 回到「还没用到画面」的状态；之后第一次用到画面时会重新创建
        for _ in range(12):
            _heartbeat(client, token)
        assert database.get_screen_pool() is None
        assert _screen_backends() == []

    def test_heartbeat_passes_a_full_gate_without_a_warning(self, client, users):
        """占用闸被画面请求占满时，旧设备的心跳照常立即 200：它不去过闸，也就不记「画面繁忙」告警"""
        from loguru import logger

        user = users[0]
        _, token = _online_device(client, user, "旧 Runtime 过满闸", screen=False)
        records = []
        handler = logger.add(lambda message: records.append(message.record), level="WARNING")
        try:
            taken = _take_gate()
            try:
                started = time.perf_counter()
                resp = client.post("/api/local-tools/runtime/heartbeat", json={}, headers=_auth(token))
                elapsed = time.perf_counter() - started
            finally:
                _give_back(taken)
        finally:
            logger.remove(handler)
        assert resp.status_code == 200, resp.text
        assert resp.json()["success"] is True and "screen_session" not in resp.json()
        assert "screen_stream" in resp.json()
        busy = [record for record in records if "画面繁忙" in record["message"]]
        print(f"\n[旧 Runtime 过满闸] {resp.status_code}，用时 {elapsed:.3f} 秒，「画面繁忙」告警 {len(busy)} 条")
        assert busy == [], f"旧设备的心跳不该去过占用闸：{[record['message'] for record in busy]}"
        assert elapsed < 1.0

    def test_screen_query_follows_the_capabilities_effective_for_this_heartbeat(self, client, users, monkeypatch):
        """查不查取决于这次心跳生效的能力体，取法与 touch_device_seen 存库时相同：这次上报的能力体非空就用它
        （哪怕和已保存的不同），否则用设备已保存的"""
        from src.local_tools import screen_stream

        user = users[0]
        with_screen = {"provider_id": PROVIDER_ID, "capabilities": ["local_v2", SCREEN_CAPABILITY]}
        without_screen = {"provider_id": PROVIDER_ID}
        # 配对成旧设备：已保存的能力不含画面
        device_id, token = _pair_device(client, user, "能力判定机", screen=False)
        gate = _GateSpy(screen_stream._connection_gate())
        monkeypatch.setattr(screen_stream, "_gate", gate)
        heartbeat = "/api/local-tools/runtime/heartbeat"

        def beat(body):
            before = gate.attempts
            resp = client.post(heartbeat, json=body, headers=_auth(token))
            assert resp.status_code == 200, resp.text
            return gate.attempts - before

        assert beat({"capabilities": with_screen}) == 1  # 这次上报了含画面的能力体：查
        assert beat({}) == 1  # 已保存的能力含画面、这次没上报能力体：查
        assert beat({"capabilities": without_screen}) == 0  # 这次上报的能力体不含画面：不查
        listed = client.get("/api/local-tools/devices", headers=_auth(user["token"])).json()["devices"]
        assert next(item for item in listed if item["device_id"] == device_id)["screen_stream"] is False

    @pytest.mark.slow
    def test_eight_legacy_runtimes_heartbeat_without_touching_the_gate(self, client, users, monkeypatch):
        """8 台旧 Runtime 每 500 毫秒同时各心跳一次，共 12 轮：全部成功，没有一次去拿过名额"""
        from src.local_tools import screen_stream

        user = users[0]
        devices = _legacy_runtimes(client, user)
        gate = _GateSpy(screen_stream._connection_gate())
        monkeypatch.setattr(screen_stream, "_gate", gate)

        def make_core_round(live):
            return lambda: [_heartbeat_step(live, token) for _, token in devices]

        core, _, _ = _storm_once(client, (), _auth(user["token"]), make_core_round)
        succeeded = sum(ok for _, ok, _ in core)
        print(f"\n[8 台旧 Runtime 同步心跳] 心跳 {succeeded}/{len(core)}；尝试拿名额 {gate.attempts} 次，"
              f"其中拿不到 {gate.refused} 次")
        assert len(core) == STORM_ROUNDS * CORE_DEVICES
        assert [detail for _, ok, detail in core if not ok] == []
        assert gate.attempts == 0


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
        """两端同时结束：会话只结束一次，end_reason 是先到的那一方。
        名额够两个请求同时处理时（缺省）两端都成功。名额只有 1 个时（SCREEN_DB_POOL_MAX=1），后到的一端会在
        占用闸上被拒（503 SCREEN_STREAM_BUSY）；被拒的一端再发一次是幂等成功"""
        user = users[0]
        device_id, token = _online_device(client, user, "两端同时结束机")
        session_id = _new_session(client, user, device_id)
        _reach(client, user, device_id, token, session_id, "streaming")
        from src.main import app

        def ends():
            return {
                "web": lambda: _web_end(TestClient(app), user, device_id, session_id),
                "runtime": lambda: _runtime_signal(TestClient(app), token, session_id, "end",
                                                   {"reason": "runtime_stopped"}),
            }

        results = dict(_run_together([lambda label=label: (label, end()) for label, end in ends().items()]))
        statuses = sorted(resp.status_code for resp in results.values())
        from src.local_tools import screen_stream
        if screen_stream.gate_capacity() >= 2:
            # 名额够两个请求同时处理：两端都成功
            assert statuses == [200, 200], results
        else:
            # 名额只有 1 个：后到的一端在占用闸上被拒
            assert statuses == [200, 503], results
        for label, resp in results.items():
            if resp.status_code == 503:
                assert _detail(resp) == BUSY
        row = _db_session(session_id)
        assert row["state"] == "ended"
        assert row["end_reason"] in {"viewer_closed", "runtime_stopped"}
        assert row["signals"] == []
        # 被占用闸拒掉的那一端再发一次：幂等成功，会话不再改动
        for label, end in ends().items():
            if results[label].status_code == 503:
                assert end().status_code == 200, label
                assert _db_session(session_id) == row


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
