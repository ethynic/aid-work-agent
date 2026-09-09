"""外部系统入口 API（list_external_systems / get_sso_url）单元测试"""

import pytest

from src.api import external_systems
from starlette.requests import Request

pytestmark = [pytest.mark.unit, pytest.mark.api]

TENANT = "t1"
SSO_META_DOC = """# 文档

```api-meta
login_url: https://erp.example.com/api/login
sso_enabled: true
sso_system_name: 10605 ERP
sso_mode: ticket_redirect
sso_url: https://erp.example.com/
sso_ticket_param: sso_ticket
sso_grant_url: https://erp.example.com/api/v1/erp.delegate/sso_grant
sso_fallback_url: https://erp.example.com/login
```
"""


def _request(tenant_id=None) -> Request:
    req = Request(scope={"type": "http", "headers": [], "query_string": b""})
    if tenant_id:
        req.state.tenant_id = tenant_id
    return req


@pytest.fixture
def authed(monkeypatch):
    monkeypatch.setattr(external_systems, "get_current_user", lambda req: {"user_id": "u1", "phone": "13800000000"})


class TestListExternalSystems:
    async def test_missing_tenant_400(self):
        resp = await external_systems.list_external_systems(_request())
        assert resp.status_code == 400

    async def test_unauthenticated_401(self, monkeypatch):
        monkeypatch.setattr(external_systems, "get_current_user", lambda req: None)
        req = _request(TENANT)
        req.state.tenant_id = TENANT
        resp = await external_systems.list_external_systems(req)
        assert resp.status_code == 401

    async def test_no_doc_returns_empty(self, authed, monkeypatch):
        monkeypatch.setattr("src.services.tenant_api_doc.load_tenant_doc", lambda tid: None)
        result = await external_systems.list_external_systems(_request(TENANT))
        assert result == {"success": True, "data": {"items": []}}

    async def test_sso_declared_returns_item(self, authed, monkeypatch):
        monkeypatch.setattr("src.services.tenant_api_doc.load_tenant_doc", lambda tid: SSO_META_DOC)
        result = await external_systems.list_external_systems(_request(TENANT))
        items = result["data"]["items"]
        assert len(items) == 1
        assert items[0]["system_id"] == "pre_sales"
        assert items[0]["name"] == "10605 ERP"
        assert items[0]["sso_ready"] is True
        # 非 direct_url 模式不透出 entry_url，前端须走 sso-url 接口
        assert items[0]["entry_url"] is None

    async def test_no_sso_declaration_returns_empty(self, authed, monkeypatch):
        monkeypatch.setattr(
            "src.services.tenant_api_doc.load_tenant_doc",
            lambda tid: "```api-meta\nlogin_url: https://a.example.com/login\n```",
        )
        result = await external_systems.list_external_systems(_request(TENANT))
        assert result["data"]["items"] == []


