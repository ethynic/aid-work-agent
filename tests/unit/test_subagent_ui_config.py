"""
自定义数字员工 chat_toolbar / upload_accept 配置单元测试

覆盖：
1. SubagentDefinitionDB.update 两字段进入 allowed（chat_toolbar 为 JSONB，upload_accept 为普通 TEXT）
2. SubagentDefinitionService.create_definition 对两字段的透传
3. CreateDefinitionRequest / UpdateDefinitionRequest 的 exclude_unset 语义
4. registry.load_from_db / factory._load_single_from_db 从 DB 行透传到 SubagentConfig
"""

from unittest.mock import MagicMock, patch

from src.models.subagent import SubagentConfig


CHAT_TOOLBAR = ["video_gen"]
UPLOAD_ACCEPT = "image/*"


# ============== DB update 允许两字段 ==============

class TestDbUpdateUiConfigAllowed:
    @staticmethod
    def _make_mock_conn():
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None
        mock_conn.cursor.return_value = mock_cursor
        # with get_db_connection() as conn 语义：__enter__ 需返回 conn 自身
        mock_conn.__enter__.return_value = mock_conn
        return mock_conn, mock_cursor

    def test_update_chat_toolbar_in_sql(self):
        """传入 chat_toolbar 时生成含 chat_toolbar 列的 UPDATE SQL"""
        from src.db.subagent_definition_db import SubagentDefinitionDB

        mock_conn, mock_cursor = self._make_mock_conn()

        with patch("src.db.subagent_definition_db.get_db_connection",
                   return_value=mock_conn), \
             patch("src.db.subagent_definition_db.SubagentDefinitionDB.get_by_agent_id",
                   return_value=None):
            SubagentDefinitionDB.update("custom-agent", chat_toolbar=CHAT_TOOLBAR)

        sql = mock_cursor.execute.call_args[0][0]
        assert "chat_toolbar = %s" in sql

    def test_update_upload_accept_in_sql(self):
        """传入 upload_accept 时生成含 upload_accept 列的 UPDATE SQL"""
        from src.db.subagent_definition_db import SubagentDefinitionDB

        mock_conn, mock_cursor = self._make_mock_conn()

        with patch("src.db.subagent_definition_db.get_db_connection",
                   return_value=mock_conn), \
             patch("src.db.subagent_definition_db.SubagentDefinitionDB.get_by_agent_id",
                   return_value=None):
            SubagentDefinitionDB.update("custom-agent", upload_accept=UPLOAD_ACCEPT)

        sql = mock_cursor.execute.call_args[0][0]
        assert "upload_accept = %s" in sql

    def test_update_empty_string_clears_upload_accept(self):
        """空串不被 None 过滤跳过（清空路径），空列表同理可清空 chat_toolbar"""
        from src.db.subagent_definition_db import SubagentDefinitionDB

        mock_conn, mock_cursor = self._make_mock_conn()

        with patch("src.db.subagent_definition_db.get_db_connection",
                   return_value=mock_conn), \
             patch("src.db.subagent_definition_db.SubagentDefinitionDB.get_by_agent_id",
                   return_value=None):
            SubagentDefinitionDB.update("custom-agent", upload_accept="", chat_toolbar=[])

        sql = mock_cursor.execute.call_args[0][0]
        assert "upload_accept = %s" in sql
        assert "chat_toolbar = %s" in sql


# ============== Service create 透传 ==============

class TestServiceCreatePassthrough:
    def test_create_passes_ui_config_to_db(self):
        """SubagentDefinitionService.create_definition 把两字段透传给 DB create"""
        from src.services import subagent_definition_service as svc

        captured = {}

        def fake_db_create(**kwargs):
            captured.update(kwargs)
            return {"agent_id": "custom-agent"}

        with patch.object(svc.SubagentDefinitionDB, "create", side_effect=fake_db_create), \
             patch.object(svc.SubagentDefinitionDB, "get_by_agent_id", return_value=None):
            svc.SubagentDefinitionService.create_definition(
                agent_id="custom-agent",
                name="自定义智能体",
                system_prompt="prompt",
                chat_toolbar=CHAT_TOOLBAR,
                upload_accept=UPLOAD_ACCEPT,
            )

        assert captured["chat_toolbar"] == CHAT_TOOLBAR
        assert captured["upload_accept"] == UPLOAD_ACCEPT


