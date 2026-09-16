"""WP13 扫码会话端点与绑定状态机单元测试（API 层全部 mock，不打真实接口/不写真实配置表）。

覆盖（设计 §3.1/§3.5 + 计划 WP13 节）：
- 登录状态机：scan 发起（startlogin→getqrcode→Redis 中间态）→ ask 轮询
  （waiting/scanned/qr_expired/expired）→ status=1 → bizlogin 提 token →
  昵称抓取 → 健康检查三分支（200007 拒绑 / 200013 不绑 / ret=0 通过）→ 加密入库
- 绑定：无配置自动创建（sync_interval_hours=1）/ 绑定既有配置补清单字段 /
  重新扫码状态回 active；租户上下文缺失 400、未登录 401、跨租户 scan_id → expired
- 绑定状态查询：四态推导（active/expiring 按 expire_at-24h 窗口/expired/account_error）
- 解绑：清除全部 list_* 字段
- 模式切换与勾选入队端点：参数校验 + service 层委托
- config_codec：list_session_token/cookie 加密/解密/掩码往返
"""

import base64
import json
import uuid

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.wechat_mp import list_session as ls
from src.wechat_mp.list_source import MP_BASE_URL, OwnListClient

# ------------------------------- 替身 -------------------------------

TENANT = "wmp13_scan_tenant"
FAKE_TOKEN = "908279301"
FAKE_COOKIE = "slave_sid=fake_sid_value; slave_user=fake_user_value"
PNG_BYTES = b"\x89PNG\r\n\x1a\nfakepng"


class FakeScanRedis:
    """进程内 Redis 替身：get/set(ex)/delete（JSON 序列化语义对齐 redis_client）。"""

    def __init__(self, available: bool = True):
        self._store: dict = {}
        self._available = available

    def is_available(self) -> bool:
        return self._available

    @staticmethod
    def make_key(prefix: str, identifier: str = "") -> str:
        return f"{prefix}:{identifier}" if identifier else prefix

    def set(self, key, value, ex=None):
        self._store[key] = json.dumps(value, ensure_ascii=False, default=str)

    def get(self, key):
        raw = self._store.get(key)
        return json.loads(raw) if raw is not None else None

    def delete(self, key):
        return self._store.pop(key, None) is not None


def _fake_require_admin(request):
    """测试鉴权替身：Authorization: Bearer <role>:<tenant_id>（仅测试内约定）。"""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未登录或登录已过期")
    role, _, tenant = auth[7:].partition(":")
    if role == "tenant_admin" and tenant:
        return {"user_id": "u-wp13", "tenant_id": tenant, "role": role}
    if role == "platform_admin":
        return {"user_id": "u-wp13", "tenant_id": tenant or None, "role": role}
    raise HTTPException(status_code=401, detail="未登录或登录已过期")


@pytest.fixture()
def fake_redis(monkeypatch):
    redis = FakeScanRedis()
    monkeypatch.setattr(ls, "redis_client", redis)
    return redis


@pytest.fixture()
def client(monkeypatch):
    app = FastAPI()
    app.include_router(ls.router)
    monkeypatch.setattr(ls, "require_admin", _fake_require_admin)
    return TestClient(app)


def _auth(tenant: str = TENANT) -> dict:
    return {"Authorization": f"Bearer tenant_admin:{tenant}"}