class TestGetSsoUrl:
    async def test_missing_tenant_400(self):
        resp = await external_systems.get_sso_url("pre_sales", _request())
        assert resp.status_code == 400

    async def test_unknown_system_404(self, authed, monkeypatch):
        monkeypatch.setattr("src.services.tenant_api_doc.load_tenant_doc", lambda tid: SSO_META_DOC)
        resp = await external_systems.get_sso_url("other", _request(TENANT))
        assert resp.status_code == 404

    async def test_direct_url_mode_400(self, authed, monkeypatch):
        monkeypatch.setattr(
            "src.services.tenant_api_doc.load_tenant_doc",
            lambda tid: "```api-meta\nlogin_url: https://a/login\nsso_enabled: true\nsso_url: https://a/\n```",
        )
        resp = await external_systems.get_sso_url("pre_sales", _request(TENANT))
        assert resp.status_code == 400

    async def test_no_phone_returns_fallback(self, authed, monkeypatch):
        monkeypatch.setattr("src.services.tenant_api_doc.load_tenant_doc", lambda tid: SSO_META_DOC)
        monkeypatch.setattr(external_systems, "get_current_user", lambda req: {"user_id": "u1", "phone": ""})
        result = await external_systems.get_sso_url("pre_sales", _request(TENANT))
        assert result["success"] is False
        assert result["fallback_url"] == "https://erp.example.com/login"

    async def test_no_agent_token_returns_fallback(self, authed, monkeypatch):
        monkeypatch.setattr("src.services.tenant_api_doc.load_tenant_doc", lambda tid: SSO_META_DOC)
        monkeypatch.setattr(
            "src.services.recap.tasks.external_push._get_agent_token", lambda tid, name: None
        )
        result = await external_systems.get_sso_url("pre_sales", _request(TENANT))
        assert result["success"] is False
        assert result["fallback_url"] == "https://erp.example.com/login"
        assert "debug" in result

    async def test_delegate_login_failure_returns_fallback(self, authed, monkeypatch):
        monkeypatch.setattr("src.services.tenant_api_doc.load_tenant_doc", lambda tid: SSO_META_DOC)
        monkeypatch.setattr(
            "src.services.recap.tasks.external_push._get_agent_token", lambda tid, name: "agt"
        )

        def _fail(*a, **kw):
            return None

        monkeypatch.setattr("src.services.recap.tasks.external_push._delegate_login", _fail)
        result = await external_systems.get_sso_url("pre_sales", _request(TENANT))
        assert result["success"] is False
        assert result["fallback_url"] == "https://erp.example.com/login"

    async def test_grant_rejected_returns_fallback(self, authed, monkeypatch):
        """用户账号在第三方不存在（Code=-1）时不阻断，返回 fallback_url 手动登录"""
        monkeypatch.setattr("src.services.tenant_api_doc.load_tenant_doc", lambda tid: SSO_META_DOC)
        monkeypatch.setattr(
            "src.services.recap.tasks.external_push._get_agent_token", lambda tid, name: "agt"
        )

        def _login(*a, **kw):
            return {"client_token": "ct", "cached": True}

        def _grant(*a, **kw):
            return {"Code": -1, "Error": "单点登录失败"}

        monkeypatch.setattr("src.services.recap.tasks.external_push._delegate_login", _login)
        monkeypatch.setattr("src.services.recap.tasks.external_push._post_json", _grant)
        result = await external_systems.get_sso_url("pre_sales", _request(TENANT))
        assert result["success"] is False
        assert result["fallback_url"] == "https://erp.example.com/login"

    async def test_success_with_third_party_url(self, authed, monkeypatch):
        monkeypatch.setattr("src.services.tenant_api_doc.load_tenant_doc", lambda tid: SSO_META_DOC)
        monkeypatch.setattr(
            "src.services.recap.tasks.external_push._get_agent_token", lambda tid, name: "agt"
        )

        def _login(*a, **kw):
            return {"client_token": "ct", "cached": True}

        def _grant(*a, **kw):
            return {"Code": 0, "Response": {"sso_ticket": "ticket", "url": "https://erp.example.com/?sso_ticket=ticket"}}

        monkeypatch.setattr("src.services.recap.tasks.external_push._delegate_login", _login)
        monkeypatch.setattr("src.services.recap.tasks.external_push._post_json", _grant)
        result = await external_systems.get_sso_url("pre_sales", _request(TENANT))
        assert result["success"] is True
        assert result["data"]["url"] == "https://erp.example.com/?client_token=ticket"

    async def test_success_url_constructed_from_ticket(self, authed, monkeypatch):
        monkeypatch.setattr("src.services.tenant_api_doc.load_tenant_doc", lambda tid: SSO_META_DOC)
        monkeypatch.setattr(
            "src.services.recap.tasks.external_push._get_agent_token", lambda tid, name: "agt"
        )

        def _login(*a, **kw):
            return {"client_token": "ct"}

        def _grant(*a, **kw):
            return {"Code": 0, "Response": {"sso_ticket": "ticket"}}

        monkeypatch.setattr("src.services.recap.tasks.external_push._delegate_login", _login)
        monkeypatch.setattr("src.services.recap.tasks.external_push._post_json", _grant)
        result = await external_systems.get_sso_url("pre_sales", _request(TENANT))
        assert result["success"] is True
        assert result["data"]["url"] == "https://erp.example.com/?sso_ticket=ticket"

    async def test_sso_url_unauthenticated_401(self, monkeypatch):
        monkeypatch.setattr(external_systems, "get_current_user", lambda req: None)
        resp = await external_systems.get_sso_url("pre_sales", _request(TENANT))
        assert resp.status_code == 401

    async def test_grant_passes_grant_url_and_client_token(self, authed, monkeypatch):
        """验证换票调用确实以声明的 grant_url + 委托会话 client_token header 发起"""
        monkeypatch.setattr("src.services.tenant_api_doc.load_tenant_doc", lambda tid: SSO_META_DOC)
        monkeypatch.setattr(
            "src.services.recap.tasks.external_push._get_agent_token", lambda tid, name: "agt"
        )
        calls = {}

        def _grant(url, body, agent_token, extra_headers=None):
            calls["url"] = url
            calls["agent_token"] = agent_token
            calls["headers"] = extra_headers
            return {"Code": 0, "Response": {"url": "https://erp.example.com/?client_token=t"}}

        monkeypatch.setattr(
            "src.services.recap.tasks.external_push._delegate_login",
            lambda *a, **kw: {"client_token": "ctok"},
        )
        monkeypatch.setattr("src.services.recap.tasks.external_push._post_json", _grant)
        result = await external_systems.get_sso_url("pre_sales", _request(TENANT))
        assert result["success"] is True
        assert calls["url"] == "https://erp.example.com/api/v1/erp.delegate/sso_grant"
        assert calls["agent_token"] == "agt"
        assert calls["headers"] == {"Client-Authorize-Token": "ctok"}

    async def test_grant_no_url_and_no_ticket_param_fallback(self, authed, monkeypatch):
        """第三方 Code=0 但未返回 url、且文档未声明 sso_ticket_param -> 兜底回退登录页"""
        doc = (
            "```api-meta\nlogin_url: https://erp.example.com/api/login\n"
            "sso_enabled: true\nsso_mode: ticket_redirect\n"
            "sso_url: https://erp.example.com/\n"
            "sso_grant_url: https://erp.example.com/api/v1/erp.delegate/sso_grant\n"
            "sso_fallback_url: https://erp.example.com/login\n```\n"
        )
        monkeypatch.setattr("src.services.tenant_api_doc.load_tenant_doc", lambda tid: doc)
        monkeypatch.setattr(
            "src.services.recap.tasks.external_push._get_agent_token", lambda tid, name: "agt"
        )
        monkeypatch.setattr(
            "src.services.recap.tasks.external_push._delegate_login",
            lambda *a, **kw: {"client_token": "ct"},
        )
        monkeypatch.setattr(
            "src.services.recap.tasks.external_push._post_json",
            lambda *a, **kw: {"Code": 0, "Response": {}},
        )
        result = await external_systems.get_sso_url("pre_sales", _request(TENANT))
        assert result["success"] is False
        assert result["fallback_url"] == "https://erp.example.com/login"

    async def test_grant_no_url_and_missing_ticket_fallback(self, authed, monkeypatch):
        """声明了 ticket_param 但第三方响应缺票据 -> 不能拼出空票据 URL，走兜底"""
        doc = SSO_META_DOC.replace("sso_fallback_url: https://erp.example.com/login\n", "")
        monkeypatch.setattr("src.services.tenant_api_doc.load_tenant_doc", lambda tid: doc)
        monkeypatch.setattr(
            "src.services.recap.tasks.external_push._get_agent_token", lambda tid, name: "agt"
        )
        monkeypatch.setattr(
            "src.services.recap.tasks.external_push._delegate_login",
            lambda *a, **kw: {"client_token": "ct"},
        )
        monkeypatch.setattr(
            "src.services.recap.tasks.external_push._post_json",
            lambda *a, **kw: {"Code": 0, "Response": {"sso_ticket": ""}},
        )
        result = await external_systems.get_sso_url("pre_sales", _request(TENANT))
        assert result["success"] is False
        # fallback_url 未声明时回退 sso_url 本身
        assert result["fallback_url"] == "https://erp.example.com/"

    async def test_success_url_ampersand_when_sso_url_has_query(self, authed, monkeypatch):
        """sso_url 已带查询串时用 & 拼接票据参数"""
        doc = SSO_META_DOC.replace(
            "sso_url: https://erp.example.com/",
            "sso_url: https://erp.example.com/entry?src=portal",
        )
        monkeypatch.setattr("src.services.tenant_api_doc.load_tenant_doc", lambda tid: doc)
        monkeypatch.setattr(
            "src.services.recap.tasks.external_push._get_agent_token", lambda tid, name: "agt"
        )
        monkeypatch.setattr(
            "src.services.recap.tasks.external_push._delegate_login",
            lambda *a, **kw: {"client_token": "ct"},
        )
        monkeypatch.setattr(
            "src.services.recap.tasks.external_push._post_json",
            lambda *a, **kw: {"Code": 0, "Response": {"sso_ticket": "tk"}},
        )
        result = await external_systems.get_sso_url("pre_sales", _request(TENANT))
        assert result["success"] is True
        assert result["data"]["url"] == "https://erp.example.com/entry?src=portal&sso_ticket=tk"

    async def test_list_direct_url_mode_exposes_entry_url(self, authed, monkeypatch):
        """direct_url 模式列表直接透出 entry_url，前端免换票打开"""
        monkeypatch.setattr(
            "src.services.tenant_api_doc.load_tenant_doc",
            lambda tid: "```api-meta\nsso_enabled: true\nsso_url: https://a.example.com/\n```",
        )
        result = await external_systems.list_external_systems(_request(TENANT))
        items = result["data"]["items"]
        assert len(items) == 1
        assert items[0]["mode"] == "direct_url"
        assert items[0]["entry_url"] == "https://a.example.com/"


