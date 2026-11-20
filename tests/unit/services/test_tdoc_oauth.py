"""腾讯文档 OAuth 授权流单测（HTTP/Redis/DB 全打桩，不连外部服务）

覆盖：authorize URL 构造 / token 响应解析与错误码映射 / 一次性 state（GETDEL 语义）/
OAuth 凭据落库 / 懒刷新（提前 24h）/ 37017 重授权 / 瞬时失败沿用旧 token / 回调路由 302。
"""

import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest

import src.services.tencent_docs.credentials as cred_mod
import src.services.tencent_docs.oauth as oauth_mod
from src.services.tencent_docs.credentials import (
    CredentialsNotBoundError,
    get_credentials_with_refresh,
    save_oauth_credentials,
)
from src.services.tencent_docs.oauth import (
    OAuthError,
    OAuthTokenResult,
    build_authorize_url,
    consume_oauth_state,
    create_oauth_state,
    exchange_code,
    get_userinfo,
    refresh_access_token,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def oauth_cfg(monkeypatch):
    """注入测试 OAuth 应用配置（client_secret 不落仓库，仅测试内存）。"""
    monkeypatch.setattr(oauth_mod.settings.tencent_docs, "oauth_client_id", "cid-test-1234", raising=False)
    monkeypatch.setattr(oauth_mod.settings.tencent_docs, "oauth_client_secret", "cs-test-secret", raising=False)
    monkeypatch.setattr(
        oauth_mod.settings.tencent_docs,
        "oauth_redirect_uri",
        "https://agent.aidingyi.cn/api/knowledge/tdoc/oauth/callback",
        raising=False,
    )


def _mock_transport(handler):
    return httpx.MockTransport(handler)


# ------------------------------- authorize URL -------------------------------


@pytest.mark.unit
class TestAuthorizeUrl:
    def test_url_contains_required_params(self, oauth_cfg):
        url = build_authorize_url("state123")
        assert url.startswith("https://docs.qq.com/oauth/v2/authorize?")
        assert "client_id=cid-test-1234" in url
        assert "response_type=code" in url
        assert "scope=all" in url
        assert "new_login=1" in url
        assert "state=state123" in url
        # redirect_uri URL Encode 且不含裸 https://
        assert "redirect_uri=https%3A%2F%2Fagent.aidingyi.cn" in url

    def test_frontend_base_url_from_redirect_uri(self, oauth_cfg):
        from src.services.tencent_docs.oauth import frontend_base_url
        assert frontend_base_url() == "https://agent.aidingyi.cn"

    def test_is_oauth_configured(self, oauth_cfg, monkeypatch):
        from src.services.tencent_docs.oauth import is_oauth_configured
        assert is_oauth_configured() is True
        monkeypatch.setattr(oauth_mod.settings.tencent_docs, "oauth_client_secret", "", raising=False)
        assert is_oauth_configured() is False


# ------------------------------- token 响应解析 -------------------------------


@pytest.mark.unit
class TestTokenResponseParsing:
    def test_exchange_code_success(self, oauth_cfg):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.params["grant_type"] == "authorization_code"
            assert request.url.params["code"] == "CODE1"
            assert request.url.params["client_secret"] == "cs-test-secret"
            return httpx.Response(200, json={
                "access_token": "AT1", "refresh_token": "RT1",
                "expires_in": 259200, "user_id": "oid1", "scope": "scope.doc.readonly",
            })

        result = _run(exchange_code("CODE1", transport=_mock_transport(handler)))
        assert result.access_token == "AT1"
        assert result.refresh_token == "RT1"
        assert result.expires_in == 259200
        assert result.open_id == "oid1"

    def test_refresh_no_new_refresh_token(self, oauth_cfg):
        """刷新响应不含 refresh_token -> 结果 refresh_token 为空（沿用旧值语义）。"""
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.params["grant_type"] == "refresh_token"
            return httpx.Response(200, json={"access_token": "AT2", "expires_in": 259200, "user_id": "oid1"})

        result = _run(refresh_access_token("RT-OLD", transport=_mock_transport(handler)))
        assert result.access_token == "AT2"
        assert result.refresh_token == ""

    def test_error_37017_requires_reauth(self, oauth_cfg):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"code": 37017, "message": "refresh token invalid"})

        with pytest.raises(OAuthError) as exc:
            _run(refresh_access_token("BAD", transport=_mock_transport(handler)))
        assert exc.value.reauthorization_required is True

    def test_error_37019_refreshable(self, oauth_cfg):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"code": 37019, "message": "token expired"})

        with pytest.raises(OAuthError) as exc:
            _run(refresh_access_token("RT", transport=_mock_transport(handler)))
        assert exc.value.reauthorization_required is False

    def test_http_error_non_json(self, oauth_cfg):
        """redirect_uri 不一致等错误 HTTP 401，body 非 JSON -> OAuthError 不崩。"""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, text="Unauthorized")

        with pytest.raises(OAuthError):
            _run(exchange_code("C", transport=_mock_transport(handler)))

    def test_userinfo_business_failure_returns_empty(self, oauth_cfg):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"ret": 10000, "msg": "not oauth token"})

        assert _run(get_userinfo("AT", transport=_mock_transport(handler))) == {}

    def test_userinfo_success(self, oauth_cfg):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"ret": 0, "data": {"openID": "o1", "nick": "张三", "unionID": "u1"}})

        data = _run(get_userinfo("AT", transport=_mock_transport(handler)))
        assert data["nick"] == "张三"
        assert data["unionID"] == "u1"

    def test_userinfo_network_failure_returns_empty(self, oauth_cfg):
        """userinfo 网络异常吞掉返回空 dict（不阻断授权落库，code 一次性）。"""
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        assert _run(get_userinfo("AT", transport=_mock_transport(handler))) == {}