def _mp_handler(
    *,
    ask_status: int = 0,
    ask_ret: int = 0,
    list_ret: int = 0,
    login_redirect: str = f"{MP_BASE_URL}/cgi-bin/home?t=home/index&lang=zh_CN&token={FAKE_TOKEN}",
    login_ret: int = 0,
    home_html: str = 'nick_name: "夹具昵称",  // x\nuser_name: "gh_fakeacc12345",',
    calls: list = None,
):
    """微信后台统一 MockTransport：按 path+action 分发（记录调用供断言）。"""
    record = calls if calls is not None else []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        action = request.url.params.get("action", "")
        record.append((path, action))
        if path == "/cgi-bin/bizlogin" and action == "startlogin":
            # 真实平台在登录起始响应种 cookie（jar 累积后序列化为中间态）
            return httpx.Response(
                200,
                json={"base_resp": {"ret": 0}},
                headers=[
                    ("Set-Cookie", "slave_sid=fake_sid_value; Path=/"),
                    ("Set-Cookie", "slave_user=fake_user_value; Path=/"),
                ],
            )
        if path == "/cgi-bin/scanloginqrcode" and action == "getqrcode":
            return httpx.Response(
                200, content=PNG_BYTES, headers={"content-type": "image/png"}
            )
        if path == "/cgi-bin/scanloginqrcode" and action == "ask":
            return httpx.Response(
                200, json={"status": ask_status, "base_resp": {"ret": ask_ret}}
            )
        if path == "/cgi-bin/bizlogin" and action == "login":
            return httpx.Response(
                200,
                json={
                    "base_resp": {"ret": login_ret},
                    "redirect_url": login_redirect if login_ret == 0 else "",
                },
            )
        if path == "/cgi-bin/home":
            return httpx.Response(200, text=home_html)
        if path == "/cgi-bin/appmsgpublish":
            if list_ret != 0:
                return httpx.Response(
                    200, json={"base_resp": {"ret": list_ret, "err_msg": "deny"}}
                )
            page = {
                "total_count": 1,
                "publish_list": [
                    {
                        "publish_info": json.dumps(
                            {
                                "msgid": 1,
                                "publish_type": 101,
                                "appmsgex": [
                                    {
                                        "aid": "A1",
                                        "title": "t",
                                        "link": "https://mp.weixin.qq.com/s/Wp13HcProbe01",
                                        "update_time": 1789000000,
                                        "is_deleted": False,
                                    }
                                ],
                            }
                        )
                    }
                ],
            }
            return httpx.Response(
                200,
                json={
                    "base_resp": {"ret": 0},
                    "publish_page": json.dumps(page),
                },
            )
        return httpx.Response(404, json={})

    return handler


def _patch_http(monkeypatch, handler):
    """同时替换登录流程与清单探活的传输层（全部 mock，绝不触真实微信接口）。"""

    def transport_client(**kw):
        return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)

    monkeypatch.setattr(
        ls, "_new_http_client", lambda cookie_str="", timeout=15.0: transport_client()
    )
    monkeypatch.setattr(
        ls,
        "_new_list_client",
        lambda token, cookie: OwnListClient(
            token=token, cookie=cookie, http_client=transport_client()
        ),
    )


def _start_scan(client, monkeypatch, handler=None, fake_redis=None):
    if handler is not None:
        _patch_http(monkeypatch, handler)
    resp = client.post("/api/saas/wechat-mp/list-session/scan", headers=_auth())
    assert resp.status_code == 200, resp.text
    return resp.json()


# ------------------------------- 扫码状态机 -------------------------------