class TestGetSsoUrlTokenParam:
    """token_param 模式：委托会话 client_token 本身作为票据拼 URL"""

    TOKEN_PARAM_DOC = (
        "```api-meta\nlogin_url: https://erp.example.com/api/login\n"
        "sso_enabled: true\nsso_mode: token_param\n"
        "sso_url: https://erp.example.com/\nsso_ticket_param: client_token\n"
        "sso_fallback_url: https://erp.example.com/login\n```\n"
    )

    async def test_success(self, authed, monkeypatch):
        monkeypatch.setattr(
            "src.services.tenant_api_doc.load_tenant_doc", lambda tid: self.TOKEN_PARAM_DOC
        )
        monkeypatch.setattr(
            "src.services.recap.tasks.external_push._get_agent_token", lambda tid, name: "agt"
        )
        monkeypatch.setattr(
            "src.services.recap.tasks.external_push._delegate_login",
            lambda *a, **kw: {"client_token": "ctok"},
        )
        result = await external_systems.get_sso_url("pre_sales", _request(TENANT))
        assert result["success"] is True
        assert result["data"]["url"] == "https://erp.example.com/?client_token=ctok"

    async def test_delegate_failure_with_sso_login_fallback(self, authed, monkeypatch):
        """委托会话不可用但文档声明 sso_login_url：走 agent_token + 手机号兜底签发"""
        doc = self.TOKEN_PARAM_DOC.replace(
            "sso_fallback_url: https://erp.example.com/login\n",
            "sso_fallback_url: https://erp.example.com/login\n"
            "sso_login_url: https://erp.example.com/api/v1/erp.delegate/sso_login\n",
        )
        monkeypatch.setattr("src.services.tenant_api_doc.load_tenant_doc", lambda tid: doc)
        monkeypatch.setattr(
            "src.services.recap.tasks.external_push._get_agent_token", lambda tid, name: "agt"
        )
        monkeypatch.setattr(
            "src.services.recap.tasks.external_push._delegate_login", lambda *a, **kw: None
        )
        calls = {}

        def _login(url, body, agent_token, extra_headers=None):
            calls["url"] = url
            calls["body"] = body
            return {"Code": 0, "Response": {"sso_ticket": "stok"}}

        monkeypatch.setattr("src.services.recap.tasks.external_push._post_json", _login)
        result = await external_systems.get_sso_url("pre_sales", _request(TENANT))
        assert result["success"] is True
        assert result["data"]["url"] == "https://erp.example.com/?client_token=stok"
        assert calls["url"] == "https://erp.example.com/api/v1/erp.delegate/sso_login"
        assert calls["body"] == {"mobile": "13800000000"}


