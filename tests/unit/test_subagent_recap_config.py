"""
自定义数字员工 recap 配置单元测试

覆盖：
1. SUBAGENT.md frontmatter recap 块解析与 serialize_to_subagent_md 回写
2. registry.load_from_db / factory._load_single_from_db 从 subagent_definitions.recap 透传
3. SubagentDefinitionDB.update recap 进入 allowed/jsonb_fields（SQL 含 recap 列）
4. UpdateDefinitionRequest recap 的 exclude_unset 语义
5. /meta/recap-tasks 元数据接口返回结构
"""

from unittest.mock import MagicMock, patch

import pytest

from src.models.subagent import SubagentConfig


RECAP_CONFIG = {
    "tasks": [
        {"name": "external_push", "when": "every_round", "enabled": True},
    ]
}


# ============== SUBAGENT.md 解析与序列化 ==============

class TestSubagentMdRecap:
    def test_parse_recap_block(self, tmp_path):
        """frontmatter recap 块被解析为 SubagentConfig.recap"""
        content = """---
name: 测试智能体
recap:
  tasks:
    - name: external_push
      when: every_round
      enabled: true
---

body
"""
        path = tmp_path / "SUBAGENT.md"
        path.write_text(content, encoding="utf-8")

        from src.subagents.loader import SubagentLoader
        config = SubagentLoader().parse_subagent_md(path)

        assert config is not None
        assert config.recap == RECAP_CONFIG

    def test_parse_no_recap_defaults_empty(self, tmp_path):
        """没有 recap 块时 recap 为空 dict"""
        content = """---
name: 测试智能体
---

body
"""
        path = tmp_path / "SUBAGENT.md"
        path.write_text(content, encoding="utf-8")

        from src.subagents.loader import SubagentLoader
        config = SubagentLoader().parse_subagent_md(path)

        assert config is not None
        assert config.recap == {}

    def test_serialize_includes_recap(self):
        """serialize_to_subagent_md 回写 recap 块"""
        config = SubagentConfig(name="测试智能体", recap=RECAP_CONFIG)
        from src.subagents.loader import SubagentLoader
        md = SubagentLoader.serialize_to_subagent_md(config, body="body content")

        assert "recap:" in md
        assert "external_push" in md
        assert "every_round" in md

    def test_serialize_omits_when_no_recap(self):
        """recap 为空时不写 recap 字段"""
        config = SubagentConfig(name="测试智能体")
        from src.subagents.loader import SubagentLoader
        md = SubagentLoader.serialize_to_subagent_md(config, body="body content")

        assert "recap" not in md


# ============== DB 加载透传 ==============

def _make_db_row(**overrides):
    row = {
        "agent_id": "custom-agent",
        "name": "自定义智能体",
        "description": "test",
        "version": "1.0.0",
        "author": "admin",
        "triggers": {},
        "tools": {},
        "skills": {},
        "context": {},
        "delegatable_to": [],
        "allow_delegation": True,
        "llm_provider": None,
        "reply_style": None,
        "business_pages": None,
        "chat_toolbar": [],
        "upload_accept": None,
        "knowledge_sources": [],
        "recap": RECAP_CONFIG,
    }
    row.update(overrides)
    return row


class TestLoadFromDbRecap:
    def test_load_from_db_passes_recap(self):
        """registry.load_from_db 把 DB recap 列透传到 SubagentConfig"""
        from src.subagents.registry import SubagentRegistry

        registry = SubagentRegistry()
        fake_row = _make_db_row()
        with patch("src.db.subagent_definition_db.SubagentDefinitionDB.list_active",
                   return_value=[fake_row]), \
             patch("src.prompts.prompt_resolver.prompt_resolver.resolve",
                   return_value="system prompt content"):
            registry.load_from_db()

        config = registry.get("自定义智能体")
        assert config is not None
        assert config.recap == RECAP_CONFIG
        assert config.from_db is True

    def test_load_from_db_recap_defaults_empty(self):
        """DB 行无 recap 值时 recap 为空 dict（旧行兼容）"""
        from src.subagents.registry import SubagentRegistry

        registry = SubagentRegistry()
        fake_row = _make_db_row(recap=None)
        with patch("src.db.subagent_definition_db.SubagentDefinitionDB.list_active",
                   return_value=[fake_row]), \
             patch("src.prompts.prompt_resolver.prompt_resolver.resolve",
                   return_value="system prompt content"):
            registry.load_from_db()

        config = registry.get("自定义智能体")
        assert config is not None
        assert config.recap == {}

    def test_load_single_from_db_passes_recap(self):
        """factory._load_single_from_db 把 DB recap 列透传到 SubagentConfig"""
        from src.subagents.factory import AgentFactory
        from src.subagents.registry import SubagentRegistry

        registry = SubagentRegistry()
        fake_row = _make_db_row()
        with patch("src.db.subagent_definition_db.SubagentDefinitionDB.get_by_agent_id",
                   return_value=fake_row), \
             patch("src.prompts.prompt_resolver.prompt_resolver.resolve",
                   return_value="system prompt content"):
            config = AgentFactory._load_single_from_db(registry, "custom-agent")

        assert config is not None
        assert config.recap == RECAP_CONFIG


