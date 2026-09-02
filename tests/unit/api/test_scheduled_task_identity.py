"""_get_request_identity 身份解析单测（无外部依赖）

租户来源：优先请求上下文（TenantMiddleware 注入），缺失时回退认证用户行上的
tenant_id（''=平台管理员/公共用户）；两处来源都缺失视为上下文不可信，403 拒绝，
绝不把未知身份隐式当成公共租户。SaaS 开关已移除，行为不再分模式。
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.scheduled_task import _get_request_identity


def test_user_row_tenant_used_when_context_missing():
    """上下文缺失时回退认证用户行租户（可信来源）"""
    request = MagicMock()
    with (
        patch("src.api.scheduled_task.get_current_tenant_id", return_value=None),
        patch("src.api.scheduled_task.get_current_user",
              return_value={"user_id": "u1", "tenant_id": "t9"}),
    ):
        assert _get_request_identity(request) == ("u1", "t9")


def test_user_row_empty_tenant_normalized_to_empty_string():
    """公共用户（用户行 tenant_id 为空串/None）归一化为 ''，不进租户过滤"""
    request = MagicMock()
    with (
        patch("src.api.scheduled_task.get_current_tenant_id", return_value=None),
        patch("src.api.scheduled_task.get_current_user",
              return_value={"user_id": "u1", "tenant_id": None}),
    ):
        assert _get_request_identity(request) == ("u1", "")


def test_missing_all_tenant_sources_fail_closed_403():
    """上下文与用户行都无租户（user dict 无 tenant_id 键）→ 403，不隐式当公共租户"""
    request = MagicMock()
    with (
        patch("src.api.scheduled_task.get_current_tenant_id", return_value=None),
        patch("src.api.scheduled_task.get_current_user",
              return_value={"user_id": "u1"}),
    ):
        with pytest.raises(HTTPException) as exc_info:
            _get_request_identity(request)
        assert exc_info.value.status_code == 403


def test_request_context_tenant_takes_priority_over_user_row():
    """请求上下文租户优先于用户行（平台管理员代管理场景语义一致）"""
    request = MagicMock()
    with (
        patch("src.api.scheduled_task.get_current_tenant_id", return_value="ctx_t"),
        patch("src.api.scheduled_task.get_current_user",
              return_value={"user_id": "u1", "tenant_id": "row_t"}),
    ):
        assert _get_request_identity(request) == ("u1", "ctx_t")


def test_unauthenticated_401():
    """未登录（get_current_user 返回 None）→ 401"""
    request = MagicMock()
    with (
        patch("src.api.scheduled_task.get_current_tenant_id", return_value=None),
        patch("src.api.scheduled_task.get_current_user", return_value=None),
    ):
        with pytest.raises(HTTPException) as exc_info:
            _get_request_identity(request)
        assert exc_info.value.status_code == 401