# ============== API 请求模型 exclude_unset 语义 ==============

class TestUiConfigRequestSemantics:
    def test_update_request_fields_preserved(self):
        """显式传入两字段（含空值清空语义）时 exclude_unset 保留"""
        from src.api.agent_definitions import UpdateDefinitionRequest

        body = UpdateDefinitionRequest.model_validate({
            "name": "xxx",
            "chat_toolbar": [],
            "upload_accept": "",
        })
        result = body.model_dump(exclude_unset=True)
        assert result["chat_toolbar"] == []
        assert result["upload_accept"] == ""

    def test_update_request_fields_omitted(self):
        """未传入两字段时被 exclude_unset 排除（不触碰已有配置）"""
        from src.api.agent_definitions import UpdateDefinitionRequest

        body = UpdateDefinitionRequest.model_validate({"name": "xxx"})
        result = body.model_dump(exclude_unset=True)
        assert "chat_toolbar" not in result
        assert "upload_accept" not in result

    def test_create_request_has_fields(self):
        from src.api.agent_definitions import CreateDefinitionRequest

        body = CreateDefinitionRequest.model_validate({
            "agent_id": "custom-agent",
            "name": "自定义智能体",
            "system_prompt": "prompt",
            "chat_toolbar": CHAT_TOOLBAR,
            "upload_accept": UPLOAD_ACCEPT,
        })
        assert body.chat_toolbar == CHAT_TOOLBAR
        assert body.upload_accept == UPLOAD_ACCEPT


# ============== DB 行加载透传 ==============

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
        "chat_toolbar": CHAT_TOOLBAR,
        "upload_accept": UPLOAD_ACCEPT,
        "knowledge_sources": [],
        "recap": {},
    }
    row.update(overrides)
    return row


class TestLoadFromDbUiConfig:
    def test_load_from_db_passes_ui_config(self):
        """registry.load_from_db 把 DB 两列透传到 SubagentConfig"""
        from src.subagents.registry import SubagentRegistry

        registry = SubagentRegistry()
        with patch("src.db.subagent_definition_db.SubagentDefinitionDB.list_active",
                   return_value=[_make_db_row()]), \
             patch("src.prompts.prompt_resolver.prompt_resolver.resolve",
                   return_value="system prompt content"):
            registry.load_from_db()

        config = registry.get("自定义智能体")
        assert config is not None
        assert config.chat_toolbar == CHAT_TOOLBAR
        assert config.upload_accept == UPLOAD_ACCEPT

    def test_load_from_db_defaults(self):
        """DB 行两列为空时使用默认值（旧行兼容）"""
        from src.subagents.registry import SubagentRegistry

        registry = SubagentRegistry()
        with patch("src.db.subagent_definition_db.SubagentDefinitionDB.list_active",
                   return_value=[_make_db_row(chat_toolbar=None, upload_accept=None)]), \
             patch("src.prompts.prompt_resolver.prompt_resolver.resolve",
                   return_value="system prompt content"):
            registry.load_from_db()

        config = registry.get("自定义智能体")
        assert config is not None
        assert config.chat_toolbar == []
        assert config.upload_accept is None

    def test_load_single_from_db_passes_ui_config(self):
        """factory._load_single_from_db 把 DB 两列透传到 SubagentConfig"""
        from src.subagents.factory import AgentFactory
        from src.subagents.registry import SubagentRegistry

        registry = SubagentRegistry()
        with patch("src.db.subagent_definition_db.SubagentDefinitionDB.get_by_agent_id",
                   return_value=_make_db_row()), \
             patch("src.prompts.prompt_resolver.prompt_resolver.resolve",
                   return_value="system prompt content"):
            config = AgentFactory._load_single_from_db(registry, "custom-agent")

        assert config is not None
        assert config.chat_toolbar == CHAT_TOOLBAR
        assert config.upload_accept == UPLOAD_ACCEPT