def _run(coro):
    import asyncio
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


# ------------------------------- 一次性 state -------------------------------


@pytest.mark.unit
class TestOAuthState:
    @pytest.fixture
    def fake_redis(self, monkeypatch):
        store = {}

        class FakeRedis:
            def set(self, key, value, ex=None):
                store[key] = value

            def getdel(self, key):
                return store.pop(key, None)

            def is_available(self):
                return True

        monkeypatch.setattr(oauth_mod, "redis_client", FakeRedis())
        return store

    def test_create_and_consume(self, fake_redis):
        state = create_oauth_state("t1", "u1")
        payload = consume_oauth_state(state)
        assert payload == {"tenant_id": "t1", "user_id": "u1"}

    def test_consume_is_one_time(self, fake_redis):
        """GETDEL 语义：二次消费同一 state 返回 None（防重放）。"""
        state = create_oauth_state("t1", "u1")
        assert consume_oauth_state(state) is not None
        assert consume_oauth_state(state) is None

    def test_consume_invalid_state(self, fake_redis):
        assert consume_oauth_state("no-such-state") is None
        assert consume_oauth_state("") is None

    def test_consume_corrupt_payload(self, fake_redis):
        fake_redis[oauth_mod._state_key("corrupt")] = "not-json"
        assert consume_oauth_state("corrupt") is None


# ------------------------------- 凭据落库与懒刷新 -------------------------------


class FakeCredDB:
    def __init__(self):
        self.store = {}

    def upsert(self, tenant_id, user_id, record):
        self.store[(tenant_id, user_id)] = {"status": "active", **record}

    def get(self, tenant_id, user_id):
        return self.store.get((tenant_id, user_id))

    def delete(self, tenant_id, user_id):
        return self.store.pop((tenant_id, user_id), None) is not None

    def mark_expired(self, tenant_id, user_id):
        row = self.store.get((tenant_id, user_id))
        if row:
            row["status"] = "expired"

    def update_access_token(self, tenant_id, user_id, access_token_enc, expires_at):
        row = self.store.get((tenant_id, user_id))
        if row:
            row["access_token_enc"] = access_token_enc
            row["expires_at"] = expires_at