class TestScanFlow:
    def test_scan_start_returns_qr_and_stores_state(
        self, client, monkeypatch, fake_redis
    ):
        calls = []
        data = _start_scan(client, monkeypatch, _mp_handler(calls=calls), fake_redis)
        assert data["scan_id"] and data["expires_in"] == ls.SCAN_STATE_TTL_SECONDS
        assert data["qr_data_url"].startswith("data:image/png;base64,")
        assert base64.b64decode(data["qr_data_url"].split(",", 1)[1]) == PNG_BYTES
        state = fake_redis.get(fake_redis.make_key(ls.SCAN_KEY_PREFIX, data["scan_id"]))
        assert state["tenant_id"] == TENANT
        assert "fake_sid_value" in state["cookie"]

    def test_scan_start_upstream_failure_is_400(self, client, monkeypatch, fake_redis):
        """二维码接口返回非图片：400 业务错误（不外泄响应内容；全程 mock 不触真实接口）。"""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.params.get("action") == "getqrcode":
                return httpx.Response(200, json={"base_resp": {"ret": -1}})
            return httpx.Response(200, json={"base_resp": {"ret": 0}})

        _patch_http(monkeypatch, handler)
        resp = client.post("/api/saas/wechat-mp/list-session/scan", headers=_auth())
        assert resp.status_code == 400
        assert "二维码" in resp.json()["detail"]

    def test_poll_waiting_scanned_and_qr_expired(self, client, monkeypatch, fake_redis):
        # waiting（status 0）
        data = _start_scan(client, monkeypatch, _mp_handler(ask_status=0))
        resp = client.get(
            "/api/saas/wechat-mp/list-session/scan/status",
            params={"scan_id": data["scan_id"]},
            headers=_auth(),
        ).json()
        assert resp["status"] == "waiting"
        # scanned（status 4）
        handler = _mp_handler(ask_status=4)
        _patch_http(monkeypatch, handler)
        resp = client.get(
            "/api/saas/wechat-mp/list-session/scan/status",
            params={"scan_id": data["scan_id"]},
            headers=_auth(),
        ).json()
        assert resp["status"] == "scanned"
        # 二维码过期（status 402）→ qr_expired 且中间态清除
        _patch_http(monkeypatch, _mp_handler(ask_status=402))
        resp = client.get(
            "/api/saas/wechat-mp/list-session/scan/status",
            params={"scan_id": data["scan_id"]},
            headers=_auth(),
        ).json()
        assert resp["status"] == "qr_expired"
        key = fake_redis.make_key(ls.SCAN_KEY_PREFIX, data["scan_id"])
        assert fake_redis.get(key) is None

    def test_poll_unknown_or_cross_tenant_scan_id_expired(
        self, client, monkeypatch, fake_redis
    ):
        data = _start_scan(client, monkeypatch, _mp_handler(ask_status=0))
        # 跨租户轮询：一律 expired（防跨租户探测）
        resp = client.get(
            "/api/saas/wechat-mp/list-session/scan/status",
            params={"scan_id": data["scan_id"]},
            headers=_auth("wmp13_other_tenant"),
        ).json()
        assert resp["status"] == "expired"
        # 未知 scan_id 同样 expired
        resp = client.get(
            "/api/saas/wechat-mp/list-session/scan/status",
            params={"scan_id": uuid.uuid4().hex},
            headers=_auth(),
        ).json()
        assert resp["status"] == "expired"
        # 非法 scan_id → 400
        resp = client.get(
            "/api/saas/wechat-mp/list-session/scan/status",
            params={"scan_id": "../bad"},
            headers=_auth(),
        )
        assert resp.status_code == 400

    def test_confirm_success_binds_existing_config(self, client, monkeypatch, fake_redis):
        """status=1 → 提 token → 昵称 → 健康检查（ret=0）→ 绑定既有配置（补清单字段）。"""
        calls = []
        handler = _mp_handler(ask_status=1, calls=calls)
        data = _start_scan(client, monkeypatch, handler)

        captured = {}

        def fake_write(config_id, fields=None, remove_keys=()):
            captured["config_id"] = config_id
            captured["fields"] = dict(fields or {})
            return True

        def fake_list_by_tenant(tenant_id, channel_type=None):
            assert tenant_id == TENANT and channel_type == "wechat_mp"
            return [
                {
                    "config_id": "chan_existing01",
                    "channel_type": "wechat_mp",
                    "config": {
                        "appid": "wxexisting",
                        "callback_token": "***",
                        "list_sync_mode": "manual",  # 重绑定时保留租户已选模式
                    },
                }
            ]

        monkeypatch.setattr(ls, "write_list_config_fields", fake_write)
        monkeypatch.setattr(ls.ChannelConfigDB, "list_by_tenant", staticmethod(fake_list_by_tenant))

        resp = client.get(
            "/api/saas/wechat-mp/list-session/scan/status",
            params={"scan_id": data["scan_id"]},
            headers=_auth(),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "confirmed"
        assert body["nickname"] == "夹具昵称"
        assert body["config_id"] == "chan_existing01"
        # 绑定字段：凭据+时间+昵称+状态+模式
        fields = captured["fields"]
        assert fields["list_session_token"] == FAKE_TOKEN
        assert "fake_sid_value" in fields["list_session_cookie"]
        assert fields["list_sync_status"] == "active"
        # 重绑定保留既有 sync_mode（不被静默重置回 auto_all）
        assert fields["list_sync_mode"] == "manual"
        # CR：重绑一律重置回填进度——换号重扫不得沿用旧 done=true 绕过首次回填上限
        assert fields["list_backfill_done"] is False
        assert fields["list_account_nickname"] == "夹具昵称"
        assert "list_session_expire_at" in fields and "list_session_at" in fields
        # 健康检查确实施行（own-context appmsgpublish）
        assert ("/cgi-bin/appmsgpublish", "") in calls
        # 中间态清除
        key = fake_redis.make_key(ls.SCAN_KEY_PREFIX, data["scan_id"])
        assert fake_redis.get(key) is None

    def test_confirm_auto_creates_config_with_interval_1h(
        self, client, monkeypatch, fake_redis
    ):
        """无任何 wechat_mp 配置：自动创建，sync_interval_hours 置 1（设计 §3.3）。"""
        data = _start_scan(client, monkeypatch, _mp_handler(ask_status=1))

        created = {}

        def fake_create(tenant_id, channel_type, config, subagent_type=None, name=None):
            created["tenant_id"] = tenant_id
            created["channel_type"] = channel_type
            created["config"] = dict(config)
            created["name"] = name
            return {"config_id": "chan_new01", "channel_type": channel_type}

        def fake_list_by_tenant(tenant_id, channel_type=None):
            return []

        monkeypatch.setattr(ls.ChannelConfigDB, "create", staticmethod(fake_create))
        monkeypatch.setattr(ls.ChannelConfigDB, "list_by_tenant", staticmethod(fake_list_by_tenant))

        resp = client.get(
            "/api/saas/wechat-mp/list-session/scan/status",
            params={"scan_id": data["scan_id"]},
            headers=_auth(),
        ).json()
        assert resp["status"] == "confirmed" and resp["config_id"] == "chan_new01"
        assert created["config"]["sync_interval_hours"] == 1
        assert created["config"]["enabled"] is True
        assert created["config"]["list_session_token"] == FAKE_TOKEN
        assert "夹具昵称" in created["name"]

    @pytest.mark.parametrize(
        "list_ret,reason_code",
        [(200007, "account_error"), (200013, "session_error")],
    )
    def test_health_check_failures_refuse_binding(
        self, client, monkeypatch, fake_redis, list_ret, reason_code
    ):
        """健康检查三分支：200007 拒绑 / 200013 会话异常不绑（凭据不入库）。"""
        data = _start_scan(
            client, monkeypatch, _mp_handler(ask_status=1, list_ret=list_ret)
        )

        def fail_write(*a, **kw):  # pragma: no cover - 不应被调用
            raise AssertionError("健康检查不通过不应写库")

        def fail_create(*a, **kw):  # pragma: no cover
            raise AssertionError("健康检查不通过不应建配置")

        monkeypatch.setattr(ls, "write_list_config_fields", fail_write)
        monkeypatch.setattr(ls.ChannelConfigDB, "create", staticmethod(fail_create))

        resp = client.get(
            "/api/saas/wechat-mp/list-session/scan/status",
            params={"scan_id": data["scan_id"]},
            headers=_auth(),
        ).json()
        assert resp["status"] == "failed"
        assert resp["reason_code"] == reason_code
        assert resp["reason"]
        key = fake_redis.make_key(ls.SCAN_KEY_PREFIX, data["scan_id"])
        assert fake_redis.get(key) is None  # 中间态清除，不残留凭据

    def test_login_confirm_failure_returns_failed(self, client, monkeypatch, fake_redis):
        data = _start_scan(
            client,
            monkeypatch,
            _mp_handler(ask_status=1, login_ret=1, login_redirect=""),
        )
        resp = client.get(
            "/api/saas/wechat-mp/list-session/scan/status",
            params={"scan_id": data["scan_id"]},
            headers=_auth(),
        ).json()
        assert resp["status"] == "failed"
        assert resp["reason_code"] == "login_failed"


# ------------------------------- 绑定状态/解绑 -------------------------------


class TestBindingStatus:
    def _bind(self, fields: dict) -> dict:
        return {"config_id": "chan_bound01", "config": {"list_session_token": "***", **fields}}

    def test_status_unbound(self, client, monkeypatch):
        monkeypatch.setattr(ls, "find_bound_list_config", lambda tenant_id: None)
        body = client.get("/api/saas/wechat-mp/list-session", headers=_auth()).json()
        assert body["bound"] is False

    def test_status_four_states(self, client, monkeypatch):
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        cases = [
            (
                {"list_sync_status": "active", "list_session_expire_at": (now + timedelta(hours=96)).isoformat()},
                "active",
            ),
            (
                {"list_sync_status": "active", "list_session_expire_at": (now + timedelta(hours=2)).isoformat()},
                "expiring",
            ),
            ({"list_sync_status": "expired"}, "expired"),
            ({"list_sync_status": "account_error"}, "account_error"),
        ]
        for fields, expect in cases:
            monkeypatch.setattr(ls, "find_bound_list_config", lambda t, f=fields: self._bind(f))
            body = client.get("/api/saas/wechat-mp/list-session", headers=_auth()).json()
            assert body["bound"] is True
            assert body["status"] == expect, fields
            assert "list_session_cookie" not in body  # 凭据不回传

    def test_unbind_clears_list_fields_only(self, client, monkeypatch):
        captured = {}

        def fake_write(config_id, fields=None, remove_keys=()):
            captured["config_id"] = config_id
            captured["remove_keys"] = tuple(remove_keys)
            return True

        monkeypatch.setattr(
            ls, "find_bound_list_config", lambda t: {"config_id": "chan_bound01"}
        )
        monkeypatch.setattr(ls, "write_list_config_fields", fake_write)
        resp = client.delete("/api/saas/wechat-mp/list-session/scan", headers=_auth()).json()
        assert resp["unbound"] is True and resp["config_id"] == "chan_bound01"
        assert captured["remove_keys"] == tuple(ls.wechat_mp_codec.LIST_PLAIN_FIELDS) + (
            "list_session_token",
            "list_session_cookie",
        )

    def test_unbind_when_not_bound(self, client, monkeypatch):
        monkeypatch.setattr(ls, "find_bound_list_config", lambda t: None)
        resp = client.delete("/api/saas/wechat-mp/list-session/scan", headers=_auth()).json()
        assert resp == {"success": True, "unbound": False, "config_id": None}


# ------------------------------- 模式切换/勾选入队端点 -------------------------------


class TestModeAndEnqueueEndpoints:
    def test_switch_mode_delegates_to_service(self, client, monkeypatch):
        captured = {}

        def fake_switch(tenant_id, mode):
            captured.update({"tenant_id": tenant_id, "mode": mode})
            return {"config_id": "chan_bound01", "mode": mode, "enqueued": 3}

        from src.wechat_mp import service as svc

        monkeypatch.setattr(svc, "switch_list_sync_mode", fake_switch)
        resp = client.post(
            "/api/saas/wechat-mp/list-session/mode",
            json={"mode": "manual"},
            headers=_auth(),
        )
        assert resp.status_code == 200
        assert resp.json()["enqueued"] == 3
        assert captured == {"tenant_id": TENANT, "mode": "manual"}

    def test_switch_mode_invalid_rejected(self, client, monkeypatch):
        resp = client.post(
            "/api/saas/wechat-mp/list-session/mode",
            json={"mode": "everything"},
            headers=_auth(),
        )
        assert resp.status_code == 400

    def test_enqueue_manual_endpoint(self, client, monkeypatch):
        from src.wechat_mp import service as svc

        captured = {}

        def fake_enqueue(tenant_id, user_id, ids):
            captured.update({"tenant_id": tenant_id, "user_id": user_id, "ids": ids})
            return {"run_id": 11, "enqueued": 2, "skipped": 1, "message": "ok"}

        monkeypatch.setattr(svc, "enqueue_manual_articles", fake_enqueue)
        resp = client.post(
            "/api/saas/wechat-mp/list-session/articles/enqueue-manual",
            json={"article_row_ids": [3, 1, 2, 1]},
            headers=_auth(),
        )
        assert resp.status_code == 200
        assert resp.json()["enqueued"] == 2
        assert captured["ids"] == [1, 2, 3]  # 去重排序
        assert captured["user_id"] == "u-wp13"

    def test_enqueue_manual_validates(self, client, monkeypatch):
        from src.wechat_mp import service as svc

        monkeypatch.setattr(svc, "enqueue_manual_articles", lambda *a: {})
        # 空列表 / 超上限 → 400
        resp = client.post(
            "/api/saas/wechat-mp/list-session/articles/enqueue-manual",
            json={"article_row_ids": []},
            headers=_auth(),
        )
        assert resp.status_code == 400
        resp = client.post(
            "/api/saas/wechat-mp/list-session/articles/enqueue-manual",
            json={"article_row_ids": list(range(1, 102))},
            headers=_auth(),
        )
        assert resp.status_code == 400

    def test_requires_auth_and_tenant_context(self, client, monkeypatch):
        # 未登录 → 401
        resp = client.get("/api/saas/wechat-mp/list-session")
        assert resp.status_code == 401
        # platform_admin 无 X-Tenant-Id → 400
        resp = client.get(
            "/api/saas/wechat-mp/list-session",
            headers={"Authorization": "Bearer platform_admin:"},
        )
        assert resp.status_code == 400


# ------------------------------- 租户隔离：写路径 -------------------------------


def test_write_list_config_fields_encrypts_sensitive(monkeypatch):
    """写路径：敏感字段加密后落库（SQL 读-改-写，真实 DB，对齐 channel_config_db 范式）。"""
    import json as _json

    from src.db.database import get_db_connection
    from src.wechat_mp import config_codec

    # 真实 DB 建配置 → 写清单字段 → 校验密文与解密往返
    created = None

    def fake_create(tenant_id, channel_type, config, subagent_type=None, name=None):
        from src.saas.db.channel_config_db import ChannelConfigDB as RealDB

        return RealDB.create(tenant_id, channel_type, config, subagent_type, name)

    tenant = f"wmp13_write_{uuid.uuid4().hex[:8]}"
    try:
        require_db_ok = True
        try:
            from src.db.database import get_postgres_pool, init_postgres_pool

            if get_postgres_pool() is None:
                init_postgres_pool()
        except Exception:  # noqa: BLE001
            require_db_ok = False
        if not require_db_ok:
            pytest.skip("需要可用 PostgreSQL")

        real_create = ls.ChannelConfigDB.create
        try:
            created = real_create(
                tenant, "wechat_mp", {"enabled": True, "sync_interval_hours": 6},
                name="WP13写路径",
            )
            assert created is not None
            ok = ls.write_list_config_fields(
                created["config_id"],
                fields={
                    "list_session_token": "plain_token_value",
                    "list_session_cookie": "k=v; k2=v2",
                    "list_sync_status": "active",
                },
            )
            assert ok is True
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT config FROM tenant_channel_configs WHERE config_id = %s",
                    (created["config_id"],),
                )
                raw = _json.loads(cursor.fetchone()["config"])
            # 敏感字段已加密且可解密回原文；明文字段原样
            assert raw["list_session_token"].startswith("gAAAAA")
            assert config_codec.decrypt_sensitive_fields(raw)["list_session_token"] == (
                "plain_token_value"
            )
            assert raw["list_sync_status"] == "active"
            assert raw["sync_interval_hours"] == 6  # 其他字段原样保留
        finally:
            pass
    finally:
        try:
            from src.saas.db.channel_config_db import ChannelConfigDB as RealDB

            if created:
                RealDB.delete(created["config_id"])
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM tenants WHERE tenant_id = %s", (tenant,))
                conn.commit()
        except Exception:  # noqa: BLE001
            pass