class TestGetSsoUrlGrantRetry:
    """缓存委托 token 被第三方判失效时强刷重试一次"""

    async def test_force_refresh_retry_success(self, authed, monkeypatch):
        monkeypatch.setattr("src.services.tenant_api_doc.load_tenant_doc", lambda tid: SSO_META_DOC)
        monkeypatch.setattr(
            "src.services.recap.tasks.external_push._get_agent_token", lambda tid, name: "agt"
        )
        login_calls = []
        grant_calls = []

        def _login(tenant, phone, token, url, force_refresh=False):
            login_calls.append(force_refresh)
            # 首次走缓存（旧 token），强刷后换新 token
            return {"client_token": "new_ct" if force_refresh else "stale_ct"}

        def _grant(url, body, agent_token, extra_headers=None):
            grant_calls.append(extra_headers)
            # 旧 token 被第三方判失效，新 token 换票成功
            code = -99 if extra_headers["Client-Authorize-Token"] == "stale_ct" else 0
            return {"Code": code, "Response": {"url": "https://erp.example.com/?client_token=t"}}

        monkeypatch.setattr("src.services.recap.tasks.external_push._delegate_login", _login)
        monkeypatch.setattr("src.services.recap.tasks.external_push._post_json", _grant)
        result = await external_systems.get_sso_url("pre_sales", _request(TENANT))
        assert result["success"] is True
        assert login_calls == [False, True]
        assert grant_calls[0]["Client-Authorize-Token"] == "stale_ct"
        assert grant_calls[1]["Client-Authorize-Token"] == "new_ct"

    async def test_sso_login_fallback_when_grant_always_fails(self, authed, monkeypatch):
        """grant 持续失败且文档声明 sso_login_url：最终走 sso_login 兜底签发"""
        doc = SSO_META_DOC.replace(
            "sso_fallback_url: https://erp.example.com/login\n",
            "sso_fallback_url: https://erp.example.com/login\n"
            "sso_login_url: https://erp.example.com/api/v1/erp.delegate/sso_login\n",
        )
        monkeypatch.setattr("src.services.tenant_api_doc.load_tenant_doc", lambda tid: doc)
        monkeypatch.setattr(
            "src.services.recap.tasks.external_push._get_agent_token", lambda tid, name: "agt"
        )
        monkeypatch.setattr(
            "src.services.recap.tasks.external_push._delegate_login",
            lambda *a, **kw: {"client_token": "ct"},
        )

        def _post(url, body, agent_token, extra_headers=None):
            if "sso_grant" in url:
                return {"Code": -1, "Error": "单点登录失败"}
            return {"Code": 0, "Response": {"sso_ticket": "ltok"}}

        monkeypatch.setattr("src.services.recap.tasks.external_push._post_json", _post)
        result = await external_systems.get_sso_url("pre_sales", _request(TENANT))
        assert result["success"] is True
        assert result["data"]["url"] == "https://erp.example.com/?sso_ticket=ltok"
