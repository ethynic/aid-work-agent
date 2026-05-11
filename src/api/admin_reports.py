"""
Token消耗报表API

提供平台级Token消耗统计报表，仅平台管理员可访问。
"""

from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Request, Query, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from src.api.auth import get_current_user
from src.config.settings import settings
from src.saas.permissions.checker import is_platform_admin
from src.db.models import ChatRecordDB
from src.saas.db.tenant_db import TenantDB

router = APIRouter(prefix="/api/admin", tags=["平台报表"])


# ============== 请求/响应模型 ==============

class PlatformTokenUsageResponse(BaseModel):
    """平台Token消耗报表响应"""
    success: bool
    month: str
    summary: Dict[str, Any]
    data: List[Dict[str, Any]]
    message: Optional[str] = None


# ============== 工具函数 ==============


# ============== API端点 ==============

@router.get("/token-usage", response_model=PlatformTokenUsageResponse)
async def get_platform_token_usage(
    request: Request,
    month: str = Query(..., description="月份，格式 YYYY-MM，如 2026-05")
):
    """
    获取平台Token消耗报表

    仅平台管理员可访问，显示所有租户在指定月份的Token消耗汇总。
    """
    # 1. 验证用户身份和权限
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")

    if not is_platform_admin(user):
        logger.warning(f"User is not platform admin: {user}")
        raise HTTPException(status_code=403, detail="仅平台管理员可访问此报表")

    logger.info(f"平台管理员 {user.get('user_id')} 请求Token消耗报表，月份: {month}")

    # 2. 调用 ChatRecordDB 方法获取平台Token消耗汇总
    try:
        result = ChatRecordDB.get_platform_token_usage(month)
    except ValueError as e:
        return PlatformTokenUsageResponse(
            success=False,
            month=month,
            summary={},
            data=[],
            message=str(e)
        )
    except Exception as e:
        logger.error(f"获取平台Token消耗报表失败: {e}")
        return PlatformTokenUsageResponse(
            success=False,
            month=month,
            summary={},
            data=[],
            message="获取报表数据时发生内部错误"
        )

    # 3. 获取租户名称信息
    tenant_data = []
    for tenant_item in result["data"]:
        tenant_id = tenant_item["tenant_id"]
        # 查询租户名称
        tenant_info = TenantDB.get_by_id(tenant_id)
        if tenant_id == "demo":
            company_name = "演示用户"
            tenant_code = "demo"
        else:
            company_name = tenant_info.get("company_name", "未知公司") if tenant_info else "未知公司"
            tenant_code = tenant_info.get("tenant_code", tenant_id) if tenant_info else tenant_id

        tenant_data.append({
            "tenant_id": tenant_id,
            "tenant_code": tenant_code,
            "company_name": company_name,
            "input_tokens": tenant_item["input_tokens"],
            "output_tokens": tenant_item["output_tokens"],
            "conversation_count": tenant_item["conversation_count"]
        })

    # 4. 构建响应（使用 ChatRecordDB 返回的汇总信息）
    return PlatformTokenUsageResponse(
        success=True,
        month=result["month"],
        summary=result["summary"],
        data=tenant_data,
        message=f"共 {len(tenant_data)} 个租户有数据"
    )