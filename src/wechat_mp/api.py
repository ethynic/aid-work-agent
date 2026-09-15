"""微信公众号内容管理 API（WP6）。

路由前缀：``/api/saas/wechat-mp``（租户端，require_admin + 租户隔离）与
``/api/saas/wechat-mp/portal/*``（平台管理端，仅 platform_admin，显式跨租户）。

分层规则（设计 §3）：本模块只做鉴权、参数解析、状态回传；受理/查询/入队逻辑
一律在 src/wechat_mp/service.py（WeChatMPSyncService 同级模块级函数）实现，
不在 API 侧复制任何抓取或入库逻辑。

- POST /import-urls       手动粘贴 URL 导入（≤50 条，域名校验，同租户 queued ≥10 拒绝）
- GET  /runs              运行记录列表（默认 limit 20，上限 100）
- GET  /runs/{run_id}     运行详情 + items（非本租户 404，防跨租户探测）
- GET  /articles          文章当前态列表（status/processing_status 过滤）
- POST /articles/{id}/retry    失败文章重入队（trigger_type='retry'）
- POST /articles/{id}/recheck  active 文章存活复核（trigger_type='recheck', action='check'）
- GET  /portal/runs       跨租户运行记录（platform_admin，tenant_id 可选过滤）
- GET  /portal/articles   跨租户文章列表（同上）

租户上下文：租户端点要求 admin["tenant_id"] 非空；platform_admin 未携带
X-Tenant-Id 时返回 400 明确业务错误（WP6 CR P2：不依赖 SQL NULL 语义）。

错误信息脱敏：列表/详情回传的 error_message 均为写库前已脱敏的值（service 层
sanitize_error_info 后落库），本模块不新增未脱敏来源；业务错误为固定中文文案。
受理成功后由 service 层 best-effort 唤醒 worker（WP7），通知丢失由 60s 兜底扫描接管。
"""

import asyncio
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from loguru import logger
from pydantic import BaseModel, Field

from src.saas.api.tenant_auth import require_admin
from src.wechat_mp import service as wechat_mp_service
from src.wechat_mp.service import WeChatMPBusinessError

router = APIRouter(prefix="/api/saas/wechat-mp", tags=["微信公众号内容"])


class ImportUrlsRequest(BaseModel):
    urls: List[str] = Field(..., description="公众号文章 URL 列表（支持任意顺序，非法 URL 单条拒收）")


def _require_platform_admin(admin: dict) -> None:
    """portal 端点权限：仅 platform_admin（对齐 client_usage_mgmt 模式）。"""
    if admin.get("role") != "platform_admin":
        raise HTTPException(status_code=403, detail="仅平台管理员可访问公众号内容跨租户视图")


def _require_tenant_context(admin: dict) -> str:
    """租户端点必须带租户上下文；缺失（platform_admin 无 X-Tenant-Id）返回 400。

    WP6 CR P2：显式业务错误，不把 tenant_id=None 传进 SQL 依赖 NULL 语义。
    """
    tenant_id = admin.get("tenant_id")
    if not tenant_id:
        raise HTTPException(
            status_code=400,
            detail="缺少租户上下文：请携带 X-Tenant-Id 请求头，或使用 portal 跨租户端点",
        )
    return tenant_id


@router.post("/import-urls")
async def import_urls(request: Request, body: ImportUrlsRequest):
    """手动粘贴 URL 导入：逐条校验 → 去重 → 同事务建 queued run + pending items。"""
    admin = require_admin(request)
    tenant_id = _require_tenant_context(admin)
    try:
        result = await asyncio.to_thread(
            wechat_mp_service.import_urls, tenant_id, admin["user_id"], body.urls
        )
    except WeChatMPBusinessError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.opt(exception=True).error(
            "后端日志：wechat_mp 手动导入失败 tenant_id={}", tenant_id
        )
        raise HTTPException(status_code=500, detail="导入失败，请稍后重试")
    return {"success": True, **result}


@router.get("/runs")
async def list_runs(
    request: Request,
    limit: int = Query(20, ge=1, le=100, description="每页条数，默认 20 上限 100"),
    offset: int = Query(0, ge=0),
):
    """本租户运行记录列表（created_at DESC）。"""
    admin = require_admin(request)
    tenant_id = _require_tenant_context(admin)
    try:
        result = await asyncio.to_thread(
            wechat_mp_service.list_runs, tenant_id, limit, offset
        )
    except Exception:
        logger.opt(exception=True).error(
            "后端日志：wechat_mp 运行记录查询失败 tenant_id={}", tenant_id
        )
        raise HTTPException(status_code=500, detail="查询失败，请稍后重试")
    return {"success": True, "runs": result["runs"], "total": result["total"], "limit": limit, "offset": offset}