# ============== DB update 允许 recap 列 ==============

class TestDbUpdateRecapAllowed:
    @staticmethod
    def _make_mock_conn():
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None
        mock_conn.cursor.return_value = mock_cursor
        # with get_db_connection() as conn 语义：__enter__ 需返回 conn 自身
        mock_conn.__enter__.return_value = mock_conn
        return mock_conn, mock_cursor

    def test_update_recap_in_sql(self):
        """SubagentDefinitionDB.update 传入 recap 时生成含 recap 列的 UPDATE SQL"""
        from src.db.subagent_definition_db import SubagentDefinitionDB

        mock_conn, mock_cursor = self._make_mock_conn()

        with patch("src.db.subagent_definition_db.get_db_connection",
                   return_value=mock_conn):
            # get_by_agent_id 返回 None 时不影响 SQL 断言
            with patch("src.db.subagent_definition_db.SubagentDefinitionDB.get_by_agent_id",
                       return_value=None):
                SubagentDefinitionDB.update("custom-agent", recap=RECAP_CONFIG)

        sql = mock_cursor.execute.call_args[0][0]
        assert "recap = %s" in sql

    def test_update_ignores_disallowed_fields(self):
        """recap 之外的未知字段仍被过滤"""
        from src.db.subagent_definition_db import SubagentDefinitionDB

        mock_conn, mock_cursor = self._make_mock_conn()

        with patch("src.db.subagent_definition_db.get_db_connection",
                   return_value=mock_conn):
            with patch("src.db.subagent_definition_db.SubagentDefinitionDB.get_by_agent_id",
                       return_value=None):
                SubagentDefinitionDB.update("custom-agent", recap={"tasks": []}, hacker_field="x")

        sql = mock_cursor.execute.call_args[0][0]
        assert "recap = %s" in sql
        assert "hacker_field" not in sql


# ============== API 请求模型 exclude_unset 语义 ==============

class TestRecapRequestSemantics:
    def test_update_request_recap_preserved(self):
        """显式传入 recap（含空 tasks 清空语义）时 exclude_unset 保留"""
        from src.api.agent_definitions import UpdateDefinitionRequest

        body = UpdateDefinitionRequest.model_validate({
            "name": "xxx",
            "recap": {"tasks": []},
        })
        result = body.model_dump(exclude_unset=True)
        assert result["recap"] == {"tasks": []}

    def test_update_request_recap_omitted(self):
        """未传入 recap 时被 exclude_unset 排除（不触碰已有配置）"""
        from src.api.agent_definitions import UpdateDefinitionRequest

        body = UpdateDefinitionRequest.model_validate({"name": "xxx"})
        result = body.model_dump(exclude_unset=True)
        assert "recap" not in result


# ============== /meta/recap-tasks 元数据接口 ==============

class TestRecapTasksMeta:
    @pytest.mark.asyncio
    async def test_meta_returns_tasks_and_when_options(self):
        from src.api import agent_definitions as api

        with patch.object(api, "_require_admin", return_value={"user_id": "admin"}):
            resp = await api.list_recap_tasks_meta(request=MagicMock())

        payload = resp.body if hasattr(resp, "body") else resp
        import json
        data = json.loads(payload) if isinstance(payload, bytes) else payload
        assert data["success"] is True
        names = [t["name"] for t in data["data"]["tasks"]]
        assert "external_push" in names
        assert data["data"]["when_options"] == ["every_round"]