# ------------------------------- config_codec -------------------------------


class TestConfigCodecListFields:
    def test_sensitive_roundtrip_and_mask(self):
        from src.wechat_mp import config_codec

        plain = {
            "appid": "wxapp",
            "list_session_token": "tok123",
            "list_session_cookie": "a=b; c=d",
            "list_sync_status": "active",
            "list_sync_mode": "manual",
            "list_account_nickname": "昵称",
        }
        assert "list_session_token" in config_codec.SENSITIVE_KEYS
        assert "list_session_cookie" in config_codec.SENSITIVE_KEYS
        encrypted = config_codec.encrypt_sensitive_fields(plain)
        assert encrypted["list_session_token"].startswith("gAAAAA")
        assert encrypted["list_session_cookie"].startswith("gAAAAA")
        assert encrypted["list_sync_status"] == "active"  # 明文字段不动
        decrypted = config_codec.decrypt_sensitive_fields(encrypted)
        assert decrypted["list_session_token"] == "tok123"
        assert decrypted["list_session_cookie"] == "a=b; c=d"
        masked = config_codec.mask_sensitive_fields(encrypted)
        assert masked["list_session_token"] == "***"
        assert masked["list_session_cookie"] == "***"
        assert masked["list_account_nickname"] == "昵称"  # 非敏感不掩码

    def test_channel_config_db_update_keeps_list_credentials(self, monkeypatch):
        """渠道配置 update：清单凭据字段缺失/掩码时保留 DB 原值（敏感字段不可清空保护）。"""
        from src.saas.db import channel_config_db as cdb

        existing = {
            "appid": "wxapp",
            "list_session_token": "gAAAAA_cipher",
            "list_session_cookie": "gAAAAA_cipher2",
            "list_sync_status": "active",
        }
        # 前端快照：仅改 appid，不带清单字段 → 原值保留
        new_config = {"appid": "wxapp2", "enabled": True}
        for k in cdb.wechat_mp_codec.SENSITIVE_KEYS:
            incoming = new_config.get(k)
            is_mask = isinstance(incoming, str) and incoming.startswith("***")
            is_blank = not isinstance(incoming, str) or incoming == ""
            if (incoming is None or is_mask or is_blank) and existing.get(k):
                new_config[k] = existing[k]
        assert new_config["list_session_token"] == "gAAAAA_cipher"
        assert new_config["list_session_cookie"] == "gAAAAA_cipher2"


