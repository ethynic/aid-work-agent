"""
回归测试：/api/subagents 接口必须按租户订阅过滤数字员工列表。

背景：
- 平台管理员通过 X-Tenant-Id 代管租户时，中间件会将 tenant_id 设置到 ContextVar
- 但 src/api/subagent.py:list_subagents 之前调用 get_allowed_agent_ids_for_user(user)
  时未传 target_tenant_id，导致 fallback 到 user["tenant_id"]（平台管理员为 NULL），
  返回全部数字员工，绕过了租户订阅权限过滤

本测试验证修复后 list_subagents 正确传递 target_tenant_id，按目标租户订阅过滤。
"""

import pytest
from unittest.mock import patch, MagicMock

import src.api.subagent as subagent_module


def _make_item(agent_id: str, name: str):
    """构造一个数字员工列表项"""
    return {
        "agent_id": agent_id,
        "name": name,
        "description": f"{name}描述",
        "type": "builtin",
        "business_pages": [],
    }


def _make_platform_admin_user():
    """构造平台管理员 user dict（tenant_id 为 None）"""
    return {
        "user_id": "admin_001",
        "username": "platform_admin",
        "role": "platform_admin",
        "tenant_id": None,
    }


@pytest.mark.asyncio
async def test_list_subagents_filters_by_target_tenant_for_platform_admin():
    """平台管理员代管租户时，应按目标租户订阅过滤，target_tenant_id 正确传递"""
    all_items = [
        _make_item("travel-consultant", "旅游咨询顾问"),
        _make_item("trade-specialist", "外贸获客智能体"),
        _make_item("after-sales", "在线电商客服"),
    ]

    mock_registry = MagicMock()
    mock_registry.load_from_db = MagicMock()
    mock_registry.get_all_subagents_with_type = MagicMock(return_value=all_items)

    target_tenant_id = "tenant_9eb3e45cab83"
    allowed_ids = ["travel-consultant"]

    with patch.object(subagent_module, "master_agent") as mock_master, \
         patch.object(subagent_module, "get_current_tenant_id", return_value=target_tenant_id), \
         patch.object(subagent_module, "get_allowed_agent_ids_for_user", return_value=allowed_ids) as mock_allowed, \
         patch("src.api.auth.get_current_user", return_value=_make_platform_admin_user()):
        mock_master.subagent_registry = mock_registry

        request = MagicMock()
        result = await subagent_module.list_subagents(request)

    # 验证 get_allowed_agent_ids_for_user 被调用时传了 target_tenant_id
    mock_allowed.assert_called_once()
    call_args = mock_allowed.call_args
    assert call_args.kwargs.get("target_tenant_id") == target_tenant_id, \
        f"应传 target_tenant_id={target_tenant_id}，实际传了 {call_args.kwargs.get('target_tenant_id')}"

    # 验证返回结果按允许列表过滤
    assert result["success"] is True
    returned_ids = [item["agent_id"] for item in result["data"]]
    assert returned_ids == ["travel-consultant"], \
        f"应只返回 travel-consultant，实际返回 {returned_ids}"


@pytest.mark.asyncio
async def test_list_subagents_no_tenant_context_returns_all_with_main():
    """无租户上下文（tenant_id 为 None）时，返回全部 + main（演示模式行为）"""
    all_items = [
        _make_item("travel-consultant", "旅游咨询顾问"),
        _make_item("trade-specialist", "外贸获客智能体"),
    ]

    mock_registry = MagicMock()
    mock_registry.load_from_db = MagicMock()
    mock_registry.get_all_subagents_with_type = MagicMock(return_value=all_items)

    with patch.object(subagent_module, "master_agent") as mock_master, \
         patch.object(subagent_module, "get_current_tenant_id", return_value=None), \
         patch.object(subagent_module, "get_allowed_agent_ids_for_user") as mock_allowed:
        mock_master.subagent_registry = mock_registry

        request = MagicMock()
        result = await subagent_module.list_subagents(request)

    # 无租户上下文时不应调用权限过滤
    mock_allowed.assert_not_called()

    # 应返回全部 + main
    assert result["success"] is True
    returned_ids = [item["agent_id"] for item in result["data"]]
    assert "main" in returned_ids, "应包含 main CEO 智能体"
    assert "travel-consultant" in returned_ids
    assert "trade-specialist" in returned_ids
