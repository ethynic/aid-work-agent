"""数字员工配置导出/导入 API 单元测试：导出结构 / 敏感信息占位 / 冲突策略 / 存在性警告。"""
import json
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import agent_definitions as api_mod


@pytest.fixture
def client(monkeypatch):
    """最小 app 挂 agent-definitions 路由，鉴权与 DB 边界全部 mock"""
    app = FastAPI()
    app.include_router(api_mod.router)
    admin = {"user_id": "admin1", "phone": "13800000000", "role": "platform_admin"}
    monkeypatch.setattr(api_mod, "_require_admin", lambda request: admin)
    yield TestClient(app, raise_server_exceptions=False)


def _definition(**overrides):
    d = {
        "agent_id": "emp_a", "name": "员工A", "description": "描述",
        "version": "1.0.0", "author": None,
        "triggers": {"keywords": ["a"]}, "tools": {"inherit": True},
        "skills": {"allowed": ["pre-sales-api"]}, "context": {},
        "delegatable_to": [], "allow_delegation": True,
        "llm_provider": None, "llm_model_codes": None,
        "reply_style": None, "business_pages": [],
        "knowledge_sources": [], "chat_toolbar": [],
        "upload_accept": None, "recap": {"tasks": []},
        "status": "active", "prompt_id": "p1",
        "production_version": 2, "latest_version": 3,
    }
    d.update(overrides)
    return d


def _import_payload(**overrides):
    payload = {
        "format": "agent_definition_export",
        "version": 1,
        "definition": {
            "agent_id": "emp_a", "name": "员工A", "system_prompt": "你是员工A",
            "tools": {"inherit": False, "additional": ["http_api"]},
            "skills": {"allowed": ["pre-sales-api"]},
            "recap": {"tasks": []},
        },
    }
    payload.update(overrides)
    return payload


def _mock_agent(monkeypatch, reg_cfg=None):
    """mock master_agent（registry 冲突判定 + 工具存在性）与 get_master_agent（技能存在性）"""
    mock_agent = MagicMock()
    registry = MagicMock()
    registry.get.return_value = reg_cfg
    mock_agent.subagent_registry = registry
    tool_registry = MagicMock()
    tool_registry._tools = {"http_api": MagicMock(), "web_search": MagicMock()}
    mock_agent.tool_registry = tool_registry

    skill_registry = MagicMock()
    # list_all_loaded_skills 真实返回 List[str]（skill 名称），mock 保持同构避免掩盖类型错误
    skill_registry.list_all_loaded_skills.return_value = ["pre-sales-api"]
    mock_agent.skill_registry = skill_registry

    monkeypatch.setattr("src.core.agent.master_agent", mock_agent, raising=False)
    monkeypatch.setattr("src.core.agent.get_master_agent", lambda: mock_agent, raising=False)
    return mock_agent


# ============== 导出 ==============

class TestExport:
    def test_export_success_with_env_placeholders(self, client, monkeypatch):
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "get_definition",
            staticmethod(lambda agent_id: _definition()),
        )
        monkeypatch.setattr(api_mod, "_get_prompt_content", lambda agent_id: "你是员工A")
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "get_sections",
            staticmethod(lambda agent_id: [
                {"section_key": "xitongtishici", "content": "系统提示词内容", "updated_by": "u1"},
            ]),
        )
        monkeypatch.setattr(
            "src.db.subagent_env_var.SubagentEnvVarDB.list_var_names",
            staticmethod(lambda name: [{"var_name": "AGENT_TOKEN", "description": "推送凭证"}]),
        )

        res = client.get("/api/admin/agent-definitions/emp_a/export")
        assert res.status_code == 200
        body = res.json()
        assert body["success"] is True
        data = body["data"]
        assert data["format"] == "agent_definition_export"
        assert data["version"] == 1
        assert data["definition"]["agent_id"] == "emp_a"
        assert data["definition"]["status"] == "active"
        assert data["definition"]["system_prompt"] == "你是员工A"
        # 分段变量值随文件导出（仅 key + content，不含审计字段）
        assert data["definition"]["sections"] == [
            {"section_key": "xitongtishici", "content": "系统提示词内容"}
        ]
        # 敏感信息仅导出占位符（名称+说明），不含任何值
        assert data["env_var_placeholders"] == [{"name": "AGENT_TOKEN", "description": "推送凭证"}]
        assert "AGENT_TOKEN" not in json.dumps(data["definition"])
        assert any("环境变量" in n for n in data["notes"])

    def test_export_sections_failure_degrades_to_empty(self, client, monkeypatch):
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "get_definition",
            staticmethod(lambda agent_id: _definition()),
        )
        monkeypatch.setattr(api_mod, "_get_prompt_content", lambda agent_id: "你是员工A")
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "get_sections",
            staticmethod(lambda agent_id: (_ for _ in ()).throw(RuntimeError("db down"))),
        )

        res = client.get("/api/admin/agent-definitions/emp_a/export")
        assert res.status_code == 200
        # 降级为 None（非空列表）：防止空 sections 覆盖导入时清空目标环境分段
        assert res.json()["data"]["definition"]["sections"] is None

    def test_export_not_found(self, client, monkeypatch):
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "get_definition", staticmethod(lambda agent_id: None),
        )
        res = client.get("/api/admin/agent-definitions/nope/export")
        assert res.status_code == 404


