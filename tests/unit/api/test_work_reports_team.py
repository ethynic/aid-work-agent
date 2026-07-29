"""
团队日报 API 端点测试

覆盖：
- GET  /api/reports/team/today          查今日团队日报缓存（不自动生成）
- GET  /api/reports/team/{report_date}  查指定日期团队日报缓存
- POST /api/reports/team/regenerate     重新生成团队日报

权限校验：
- 仅租户管理员（含平台管理员）可访问
- 普通用户访问返回 403

不自动生成：
- GET /team/today 和 GET /team/{date} 缓存不存在时返回 data=null
"""

from datetime import date
from unittest.mock import patch, MagicMock, AsyncMock

import pytest
from fastapi import HTTPException

import src.api.work_reports as wr_module
from src.api.work_reports import (
    get_team_today,
    get_team_by_date,
    regenerate_team,
    _generate_team_report,
)


# ============================================================
# 工具：构造 mock request / user
# ============================================================

def _make_request(
    user: dict,
    tenant_id: str = "tenant_001",
    has_x_tenant_header: bool = False,
):
    """构造一个带登录态和租户 header 的 mock Request"""
    request = MagicMock()
    request.state.tenant_id = tenant_id
    headers = {}
    if has_x_tenant_header:
        headers["X-Tenant-Id"] = tenant_id
    request.headers = headers
    return request


def _tenant_admin_user():
    return {
        "user_id": "admin_001",
        "role": "tenant_admin",
        "tenant_id": "tenant_001",
        "username": "管理员张三",
    }


def _platform_admin_user():
    return {
        "user_id": "platform_001",
        "role": "platform_admin",
        "tenant_id": None,  # 平台管理员账号本身无租户
        "username": "平台管理员",
    }


def _normal_user():
    return {
        "user_id": "user_001",
        "role": "tenant_user",
        "tenant_id": "tenant_001",
        "username": "普通员工",
    }


# ============================================================
# 权限校验
# ============================================================

class TestTeamPermissionCheck:
    """team 端点权限校验"""

    @pytest.mark.asyncio
    async def test_normal_user_get_today_returns_403(self):
        """普通用户访问 GET /team/today 返回 403"""
        user = _normal_user()
        request = _make_request(user)
        with patch.object(wr_module, "get_current_user", return_value=user):
            with pytest.raises(HTTPException) as exc:
                await get_team_today(request, report_type="daily")
        assert exc.value.status_code == 403
        assert "仅租户管理员" in exc.value.detail

    @pytest.mark.asyncio
    async def test_normal_user_get_by_date_returns_403(self):
        """普通用户访问 GET /team/{date} 返回 403"""
        user = _normal_user()
        request = _make_request(user)
        with patch.object(wr_module, "get_current_user", return_value=user):
            with pytest.raises(HTTPException) as exc:
                await get_team_by_date(request, report_date="2026-07-22", report_type="daily")
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_normal_user_regenerate_returns_403(self):
        """普通用户访问 POST /team/regenerate 返回 403"""
        user = _normal_user()
        request = _make_request(user)
        body = wr_module.RegenerateRequest(report_date="2026-07-22", report_type="daily")
        with patch.object(wr_module, "get_current_user", return_value=user):
            with pytest.raises(HTTPException) as exc:
                await regenerate_team(request, body)
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_unauthenticated_get_today_returns_401(self):
        """未登录访问 GET /team/today 返回 401"""
        request = _make_request({})
        with patch.object(wr_module, "get_current_user", return_value=None):
            with pytest.raises(HTTPException) as exc:
                await get_team_today(request, report_type="daily")
        assert exc.value.status_code == 401


# ============================================================
# 不自动生成
# ============================================================

