"""tenant_api_doc（api-meta sso 配置解析）单元测试"""

import pytest

from src.services.tenant_api_doc import (
    extract_api_meta,
    parse_sso_config,
    load_tenant_doc,
    load_sso_config,
    load_sso_configs,
    list_doc_subagent_names,
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

    def test_system_id_is_subagent_name(self):
        cfg = parse_sso_config(
            {"sso_enabled": "true", "sso_url": "https://a/", "login_url": "https://a/login"},
            "pre-sales",
        )
        assert cfg["system_id"] == "pre-sales"
        assert cfg["login_url"] == "https://a/login"

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
        assert load_tenant_doc("__no_such_tenant__", "pre-sales") is None

    def test_doc_path_derived_from_subagent_name(self):
        from src.services.tenant_api_doc import tenant_doc_path

        assert tenant_doc_path("tenant_x", "my-custom-agent").name == "my-custom-agent-api.md"

    def test_real_10605_template_sso_config(self):
        """ext 模板若有对应租户目录则验证解析，否则跳过"""
        doc = load_tenant_doc("1dc997a1806b", "pre-sales")
        if doc is None:
            pytest.skip("本地无该租户文档副本")
        cfg = parse_sso_config(extract_api_meta(doc), "pre-sales")
        assert cfg is not None
        assert cfg["mode"] == "ticket_redirect"
        assert cfg["sso_ready"] is True


class TestDocScan:
    """list_doc_subagent_names / load_sso_config(s) 多文档扫描"""

    SSO_DOC = (
        "```api-meta\nlogin_url: https://erp.example.com/api/login\n"
        "sso_enabled: true\nsso_system_name: 10605 ERP\n"
        "sso_mode: ticket_redirect\nsso_url: https://erp.example.com/\n"
        "sso_grant_url: https://erp.example.com/api/sso_grant\n```\n"
    )
    PUSH_ONLY_DOC = "```api-meta\nlogin_url: https://b.example.com/login\n```\n"

    def _setup_tenant(self, monkeypatch, files: dict):
        """files: {subagent_name: doc 内容}，无对应文档的名字返回 None"""
        monkeypatch.setattr(
            "src.services.tenant_api_doc.list_doc_subagent_names",
            lambda tid: list(files.keys()),
        )
        monkeypatch.setattr(
            "src.services.tenant_api_doc.load_tenant_doc",
            lambda tid, name: files.get(name),
        )

    def test_list_names_from_templates_dir(self, monkeypatch, tmp_path):
        """glob 真实逻辑：只认 {name}-api.md，其余文件忽略；路径基于模块 __file__ 推导"""
        # normalize_tenant_id("tenant_x") 剥离 tenant_ 前缀，实际目录为 tenants/x/templates
        templates = tmp_path / "storage" / "tenants" / "x" / "templates"
        templates.mkdir(parents=True)
        (templates / "pre-sales-api.md").write_text(self.SSO_DOC, encoding="utf-8")
        (templates / "another-agent-api.md").write_text(self.PUSH_ONLY_DOC, encoding="utf-8")
        (templates / "readme.md").write_text("非接口文档", encoding="utf-8")
        # probe 文件位于 tmp_path/a/b/probe.py，resolve().parents[2] == tmp_path
        probe_dir = tmp_path / "a" / "b"
        probe_dir.mkdir(parents=True)
        probe = probe_dir / "probe.py"
        probe.write_text("", encoding="utf-8")
        monkeypatch.setattr("src.services.tenant_api_doc.__file__", str(probe))
        assert list_doc_subagent_names("tenant_x") == ["another-agent", "pre-sales"]

    def test_load_sso_config_by_system_id(self, monkeypatch):
        self._setup_tenant(monkeypatch, {"pre-sales": self.SSO_DOC})
        cfg = load_sso_config("tenant_x", "pre-sales")
        assert cfg is not None
        assert cfg["system_id"] == "pre-sales"

    def test_load_sso_config_missing_doc_returns_none(self, monkeypatch):
        self._setup_tenant(monkeypatch, {})
        assert load_sso_config("tenant_x", "pre-sales") is None

    def test_load_sso_configs_skips_push_only_docs(self, monkeypatch):
        """仅一份声明 sso_* → 列表只出 1 个系统；纯推送文档跳过"""
        self._setup_tenant(monkeypatch, {
            "pre-sales": self.SSO_DOC,
            "another-agent": self.PUSH_ONLY_DOC,
        })
        configs = load_sso_configs("tenant_x")
        assert len(configs) == 1
        assert configs[0]["system_id"] == "pre-sales"

    def test_load_sso_configs_empty_when_no_docs(self, monkeypatch):
        self._setup_tenant(monkeypatch, {})
        assert load_sso_configs("tenant_x") == []
