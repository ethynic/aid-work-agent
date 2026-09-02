"""
协会客户端运行日志 管理 API（平台管理员用）。

设计文档：docs/tools/association-client-design.md（可观测性/遥测章节）
路由前缀：/api/saas/client-usage-logs
- GET /list           列表（tenant/binding/session/stage/status/date 筛选 + 分页）
- GET /recent-errors  近 N 小时错误/告警（跨租户，「及时发现」仪表用）

权限：仅 platform_admin

数据来源 client_usage_logs 表，同表含两类行：
- LLM 计费行（stage=llm/purpose, credit_cost>0）
- 客户端遥测/日志行（stage=run_start/run_complete/wenxin_browser/..., credit_cost=0, detail.level 保留级别）
"""

from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from loguru import logger

from src.db.client_binding_db import ClientUsageLogDB
from src.saas.api.tenant_auth import require_admin, sanitize_error_info
from src.saas.db.tenant_db import TenantDB


router = APIRouter(prefix="/api/saas/client-usage-logs", tags=["SaaS 客户端运行日志"])


# ============== 权限校验 ==============

def _require_platform_admin(admin: dict) -> None:
    """仅 platform_admin 可访问（跨租户浏览）。"""
    if admin.get("role") != "platform_admin":
        raise HTTPException(status_code=403, detail="仅平台管理员可访问客户端运行日志")


def _parse_detail(raw: Any) -> Any:
    """detail 列是 JSON 字符串，解析回 dict；失败原样返回。"""
    if not raw:
        return None
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return raw


def _attach_tenant_names(items: list[dict]) -> None:
    """批量补 tenant_name（同租户复用查询结果）。"""
    tenant_map: dict[str, str] = {}
    for item in items:
        tid = item.get("tenant_id")
        if not tid:
            item["tenant_name"] = "-"
            continue
        if tid not in tenant_map:
            tenant = TenantDB.get_by_id(tid)
            tenant_map[tid] = (tenant.get("company_name") if tenant else "-") or "-"
        item["tenant_name"] = tenant_map[tid]


def _serialize(item: dict) -> dict:
    """行清洗：解析 detail、created_at 转 ISO 字符串。"""
    item = dict(item)
    item["detail"] = _parse_detail(item.get("detail"))
    created_at = item.get("created_at")
    item["created_at"] = created_at.isoformat() if hasattr(created_at, "isoformat") else created_at
    return item


# ============== API 端点 ==============

@router.get("/list")
async def list_client_usage_logs(
    request: Request,
    tenant_id: Optional[str] = Query(None),
    binding_id: Optional[str] = Query(None),
    session_id: Optional[str] = Query(None),
    stage: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
):
    """客户端运行日志/消耗列表（按 created_at DESC）。"""
    admin = require_admin(request)
    _require_platform_admin(admin)

    try:
        result = ClientUsageLogDB.list(
            tenant_id=tenant_id,
            binding_id=binding_id,
            session_id=session_id,
            stage=stage,
            status=status,
            date_from=date_from,
            date_to=date_to,
            page=page,
            page_size=page_size,
        )
        items = [_serialize(it) for it in result["items"]]
        _attach_tenant_names(items)
        return {
            "success": True,
            "items": items,
            "total": result["total"],
            "page": result["page"],
            "page_size": result["page_size"],
        }
    except Exception as e:
        logger.error(f"客户端运行日志列表查询失败: {e}")
        return {"success": False, "message": "查询失败", "debug": sanitize_error_info(str(e))}


@router.get("/recent-errors")
async def recent_client_errors(
    request: Request,
    hours: int = Query(24, ge=1, le=168),
    limit: int = Query(100, ge=1, le=500),
    tenant_id: Optional[str] = Query(None),
):
    """近 N 小时的客户端错误/告警（跨租户，「及时发现」仪表用）。"""
    admin = require_admin(request)
    _require_platform_admin(admin)

    try:
        items = [_serialize(it) for it in ClientUsageLogDB.recent_errors(hours=hours, limit=limit, tenant_id=tenant_id)]
        _attach_tenant_names(items)
        return {"success": True, "items": items}
    except Exception as e:
        logger.error(f"客户端近期错误查询失败: {e}")
        return {"success": False, "message": "查询失败", "debug": sanitize_error_info(str(e))}