class TestTeamGetDoesNotAutoGenerate:
    """GET /team/today 和 GET /team/{date} 不自动生成"""

    @pytest.mark.asyncio
    async def test_today_no_cache_returns_null(self):
        """今日团队日报缓存不存在时返回 data=null，不触发生成"""
        user = _tenant_admin_user()
        request = _make_request(user)
        with patch.object(wr_module, "get_current_user", return_value=user), \
             patch.object(wr_module.WorkDailyReportDB, "get", return_value=None) as mock_get:
            result = await get_team_today(request, report_type="daily")

        # 应查询缓存
        mock_get.assert_called_once()
        # 返回 data=null，cached=False
        assert result["success"] is True
        assert result["data"] is None
        assert result["cached"] is False

    @pytest.mark.asyncio
    async def test_today_with_cache_returns_data(self):
        """今日团队日报缓存存在时返回缓存数据"""
        user = _tenant_admin_user()
        request = _make_request(user)
        cached_report = {
            "report_id": "wdr_team_001",
            "scope": "team",
            "report_type": "daily",
            "report_date": "2026-07-22",
            "metrics": {"active_user_count": 5, "input_truncated": False},
            "summary_text": "团队工作摘要",
        }
        with patch.object(wr_module, "get_current_user", return_value=user), \
             patch.object(wr_module.WorkDailyReportDB, "get", return_value=cached_report):
            result = await get_team_today(request, report_type="daily")

        assert result["success"] is True
        assert result["data"] == cached_report
        assert result["cached"] is True

    @pytest.mark.asyncio
    async def test_by_date_no_cache_returns_null(self):
        """指定日期团队日报缓存不存在时返回 data=null"""
        user = _tenant_admin_user()
        request = _make_request(user)
        with patch.object(wr_module, "get_current_user", return_value=user), \
             patch.object(wr_module.WorkDailyReportDB, "get", return_value=None):
            result = await get_team_by_date(request, report_date="2026-07-22", report_type="daily")

        assert result["success"] is True
        assert result["data"] is None
        assert result["cached"] is False

    @pytest.mark.asyncio
    async def test_invalid_date_returns_400(self):
        """非法日期格式返回 400"""
        user = _tenant_admin_user()
        request = _make_request(user)
        with patch.object(wr_module, "get_current_user", return_value=user):
            with pytest.raises(HTTPException) as exc:
                await get_team_by_date(request, report_date="invalid-date", report_type="daily")
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_invalid_report_type_returns_400(self):
        """非法 report_type 返回 400"""
        user = _tenant_admin_user()
        request = _make_request(user)
        with patch.object(wr_module, "get_current_user", return_value=user):
            with pytest.raises(HTTPException) as exc:
                await get_team_today(request, report_type="quarterly")
        assert exc.value.status_code == 400


# ============================================================
# POST /team/regenerate
# ============================================================

