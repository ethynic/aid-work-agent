"""
工作日报 API 路由

提供：
- GET  /api/reports/personal/today          获取今日个人日报（自动生成或返回缓存）
- GET  /api/reports/personal/{date}         获取指定日期个人日报
- POST /api/reports/personal/regenerate     重新生成（扣积分）
- GET  /api/reports/personal/list           历史日报列表
- GET  /api/reports/preferences             获取推送配置
- PUT  /api/reports/preferences             更新推送配置

详见 docs/research/ai-agent-experience-daily-report-research.md §4.7.2
"""

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request, Query, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from src.api.auth import get_current_user
from src.db.models import UserDB
from src.reports import ReportGenerator, WorkDailyReportDB, WorkReportPreferenceDB
from src.saas.db.tenant_db import TenantDB
from src.saas.permissions.checker import is_platform_admin, is_tenant_admin

router = APIRouter(prefix="/api/reports", tags=["工作日报"])


# ============== 请求/响应模型 ==============

class RegenerateRequest(BaseModel):
    report_date: str = Field(..., description="报告日期 YYYY-MM-DD")
    report_type: str = Field("daily", description="daily / weekly / monthly")


class UpdatePreferencesRequest(BaseModel):
    personal_report_enabled: Optional[bool] = None
    personal_report_types: Optional[List[str]] = None
    personal_push_channels: Optional[List[str]] = None
    personal_push_time: Optional[str] = None
    team_report_enabled: Optional[bool] = None
    team_report_types: Optional[List[str]] = None
    team_push_channels: Optional[List[str]] = None
    team_push_time: Optional[str] = None


# ============== 工具函数 ==============

