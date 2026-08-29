"""租户上下文中间件 X-Tenant-Id 采纳规则单元测试（无 PG/Redis，全 mock）。

X-Tenant-Id 头必须先走完整认证链（Bearer token → verify_token → 查 users 行
拿 role+tenant_id）才可能被采纳：
- platform_admin：可采纳任意存在租户（代管理）
- tenant_admin/普通用户：仅当 header 值等于自身 tenant_id 时采纳，否则请求被拒（403）
- 无有效 token：header 一律不采纳，按原回退逻辑处理（匿名语义不变）
- 认证成功后 request.state.user_role 暴露给下游（知识库平台管理员全局视图输入）
"""

from unittest.mock import MagicMock, patch

import pytest
from starlette.requests import Request
from starlette.responses import Response

from src.saas.middleware import TenantContextMiddleware
from src.saas.context import set_tenant_context


def _make_request(path="/api/chat/x", headers=None):
    """构造带 header 的最小 Starlette Request 假件"""
    raw_headers = [
        (k.lower().encode("latin-1"), v.encode("latin-1"))
        for k, v in (headers or {}).items()
    ]
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "raw_path": path.encode(),
        "headers": raw_headers,
        "query_string": b"",
        "server": ("testserver", 80),
        "scheme": "http",
        "http_version": "1.1",
    }
    return Request(scope)


class _FakeCursor:
    def __init__(self, user_row=None, captured=None):
        self._user_row = user_row
        self.captured = captured if captured is not None else []

    def execute(self, sql, params=None):
        self.captured.append((sql, params))

    def fetchone(self):
        return self._user_row


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _setup_auth(user_row, token_user="user-1"):
    """mock 认证链：verify_token 返回 token_user，users 行返回 user_row"""
    return patch("src.api.auth.verify_token", return_value=token_user), \
        patch("src.saas.middleware.get_db_connection",
              return_value=_FakeConn(_FakeCursor(user_row)))


def _tenant_exists():
    return patch("src.saas.db.tenant_db.TenantDB.get_by_id", return_value={"tenant_id": "t2"})


def _make_call_next():
    """记录调用并返回 200 的 call_next 假件"""
    calls = {"invoked": False, "tenant_id": "unset", "user_role": "unset"}

    async def call_next(request):
        calls["invoked"] = True
        calls["tenant_id"] = getattr(request.state, "tenant_id", "unset")
        calls["user_role"] = getattr(request.state, "user_role", "unset")
        return Response("ok", status_code=200)

    return call_next, calls


@pytest.fixture(autouse=True)
def _restore_tenant_context():
    """用例结束后恢复租户上下文，避免用例间串扰"""
    yield
    set_tenant_context(None, None)


