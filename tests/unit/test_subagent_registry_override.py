"""
同 agent_id 数字员工覆盖逻辑单元测试

场景：自定义数字员工（DB 定义）与内置数字员工（文件系统 SUBAGENT.md）
agent_id 相同时，DB 定义应覆盖内置版本：
1. registry.get_all_subagents_with_type() 同 agent_id 只出现一条（type=custom）
2. registry.get(agent_id) 返回 DB 版本
3. DB 定义删除后，内置版本可恢复
4. factory._load_single_from_db 同样覆盖而非并存
"""

from unittest.mock import patch

import pytest

from src.models.subagent import SubagentConfig


BUILTIN_NAME = "售前咨询专员"
CUSTOM_NAME = "爱定义AI数字员工顾问"
AGENT_ID = "pre-sales"


def _make_db_row(**overrides):
    row = {
        "agent_id": AGENT_ID,
        "name": CUSTOM_NAME,
        "description": "custom desc",
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
        "recap": {},
    }
    row.update(overrides)
    return row


def _make_registry_with_builtin():
    """构造等价于 load_from_directory 后的 registry（内置版以显示名为键）"""
    from src.subagents.registry import SubagentRegistry

    registry = SubagentRegistry()
    registry._configs[BUILTIN_NAME] = SubagentConfig(
        name=BUILTIN_NAME,
        dir_name=AGENT_ID,
        description="builtin desc",
        system_prompt="builtin prompt",
    )
    registry._builtin_names = set(registry._configs.keys())
    return registry


def _load_db_definitions(registry, rows):
    with patch("src.db.subagent_definition_db.SubagentDefinitionDB.list_active",
               return_value=rows), \
         patch("src.prompts.prompt_resolver.prompt_resolver.resolve",
               return_value="custom prompt"):
        registry.load_from_db()


class TestSameAgentIdOverride:
    def test_db_overrides_builtin_no_duplicate(self):
        """DB 定义应移除同 agent_id 的内置条目，列表只出现一条 custom"""
        registry = _make_registry_with_builtin()
        _load_db_definitions(registry, [_make_db_row()])

        items = [i for i in registry.get_all_subagents_with_type()
                 if i["agent_id"] == AGENT_ID]
        assert len(items) == 1
        assert items[0]["name"] == CUSTOM_NAME
        assert items[0]["type"] == "custom"

    def test_get_by_agent_id_returns_db_version(self):
        """get(agent_id) 应返回 DB 版本而非内置版"""
        registry = _make_registry_with_builtin()
        _load_db_definitions(registry, [_make_db_row()])

        config = registry.get(AGENT_ID)
        assert config is not None
        assert config.name == CUSTOM_NAME
        assert config.from_db is True

    def test_builtin_restored_after_db_deleted(self):
        """DB 定义删除后再次 load_from_db，内置版本应恢复"""
        registry = _make_registry_with_builtin()
        _load_db_definitions(registry, [_make_db_row()])
        _load_db_definitions(registry, [])

        items = [i for i in registry.get_all_subagents_with_type()
                 if i["agent_id"] == AGENT_ID]
        assert len(items) == 1
        assert items[0]["name"] == BUILTIN_NAME
        assert items[0]["type"] == "builtin"

    def test_db_renamed_replaces_old_entry(self):
        """DB 定义改名后，旧显示名条目不应残留"""
        registry = _make_registry_with_builtin()
        _load_db_definitions(registry, [_make_db_row(name=CUSTOM_NAME)])
        _load_db_definitions(registry, [_make_db_row(name="改名后的员工")])

        names = {i["name"] for i in registry.get_all_subagents_with_type()
                 if i["agent_id"] == AGENT_ID}
        assert names == {"改名后的员工"}

    def test_db_def_without_prompt_falls_back_to_builtin(self):
        """DB 定义存在但 system_prompt 缺失时，自定义条目被移除、内置版兜底且可自愈"""
        registry = _make_registry_with_builtin()
        _load_db_definitions(registry, [_make_db_row()])
        with patch("src.db.subagent_definition_db.SubagentDefinitionDB.list_active",
                   return_value=[_make_db_row()]), \
             patch("src.prompts.prompt_resolver.prompt_resolver.resolve",
                   return_value=""):
            registry.load_from_db()

        items = [i for i in registry.get_all_subagents_with_type()
                 if i["agent_id"] == AGENT_ID]
        assert len(items) == 1
        assert items[0]["name"] == BUILTIN_NAME
        assert items[0]["type"] == "builtin"


class TestFactorySingleLoadOverride:
    def test_load_single_from_db_overrides_builtin(self):
        """factory._load_single_from_db 同样覆盖同 agent_id 的内置条目"""
        from src.subagents.factory import AgentFactory

        registry = _make_registry_with_builtin()
        with patch("src.db.subagent_definition_db.SubagentDefinitionDB.get_by_agent_id",
                   return_value=_make_db_row()), \
             patch("src.prompts.prompt_resolver.prompt_resolver.resolve",
                   return_value="custom prompt"):
            config = AgentFactory._load_single_from_db(registry, AGENT_ID)

        assert config is not None
        assert config.name == CUSTOM_NAME
        items = [i for i in registry.get_all_subagents_with_type()
                 if i["agent_id"] == AGENT_ID]
        assert len(items) == 1
        assert items[0]["type"] == "custom"
