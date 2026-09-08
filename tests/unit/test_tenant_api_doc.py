"""tenant_api_doc（api-meta sso 配置解析）单元测试"""

import pytest

from src.services.tenant_api_doc import (
    extract_api_meta,
    parse_sso_config,
    load_tenant_doc,
)

pytestmark = [pytest.mark.unit, pytest.mark.api]


def _doc(body: str) -> str:
    return f"# 标题\n\n```api-meta\n{body}\n```\n\n正文\n"


class TestExtractApiMeta:
    def test_normal_block(self):
        meta = extract_api_meta(_doc("login_url: https://a.example.com/login\nsso_enabled: true"))
        assert meta["login_url"] == "https://a.example.com/login"
        assert meta["sso_enabled"] == "true"

    def test_no_block_returns_empty(self):
        assert extract_api_meta("无 api-meta 块的文档") == {}

    def test_comments_and_empty_lines_skipped(self):
        meta = extract_api_meta(_doc("<!-- 注释 -->\n\n# 注释行\nlogin_url: https://a.example.com/login\n"))
        assert meta == {"login_url": "https://a.example.com/login"}

    def test_value_trailing_comma_stripped(self):
        meta = extract_api_meta(_doc("push_exclude_sections: 登录,详情,\n"))
        assert meta["push_exclude_sections"] == "登录,详情"


class TestParseSsoConfig:
    def test_not_enabled_returns_none(self):
        assert parse_sso_config({"sso_url": "https://a.example.com/"}) is None
        assert parse_sso_config({"sso_enabled": "false", "sso_url": "https://a"}) is None

    def test_missing_sso_url_returns_none(self):
        assert parse_sso_config({"sso_enabled": "true"}) is None

    def test_direct_url_default_mode_ready(self):
        cfg = parse_sso_config({"sso_enabled": "true", "sso_url": "https://a.example.com/"})
        assert cfg["mode"] == "direct_url"
        assert cfg["sso_ready"] is True
        assert cfg["system_name"] == "第三方系统"
        assert cfg["fallback_url"] == "https://a.example.com/"

    def test_ticket_redirect_requires_grant_url(self):
        meta = {
            "sso_enabled": "true",
            "sso_url": "https://a.example.com/",
            "sso_mode": "ticket_redirect",
        }
        assert parse_sso_config(meta)["sso_ready"] is False
        meta["sso_grant_url"] = "https://a.example.com/api/sso_grant"
        cfg = parse_sso_config(meta)
        assert cfg["sso_ready"] is True
        assert cfg["grant_url"] == "https://a.example.com/api/sso_grant"

    def test_token_param_requires_ticket_param(self):
        meta = {"sso_enabled": "true", "sso_url": "https://a/", "sso_mode": "token_param"}
        assert parse_sso_config(meta)["sso_ready"] is False
        meta["sso_ticket_param"] = "client_token"
        assert parse_sso_config(meta)["sso_ready"] is True

    def test_unknown_mode_not_ready(self):
        cfg = parse_sso_config({"sso_enabled": "true", "sso_url": "https://a/", "sso_mode": "magic"})
        assert cfg["sso_ready"] is False

    def test_system_name_and_fallback(self):
        cfg = parse_sso_config({
            "sso_enabled": "true",
            "sso_url": "https://a/",
            "sso_system_name": "10605 ERP",
            "sso_fallback_url": "https://a/login",
        })
        assert cfg["system_name"] == "10605 ERP"
        assert cfg["fallback_url"] == "https://a/login"


class TestLoadTenantDoc:
    def test_missing_doc_returns_none(self):
        assert load_tenant_doc("__no_such_tenant__") is None

    def test_real_10605_template_sso_config(self):
        """ext 模板若有对应租户目录则验证解析，否则跳过"""
        doc = load_tenant_doc("1dc997a1806b")
        if doc is None:
            pytest.skip("本地无该租户文档副本")
        cfg = parse_sso_config(extract_api_meta(doc))
        assert cfg is not None
        assert cfg["mode"] == "ticket_redirect"
        assert cfg["sso_ready"] is True