class TestUserTenantHeaderAdoption:
    """/api/chat 与 /api/ 路径（_resolve_user_tenant）的 header 采纳规则"""

    @pytest.mark.asyncio
    async def test_employee_forged_other_tenant_403(self):
        """普通用户 + 伪造他人租户 header → 403（dispatch 捕获路径），不进入业务处理"""
        request = _make_request("/api/chat/x", {
            "Authorization": "Bearer good-token", "X-Tenant-Id": "t2"})
        call_next, calls = _make_call_next()
        mw = TenantContextMiddleware(None)
        verify_patch, db_patch = _setup_auth({"role": "employee", "tenant_id": "t1"})

        with verify_patch, db_patch, _tenant_exists():
            response = await mw.dispatch(request, call_next)

        assert response.status_code == 403
        assert "无权访问指定租户".encode("utf-8") in response.body
        assert calls["invoked"] is False  # 业务处理未执行

    @pytest.mark.asyncio
    async def test_employee_own_tenant_header_adopted(self):
        """普通用户 + header == 自身租户 → 正常采纳"""
        request = _make_request("/api/chat/x", {
            "Authorization": "Bearer good-token", "X-Tenant-Id": "t1"})
        call_next, calls = _make_call_next()
        mw = TenantContextMiddleware(None)
        verify_patch, db_patch = _setup_auth({"role": "employee", "tenant_id": "t1"})

        with verify_patch, db_patch:
            response = await mw.dispatch(request, call_next)

        assert response.status_code == 200
        assert calls["invoked"] is True
        assert calls["tenant_id"] == "t1"

    @pytest.mark.asyncio
    async def test_tenant_admin_other_tenant_403(self):
        """tenant_admin + 他人租户 → 403"""
        request = _make_request("/api/chat/x", {
            "Authorization": "Bearer good-token", "X-Tenant-Id": "t2"})
        call_next, calls = _make_call_next()
        mw = TenantContextMiddleware(None)
        verify_patch, db_patch = _setup_auth({"role": "tenant_admin", "tenant_id": "t1"})

        with verify_patch, db_patch, _tenant_exists():
            response = await mw.dispatch(request, call_next)

        assert response.status_code == 403
        assert calls["invoked"] is False

    @pytest.mark.asyncio
    async def test_tenant_admin_own_tenant_adopted(self):
        """tenant_admin + 自身租户 → 采纳"""
        request = _make_request("/api/chat/x", {
            "Authorization": "Bearer good-token", "X-Tenant-Id": "t1"})
        call_next, calls = _make_call_next()
        mw = TenantContextMiddleware(None)
        verify_patch, db_patch = _setup_auth({"role": "tenant_admin", "tenant_id": "t1"})

        with verify_patch, db_patch:
            response = await mw.dispatch(request, call_next)

        assert response.status_code == 200
        assert calls["tenant_id"] == "t1"

    @pytest.mark.asyncio
    async def test_platform_admin_any_existing_tenant_adopted(self):
        """platform_admin + 任意存在租户 → 采纳（代管理行为保留）"""
        request = _make_request("/api/chat/x", {
            "Authorization": "Bearer good-token", "X-Tenant-Id": "t2"})
        call_next, calls = _make_call_next()
        mw = TenantContextMiddleware(None)
        verify_patch, db_patch = _setup_auth({"role": "platform_admin", "tenant_id": None})

        with verify_patch, db_patch, _tenant_exists():
            response = await mw.dispatch(request, call_next)

        assert response.status_code == 200
        assert calls["tenant_id"] == "t2"

    @pytest.mark.asyncio
    async def test_anonymous_header_ignored_not_denied(self):
        """匿名（无 Authorization）+ 任意 header → 忽略（None 语义），不 403"""
        request = _make_request("/api/chat/x", {"X-Tenant-Id": "t2"})
        call_next, calls = _make_call_next()
        mw = TenantContextMiddleware(None)

        with _tenant_exists() as tenant_mock:
            response = await mw.dispatch(request, call_next)

        assert response.status_code == 200
        assert calls["invoked"] is True
        assert calls["tenant_id"] is None
        assert not tenant_mock.called  # 未认证不查目标租户

    @pytest.mark.asyncio
    async def test_bad_token_header_ignored(self):
        """坏 token（verify_token 失败）+ header → 忽略，不 403"""
        request = _make_request("/api/chat/x", {
            "Authorization": "Bearer bad-token", "X-Tenant-Id": "t2"})
        call_next, calls = _make_call_next()
        mw = TenantContextMiddleware(None)

        with patch("src.api.auth.verify_token", return_value=None) as verify_mock, \
                _tenant_exists() as tenant_mock:
            response = await mw.dispatch(request, call_next)

        assert response.status_code == 200
        assert calls["tenant_id"] is None
        assert verify_mock.called
        assert not tenant_mock.called

    @pytest.mark.asyncio
    async def test_no_header_normal_flow_unchanged(self):
        """普通用户无 header → 回退自身租户（既有正确路径零行为变化）"""
        request = _make_request("/api/chat/x", {"Authorization": "Bearer good-token"})
        call_next, calls = _make_call_next()
        mw = TenantContextMiddleware(None)
        verify_patch, db_patch = _setup_auth({"role": "employee", "tenant_id": "t1"})

        with verify_patch, db_patch:
            response = await mw.dispatch(request, call_next)

        assert response.status_code == 200
        assert calls["tenant_id"] == "t1"


class TestSessionTenantHeaderAdoption:
    """/api/sessions 路径（_resolve_session_tenant）同套规则"""

    @pytest.mark.asyncio
    async def test_session_forged_other_tenant_403(self):
        request = _make_request("/api/sessions/list", {
            "Authorization": "Bearer good-token", "X-Tenant-Id": "t2"})
        call_next, calls = _make_call_next()
        mw = TenantContextMiddleware(None)
        verify_patch, db_patch = _setup_auth({"role": "employee", "tenant_id": "t1"})

        with verify_patch, db_patch, _tenant_exists():
            response = await mw.dispatch(request, call_next)

        assert response.status_code == 403
        assert calls["invoked"] is False

    @pytest.mark.asyncio
    async def test_session_own_tenant_adopted(self):
        request = _make_request("/api/sessions/list", {
            "Authorization": "Bearer good-token", "X-Tenant-Id": "t1"})
        call_next, calls = _make_call_next()
        mw = TenantContextMiddleware(None)
        verify_patch, db_patch = _setup_auth({"role": "tenant_admin", "tenant_id": "t1"})

        with verify_patch, db_patch:
            response = await mw.dispatch(request, call_next)

        assert response.status_code == 200
        assert calls["tenant_id"] == "t1"