@router.get("/runs/{run_id}")
async def get_run(run_id: int, request: Request):
    """运行详情 + items 列表；非本租户/不存在一律 404（不区分原因防探测）。"""
    admin = require_admin(request)
    tenant_id = _require_tenant_context(admin)
    try:
        result = await asyncio.to_thread(
            wechat_mp_service.get_run, tenant_id, run_id
        )
    except Exception:
        logger.opt(exception=True).error(
            "后端日志：wechat_mp 运行详情查询失败 tenant_id={} run_id={}", tenant_id, run_id
        )
        raise HTTPException(status_code=500, detail="查询失败，请稍后重试")
    if result is None:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    return {"success": True, "run": result["run"], "items": result["items"]}


@router.get("/articles")
async def list_articles(
    request: Request,
    status: Optional[str] = Query(None, description="文章状态过滤：active/missing/deleted/alias/unconfirmed"),
    processing_status: Optional[str] = Query(None, description="处理状态过滤：pending/success/sync_failed/deferred"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """本租户文章当前态列表。"""
    admin = require_admin(request)
    tenant_id = _require_tenant_context(admin)
    try:
        result = await asyncio.to_thread(
            wechat_mp_service.list_articles,
            tenant_id, status, processing_status, limit, offset,
        )
    except Exception:
        logger.opt(exception=True).error(
            "后端日志：wechat_mp 文章列表查询失败 tenant_id={}", tenant_id
        )
        raise HTTPException(status_code=500, detail="查询失败，请稍后重试")
    return {"success": True, "articles": result["articles"], "total": result["total"], "limit": limit, "offset": offset}


@router.post("/articles/{article_row_id}/retry")
async def retry_article(article_row_id: int, request: Request):
    """失败/可重试文章重入队：新 queued run（trigger_type='retry'）+ 单 item。"""
    admin = require_admin(request)
    tenant_id = _require_tenant_context(admin)
    try:
        result = await asyncio.to_thread(
            wechat_mp_service.retry_article, tenant_id, admin["user_id"], article_row_id
        )
    except WeChatMPBusinessError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.opt(exception=True).error(
            "后端日志：wechat_mp 文章重试入队失败 tenant_id={} article_row_id={}",
            tenant_id, article_row_id,
        )
        raise HTTPException(status_code=500, detail="操作失败，请稍后重试")
    if result is None:
        raise HTTPException(status_code=404, detail="文章不存在")
    return {"success": True, **result}


@router.post("/articles/{article_row_id}/recheck")
async def recheck_article(article_row_id: int, request: Request):
    """对 active 文章发起存活复核：新 queued run（trigger_type='recheck'）+ 单 item（action='check'）。"""
    admin = require_admin(request)
    tenant_id = _require_tenant_context(admin)
    try:
        result = await asyncio.to_thread(
            wechat_mp_service.recheck_article, tenant_id, admin["user_id"], article_row_id
        )
    except WeChatMPBusinessError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.opt(exception=True).error(
            "后端日志：wechat_mp 文章复核入队失败 tenant_id={} article_row_id={}",
            tenant_id, article_row_id,
        )
        raise HTTPException(status_code=500, detail="操作失败，请稍后重试")
    if result is None:
        raise HTTPException(status_code=404, detail="文章不存在")
    return {"success": True, **result}


# ==================== portal 跨租户端点（仅 platform_admin） ====================


@router.get("/portal/runs")
async def portal_list_runs(
    request: Request,
    tenant_id: Optional[str] = Query(None, description="可选按租户过滤"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """跨租户运行记录列表（平台管理端审计视图）。"""
    admin = require_admin(request)
    _require_platform_admin(admin)
    try:
        result = await asyncio.to_thread(
            wechat_mp_service.portal_list_runs, tenant_id, limit, offset
        )
    except Exception:
        logger.opt(exception=True).error("后端日志：wechat_mp portal 运行记录查询失败")
        raise HTTPException(status_code=500, detail="查询失败，请稍后重试")
    return {"success": True, "runs": result["runs"], "total": result["total"], "limit": limit, "offset": offset}


@router.get("/portal/articles")
async def portal_list_articles(
    request: Request,
    tenant_id: Optional[str] = Query(None, description="可选按租户过滤"),
    status: Optional[str] = Query(None),
    processing_status: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """跨租户文章当前态列表（平台管理端审计视图）。"""
    admin = require_admin(request)
    _require_platform_admin(admin)
    try:
        result = await asyncio.to_thread(
            wechat_mp_service.portal_list_articles,
            tenant_id, status, processing_status, limit, offset,
        )
    except Exception:
        logger.opt(exception=True).error("后端日志：wechat_mp portal 文章列表查询失败")
        raise HTTPException(status_code=500, detail="查询失败，请稍后重试")
    return {"success": True, "articles": result["articles"], "total": result["total"], "limit": limit, "offset": offset}
