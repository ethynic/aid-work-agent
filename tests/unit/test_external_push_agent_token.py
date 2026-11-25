"""external_push AGENT_TOKEN 按智能体隔离单测

覆盖 2026-11-24 定版：移除「兜底遍历租户全部」语义。
缺陷背景：travel-consultant 配置的 AGENT_TOKEN 被同租户未配置的
sales-assistant-v2 / 副本实例借用，对话内容全部推到旅游咨询顾问的
qbweb 应用（tr_runner_b73af3e864144ea889f884306b043581 等 3 条 trace）。
"""

from unittest.mock import MagicMock, patch

import pytest

from src.services.recap.tasks.external_push import _get_agent_token

pytestmark = pytest.mark.unit


def _mock_db(get_vars_rows, all_vars_rows):
    mock_db = MagicMock()
    mock_db.get_vars.return_value = get_vars_rows
    mock_db.get_all_vars_for_tenant.return_value = all_vars_rows
    return mock_db


class TestGetAgentTokenIsolation:
    def test_exact_agent_var_returned(self):
        mock_db = _mock_db(
            [{"var_name": "AGENT_TOKEN", "var_value": "tok_exact"}],
            [{"var_name": "AGENT_TOKEN", "var_value": "tok_other", "subagent_name": "other-agent"}],
        )
        with patch("src.db.subagent_env_var.SubagentEnvVarDB", mock_db):
            assert _get_agent_token("tenant_x", "travel-consultant") == "tok_exact"

    def test_no_borrow_from_other_agents_in_tenant(self):
        """同租户其他智能体配置的 AGENT_TOKEN 不得借用（按智能体隔离）"""
        mock_db = _mock_db(
            [],
            [{"var_name": "AGENT_TOKEN", "var_value": "tok_travel", "subagent_name": "travel-consultant"}],
        )
        with patch("src.db.subagent_env_var.SubagentEnvVarDB", mock_db):
            assert _get_agent_token("tenant_x", "sales-assistant-v2") is None

    def test_replica_resolves_parent_definition(self):
        """副本实例按父定义名查（副本无独立环境变量配置入口）"""
        mock_db = _mock_db(
            [{"var_name": "AGENT_TOKEN", "var_value": "tok_base"}],
            [],
        )
        with patch("src.db.subagent_env_var.SubagentEnvVarDB", mock_db), \
             patch("src.services.tenant_api_doc.resolve_base_agent_id",
                   return_value="sales-assistant"):
            token = _get_agent_token("tenant_x", "sales-assistant-tenant-aa3c4ef6c4f3-pzlt")
        assert mock_db.get_vars.call_args[0] == ("tenant_x", "sales-assistant")
        assert token == "tok_base"

    def test_empty_var_value_ignored(self):
        """空值变量视为未配置（不返回空串）"""
        mock_db = _mock_db(
            [{"var_name": "AGENT_TOKEN", "var_value": ""}],
            [],
        )
        with patch("src.db.subagent_env_var.SubagentEnvVarDB", mock_db):
            assert _get_agent_token("tenant_x", "travel-consultant") is None

    def test_db_error_returns_none(self):
        with patch("src.db.subagent_env_var.SubagentEnvVarDB", side_effect=RuntimeError("db down")):
            assert _get_agent_token("tenant_x", "travel-consultant") is None