class TestUserRoleExposure:
    """认证成功后 state.user_role 暴露给下游（知识库全局视图的输入）"""

    @pytest.mark.asyncio
    async def test_platform_admin_role_exposed(self):
        """非 demo 部署：platform_admin 无 header → 租户为 None（全局视图条件成立）"""
        from types import SimpleNamespace

        request = _make_request("/api/knowledge/documents", {
            "Authorization": "Bearer good-token"})
        call_next, calls = _make_call_next()
        mw = TenantContextMiddleware(None)
        verify_patch, db_patch = _setup_auth({"role": "platform_admin", "tenant_id": None})

        with verify_patch, db_patch, \
                patch("src.config.settings.settings", SimpleNamespace(demo=None)):
            response = await mw.dispatch(request, call_next)

        assert response.status_code == 200
        assert calls["user_role"] == "platform_admin"
        assert calls["tenant_id"] is None  # platform_admin 自身租户为空 → 全局视图条件成立

    @pytest.mark.asyncio
    async def test_platform_admin_demo_fallback_unchanged(self):
        """demo 部署：platform_admin 无 header → 照旧回落 demo 租户（既有回退不动）"""
        request = _make_request("/api/knowledge/documents", {
            "Authorization": "Bearer good-token"})
        call_next, calls = _make_call_next()
        mw = TenantContextMiddleware(None)
        verify_patch, db_patch = _setup_auth({"role": "platform_admin", "tenant_id": None})

        with verify_patch, db_patch:
            response = await mw.dispatch(request, call_next)

        assert response.status_code == 200
        assert calls["user_role"] == "platform_admin"
        assert calls["tenant_id"] == "demo"

    @pytest.mark.asyncio
    async def test_employee_role_exposed(self):
        request = _make_request("/api/knowledge/documents", {
            "Authorization": "Bearer good-token"})
        call_next, calls = _make_call_next()
        mw = TenantContextMiddleware(None)
        verify_patch, db_patch = _setup_auth({"role": "employee", "tenant_id": "t1"})

        with verify_patch, db_patch:
            response = await mw.dispatch(request, call_next)

        assert response.status_code == 200
        assert calls["user_role"] == "employee"
        assert calls["tenant_id"] == "t1"

    @pytest.mark.asyncio
    async def test_anonymous_role_stays_none(self):
        request = _make_request("/api/knowledge/documents")
        call_next, calls = _make_call_next()
        mw = TenantContextMiddleware(None)

        response = await mw.dispatch(request, call_next)

        assert response.status_code == 200
        assert calls["user_role"] is None


class TestSaasAdminAndCallbackUnchanged:
    """既有正确路径零行为变化：/api/saas/ 管理流与 /t/ 回调流"""

    @pytest.mark.asyncio
    async def test_admin_flow_platform_admin_with_header(self):
        """/api/saas/ 管理流：platform_admin 指定租户照常生效"""
        request = _make_request("/api/saas/tenants", {
            "Authorization": "Bearer admin-token", "X-Tenant-Id": "t2"})
        call_next, calls = _make_call_next()
        mw = TenantContextMiddleware(None)
        verify_patch, db_patch = _setup_auth({"role": "platform_admin", "tenant_id": None})

        with verify_patch, db_patch, _tenant_exists():
            response = await mw.dispatch(request, call_next)

        assert response.status_code == 200
        assert calls["tenant_id"] == "t2"
        assert calls["user_role"] == "platform_admin"

    @pytest.mark.asyncio
    async def test_tenant_callback_from_path(self):
        """/t/{tenant_id}/ 回调流：租户取自 URL，与 header/认证无关"""
        request = _make_request("/t/t9/wecom/callback")
        call_next, calls = _make_call_next()
        mw = TenantContextMiddleware(None)

        response = await mw.dispatch(request, call_next)

        assert response.status_code == 200
        assert calls["tenant_id"] == "t9"
        assert calls["user_role"] is None