@pytest.fixture
def cred_env(monkeypatch):
    monkeypatch.setattr(cred_mod, "TdocCredentialDB", FakeCredDB())
    monkeypatch.setenv("APP_SECRET_KEY", "test-master-key-for-tdoc-oauth")
    db = cred_mod.TdocCredentialDB
    refresh_mock = AsyncMock(return_value=OAuthTokenResult(
        access_token="AT-NEW", expires_in=259200, open_id="oid-1",
    ))
    monkeypatch.setattr(cred_mod, "refresh_access_token", refresh_mock)
    monkeypatch.setattr(cred_mod, "_oauth_client_id", lambda: "cid-oauth-app")
    return db, refresh_mock


def _save_oauth_row(db, expires_in_seconds=259200, refresh_expires_in_days=365):
    _run(save_oauth_credentials(
        "t1", "u1",
        OAuthTokenResult(access_token="AT-OLD", expires_in=expires_in_seconds,
                         open_id="oid-1", refresh_token="RT-1", scope="scope.doc.readonly"),
        userinfo={"nick": "张三", "unionID": "u-union"},
    ))


@pytest.mark.unit
class TestSaveOAuthCredentials:
    def test_save_and_decrypt(self, cred_env):
        db, _ = cred_env
        _save_oauth_row(db)
        row = db.store[("t1", "u1")]
        assert row["auth_type"] == "oauth"
        assert row["nick"] == "张三"
        assert row["union_id"] == "u-union"
        assert row["expires_at"] > datetime.now()
        assert row["refresh_expires_at"] > datetime.now() + timedelta(days=360)
        # 明文不落库
        assert "AT-OLD" not in str(row)

    def test_personal_binding_still_works(self, cred_env):
        db, _ = cred_env
        cred_mod.save_credentials("t1", "u2", "cid", "PT", "oid")
        row = db.store[("t1", "u2")]
        assert row["auth_type"] == "personal"