def _resolve_user(request: Request) -> Dict[str, Any]:
    """解析当前登录用户，未登录抛 401"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    return user


def _resolve_tenant_id(request: Request, user: Dict[str, Any]) -> str:
    """解析租户 ID

    优先级：X-Tenant-Id header（平台管理员代租户操作）> user.tenant_id
    """
    header_tenant = request.headers.get("X-Tenant-Id")
    if header_tenant and is_platform_admin(user):
        return header_tenant
    tenant_id = user.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=403, detail="无租户归属，无法生成报告")
    return tenant_id


def _parse_date(date_str: str) -> date:
    """解析 YYYY-MM-DD 字符串为 date 对象"""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail=f"日期格式错误：{date_str}，应为 YYYY-MM-DD")


def _validate_report_type(report_type: str) -> str:
    if report_type not in ("daily", "weekly", "monthly"):
        raise HTTPException(status_code=400, detail=f"report_type 必须为 daily/weekly/monthly")
    return report_type


def _validate_report_types(types: Optional[List[str]]) -> Optional[List[str]]:
    if types is None:
        return None
    invalid = [t for t in types if t not in ("daily", "weekly", "monthly")]
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"report_types 含非法值：{invalid}，应为 daily/weekly/monthly"
        )
    return types


# ============== 个人日报 ==============

@router.get("/personal/today")
async def get_personal_today(request: Request, report_type: str = Query("daily")):
    """获取今日个人日报（自动生成或返回缓存）"""
    user = _resolve_user(request)
    tenant_id = _resolve_tenant_id(request, user)
    _validate_report_type(report_type)

    today = date.today()
    return await _get_or_generate_personal(
        tenant_id=tenant_id,
        user=user,
        report_date=today,
        report_type=report_type,
        auto_generate=True,
    )


@router.get("/personal/{report_date}")
async def get_personal_by_date(request: Request, report_date: str, report_type: str = Query("daily")):
    """获取指定日期个人日报（不自动生成，仅查缓存）"""
    user = _resolve_user(request)
    tenant_id = _resolve_tenant_id(request, user)
    _validate_report_type(report_type)
    target_date = _parse_date(report_date)

    return await _get_or_generate_personal(
        tenant_id=tenant_id,
        user=user,
        report_date=target_date,
        report_type=report_type,
        auto_generate=False,
    )


@router.post("/personal/regenerate")
async def regenerate_personal(request: Request, body: RegenerateRequest):
    """重新生成个人日报（强制刷新，扣积分）"""
    user = _resolve_user(request)
    tenant_id = _resolve_tenant_id(request, user)
    _validate_report_type(body.report_type)
    target_date = _parse_date(body.report_date)

    logger.info(
        f"重新生成个人日报: tenant={tenant_id}, user={user.get('user_id')}, "
        f"date={target_date}, type={body.report_type}"
    )
    return await _generate_personal_report(
        tenant_id=tenant_id,
        user=user,
        report_date=target_date,
        report_type=body.report_type,
        is_regenerate=True,
    )


@router.get("/personal/list")
async def list_personal_reports(
    request: Request,
    report_type: Optional[str] = Query(None, description="按报告类型筛选"),
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """历史日报列表"""
    user = _resolve_user(request)
    tenant_id = _resolve_tenant_id(request, user)
    if report_type:
        _validate_report_type(report_type)

    try:
        reports = WorkDailyReportDB.list_by_user(
            tenant_id=tenant_id,
            user_id=user["user_id"],
            scope="personal",
            report_type=report_type,
            limit=limit,
            offset=offset,
        )
        return {"success": True, "data": reports, "total": len(reports)}
    except Exception as e:
        logger.error(f"获取历史日报列表失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="获取历史日报列表失败")


# ============== 推送配置 ==============

@router.get("/preferences")
async def get_preferences(request: Request):
    """获取推送配置"""
    user = _resolve_user(request)
    tenant_id = _resolve_tenant_id(request, user)
    try:
        prefs = WorkReportPreferenceDB.get(tenant_id, user["user_id"])
        return {"success": True, "data": prefs}
    except Exception as e:
        logger.error(f"获取推送配置失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="获取推送配置失败")


@router.put("/preferences")
async def update_preferences(request: Request, body: UpdatePreferencesRequest):
    """更新推送配置"""
    user = _resolve_user(request)
    tenant_id = _resolve_tenant_id(request, user)

    # 团队配置字段统一鉴权：只要请求中包含任意 team_* 字段，需管理员身份
    has_team_fields = any(v is not None for v in (
        body.team_report_enabled, body.team_report_types,
        body.team_push_channels, body.team_push_time,
    ))
    if has_team_fields and not is_platform_admin(user) and user.get("role") != "tenant_admin":
        raise HTTPException(status_code=403, detail="仅管理员可配置团队报告")

    # 字段校验
    fields: Dict[str, Any] = {}
    if body.personal_report_enabled is not None:
        fields["personal_report_enabled"] = body.personal_report_enabled
    if body.personal_report_types is not None:
        fields["personal_report_types"] = _validate_report_types(body.personal_report_types)
    if body.personal_push_channels is not None:
        fields["personal_push_channels"] = body.personal_push_channels
    if body.personal_push_time is not None:
        fields["personal_push_time"] = body.personal_push_time
    if body.team_report_enabled is not None:
        fields["team_report_enabled"] = body.team_report_enabled
    if body.team_report_types is not None:
        fields["team_report_types"] = _validate_report_types(body.team_report_types)
    if body.team_push_channels is not None:
        fields["team_push_channels"] = body.team_push_channels
    if body.team_push_time is not None:
        fields["team_push_time"] = body.team_push_time

    if not fields:
        return {"success": True, "data": WorkReportPreferenceDB.get(tenant_id, user["user_id"])}

    try:
        prefs = WorkReportPreferenceDB.upsert(tenant_id, user["user_id"], **fields)
        return {"success": True, "data": prefs}
    except Exception as e:
        logger.error(f"更新推送配置失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="更新推送配置失败")


# ============== 内部函数 ==============

async def _get_or_generate_personal(
    tenant_id: str,
    user: Dict[str, Any],
    report_date: date,
    report_type: str,
    auto_generate: bool,
):
    """查询缓存报告，不存在时按需生成"""
    # 1. 查缓存
    try:
        existing = WorkDailyReportDB.get(
            tenant_id=tenant_id,
            scope="personal",
            report_type=report_type,
            report_date=report_date,
            target_user_id=user["user_id"],
        )
        if existing:
            return {"success": True, "data": existing, "cached": True}
    except Exception as e:
        logger.error(f"查询个人日报缓存失败: {e}", exc_info=True)

    # 2. 不自动生成（指定日期查询场景）
    if not auto_generate:
        return {"success": True, "data": None, "cached": False}

    # 3. 自动生成
    return await _generate_personal_report(
        tenant_id=tenant_id,
        user=user,
        report_date=report_date,
        report_type=report_type,
        is_regenerate=False,
    )


async def _generate_personal_report(
    tenant_id: str,
    user: Dict[str, Any],
    report_date: date,
    report_type: str,
    is_regenerate: bool,
):
    """实际生成个人日报"""
    user_id = user["user_id"]
    user_name = user.get("username") or user.get("nickname") or f"用户{user_id[-4:]}"
    department = None  # users 表无 department 字段，暂传 None

    try:
        generator = ReportGenerator()
        report = await generator.generate_personal(
            tenant_id=tenant_id,
            user_id=user_id,
            user_name=user_name,
            department=department,
            report_date=report_date,
            report_type=report_type,
            is_regenerate=is_regenerate,
        )
        return {"success": True, "data": report, "cached": False}
    except Exception as e:
        logger.error(
            f"生成个人日报失败: tenant={tenant_id}, user={user_id}, "
            f"date={report_date}, type={report_type}, error={e}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail=f"生成日报失败：{e}")


# ============== 团队日报 ==============

def _require_team_admin(user: Dict[str, Any]) -> None:
    """校验团队报告权限：仅租户管理员（含平台管理员）可访问"""
    if not is_tenant_admin(user):
        raise HTTPException(status_code=403, detail="仅租户管理员可查看/生成团队报告")


@router.get("/team/today")
async def get_team_today(request: Request, report_type: str = Query("daily")):
    """获取今日团队日报缓存（不自动生成）

    团队日报成本较高，仅手动触发 POST /team/regenerate 生成。
    缓存不存在时返回 data=null。
    """
    user = _resolve_user(request)
    _require_team_admin(user)
    tenant_id = _resolve_tenant_id(request, user)
    _validate_report_type(report_type)

    today = date.today()
    try:
        existing = WorkDailyReportDB.get(
            tenant_id=tenant_id,
            scope="team",
            report_type=report_type,
            report_date=today,
            target_user_id=None,
        )
        return {"success": True, "data": existing, "cached": existing is not None}
    except Exception as e:
        logger.error(f"查询团队日报缓存失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="查询团队日报失败")


@router.get("/team/{report_date}")
async def get_team_by_date(request: Request, report_date: str, report_type: str = Query("daily")):
    """获取指定日期团队日报缓存（不自动生成）"""
    user = _resolve_user(request)
    _require_team_admin(user)
    tenant_id = _resolve_tenant_id(request, user)
    _validate_report_type(report_type)
    target_date = _parse_date(report_date)

    try:
        existing = WorkDailyReportDB.get(
            tenant_id=tenant_id,
            scope="team",
            report_type=report_type,
            report_date=target_date,
            target_user_id=None,
        )
        return {"success": True, "data": existing, "cached": existing is not None}
    except Exception as e:
        logger.error(f"查询团队日报缓存失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="查询团队日报失败")


@router.post("/team/regenerate")
async def regenerate_team(request: Request, body: RegenerateRequest):
    """重新生成团队日报（强制刷新，扣积分，仅 1 次 LLM 调用）"""
    user = _resolve_user(request)
    _require_team_admin(user)
    tenant_id = _resolve_tenant_id(request, user)
    _validate_report_type(body.report_type)
    target_date = _parse_date(body.report_date)

    logger.info(
        f"重新生成团队日报: tenant={tenant_id}, admin={user.get('user_id')}, "
        f"date={target_date}, type={body.report_type}"
    )
    return await _generate_team_report(
        tenant_id=tenant_id,
        report_date=target_date,
        report_type=body.report_type,
        is_regenerate=True,
    )


async def _generate_team_report(
    tenant_id: str,
    report_date: date,
    report_type: str,
    is_regenerate: bool,
):
    """实际生成团队日报

    1. 查租户信息取 company_name 作为 tenant_name
    2. 查租户活跃用户数（UserDB.list_by_tenant 取 total）
    3. 调 ReportGenerator().generate_team
    """
    try:
        tenant = TenantDB.get_by_id(tenant_id)
        tenant_name = (tenant or {}).get("company_name") or tenant_id

        # 取租户活跃用户数（status=active）
        users_page = UserDB.list_by_tenant(tenant_id, page=1, page_size=1)
        total_users = int(users_page.get("total") or 0)

        generator = ReportGenerator()
        report = await generator.generate_team(
            tenant_id=tenant_id,
            tenant_name=tenant_name,
            total_users=total_users,
            report_date=report_date,
            report_type=report_type,
            is_regenerate=is_regenerate,
        )
        return {"success": True, "data": report, "cached": False}
    except Exception as e:
        logger.error(
            f"生成团队日报失败: tenant={tenant_id}, date={report_date}, "
            f"type={report_type}, error={e}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail=f"生成团队日报失败：{e}")