# ============== 导入 ==============

class TestImport:
    def test_import_bad_format(self, client):
        res = client.post("/api/admin/agent-definitions/import", json=_import_payload(format="other"))
        assert res.status_code == 400

    def test_import_too_new_version(self, client):
        res = client.post("/api/admin/agent-definitions/import", json=_import_payload(version=99))
        assert res.status_code == 400

    def test_import_missing_required_fields(self, client):
        payload = _import_payload()
        payload["definition"]["system_prompt"] = ""
        res = client.post("/api/admin/agent-definitions/import", json=payload)
        assert res.status_code == 400

    def test_import_invalid_agent_id(self, client):
        payload = _import_payload()
        payload["definition"]["agent_id"] = "bad id!"
        res = client.post("/api/admin/agent-definitions/import", json=payload)
        assert res.status_code == 400

    def test_import_create_success(self, client, monkeypatch):
        _mock_agent(monkeypatch)
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "get_definition", staticmethod(lambda agent_id: None),
        )
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "apply_skill_requirements",
            staticmethod(lambda tools, skills, recap: (tools, recap, [])),
        )
        create_mock = MagicMock(return_value={"definition": {}})
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "create_definition", staticmethod(create_mock),
        )

        res = client.post("/api/admin/agent-definitions/import", json=_import_payload())
        assert res.status_code == 200
        body = res.json()
        assert body["success"] is True
        assert body["data"]["mode"] == "create"
        assert create_mock.call_args.kwargs["agent_id"] == "emp_a"
        assert create_mock.call_args.kwargs["system_prompt"] == "你是员工A"

    def test_import_no_skill_requirement_completion(self, client, monkeypatch):
        """导入按文件原样：不按技能声明自动补全 tools/recap（2026-10-23 PM 反馈：
        导出文件不含 lead_refresh，导入后被自动补上，与源环境配置不一致）"""
        _mock_agent(monkeypatch)
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "get_definition", staticmethod(lambda agent_id: None),
        )
        apply_mock = MagicMock(return_value=({}, {}, ["recap: lead_refresh(every_round)"]))
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "apply_skill_requirements", apply_mock,
        )
        create_mock = MagicMock(return_value={"definition": {}})
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "create_definition", staticmethod(create_mock),
        )
        payload = _import_payload()
        payload["definition"]["recap"] = {
            "tasks": [{"name": "external_push", "when": "every_round", "enabled": True}],
        }

        res = client.post("/api/admin/agent-definitions/import", json=payload)
        assert res.status_code == 200
        apply_mock.assert_not_called()
        assert create_mock.call_args.kwargs["tools"] == {"inherit": False, "additional": ["http_api"]}
        assert create_mock.call_args.kwargs["recap"] == {
            "tasks": [{"name": "external_push", "when": "every_round", "enabled": True}],
        }
        assert "auto_completed" not in res.json()

    def test_import_conflict_returns_409_with_info(self, client, monkeypatch):
        reg_cfg = MagicMock()
        reg_cfg.from_db = True
        reg_cfg.name = "已有员工"
        _mock_agent(monkeypatch, reg_cfg=reg_cfg)
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "get_definition",
            staticmethod(lambda agent_id: _definition(agent_id=agent_id)),
        )

        res = client.post("/api/admin/agent-definitions/import", json=_import_payload())
        assert res.status_code == 409
        conflict = res.json()["conflict"]
        assert conflict["agent_id"] == "emp_a"
        assert conflict["type"] == "custom"

    def test_import_conflict_pure_builtin(self, client, monkeypatch):
        reg_cfg = MagicMock()
        reg_cfg.from_db = False
        reg_cfg.name = "内置员工"
        _mock_agent(monkeypatch, reg_cfg=reg_cfg)
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "get_definition", staticmethod(lambda agent_id: None),
        )

        res = client.post("/api/admin/agent-definitions/import", json=_import_payload())
        assert res.status_code == 409
        assert res.json()["conflict"]["type"] == "builtin"

    def test_import_overwrite_builtin_rejected(self, client, monkeypatch):
        reg_cfg = MagicMock()
        reg_cfg.from_db = False
        reg_cfg.name = "内置员工"
        _mock_agent(monkeypatch, reg_cfg=reg_cfg)
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "get_definition", staticmethod(lambda agent_id: None),
        )

        res = client.post(
            "/api/admin/agent-definitions/import",
            json=_import_payload(conflict_strategy="overwrite"),
        )
        assert res.status_code == 409

    def test_import_overwrite_updates_definition_and_prompt(self, client, monkeypatch):
        reg_cfg = MagicMock()
        reg_cfg.from_db = True
        reg_cfg.name = "已有员工"
        _mock_agent(monkeypatch, reg_cfg=reg_cfg)
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "get_definition",
            staticmethod(lambda agent_id: _definition(agent_id=agent_id)),
        )
        update_mock = MagicMock(return_value={})
        prompt_mock = MagicMock(return_value={})
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "update_definition", staticmethod(update_mock),
        )
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "update_system_prompt", staticmethod(prompt_mock),
        )
        monkeypatch.setattr("src.core.cache_utils.delete_cached", lambda *a, **k: None)

        res = client.post(
            "/api/admin/agent-definitions/import",
            json=_import_payload(conflict_strategy="overwrite"),
        )
        assert res.status_code == 200
        assert res.json()["data"]["mode"] == "overwrite"
        # 覆盖时空容器/空串透传（DB update 过滤 None，空值是清残留的唯一途径）
        kwargs = update_mock.call_args.kwargs
        assert kwargs["name"] == "员工A"
        assert "recap" in kwargs and "triggers" in kwargs
        # 按文件原样透传，不按技能声明补全（2026-10-23 导入保真）
        assert kwargs["tools"] == {"inherit": False, "additional": ["http_api"]}
        assert kwargs["recap"] == {"tasks": []}
        # 知识库栏目代号跨环境不通用：不随导入迁移，保持目标环境原值
        assert "knowledge_sources" not in kwargs
        prompt_mock.assert_called_once()
        # system_prompt 以位置参数传入（agent_id, content, ...）
        assert prompt_mock.call_args.args[1] == "你是员工A"

    def test_import_rename_creates_new(self, client, monkeypatch):
        reg_cfg = MagicMock()
        reg_cfg.from_db = True
        reg_cfg.name = "已有员工"
        _mock_agent(monkeypatch, reg_cfg=reg_cfg)
        # 第一次查 emp_a 命中冲突，第二次查新 ID 未命中
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "get_definition",
            staticmethod(lambda agent_id: _definition(agent_id=agent_id) if agent_id == "emp_a" else None),
        )
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "apply_skill_requirements",
            staticmethod(lambda tools, skills, recap: (tools, recap, [])),
        )
        create_mock = MagicMock(return_value={"definition": {}})
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "create_definition", staticmethod(create_mock),
        )

        res = client.post(
            "/api/admin/agent-definitions/import",
            json=_import_payload(
                conflict_strategy="rename", new_agent_id="emp_b", new_name="员工B",
            ),
        )
        assert res.status_code == 200
        body = res.json()
        assert body["data"]["mode"] == "create"
        assert body["data"]["agent_id"] == "emp_b"
        assert create_mock.call_args.kwargs["agent_id"] == "emp_b"
        assert create_mock.call_args.kwargs["name"] == "员工B"

    def test_import_rename_to_existing_id_rejected(self, client, monkeypatch):
        reg_cfg = MagicMock()
        reg_cfg.from_db = True
        reg_cfg.name = "已有员工"
        _mock_agent(monkeypatch, reg_cfg=reg_cfg)
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "get_definition",
            staticmethod(lambda agent_id: _definition(agent_id=agent_id)),
        )

        res = client.post(
            "/api/admin/agent-definitions/import",
            json=_import_payload(
                conflict_strategy="rename", new_agent_id="emp_a",
            ),
        )
        assert res.status_code == 409

    def test_import_missing_tool_warning_not_blocking(self, client, monkeypatch):
        _mock_agent(monkeypatch)
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "get_definition", staticmethod(lambda agent_id: None),
        )
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "apply_skill_requirements",
            staticmethod(lambda tools, skills, recap: (tools, recap, [])),
        )
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "create_definition",
            staticmethod(lambda **kwargs: {"definition": {}}),
        )
        payload = _import_payload()
        payload["definition"]["tools"] = {"inherit": False, "additional": ["http_api", "ghost_tool"]}

        res = client.post("/api/admin/agent-definitions/import", json=payload)
        assert res.status_code == 200
        assert any("ghost_tool" in w for w in res.json()["warnings"])

    def test_import_missing_skill_warning(self, client, monkeypatch):
        _mock_agent(monkeypatch)
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "get_definition", staticmethod(lambda agent_id: None),
        )
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "apply_skill_requirements",
            staticmethod(lambda tools, skills, recap: (tools, recap, [])),
        )
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "create_definition",
            staticmethod(lambda **kwargs: {"definition": {}}),
        )
        payload = _import_payload()
        payload["definition"]["skills"] = {"allowed": ["pre-sales-api", "ghost-skill"]}

        res = client.post("/api/admin/agent-definitions/import", json=payload)
        assert res.status_code == 200
        assert any("ghost-skill" in w for w in res.json()["warnings"])


