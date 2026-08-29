"""_get_request_identity 身份解析单测（无外部依赖）

非 SaaS 部署：用户行自带租户（如 demo 用户 tenant_id='demo'）也统一解析为 ''，
与工具层 _resolve_runtime_tenant_id 的非 SaaS 语义一致（否则工具创建的 ''
任务在 API 视图不可见）；SaaS 部署缺失租户来源时 fail-closed 403。
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from src.config.settings import settings as app_settings
from src.api.scheduled_task import _get_request_identity


def test_non_saas_user_row_tenant_normalized_to_empty():
    """非 SaaS：用户行 tenant_id='demo' 不进 API 过滤条件，统一 ''（与工具层一致）"""
    request = MagicMock()
    with (
        patch.object(app_settings.saas, "enabled", False),
        patch("src.api.scheduled_task.get_current_tenant_id", return_value=None),
        patch("src.api.scheduled_task.get_current_user",
              return_value={"user_id": "u1", "tenant_id": "demo"}),
    ):
        assert _get_request_identity(request) == ("u1", "")


def test_saas_falls_back_to_authenticated_user_row_tenant():
    """SaaS：请求上下文缺失时回退认证用户行租户（可信来源）"""
    request = MagicMock()
    with (
        patch.object(app_settings.saas, "enabled", True),
        patch("src.api.scheduled_task.get_current_tenant_id", return_value=None),
        patch("src.api.scheduled_task.get_current_user",
              return_value={"user_id": "u1", "tenant_id": "t9"}),
    ):
        assert _get_request_identity(request) == ("u1", "t9")


def test_saas_missing_all_tenant_sources_fail_closed_403():
    """SaaS：上下文与用户行都无租户 → 403，不隐式当公共租户"""
    request = MagicMock()
    with (
        patch.object(app_settings.saas, "enabled", True),
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
        patch.object(app_settings.saas, "enabled", True),
        patch("src.api.scheduled_task.get_current_tenant_id", return_value="ctx_t"),
        patch("src.api.scheduled_task.get_current_user",
              return_value={"user_id": "u1", "tenant_id": "row_t"}),
    ):
        assert _get_request_identity(request) == ("u1", "ctx_t")
