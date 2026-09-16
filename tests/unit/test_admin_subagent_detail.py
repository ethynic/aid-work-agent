"""
内置/自定义数字员工详情端点配置字段单元测试

覆盖：GET /api/admin/subagents/{agent_id} 返回 SubagentConfig 上的
reply_style / llm_provider / llm_model_codes / recap / chat_toolbar /
upload_accept / business_pages，内置与自定义（from_db）均返回。
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from src.models.subagent import SubagentConfig


def _make_config(from_db: bool) -> SubagentConfig:
    return SubagentConfig(
        name="测试智能体",
        dir_name="test-agent",
        description="test",
        system_prompt="prompt body",
        reply_style="human-like",
        llm_provider="deepseek",
        llm_model_codes={"deepseek": "deepseek-flash", "qwen": "qwen3.8-flash"},
        recap={"tasks": [{"name": "external_push", "when": "every_round", "enabled": True}]},
        chat_toolbar=["video_gen"],
        upload_accept="image/*",
        business_pages=[{"id": "vehicles", "title": "车辆价格", "icon": "M3", "route": "/test-agent/vehicles"}],
        from_db=from_db,
    )


async def _call_detail(agent_id: str, config):
    from src.api import admin_subagent as api

    mock_registry = MagicMock()
    mock_registry.get.return_value = config
    mock_registry.is_builtin.return_value = not config.from_db

    request = MagicMock()
    with patch.object(api, "_require_admin", return_value={"user_id": "admin"}), \
         patch.object(api.master_agent, "subagent_registry", mock_registry), \
         patch.object(api.master_agent.subagent_registry, "load_from_db", MagicMock()):
        resp = await api.get_subagent_detail(request=request, agent_id=agent_id)
    payload = resp.body if hasattr(resp, "body") else resp
    return json.loads(payload) if isinstance(payload, bytes) else payload


class TestSubagentDetailUiConfig:
    @pytest.mark.asyncio
    async def test_builtin_detail_returns_ui_config(self):
        """内置数字员工详情返回全部配置字段"""
        data = await _call_detail("test-agent", _make_config(from_db=False))

        assert data["success"] is True
        d = data["data"]
        assert d["type"] == "builtin"
        assert d["reply_style"] == "human-like"
        assert d["llm_provider"] == "deepseek"
        assert d["llm_model_codes"]["deepseek"] == "deepseek-flash"
        assert d["recap"]["tasks"][0]["name"] == "external_push"
        assert d["chat_toolbar"] == ["video_gen"]
        assert d["upload_accept"] == "image/*"
        assert d["business_pages"][0]["id"] == "vehicles"

    @pytest.mark.asyncio
    async def test_custom_detail_returns_ui_config(self):
        """自定义数字员工（from_db）详情同样返回全部配置字段"""
        data = await _call_detail("test-agent", _make_config(from_db=True))

        assert data["success"] is True
        d = data["data"]
        assert d["type"] == "custom"
        assert d["reply_style"] == "human-like"
        assert d["upload_accept"] == "image/*"

    @pytest.mark.asyncio
    async def test_detail_empty_values_returned_verbatim(self):
        """字段为空时原样返回（None/空容器），前端按默认值展示"""
        config = SubagentConfig(name="极简智能体", dir_name="minimal", system_prompt="p")
        data = await _call_detail("minimal", config)

        d = data["data"]
        assert d["reply_style"] is None
        assert d["chat_toolbar"] == []
        assert d["recap"] == {}
        assert d["business_pages"] is None