@pytest.mark.unit
class TestLazyRefresh:
    def test_personal_type_never_refreshes(self, cred_env):
        db, refresh_mock = cred_env
        cred_mod.save_credentials("t1", "u2", "cid", "PT", "oid")
        creds = _run(get_credentials_with_refresh("t1", "u2"))
        assert creds.access_token == "PT"
        refresh_mock.assert_not_awaited()

    def test_fresh_oauth_token_no_refresh(self, cred_env):
        db, refresh_mock = cred_env
        _save_oauth_row(db, expires_in_seconds=259200)  # 3 天后过期 > 24h 阈值
        creds = _run(get_credentials_with_refresh("t1", "u1"))
        assert creds.access_token == "AT-OLD"
        refresh_mock.assert_not_awaited()

    def test_near_expiry_triggers_refresh_and_persists(self, cred_env):
        db, refresh_mock = cred_env
        _save_oauth_row(db, expires_in_seconds=3600)  # 1h 后过期 < 24h
        creds = _run(get_credentials_with_refresh("t1", "u1"))
        refresh_mock.assert_awaited_once()
        assert creds.access_token == "AT-NEW"
        row = db.store[("t1", "u1")]
        # 新 token 已落库且 > 24h
        assert row["expires_at"] > datetime.now() + timedelta(hours=24)
        # refresh_token 沿用旧值（官方刷新不换新）
        from src.core.secret_crypto import decrypt_secret
        assert decrypt_secret(row["refresh_token_enc"]).decode() == "RT-1"

    def test_expired_token_refreshes(self, cred_env):
        db, _ = cred_env
        _save_oauth_row(db)
        db.store[("t1", "u1")]["expires_at"] = datetime.now() - timedelta(hours=1)
        creds = _run(get_credentials_with_refresh("t1", "u1"))
        assert creds.access_token == "AT-NEW"

    def test_refresh_37017_marks_expired(self, cred_env):
        db, refresh_mock = cred_env
        _save_oauth_row(db, expires_in_seconds=3600)
        refresh_mock.side_effect = OAuthError(37017, "refresh token invalid", reauthorization_required=True)
        with pytest.raises(CredentialsNotBoundError):
            _run(get_credentials_with_refresh("t1", "u1"))
        assert db.store[("t1", "u1")]["status"] == "expired"

    def test_refresh_token_lifetime_expired_marks_expired(self, cred_env):
        """refresh token 超 1 年（access token 同时进入刷新窗口）-> 不发起刷新直接标记失效。"""
        db, refresh_mock = cred_env
        _save_oauth_row(db, expires_in_seconds=3600)
        db.store[("t1", "u1")]["refresh_expires_at"] = datetime.now() - timedelta(days=1)
        with pytest.raises(CredentialsNotBoundError):
            _run(get_credentials_with_refresh("t1", "u1"))
        refresh_mock.assert_not_awaited()
        assert db.store[("t1", "u1")]["status"] == "expired"

    def test_transient_refresh_failure_keeps_valid_old_token(self, cred_env):
        """瞬时失败（如网络错）且旧 token 仍有效 -> 沿用旧 token，不报错。"""
        db, refresh_mock = cred_env
        _save_oauth_row(db, expires_in_seconds=3600)
        refresh_mock.side_effect = OAuthError(-1, "network error")
        creds = _run(get_credentials_with_refresh("t1", "u1"))
        assert creds.access_token == "AT-OLD"

    def test_transient_refresh_failure_with_expired_old_token_raises(self, cred_env):
        db, refresh_mock = cred_env
        _save_oauth_row(db)
        db.store[("t1", "u1")]["expires_at"] = datetime.now() - timedelta(hours=1)
        refresh_mock.side_effect = OAuthError(-1, "network error")
        with pytest.raises(CredentialsNotBoundError):
            _run(get_credentials_with_refresh("t1", "u1"))

    def test_httpx_network_error_keeps_valid_old_token(self, cred_env):
        """刷新时网络异常（未到 OAuthError）且旧 token 仍有效 -> 沿用旧 token。"""
        import httpx as _httpx
        db, refresh_mock = cred_env
        _save_oauth_row(db, expires_in_seconds=3600)
        refresh_mock.side_effect = _httpx.ConnectError("connection refused")
        creds = _run(get_credentials_with_refresh("t1", "u1"))
        assert creds.access_token == "AT-OLD"

    def test_httpx_network_error_with_expired_old_token_raises(self, cred_env):
        import httpx as _httpx
        db, refresh_mock = cred_env
        _save_oauth_row(db)
        db.store[("t1", "u1")]["expires_at"] = datetime.now() - timedelta(hours=1)
        refresh_mock.side_effect = _httpx.ReadTimeout("timeout")
        with pytest.raises(CredentialsNotBoundError):
            _run(get_credentials_with_refresh("t1", "u1"))

    def test_not_bound_raises(self, cred_env):
        with pytest.raises(CredentialsNotBoundError):
            _run(get_credentials_with_refresh("t1", "nobody"))


# ------------------------------- 回调路由 -------------------------------


