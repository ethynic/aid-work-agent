"""tenant_api_doc（api-meta sso 配置解析 + 通用模板 fallback + ${VAR} 渲染）单元测试"""

from unittest.mock import MagicMock, patch

import pytest

from src.services import tenant_api_doc
from src.services.tenant_api_doc import (
    extract_api_meta,
    parse_sso_config,
    load_tenant_doc,
    load_sso_config,
    load_sso_configs,
    list_doc_subagent_names,
    list_sso_candidate_subagent_names,
    resolve_doc_text,
    resolve_base_agent_id,
    load_tenant_env_vars,
    render_doc_placeholders,
    find_unresolved_placeholders,
    db_tenant_id,
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
        """files: {subagent_name: doc 内容}，无对应文档的名字返回 None。

        同时隔离技能白名单（DB/registry 查询），保证纯单元、无外部依赖。
        """
        monkeypatch.setattr(
            "src.services.tenant_api_doc.list_doc_subagent_names",
            lambda tid: list(files.keys()),
        )
        monkeypatch.setattr(
            "src.services.tenant_api_doc.load_tenant_doc",
            lambda tid, name: files.get(name),
        )
        monkeypatch.setattr(
            "src.services.tenant_api_doc._subagent_allowed_skills",
            lambda name: [],
        )
        # 隔离内置/自定义智能体枚举，保证纯单元、不访问磁盘注册表与 DB
        monkeypatch.setattr(
            "src.services.tenant_api_doc._list_builtin_subagent_names",
            lambda: [],
        )
        monkeypatch.setattr(
            "src.services.tenant_api_doc._list_custom_agent_ids",
            lambda: [],
        )
        monkeypatch.setattr(
            "src.services.tenant_api_doc._allowed_skills_map",
            lambda names: {n: [] for n in names},
        )
        monkeypatch.setattr(
            "src.services.tenant_api_doc.load_tenant_env_vars_map",
            lambda tid: {"*": {}},
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

    def test_candidate_names_include_builtin_and_custom(self, monkeypatch):
        """租户文档之外还须枚内置/自定义智能体，才能发现模板兜底的入口（如 auto-form-filler）"""
        monkeypatch.setattr(
            "src.services.tenant_api_doc.list_doc_subagent_names",
            lambda tid: ["pre-sales"],
        )
        monkeypatch.setattr(
            "src.services.tenant_api_doc._list_builtin_subagent_names",
            lambda: ["auto-form-filler", "pre-sales"],
        )
        monkeypatch.setattr(
            "src.services.tenant_api_doc._list_custom_agent_ids",
            lambda: ["sales-assistant"],
        )
        assert list_sso_candidate_subagent_names("tenant_x") == [
            "pre-sales", "auto-form-filler", "sales-assistant",
        ]


# ============== 通用模板 fallback 与 ${VAR} 加载期渲染 ==============

_TEMPLATE = """# 通用模板
```api-meta
login_url: https://erp${APP_ID}.aidingyi.cn/api/v1/erp.delegate/login
user_token_name: client_token
sso_enabled: true
sso_url: https://erp${APP_ID}.aidingyi.cn/pages/list
sso_mode: direct_url
```
鉴权：${AGENT_TOKEN}，用户 token：${client_token}
"""

_RENDERED_TEMPLATE = _TEMPLATE.replace("${APP_ID}", "10607")


@pytest.fixture
def template_dir(tmp_path, monkeypatch):
    d = tmp_path / "api_doc_templates"
    d.mkdir()
    (d / "pre-sales-api.md").write_text(_TEMPLATE, encoding="utf-8")
    monkeypatch.setattr(tenant_api_doc, "_TEMPLATE_DIR", d)
    return d


def _patch_env_db(vars_by_name):
    """patch SubagentEnvVarDB：get_vars 返回给定变量，租户兜底返回空"""
    mock_db = MagicMock()
    mock_db.get_vars.return_value = [
        {"var_name": k, "var_value": v} for k, v in vars_by_name.items()
    ]
    mock_db.get_all_vars_for_tenant.return_value = []
    return patch("src.db.subagent_env_var.SubagentEnvVarDB", mock_db)


def _patch_instance_db(row):
    """patch TenantAgentInstanceDB：get 返回给定实例行（None = 无实例）"""
    mock_db = MagicMock()
    mock_db.get.return_value = row
    return patch("src.db.tenant_agent_instance_db.TenantAgentInstanceDB", mock_db)


_REPLICA_ROW = {
    "agent_id": "sales-assistant-tenant-e9b2fab93a2f-1upa",
    "base_agent_id": "sales-assistant",
}
_DIRECT_ROW = {"agent_id": "sales-assistant", "base_agent_id": "sales-assistant"}


@pytest.fixture(autouse=True)
def _no_replica_instance():
    """默认无副本实例行（resolve_base_agent_id 原样返回），副本用例内自行覆盖"""
    p = _patch_instance_db(None)
    p.start()
    yield
    p.stop()


class TestResolveDocText:
    def test_tenant_doc_takes_priority(self):
        """租户上传的文档优先，不查技能模板"""
        with patch.object(tenant_api_doc, "load_tenant_doc", return_value="# 租户文档") as m_load, \
             patch.object(tenant_api_doc, "_subagent_allowed_skills") as m_skills:
            assert resolve_doc_text("tenant_x", "pre-sales") == "# 租户文档"
        m_load.assert_called_once_with("tenant_x", "pre-sales")
        m_skills.assert_not_called()

    def test_template_fallback_by_skill_whitelist(self, template_dir):
        """租户文档缺失时按技能白名单命中模板（自定义智能体 sales-assistant 同样适用）"""
        with patch.object(tenant_api_doc, "load_tenant_doc", return_value=None), \
             patch.object(tenant_api_doc, "_subagent_allowed_skills", return_value=["pre-sales-api"]):
            assert resolve_doc_text("tenant_x", "sales-assistant") == _TEMPLATE

    def test_no_doc_and_no_matching_skill_returns_none(self, template_dir):
        with patch.object(tenant_api_doc, "load_tenant_doc", return_value=None), \
             patch.object(tenant_api_doc, "_subagent_allowed_skills", return_value=["other-skill"]):
            assert resolve_doc_text("tenant_x", "pre-sales") is None

    def test_no_skills_at_all_returns_none(self):
        with patch.object(tenant_api_doc, "load_tenant_doc", return_value=None), \
             patch.object(tenant_api_doc, "_subagent_allowed_skills", return_value=[]):
            assert resolve_doc_text("tenant_x", "pre-sales") is None

    def test_replica_resolves_parent_doc_and_skills(self, template_dir):
        """副本实例无独立文档与技能配置，文档与技能白名单均按父定义解析"""
        replica = "sales-assistant-tenant-e9b2fab93a2f-1upa"
        with _patch_instance_db(_REPLICA_ROW), \
             patch.object(tenant_api_doc, "load_tenant_doc", return_value=None) as m_load, \
             patch.object(tenant_api_doc, "_subagent_allowed_skills",
                          return_value=["pre-sales-api"]) as m_skills:
            assert resolve_doc_text("tenant_x", replica) == _TEMPLATE
        m_load.assert_called_once_with("tenant_x", "sales-assistant")
        m_skills.assert_called_once_with("sales-assistant")


class TestResolveBaseAgentId:
    def test_replica_resolves_to_base(self):
        with _patch_instance_db(_REPLICA_ROW):
            assert resolve_base_agent_id(
                "tenant_x", "sales-assistant-tenant-e9b2fab93a2f-1upa"
            ) == "sales-assistant"

    def test_tenant_id_prefix_normalized_for_lookup(self):
        with _patch_instance_db(_REPLICA_ROW) as mock_db:
            resolve_base_agent_id("e9b2fab93a2f", "sales-assistant-tenant-x-1upa")
        assert mock_db.get.call_args[0] == ("tenant_e9b2fab93a2f", "sales-assistant-tenant-x-1upa")

    def test_direct_instance_unchanged(self):
        with _patch_instance_db(_DIRECT_ROW):
            assert resolve_base_agent_id("tenant_x", "sales-assistant") == "sales-assistant"

    def test_unknown_agent_unchanged(self):
        with _patch_instance_db(None):
            assert resolve_base_agent_id("tenant_x", "pre-sales") == "pre-sales"

    def test_db_error_unchanged(self):
        mock_db = MagicMock()
        mock_db.get.side_effect = RuntimeError("db down")
        with patch("src.db.tenant_agent_instance_db.TenantAgentInstanceDB", mock_db):
            assert resolve_base_agent_id("tenant_x", "pre-sales") == "pre-sales"

    def test_empty_inputs_unchanged(self):
        assert resolve_base_agent_id("", "pre-sales") == "pre-sales"
        assert resolve_base_agent_id("tenant_x", "") == ""


class TestSubagentAllowedSkills:
    def test_db_definition_priority(self):
        mock_db = MagicMock()
        mock_db.get_by_agent_id.return_value = {"skills": {"allowed": ["pre-sales-api"]}}
        with patch("src.db.subagent_definition_db.SubagentDefinitionDB", mock_db):
            assert tenant_api_doc._subagent_allowed_skills("sales-assistant") == ["pre-sales-api"]

    def test_registry_fallback_when_db_empty(self):
        mock_db = MagicMock()
        mock_db.get_by_agent_id.return_value = {"skills": {"allowed": []}}
        cfg = MagicMock()
        cfg.get_allowed_skills.return_value = ["pre-sales-api"]
        mock_registry = MagicMock()
        mock_registry.get.return_value = cfg
        with patch("src.db.subagent_definition_db.SubagentDefinitionDB", mock_db), \
             patch("src.services.agent_runner.runtime.resource_cache.cached_builtin_subagent_registry",
                   return_value=mock_registry):
            assert tenant_api_doc._subagent_allowed_skills("pre-sales") == ["pre-sales-api"]

    def test_builtin_registry_loaded_without_manual_init(self):
        """回归：不依赖全局单例手动加载，也能读到内置子智能体技能白名单

        缺陷背景：旧实现用 src.subagents.registry.subagent_registry 全局单例，
        该单例无参构造不加载；skill 脚本子进程（create_subprocess_shell 起的独立
        解释器）里恒为空，导致 auto-form-filler 等内置智能体读不到白名单、兜底
        模板失效。
        """
        mock_db = MagicMock()
        mock_db.get_by_agent_id.return_value = None  # DB 无该定义
        with patch("src.db.subagent_definition_db.SubagentDefinitionDB", mock_db):
            skills = tenant_api_doc._subagent_allowed_skills("pre-sales")
        assert "pre-sales-api" in skills

    def test_both_sources_failed_returns_empty(self):
        with patch("src.db.subagent_definition_db.SubagentDefinitionDB",
                   side_effect=RuntimeError("no db")), \
             patch("src.services.agent_runner.runtime.resource_cache.cached_builtin_subagent_registry",
                   side_effect=RuntimeError("no registry")):
            assert tenant_api_doc._subagent_allowed_skills("pre-sales") == []


class TestRenderDocPlaceholders:
    def test_substitutes_non_credential_vars(self):
        with _patch_env_db({"APP_ID": "10607"}):
            rendered = render_doc_placeholders(_TEMPLATE, "tenant_x", "pre-sales")
        assert "erp10607.aidingyi.cn" in rendered
        assert "${APP_ID}" not in rendered

    def test_credential_placeholders_never_rendered(self):
        """AGENT_TOKEN / user_token_name 声明的凭证即使配置了也不得渲染进文档（防 LLM 上下文泄漏）"""
        with _patch_env_db({"APP_ID": "10607", "AGENT_TOKEN": "secret-agent", "client_token": "secret-user"}):
            rendered = render_doc_placeholders(_TEMPLATE, "tenant_x", "pre-sales")
        assert "${AGENT_TOKEN}" in rendered
        assert "${client_token}" in rendered
        assert "secret-agent" not in rendered
        assert "secret-user" not in rendered

    def test_process_env_fallback(self, monkeypatch):
        monkeypatch.setenv("APP_ID", "10608")
        with _patch_env_db({}):
            rendered = render_doc_placeholders(_TEMPLATE, "tenant_x", "pre-sales")
        assert "erp10608.aidingyi.cn" in rendered

    def test_unresolved_placeholder_kept_and_reported(self):
        with _patch_env_db({}):
            rendered = render_doc_placeholders(_TEMPLATE, "tenant_x", "pre-sales")
        assert "${APP_ID}" in rendered
        assert find_unresolved_placeholders(rendered) == ["APP_ID"]

    def test_env_db_error_falls_back_to_process_env(self, monkeypatch):
        monkeypatch.setenv("APP_ID", "10609")
        with patch("src.db.subagent_env_var.SubagentEnvVarDB", side_effect=RuntimeError("db down")):
            rendered = render_doc_placeholders(_TEMPLATE, "tenant_x", "pre-sales")
        assert "erp10609.aidingyi.cn" in rendered

    def test_plain_doc_untouched(self):
        doc = "# 普通文档，无占位符"
        assert render_doc_placeholders(doc, "tenant_x", "pre-sales") == doc

    def test_replica_env_vars_lookup_uses_base_name(self):
        """副本实例环境变量按父定义名精确查（副本无独立环境变量配置入口）"""
        mock_db = MagicMock()
        mock_db.get_vars.return_value = [{"var_name": "APP_ID", "var_value": "10607"}]
        mock_db.get_all_vars_for_tenant.return_value = []
        with _patch_instance_db(_REPLICA_ROW), \
             patch("src.db.subagent_env_var.SubagentEnvVarDB", mock_db):
            env = load_tenant_env_vars("tenant_x", "sales-assistant-tenant-e9b2fab93a2f-1upa")
        assert mock_db.get_vars.call_args[0] == ("tenant_x", "sales-assistant")
        assert env == {"APP_ID": "10607"}

    def test_unprefixed_tenant_id_normalized_for_env_db(self):
        """回归：skill 脚本剥离 `tenant_` 前缀后传入，DB 查询须补齐前缀

        缺陷背景：load_api_config.py 为存储路径剥离前缀后调 load_rendered_doc，
        而 subagent_env_vars 存带前缀 ID，前缀不匹配导致 ${APP_ID} 读不到、模板
        渲染残留占位符、被误判为「未配置」。
        """
        mock_db = MagicMock()
        mock_db.get_vars.return_value = [{"var_name": "APP_ID", "var_value": "10607"}]
        mock_db.get_all_vars_for_tenant.return_value = []
        with patch("src.db.subagent_env_var.SubagentEnvVarDB", mock_db):
            rendered = render_doc_placeholders(_TEMPLATE, "abc123", "pre-sales")
        assert mock_db.get_vars.call_args[0][0] == "tenant_abc123"
        assert "erp10607.aidingyi.cn" in rendered
        assert find_unresolved_placeholders(rendered) == []


class TestDbTenantId:
    def test_adds_missing_prefix(self):
        assert db_tenant_id("abc123") == "tenant_abc123"

    def test_keeps_existing_prefix(self):
        assert db_tenant_id("tenant_abc123") == "tenant_abc123"

    def test_anonymous_placeholder_untouched(self):
        assert db_tenant_id("_anonymous") == "_anonymous"

    def test_empty_returns_empty(self):
        assert db_tenant_id("") == ""


class TestFindUnresolvedPlaceholders:
    def test_excludes_credential_vars(self):
        # 原始模板含未渲染凭证占位符，不算未解析
        assert find_unresolved_placeholders(_TEMPLATE) == ["APP_ID"]

    def test_fully_resolved_returns_empty(self):
        with _patch_env_db({"APP_ID": "10607"}):
            rendered = render_doc_placeholders(_TEMPLATE, "tenant_x", "pre-sales")
        assert find_unresolved_placeholders(rendered) == []


class TestLoadSsoConfigWithTemplate:
    def test_returns_config_when_app_id_resolved(self):
        with patch("src.services.tenant_api_doc.load_rendered_doc",
                   return_value=_RENDERED_TEMPLATE):
            cfg = load_sso_config("tenant_x", "pre-sales")
        assert cfg is not None
        assert cfg["sso_url"] == "https://erp10607.aidingyi.cn/pages/list"
        assert cfg["login_url"] == "https://erp10607.aidingyi.cn/api/v1/erp.delegate/login"
        assert cfg["sso_ready"] is True

    def test_unresolved_app_id_returns_none(self):
        """模板渲染后仍残留 ${APP_ID}（租户环境变量未配置），入口按未配置处理"""
        with patch("src.services.tenant_api_doc.load_rendered_doc", return_value=_TEMPLATE):
            assert load_sso_config("tenant_x", "pre-sales") is None

    def test_missing_doc_returns_none(self):
        with patch("src.services.tenant_api_doc.load_rendered_doc", return_value=None):
            assert load_sso_config("tenant_x", "pre-sales") is None

    def test_load_sso_configs_scans_all_docs(self):
        cfg = {"system_id": "pre-sales", "sso_ready": True}
        with patch("src.services.tenant_api_doc.list_sso_candidate_subagent_names",
                   return_value=["pre-sales", "other"]), \
             patch("src.services.tenant_api_doc._allowed_skills_map",
                   side_effect=lambda names: {n: [] for n in names}), \
             patch("src.services.tenant_api_doc.load_tenant_env_vars_map",
                   return_value={"*": {}}), \
             patch("src.services.tenant_api_doc.load_sso_config", side_effect=[cfg, None]) as m_cfg:
            configs = load_sso_configs("tenant_x")
        assert configs == [cfg]
        assert m_cfg.call_count == 2

    def test_load_sso_configs_discovers_template_fallback_agent(self):
        """未上传租户文档的智能体（auto-form-filler）经技能模板兜底也要出现在入口列表"""
        with patch("src.services.tenant_api_doc.list_sso_candidate_subagent_names",
                   return_value=["auto-form-filler"]), \
             patch("src.services.tenant_api_doc._allowed_skills_map",
                   side_effect=lambda names: {n: [] for n in names}), \
             patch("src.services.tenant_api_doc.load_tenant_env_vars_map",
                   return_value={"*": {}}), \
             patch("src.services.tenant_api_doc.load_rendered_doc",
                   return_value=_RENDERED_TEMPLATE):
            configs = load_sso_configs("tenant_x")
        assert [c["system_id"] for c in configs] == ["auto-form-filler"]
        assert configs[0]["sso_ready"] is True

    def test_load_sso_configs_prefers_ready_duplicate(self):
        """同一系统首个声明未就绪、后续就绪时，保留就绪的那份（避免入口误显示不可用）"""
        broken = {
            "system_id": "pre-sales", "system_name": "CRM", "mode": "ticket_redirect",
            "sso_url": "https://erp.example.com/", "sso_ready": False,
        }
        ready = {
            "system_id": "auto-form-filler", "system_name": "CRM", "mode": "ticket_redirect",
            "sso_url": "https://erp.example.com/", "sso_ready": True,
        }
        with patch("src.services.tenant_api_doc.list_sso_candidate_subagent_names",
                   return_value=["pre-sales", "auto-form-filler"]), \
             patch("src.services.tenant_api_doc._allowed_skills_map",
                   side_effect=lambda names: {n: [] for n in names}), \
             patch("src.services.tenant_api_doc.load_tenant_env_vars_map",
                   return_value={"*": {}}), \
             patch("src.services.tenant_api_doc.load_sso_config",
                   side_effect=[broken, ready]):
            configs = load_sso_configs("tenant_x")
        assert configs == [ready]

    def test_load_sso_configs_dedupes_same_system(self):
        """多个智能体指向同一外部系统时只保留首个卡片，避免页面重复"""
        with patch("src.services.tenant_api_doc.list_sso_candidate_subagent_names",
                   return_value=["pre-sales", "auto-form-filler"]), \
             patch("src.services.tenant_api_doc._allowed_skills_map",
                   side_effect=lambda names: {n: [] for n in names}), \
             patch("src.services.tenant_api_doc.load_tenant_env_vars_map",
                   return_value={"*": {}}), \
             patch("src.services.tenant_api_doc.load_rendered_doc",
                   return_value=_RENDERED_TEMPLATE):
            configs = load_sso_configs("tenant_x")
        assert [c["system_id"] for c in configs] == ["pre-sales"]