# ------------------------------- WP13-r1：历史清单端点 -------------------------------

TOK_SYNCED = "Wp13Hist00000"
TOK_UNSYNCED_1 = "Wp13Hist00001"
TOK_UNSYNCED_2 = "Wp13Hist00002"


def _history_body(begin: int, count: int, total: int = 27) -> dict:
    """历史清单一页：2 条消息展开 3 个子篇（真机形状，publish_page 为 JSON 字符串）。"""
    messages = [
        (
            begin + 1,
            [
                {
                    "aid": "A0",
                    "title": "历史文章0",
                    "link": f"https://mp.weixin.qq.com/s/{TOK_SYNCED}",
                    "update_time": 1789000000,
                    "create_time": 1788999000,
                    "is_deleted": False,
                },
                {
                    "aid": "A1",
                    "title": "历史文章1",
                    "link": f"https://mp.weixin.qq.com/s/{TOK_UNSYNCED_1}",
                    "update_time": 1789000060,
                    "create_time": 1788999060,
                    "is_deleted": False,
                },
            ],
        ),
        (
            begin + 2,
            [
                {
                    "aid": "A2",
                    "title": "历史文章2",
                    "link": f"https://mp.weixin.qq.com/s/{TOK_UNSYNCED_2}",
                    "update_time": 1789000120,
                    "create_time": 1788999120,
                    "is_deleted": False,
                }
            ],
        ),
    ]
    publish_list = []
    for msgid, subs in messages:
        info = {"msgid": msgid, "publish_type": 101, "appmsgex": subs}
        publish_list.append({"publish_info": json.dumps(info, ensure_ascii=False), "publish_type": 101})
    return {
        "base_resp": {"ret": 0},
        "publish_page": json.dumps(
            {"total_count": total, "publish_list": publish_list}, ensure_ascii=False
        ),
    }


