"""
同 agent_id 数字员工覆盖逻辑单元测试

场景：自定义数字员工（DB 定义）与内置数字员工（文件系统 SUBAGENT.md）
agent_id 相同时，DB 定义应覆盖内置版本：
1. registry.get_all_subagents_with_type() 同 agent_id 只出现一条（type=custom）
2. registry.get(agent_id) 返回 DB 版本
3. DB 定义删除后，内置版本可恢复
4. factory._load_single_from_db 同样覆盖而非并存
5. 不同 agent_id 的 DB 定义显示名相同时两条并存（同名覆盖事故回归）
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
    """构造等价于 load_from_directory 后的 registry（_configs 以 dir_name 为键）"""
    from src.subagents.registry import SubagentRegistry

    registry = SubagentRegistry()
    registry._configs[AGENT_ID] = SubagentConfig(
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


class TestDuplicateDisplayNameCoexist:
    """生产事故回归：不同 agent_id 的 DB 定义显示名相同时，两条必须并存（不得互相覆盖）"""

    OTHER_AGENT_ID = "aidefine-sales-assistant"

    def test_same_display_name_two_agent_ids_both_kept(self):
        registry = _make_registry_with_builtin()
        _load_db_definitions(registry, [
            _make_db_row(),
            _make_db_row(agent_id=self.OTHER_AGENT_ID),
        ])

        items = registry.get_all_subagents_with_type()
        agent_ids = {i["agent_id"] for i in items}
        assert AGENT_ID in agent_ids
        assert self.OTHER_AGENT_ID in agent_ids

        # 各自 get(agent_id) 返回正确版本
        cfg_a = registry.get(AGENT_ID)
        cfg_b = registry.get(self.OTHER_AGENT_ID)
        assert cfg_a is not None and cfg_a.name == CUSTOM_NAME and cfg_a.dir_name == AGENT_ID
        assert cfg_b is not None and cfg_b.name == CUSTOM_NAME and cfg_b.dir_name == self.OTHER_AGENT_ID

    def test_db_delete_only_removes_target_agent(self):
        """DB 删除其中一个定义后，另一个同名定义不受影响"""
        registry = _make_registry_with_builtin()
        _load_db_definitions(registry, [
            _make_db_row(),
            _make_db_row(agent_id=self.OTHER_AGENT_ID),
        ])
        _load_db_definitions(registry, [_make_db_row(agent_id=self.OTHER_AGENT_ID)])

        items = registry.get_all_subagents_with_type()
        agent_ids = {i["agent_id"] for i in items}
        assert AGENT_ID in agent_ids and self.OTHER_AGENT_ID in agent_ids
        # pre-sales 回退为内置版本
        cfg = registry.get(AGENT_ID)
        assert cfg is not None and cfg.name == BUILTIN_NAME


class TestGetLookupOrder:
    """get() 查找顺序：dir_name 直达优先，显示名兜底，撞名时 dir_name 胜出"""

    def test_get_by_dir_name_direct_hit(self):
        registry = _make_registry_with_builtin()
        assert registry.get(AGENT_ID).name == BUILTIN_NAME

    def test_get_by_display_name_fallback(self):
        registry = _make_registry_with_builtin()
        assert registry.get(BUILTIN_NAME).dir_name == AGENT_ID

    def test_get_unknown_returns_none(self):
        registry = _make_registry_with_builtin()
        assert registry.get("不存在的员工") is None

    def test_dir_name_wins_when_collides_with_display_name(self):
        """一个 agent 的显示名恰好等于另一个 agent 的 dir_name 时，dir_name 直达优先"""
        registry = _make_registry_with_builtin()
        registry._configs[BUILTIN_NAME] = SubagentConfig(
            name="其他员工",
            dir_name=BUILTIN_NAME,
            description="dir_name 撞显示名",
            system_prompt="x",
        )
        # BUILTIN_NAME 既是 agent_id 又是另一条的显示名 → 应命中 dir_name 直达的那条
        cfg = registry.get(BUILTIN_NAME)
        assert cfg.dir_name == BUILTIN_NAME
        assert cfg.name == "其他员工"