class TestTeamRegenerate:
    """POST /team/regenerate 测试"""

    @pytest.mark.asyncio
    async def test_regenerate_success(self):
        """租户管理员重新生成团队日报成功"""
        user = _tenant_admin_user()
        request = _make_request(user)
        body = wr_module.RegenerateRequest(report_date="2026-07-22", report_type="daily")

        mock_tenant = {"tenant_id": "tenant_001", "company_name": "某科技公司"}
        mock_users_page = {"users": [], "total": 20, "page": 1, "page_size": 1}
        mock_report = {
            "report_id": "wdr_team_new",
            "scope": "team",
            "report_type": "daily",
            "report_date": "2026-07-22",
            "metrics": {"active_user_count": 5, "input_truncated": False},
            "summary_text": "团队工作摘要",
            "credit_cost": 8.0,
        }

        with patch.object(wr_module, "get_current_user", return_value=user), \
             patch.object(wr_module.TenantDB, "get_by_id", return_value=mock_tenant) as mock_tenant_db, \
             patch.object(wr_module.UserDB, "list_by_tenant", return_value=mock_users_page) as mock_user_db, \
             patch.object(wr_module.ReportGenerator, "generate_team", new=AsyncMock(return_value=mock_report)) as mock_gen:
            result = await regenerate_team(request, body)

        # 验证 TenantDB.get_by_id 被调用
        mock_tenant_db.assert_called_once_with("tenant_001")
        # 验证 UserDB.list_by_tenant 被调用（取 total）
        mock_user_db.assert_called_once_with("tenant_001", page=1, page_size=1)
        # 验证 generate_team 被调用，参数正确
        mock_gen.assert_called_once()
        call_kwargs = mock_gen.call_args.kwargs
        assert call_kwargs["tenant_id"] == "tenant_001"
        assert call_kwargs["tenant_name"] == "某科技公司"
        assert call_kwargs["total_users"] == 20
        assert call_kwargs["report_date"] == date(2026, 7, 22)
        assert call_kwargs["report_type"] == "daily"
        assert call_kwargs["is_regenerate"] is True
        # 验证返回
        assert result["success"] is True
        assert result["data"] == mock_report
        assert result["cached"] is False

    @pytest.mark.asyncio
    async def test_regenerate_platform_admin_via_header(self):
        """平台管理员通过 X-Tenant-Id 代管租户生成团队日报"""
        user = _platform_admin_user()
        request = _make_request(user, tenant_id="tenant_001", has_x_tenant_header=True)
        body = wr_module.RegenerateRequest(report_date="2026-07-22", report_type="weekly")

        mock_tenant = {"tenant_id": "tenant_001", "company_name": "代管租户"}
        mock_users_page = {"users": [], "total": 50, "page": 1, "page_size": 1}
        mock_report = {"report_id": "wdr_team_pa", "scope": "team", "report_type": "weekly"}

        with patch.object(wr_module, "get_current_user", return_value=user), \
             patch.object(wr_module.TenantDB, "get_by_id", return_value=mock_tenant), \
             patch.object(wr_module.UserDB, "list_by_tenant", return_value=mock_users_page), \
             patch.object(wr_module.ReportGenerator, "generate_team", new=AsyncMock(return_value=mock_report)) as mock_gen:
            result = await regenerate_team(request, body)

        # 验证使用了 header 中的 tenant_id
        call_kwargs = mock_gen.call_args.kwargs
        assert call_kwargs["tenant_id"] == "tenant_001"
        assert call_kwargs["tenant_name"] == "代管租户"
        assert call_kwargs["total_users"] == 50
        assert call_kwargs["report_type"] == "weekly"
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_regenerate_failure_returns_500(self):
        """生成失败时返回 500"""
        user = _tenant_admin_user()
        request = _make_request(user)
        body = wr_module.RegenerateRequest(report_date="2026-07-22", report_type="daily")

        with patch.object(wr_module, "get_current_user", return_value=user), \
             patch.object(wr_module.TenantDB, "get_by_id", return_value={"company_name": "某公司"}), \
             patch.object(wr_module.UserDB, "list_by_tenant", return_value={"total": 20}), \
             patch.object(wr_module.ReportGenerator, "generate_team", new=AsyncMock(side_effect=RuntimeError("LLM 超时"))):
            with pytest.raises(HTTPException) as exc:
                await regenerate_team(request, body)
        assert exc.value.status_code == 500
        assert "生成团队日报失败" in exc.value.detail


# ============================================================
# _generate_team_report 内部函数
# ============================================================

class TestGenerateTeamReportInternal:
    """_generate_team_report 内部函数测试"""

    @pytest.mark.asyncio
    async def test_tenant_name_fallback_to_tenant_id(self):
        """租户不存在时 tenant_name 退化为 tenant_id"""
        mock_report = {"report_id": "wdr_x", "scope": "team"}

        with patch.object(wr_module.TenantDB, "get_by_id", return_value=None), \
             patch.object(wr_module.UserDB, "list_by_tenant", return_value={"total": 10}), \
             patch.object(wr_module.ReportGenerator, "generate_team", new=AsyncMock(return_value=mock_report)) as mock_gen:
            result = await _generate_team_report(
                tenant_id="tenant_xxx",
                report_date=date(2026, 7, 22),
                report_type="daily",
                is_regenerate=False,
            )

        # tenant_name 应 fallback 为 tenant_id
        call_kwargs = mock_gen.call_args.kwargs
        assert call_kwargs["tenant_name"] == "tenant_xxx"
        assert call_kwargs["total_users"] == 10
        assert result["data"] == mock_report
