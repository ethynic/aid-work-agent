"""
租户充值管理 API（#37 租户积分充值与计费）

路由：/api/saas/billing/recharges/*
- GET  /list           列表（支持 tenant_id/日期筛选 + 分页）
- POST /               创建充值记录（同步 tenants.credit_balance += credits）
- DELETE /{recharge_id} 删除充值记录（同步 tenants.credit_balance -= credits）
- GET  /stats          汇总统计（总金额、总积分、最近 7 天趋势）

权限：仅 platform_admin 可访问
"""

from typing import Optional

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, Field
from loguru import logger

from src.config.settings import settings
from src.saas.api.tenant_auth import require_admin, sanitize_error_info
from src.saas.db.tenant_db import TenantDB
from src.db.models import TenantRechargesDB


router = APIRouter(prefix="/api/saas/billing/recharges", tags=["SaaS 充值管理"])


# ============== 请求模型 ==============

class RechargeCreateRequest(BaseModel):
    """创建充值请求"""
    tenant_id: str = Field(..., description="租户 ID")
    amount_yuan: int = Field(..., ge=1, description="充值金额（元），正整数")
    credits: int = Field(..., ge=1, description="转化积分，正整数")
    created_at: Optional[str] = Field(None, description="充值日期（ISO 8601 / YYYY-MM-DDTHH:MM），未传则使用当前时间")
    remark: Optional[str] = Field(None, description="备注")


# ============== 权限校验 ==============

def _require_platform_admin(admin: dict) -> None:
    """仅 platform_admin 可访问"""
    if admin.get("role") != "platform_admin":
        raise HTTPException(status_code=403, detail="仅平台管理员可访问充值管理")


# ============== API 端点 ==============

@router.get("/list")
async def list_recharges(
    request: Request,
    tenant_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    """充值记录列表（按 created_at DESC）"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)
    _require_platform_admin(admin)

    try:
        if page < 1:
            page = 1
        if page_size < 1 or page_size > 200:
            page_size = 20
        result = TenantRechargesDB.list(
            tenant_id=tenant_id,
            date_from=date_from,
            date_to=date_to,
            page=page,
            page_size=page_size,
        )
        # 关联租户名以便前端展示
        tenant_map: dict = {}
        for item in result["items"]:
            tid = item.get("tenant_id")
            if tid and tid not in tenant_map:
                tenant = TenantDB.get_by_id(tid)
                tenant_map[tid] = tenant.get("company_name") if tenant else "-"
            item["tenant_name"] = tenant_map.get(tid, "-")
        return {"success": True, **result}
    except Exception as e:
        logger.error(f"充值记录列表查询失败: {e}")
        return {"success": False, "message": "查询失败", "debug": sanitize_error_info(str(e))}


@router.post("/")
async def create_recharge(request: Request, body: RechargeCreateRequest):
    """创建充值记录，同步增加租户余额（同事务原子）"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)
    _require_platform_admin(admin)

    # 校验租户存在
    tenant = TenantDB.get_by_id(body.tenant_id)
    if not tenant:
        return {"success": False, "message": "租户不存在"}

    # 兼容历史 schema：rate 字段已下线，但 DB 列仍 NOT NULL，统一写 0
    rate = 0

    # 解析可选的充值日期（未传则由 DB 默认 CURRENT_TIMESTAMP 填充）
    parsed_created_at: Optional[str] = None
    if body.created_at:
        from datetime import datetime
        # 统一用空格分隔，便于后续按长度判定
        normalized = body.created_at.replace("T", " ") if "T" in body.created_at else body.created_at
        try:
            if len(normalized) >= 19:
                # "YYYY-MM-DD HH:MM:SS"（截取前 19 位，兼容尾部带毫秒/时区的情况）
                datetime.strptime(normalized[:19], "%Y-%m-%d %H:%M:%S")
                parsed_created_at = normalized[:19]
            elif len(normalized) == 16:
                # "YYYY-MM-DD HH:MM"（HTML datetime-local 默认格式，补 ":00" 秒位）
                datetime.strptime(normalized, "%Y-%m-%d %H:%M")
                parsed_created_at = normalized + ":00"
            else:
                return {"success": False, "message": "充值日期格式错误，应为 YYYY-MM-DDTHH:MM 或 YYYY-MM-DDTHH:MM:SS"}
        except ValueError:
            return {"success": False, "message": "充值日期格式错误，应为 YYYY-MM-DDTHH:MM 或 YYYY-MM-DDTHH:MM:SS"}

    try:
        record = TenantRechargesDB.create(
            tenant_id=body.tenant_id,
            amount_yuan=body.amount_yuan,
            credits=body.credits,
            rate=rate,
            source="manual",
            operator_id=admin.get("user_id"),
            operator_name=admin.get("username") or admin.get("phone"),
            remark=body.remark,
            created_at=parsed_created_at,
        )
        if not record:
            return {"success": False, "message": "创建失败"}
        # 失效租户缓存（余额变更后）
        try:
            from src.core.cache_utils import invalidate_tenant_cache
            invalidate_tenant_cache(body.tenant_id)
        except Exception as cache_err:
            logger.warning(f"失效租户缓存失败（非阻断）: {cache_err}")
        return {"success": True, "recharge": record}
    except Exception as e:
        logger.error(f"创建充值记录失败: {e}")
        return {"success": False, "message": "创建失败", "debug": sanitize_error_info(str(e))}


@router.delete("/{recharge_id}")
async def delete_recharge(request: Request, recharge_id: int):
    """删除充值记录，同步回扣租户余额（同事务原子）"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)
    _require_platform_admin(admin)

    try:
        # 删除前查记录，校验存在性
        existing = TenantRechargesDB.get_by_id(recharge_id)
        if not existing:
            return {"success": False, "message": "充值记录不存在"}

        deleted = TenantRechargesDB.delete(recharge_id)
        if not deleted:
            return {"success": False, "message": "删除失败"}
        # 失效租户缓存
        try:
            from src.core.cache_utils import invalidate_tenant_cache
            invalidate_tenant_cache(deleted.get("tenant_id"))
        except Exception as cache_err:
            logger.warning(f"失效租户缓存失败（非阻断）: {cache_err}")
        return {"success": True, "recharge": deleted}
    except Exception as e:
        logger.error(f"删除充值记录失败: {e}")
        return {"success": False, "message": "删除失败", "debug": sanitize_error_info(str(e))}


@router.get("/stats")
async def recharge_stats(request: Request, tenant_id: Optional[str] = None):
    """汇总统计：总充值金额、总积分、最近 7 天趋势"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)
    _require_platform_admin(admin)

    try:
        stats = TenantRechargesDB.stats(tenant_id=tenant_id)
        return {"success": True, "stats": stats}
    except Exception as e:
        logger.error(f"充值汇总查询失败: {e}")
        return {"success": False, "message": "查询失败", "debug": sanitize_error_info(str(e))}