# ============== 分段变量值导出/导入 ==============

def _mock_section_db(monkeypatch):
    """mock subagent_prompt_sections DB 层，记录调用"""
    calls = {"upsert": [], "delete": 0}

    def _upsert(agent_id, section_key, content, updated_by=None):
        calls["upsert"].append((agent_id, section_key, content))
        return {"agent_id": agent_id, "section_key": section_key, "content": content}

    def _delete(agent_id):
        calls["delete"] += 1
        return True

    monkeypatch.setattr(
        "src.db.subagent_prompt_section_db.SubagentPromptSectionDB.upsert_section",
        staticmethod(_upsert),
    )
    monkeypatch.setattr(
        "src.db.subagent_prompt_section_db.SubagentPromptSectionDB.delete_sections",
        staticmethod(_delete),
    )
    monkeypatch.setattr("src.core.cache_utils.delete_cached", lambda *a, **k: None)
    return calls


class TestImportSections:
    def test_import_old_format_without_sections_warns(self, client, monkeypatch):
        _mock_agent(monkeypatch)
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "get_definition", staticmethod(lambda agent_id: None),
        )
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "apply_skill_requirements",
            staticmethod(lambda tools, skills, recap: (tools, recap, [])),
        )
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "create_definition",
            staticmethod(lambda **kwargs: {"definition": {}}),
        )
        calls = _mock_section_db(monkeypatch)
        # 旧版导出格式：definition 中完全没有 sections 键
        payload = _import_payload()

        res = client.post("/api/admin/agent-definitions/import", json=payload)
        assert res.status_code == 200
        assert any("分段变量" in w for w in res.json()["warnings"])
        assert calls["upsert"] == []
        assert calls["delete"] == 0

    def test_import_create_restores_sections(self, client, monkeypatch):
        _mock_agent(monkeypatch)
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "get_definition", staticmethod(lambda agent_id: None),
        )
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "apply_skill_requirements",
            staticmethod(lambda tools, skills, recap: (tools, recap, [])),
        )
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "create_definition",
            staticmethod(lambda **kwargs: {"definition": {}}),
        )
        calls = _mock_section_db(monkeypatch)
        payload = _import_payload()
        payload["definition"]["sections"] = [
            {"section_key": "xitongtishici", "content": "内容A"},
            {"section_key": "bad key!", "content": "非法key"},
            {"section_key": "", "content": "空key"},
        ]

        res = client.post("/api/admin/agent-definitions/import", json=payload)
        assert res.status_code == 200
        assert calls["upsert"] == [("emp_a", "xitongtishici", "内容A")]
        assert calls["delete"] == 0
        warnings = res.json()["warnings"]
        assert any("bad key!" in w for w in warnings)

    def test_import_overwrite_replaces_sections(self, client, monkeypatch):
        reg_cfg = MagicMock()
        reg_cfg.from_db = True
        reg_cfg.name = "已有员工"
        _mock_agent(monkeypatch, reg_cfg=reg_cfg)
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "get_definition",
            staticmethod(lambda agent_id: _definition(agent_id=agent_id)),
        )
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "apply_skill_requirements",
            staticmethod(lambda tools, skills, recap: (tools, recap, [])),
        )
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "update_definition", staticmethod(MagicMock(return_value={})),
        )
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "update_system_prompt", staticmethod(MagicMock(return_value={})),
        )
        calls = _mock_section_db(monkeypatch)
        payload = _import_payload(conflict_strategy="overwrite")
        payload["definition"]["sections"] = [{"section_key": "k1", "content": "v1"}]

        res = client.post("/api/admin/agent-definitions/import", json=payload)
        assert res.status_code == 200
        # 覆盖模式：先清残留再写入，避免目标环境旧分段遗留
        assert calls["delete"] == 1
        assert calls["upsert"] == [("emp_a", "k1", "v1")]

    def test_import_overwrite_old_format_keeps_existing_sections(self, client, monkeypatch):
        """旧版导出文件覆盖导入时不得清空目标环境已有分段"""
        reg_cfg = MagicMock()
        reg_cfg.from_db = True
        reg_cfg.name = "已有员工"
        _mock_agent(monkeypatch, reg_cfg=reg_cfg)
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "get_definition",
            staticmethod(lambda agent_id: _definition(agent_id=agent_id)),
        )
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "apply_skill_requirements",
            staticmethod(lambda tools, skills, recap: (tools, recap, [])),
        )
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "update_definition", staticmethod(MagicMock(return_value={})),
        )
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "update_system_prompt", staticmethod(MagicMock(return_value={})),
        )
        calls = _mock_section_db(monkeypatch)
        payload = _import_payload(conflict_strategy="overwrite")  # 无 sections 键

        res = client.post("/api/admin/agent-definitions/import", json=payload)
        assert res.status_code == 200
        assert calls["delete"] == 0
        assert calls["upsert"] == []

    def test_import_rename_restores_sections_to_new_id(self, client, monkeypatch):
        reg_cfg = MagicMock()
        reg_cfg.from_db = True
        reg_cfg.name = "已有员工"
        _mock_agent(monkeypatch, reg_cfg=reg_cfg)
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "get_definition",
            staticmethod(lambda agent_id: _definition(agent_id=agent_id) if agent_id == "emp_a" else None),
        )
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "apply_skill_requirements",
            staticmethod(lambda tools, skills, recap: (tools, recap, [])),
        )
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "create_definition",
            staticmethod(lambda **kwargs: {"definition": {}}),
        )
        calls = _mock_section_db(monkeypatch)
        payload = _import_payload(
            conflict_strategy="rename", new_agent_id="emp_b", new_name="员工B",
        )
        payload["definition"]["sections"] = [{"section_key": "k1", "content": "v1"}]

        res = client.post("/api/admin/agent-definitions/import", json=payload)
        assert res.status_code == 200
        assert calls["upsert"] == [("emp_b", "k1", "v1")]

    def test_import_sections_non_dict_item_warns(self, client, monkeypatch):
        _mock_agent(monkeypatch)
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "get_definition", staticmethod(lambda agent_id: None),
        )
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "apply_skill_requirements",
            staticmethod(lambda tools, skills, recap: (tools, recap, [])),
        )
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "create_definition",
            staticmethod(lambda **kwargs: {"definition": {}}),
        )
        calls = _mock_section_db(monkeypatch)
        payload = _import_payload()
        payload["definition"]["sections"] = ["not_a_dict", {"section_key": "k1", "content": "v1"}]

        res = client.post("/api/admin/agent-definitions/import", json=payload)
        assert res.status_code == 200
        assert calls["upsert"] == [("emp_a", "k1", "v1")]
        assert any("格式异常" in w for w in res.json()["warnings"])

    def test_import_overwrite_delete_failure_warns_and_keeps_upsert(self, client, monkeypatch):
        reg_cfg = MagicMock()
        reg_cfg.from_db = True
        reg_cfg.name = "已有员工"
        _mock_agent(monkeypatch, reg_cfg=reg_cfg)
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "get_definition",
            staticmethod(lambda agent_id: _definition(agent_id=agent_id)),
        )
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "apply_skill_requirements",
            staticmethod(lambda tools, skills, recap: (tools, recap, [])),
        )
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "update_definition", staticmethod(MagicMock(return_value={})),
        )
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "update_system_prompt", staticmethod(MagicMock(return_value={})),
        )
        monkeypatch.setattr(
            "src.db.subagent_prompt_section_db.SubagentPromptSectionDB.delete_sections",
            staticmethod(lambda agent_id: False),
        )
        monkeypatch.setattr(
            "src.db.subagent_prompt_section_db.SubagentPromptSectionDB.upsert_section",
            staticmethod(lambda agent_id, key, content, updated_by=None: {"section_key": key}),
        )
        monkeypatch.setattr("src.core.cache_utils.delete_cached", lambda *a, **k: None)
        payload = _import_payload(conflict_strategy="overwrite")
        payload["definition"]["sections"] = [{"section_key": "k1", "content": "v1"}]

        res = client.post("/api/admin/agent-definitions/import", json=payload)
        assert res.status_code == 200
        assert any("清理目标环境已有分段变量失败" in w for w in res.json()["warnings"])

    def test_import_status_disabled_on_create(self, client, monkeypatch):
        _mock_agent(monkeypatch)
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "get_definition", staticmethod(lambda agent_id: None),
        )
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "apply_skill_requirements",
            staticmethod(lambda tools, skills, recap: (tools, recap, [])),
        )
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "create_definition",
            staticmethod(lambda **kwargs: {"definition": {}}),
        )
        update_mock = MagicMock(return_value={})
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "update_definition", staticmethod(update_mock),
        )
        payload = _import_payload()
        payload["definition"]["status"] = "disabled"

        res = client.post("/api/admin/agent-definitions/import", json=payload)
        assert res.status_code == 200
        # DB create 默认 active，禁用状态需补一次 update
        update_mock.assert_called_once()
        assert update_mock.call_args.args[0] == "emp_a"
        assert update_mock.call_args.kwargs["status"] == "disabled"

    def test_import_overwrite_passes_status(self, client, monkeypatch):
        reg_cfg = MagicMock()
        reg_cfg.from_db = True
        reg_cfg.name = "已有员工"
        _mock_agent(monkeypatch, reg_cfg=reg_cfg)
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "get_definition",
            staticmethod(lambda agent_id: _definition(agent_id=agent_id)),
        )
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "apply_skill_requirements",
            staticmethod(lambda tools, skills, recap: (tools, recap, [])),
        )
        update_mock = MagicMock(return_value={})
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "update_definition", staticmethod(update_mock),
        )
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "update_system_prompt", staticmethod(MagicMock(return_value={})),
        )
        monkeypatch.setattr("src.core.cache_utils.delete_cached", lambda *a, **k: None)
        payload = _import_payload(conflict_strategy="overwrite")
        payload["definition"]["status"] = "disabled"

        res = client.post("/api/admin/agent-definitions/import", json=payload)
        assert res.status_code == 200
        assert update_mock.call_args.kwargs["status"] == "disabled"

    def test_import_invalid_status_warns(self, client, monkeypatch):
        _mock_agent(monkeypatch)
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "get_definition", staticmethod(lambda agent_id: None),
        )
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "apply_skill_requirements",
            staticmethod(lambda tools, skills, recap: (tools, recap, [])),
        )
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "create_definition",
            staticmethod(lambda **kwargs: {"definition": {}}),
        )
        update_mock = MagicMock(return_value={})
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "update_definition", staticmethod(update_mock),
        )
        _mock_section_db(monkeypatch)
        payload = _import_payload()
        payload["definition"]["status"] = "paused"

        res = client.post("/api/admin/agent-definitions/import", json=payload)
        assert res.status_code == 200
        assert any("paused" in w for w in res.json()["warnings"])
        update_mock.assert_not_called()

    def test_import_knowledge_sources_not_migrated(self, client, monkeypatch):
        """知识库栏目代号跨环境不通用：导入时不迁移并警告"""
        _mock_agent(monkeypatch)
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "get_definition", staticmethod(lambda agent_id: None),
        )
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "apply_skill_requirements",
            staticmethod(lambda tools, skills, recap: (tools, recap, [])),
        )
        create_mock = MagicMock(return_value={"definition": {}})
        monkeypatch.setattr(
            api_mod.SubagentDefinitionService, "create_definition", staticmethod(create_mock),
        )
        payload = _import_payload()
        payload["definition"]["knowledge_sources"] = [
            {"source_type": "k_abc123def456", "display_name": "产品手册"},
        ]

        res = client.post("/api/admin/agent-definitions/import", json=payload)
        assert res.status_code == 200
        assert any("知识库栏目关联不随导入迁移" in w for w in res.json()["warnings"])
        assert "knowledge_sources" not in create_mock.call_args.kwargs