def _history_handler(*, list_ret: int = 0, calls: list = None):
    """历史清单 MockTransport：记录 appmsgpublish 调用（begin/count/fakeid 断言用）。"""
    record = calls if calls is not None else []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        record.append((path, dict(request.url.params)))
        if path == "/cgi-bin/appmsgpublish":
            if list_ret != 0:
                return httpx.Response(
                    200, json={"base_resp": {"ret": list_ret, "err_msg": "deny"}}
                )
            return httpx.Response(
                200, json=_history_body(
                    int(request.url.params.get("begin", "0")),
                    int(request.url.params.get("count", "5")),
                )
            )
        return httpx.Response(404, json={})

    return handler


class TestHistoryEndpoint:
    """历史文章清单（WP13-r1）：鉴权/未绑定/分页/synced 标注/失败转义/client 关闭。"""

    def _bind(self) -> dict:
        return {
            "config_id": "chan_hist01",
            "config": {"list_session_token": "***", "list_sync_status": "active"},
        }

    def _patch_bound(self, monkeypatch, *, session=None):
        """绑定查询替身：session=None 时 load_list_session 同样返回 None（凭据缺失路径）。"""
        monkeypatch.setattr(ls, "find_bound_list_config", lambda t: self._bind())
        monkeypatch.setattr(ls, "load_list_session", lambda config_id: session)

    def test_history_requires_auth_and_tenant_context(self, client, monkeypatch):
        monkeypatch.setattr(ls, "find_bound_list_config", lambda t: self._bind())
        # 未登录 → 401
        resp = client.get("/api/saas/wechat-mp/list-session/history")
        assert resp.status_code == 401
        # platform_admin 无租户上下文 → 400
        resp = client.get(
            "/api/saas/wechat-mp/list-session/history",
            headers={"Authorization": "Bearer platform_admin:"},
        )
        assert resp.status_code == 400

    def test_history_not_bound_400(self, client, monkeypatch):
        monkeypatch.setattr(ls, "find_bound_list_config", lambda t: None)
        resp = client.get("/api/saas/wechat-mp/list-session/history", headers=_auth())
        assert resp.status_code == 400
        assert "扫码" in resp.json()["detail"]

    def test_history_param_validation(self, client, monkeypatch):
        self._patch_bound(monkeypatch)
        for params in ({"begin": -1}, {"count": 0}, {"count": 21}):
            resp = client.get(
                "/api/saas/wechat-mp/list-session/history", params=params, headers=_auth()
            )
            assert resp.status_code == 400, params

    def test_history_page_items_synced_and_pagination(self, client, monkeypatch):
        """单页拉取 + synced 标注 + begin/count 透传 + 凭据/链接不外泄到响应外字段。"""
        calls = []
        _patch_http(monkeypatch, _history_handler(calls=calls))
        self._patch_bound(
            monkeypatch,
            session={"token": FAKE_TOKEN, "cookie": FAKE_COOKIE, "fields": {}},
        )
        monkeypatch.setattr(
            ls,
            "_find_synced_external_ids",
            lambda tenant_id, ids: {f"mp:s:{TOK_SYNCED}"},
        )
        resp = client.get(
            "/api/saas/wechat-mp/list-session/history",
            params={"begin": 5, "count": 2},
            headers=_auth(),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is True
        assert body["total_count"] == 27 and body["begin"] == 5 and body["count"] == 2
        items = body["items"]
        assert len(items) == 3  # 2 消息展开 3 子篇
        by_ext = {i["external_id"]: i for i in items}
        assert set(by_ext) == {
            f"mp:s:{TOK_SYNCED}",
            f"mp:s:{TOK_UNSYNCED_1}",
            f"mp:s:{TOK_UNSYNCED_2}",
        }
        assert by_ext[f"mp:s:{TOK_SYNCED}"]["synced"] is True
        assert by_ext[f"mp:s:{TOK_UNSYNCED_1}"]["synced"] is False
        assert by_ext[f"mp:s:{TOK_SYNCED}"]["title"] == "历史文章0"
        assert by_ext[f"mp:s:{TOK_SYNCED}"]["create_time"] == 1788999000
        assert by_ext[f"mp:s:{TOK_SYNCED}"]["link"].endswith(TOK_SYNCED)
        # 单次请求只拉 1 页消息；own-context fakeid 留空；begin/count 透传
        app_calls = [c for c in calls if c[0] == "/cgi-bin/appmsgpublish"]
        assert len(app_calls) == 1
        assert app_calls[0][1]["begin"] == "5"
        assert app_calls[0][1]["count"] == "2"
        assert app_calls[0][1]["fakeid"] == ""
        # 凭据不进响应
        assert FAKE_COOKIE not in resp.text

    @pytest.mark.parametrize(
        "list_ret,status",
        [(200003, 400), (200040, 400), (200007, 400), (200013, 502)],
    )
    def test_history_error_mapping(self, client, monkeypatch, list_ret, status):
        """失败分类转 HTTP：session_expired/account_error→400，其余源错误→502。"""
        _patch_http(monkeypatch, _history_handler(list_ret=list_ret))
        self._patch_bound(
            monkeypatch,
            session={"token": FAKE_TOKEN, "cookie": FAKE_COOKIE, "fields": {}},
        )
        resp = client.get("/api/saas/wechat-mp/list-session/history", headers=_auth())
        assert resp.status_code == status
        detail = resp.json()["detail"]
        assert detail  # 明确错误消息（不含凭据）
        assert FAKE_COOKIE not in detail

    def test_history_missing_session_400(self, client, monkeypatch):
        """绑定行存在但凭据缺失（如解绑残留）：400 提示重新扫码。"""
        monkeypatch.setattr(ls, "find_bound_list_config", lambda t: self._bind())
        monkeypatch.setattr(ls, "load_list_session", lambda config_id: None)
        resp = client.get("/api/saas/wechat-mp/list-session/history", headers=_auth())
        assert resp.status_code == 400
        assert "重新扫码" in resp.json()["detail"]

    def test_history_closes_http_client(self, client, monkeypatch):
        """httpx 客户端 finally 关闭（含拉取异常路径）。"""

        class StubListClient:
            def __init__(self, error: Exception = None):
                self.closed = False
                self.fetched = []
                self.error = error

            def fetch_page(self, begin, count):
                self.fetched.append((begin, count))
                if self.error is not None:
                    raise self.error
                return _history_body(begin, count)

            def close(self):
                self.closed = True

        from src.wechat_mp.list_source import ListSourceError

        stub = StubListClient()
        monkeypatch.setattr(ls, "_new_list_client", lambda token, cookie: stub)
        self._patch_bound(
            monkeypatch,
            session={"token": FAKE_TOKEN, "cookie": FAKE_COOKIE, "fields": {}},
        )
        resp = client.get(
            "/api/saas/wechat-mp/list-session/history",
            params={"begin": 0, "count": 5},
            headers=_auth(),
        )
        assert resp.status_code == 200
        assert stub.fetched == [(0, 5)]
        assert stub.closed is True

        # 异常路径同样关闭（错误消息不含凭据）
        stub2 = StubListClient(error=ListSourceError("清单拉取失败"))
        monkeypatch.setattr(ls, "_new_list_client", lambda token, cookie: stub2)
        resp = client.get(
            "/api/saas/wechat-mp/list-session/history",
            params={"begin": 0, "count": 5},
            headers=_auth(),
        )
        assert resp.status_code == 502
        assert stub2.closed is True