@pytest.mark.unit
class TestOAuthCallbackRoute:
    @pytest.fixture
    def route_env(self, monkeypatch):
        monkeypatch.setattr(oauth_mod, "consume_oauth_state", lambda s: (
            {"tenant_id": "t1", "user_id": "u1"} if s == "GOOD" else None))
        exchange_mock = AsyncMock(return_value=OAuthTokenResult(
            access_token="AT", expires_in=259200, open_id="oid-1", refresh_token="RT", scope=""))
        monkeypatch.setattr(oauth_mod, "exchange_code", exchange_mock)
        monkeypatch.setattr(oauth_mod, "get_userinfo", AsyncMock(return_value={"nick": "张三"}))
        save_mock = AsyncMock()
        monkeypatch.setattr("src.api.knowledge_tdoc.save_oauth_credentials", save_mock)
        return exchange_mock, save_mock

    def _call(self, code="C1", state="GOOD", error="", error_description=""):
        import asyncio
        from src.api.knowledge_tdoc import oauth_callback
        request = type("Req", (), {})()
        return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
            oauth_callback(request, code=code, state=state, error=error, error_description=error_description)
        )

    def test_success_redirects_to_tenant_page(self, route_env):
        _, save_mock = route_env
        resp = self._call()
        assert resp.status_code == 302
        assert "/t/t1/knowledge/tdoc-imports?tdoc_auth=success" in resp.headers["location"]
        save_mock.assert_awaited_once()

    def test_invalid_state_redirects_to_root(self, route_env):
        resp = self._call(state="BAD")
        assert resp.status_code == 302
        loc = resp.headers["location"]
        assert loc.startswith("https://agent.aidingyi.cn/?tdoc_auth=failed")
        assert "/t/" not in loc

    def test_provider_error_redirects_with_reason(self, route_env):
        resp = self._call(error="server_error", error_description="bad redirect_uri")
        loc = resp.headers["location"]
        assert "tdoc_auth=failed" in loc
        assert "bad+redirect_uri" in loc or "bad%20redirect_uri" in loc or "bad_redirect_uri" in loc

    def test_missing_code_redirects_failed(self, route_env):
        resp = self._call(code="")
        assert "tdoc_auth=failed" in resp.headers["location"]

    def test_exchange_failure_redirects_failed(self, route_env):
        exchange_mock, _ = route_env
        exchange_mock.side_effect = OAuthError(400, "invalid code")
        resp = self._call()
        assert "tdoc_auth=failed" in resp.headers["location"]

    def test_reason_is_url_encoded(self, route_env):
        """失败原因含中文时必须编码进 URL（header 不允许裸非 ASCII）。"""
        resp = self._call(error="access_denied", error_description="用户拒绝授权")
        loc = resp.headers["location"]
        assert all(ord(ch) < 128 for ch in loc)


@pytest.mark.unit
class TestOAuthStartRoute:
    @pytest.fixture
    def start_env(self, monkeypatch, oauth_cfg):
        import src.api.knowledge_tdoc as api_mod
        monkeypatch.setattr(api_mod, "_require_auth", lambda r: {"tenant_id": "t1", "user_id": "u1"})
        return api_mod

    def test_redis_unavailable_fail_fast_503(self, start_env, monkeypatch):
        """Redis 不可用时 /oauth/start 直接 503（state 内存降级会让回调必然失败）。"""
        import asyncio
        import pytest as _pytest
        from fastapi import HTTPException

        monkeypatch.setattr(oauth_mod, "redis_available", lambda: False)
        with _pytest.raises(HTTPException) as exc:
            asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
                start_env.start_oauth(None))
        assert exc.value.status_code == 503

    def test_not_configured_409(self, start_env, monkeypatch):
        import asyncio
        import pytest as _pytest
        from fastapi import HTTPException

        monkeypatch.setattr(oauth_mod, "is_oauth_configured", lambda: False)
        with _pytest.raises(HTTPException) as exc:
            asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
                start_env.start_oauth(None))
        assert exc.value.status_code == 409

    def test_success_returns_authorize_url(self, start_env, monkeypatch):
        import asyncio

        monkeypatch.setattr(oauth_mod, "redis_available", lambda: True)
        monkeypatch.setattr(oauth_mod, "create_oauth_state", lambda t, u: "STATE-X")
        result = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
            start_env.start_oauth(None))
        assert result["success"] is True
        assert result["authorize_url"].startswith("https://docs.qq.com/oauth/v2/authorize?")
        assert "state=STATE-X" in result["authorize_url"]
