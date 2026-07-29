"""
工作成果 API 路由

提供：
- GET    /api/work-outcomes                列表查询（支持筛选 + 分页）
- GET    /api/work-outcomes/stats          统计聚合
- GET    /api/work-outcomes/{outcome_id}   单条详情
- DELETE /api/work-outcomes/{outcome_id}   删除单条
- POST   /api/work-outcomes/review/run     手动触发复盘任务（仅管理员）

设计文档：docs/system/work-outcome-record-design.md §7
"""

import asyncio
from datetime import date, datetime, timedelta
from typing import Any, Dict, Optional

from fastapi import APIRouter, Request, Query, HTTPException
from loguru import logger

from src.api.auth import get_current_user
from src.reports.work_outcome_db import WorkOutcomeDB
from src.saas.permissions.checker import is_platform_admin, is_tenant_admin

router = APIRouter(prefix="/api/work-outcomes", tags=["工作成果"])


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
        raise HTTPException(status_code=403, detail="无租户归属，无法查询工作成果")
    return tenant_id


def _is_admin(user: Dict[str, Any]) -> bool:
    """判断是否为管理员（平台管理员或租户管理员）"""
    return is_platform_admin(user) or is_tenant_admin(user) or user.get("role") == "tenant_admin"


def _parse_date(date_str: Optional[str]) -> Optional[date]:
    """解析 YYYY-MM-DD 字符串为 date 对象，None/空串返回 None"""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"日期格式错误：{date_str}，应为 YYYY-MM-DD",
        )


def _validate_outcome_type(outcome_type: Optional[str]) -> Optional[str]:
    if outcome_type is None:
        return None
    valid = {"file", "action", "decision", "other"}
    if outcome_type not in valid:
        raise HTTPException(
            status_code=400,
            detail=f"outcome_type 必须为 {valid} 之一",
        )
    return outcome_type


def _validate_source(source: Optional[str]) -> Optional[str]:
    if source is None:
        return None
    valid = {"cp_realtime", "scheduled_review", "manual"}
    if source not in valid:
        raise HTTPException(
            status_code=400,
            detail=f"source 必须为 {valid} 之一",
        )
    return source


# ============== 列表查询 ==============

@router.get("")
async def list_work_outcomes(
    request: Request,
    user_id: Optional[str] = Query(None, description="按用户筛选（普通用户强制为自己）"),
    subagent_id: Optional[str] = Query(None, description="按子智能体筛选"),
    outcome_type: Optional[str] = Query(None, description="file / action / decision / other"),
    source: Optional[str] = Query(None, description="cp_realtime / scheduled_review / manual"),
    channel: Optional[str] = Query(None, description="web / wecom / dingtalk / feishu / wecom_kf"),
    start_date: Optional[str] = Query(None, description="开始日期 YYYY-MM-DD（含）"),
    end_date: Optional[str] = Query(None, description="结束日期 YYYY-MM-DD（含）"),
    keyword: Optional[str] = Query(None, description="摘要关键词（ILIKE）"),
    min_confidence: Optional[float] = Query(None, ge=0.0, le=1.0, description="最小置信度"),
    page: int = Query(1, ge=1, description="页码（1-based）"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
):
    """工作成果列表查询

    - 普通用户只能查看自己的工作成果（user_id 强制为自己）
    - 管理员可查看租户下所有用户的工作成果
    """
    user = _resolve_user(request)
    tenant_id = _resolve_tenant_id(request, user)

    # 普通用户强制 user_id = 自己
    if not _is_admin(user):
        user_id = user["user_id"]
    elif user_id is None:
        # 管理员未指定 user_id：查全租户（user_id=None）
        pass

    _validate_outcome_type(outcome_type)
    _validate_source(source)
    start = _parse_date(start_date)
    end = _parse_date(end_date)

    try:
        result = await asyncio.to_thread(
            WorkOutcomeDB.list_by_tenant,
            tenant_id=tenant_id,
            user_id=user_id,
            subagent_id=subagent_id,
            outcome_type=outcome_type,
            source=source,
            channel=channel,
            start_date=start,
            end_date=end,
            keyword=keyword,
            min_confidence=min_confidence,
            page=page,
            page_size=page_size,
        )
        return {"success": True, "data": result}
    except Exception as e:
        logger.error(f"查询工作成果列表失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="查询工作成果列表失败")


# ============== 统计聚合 ==============

@router.get("/stats")
async def get_work_outcome_stats(
    request: Request,
    user_id: Optional[str] = Query(None, description="按用户筛选（普通用户强制为自己）"),
    start_date: Optional[str] = Query(None, description="开始日期 YYYY-MM-DD（含）"),
    end_date: Optional[str] = Query(None, description="结束日期 YYYY-MM-DD（含）"),
):
    """工作成果统计聚合

    返回总数、按类型/子智能体/来源/渠道分组、复盘批次统计等。
    """
    user = _resolve_user(request)
    tenant_id = _resolve_tenant_id(request, user)

    if not _is_admin(user):
        user_id = user["user_id"]

    start = _parse_date(start_date)
    end = _parse_date(end_date)

    try:
        stats = await asyncio.to_thread(
            WorkOutcomeDB.get_stats,
            tenant_id=tenant_id,
            user_id=user_id,
            start_date=start,
            end_date=end,
        )
        return {"success": True, "data": stats}
    except Exception as e:
        logger.error(f"查询工作成果统计失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="查询工作成果统计失败")


# ============== 单条详情 ==============

@router.get("/{outcome_id}")
async def get_work_outcome(request: Request, outcome_id: str):
    """按 outcome_id 查询单条工作成果"""
    user = _resolve_user(request)
    tenant_id = _resolve_tenant_id(request, user)

    try:
        outcome = await asyncio.to_thread(
            WorkOutcomeDB.get_by_outcome_id, outcome_id
        )
    except Exception as e:
        logger.error(f"查询工作成果详情失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="查询工作成果详情失败")

    if not outcome:
        raise HTTPException(status_code=404, detail="工作成果不存在")

    # 租户隔离校验：确保查询的成果属于当前租户
    if outcome.get("tenant_id") != tenant_id:
        raise HTTPException(status_code=404, detail="工作成果不存在")

    # 普通用户只能看自己的
    if not _is_admin(user) and outcome.get("user_id") != user["user_id"]:
        raise HTTPException(status_code=403, detail="无权访问该工作成果")

    return {"success": True, "data": outcome}


# ============== 删除 ==============

@router.delete("/{outcome_id}")
async def delete_work_outcome(request: Request, outcome_id: str):
    """删除单条工作成果（仅管理员）"""
    user = _resolve_user(request)
    tenant_id = _resolve_tenant_id(request, user)

    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="仅管理员可删除工作成果")

    # 先查存在性 + 租户校验
    try:
        outcome = await asyncio.to_thread(
            WorkOutcomeDB.get_by_outcome_id, outcome_id
        )
    except Exception as e:
        logger.error(f"删除前查询工作成果失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="删除工作成果失败")

    if not outcome or outcome.get("tenant_id") != tenant_id:
        raise HTTPException(status_code=404, detail="工作成果不存在")

    try:
        deleted = await asyncio.to_thread(
            WorkOutcomeDB.delete_by_outcome_id, outcome_id
        )
    except Exception as e:
        logger.error(f"删除工作成果失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="删除工作成果失败")

    if not deleted:
        raise HTTPException(status_code=404, detail="工作成果不存在")

    logger.info(
        f"工作成果删除: outcome_id={outcome_id}, tenant={tenant_id}, "
        f"operator={user.get('user_id')}"
    )
    return {"success": True, "data": {"outcome_id": outcome_id, "deleted": True}}


# ============== 手动触发复盘任务 ==============

class ReviewRunRequest:
    """手动触发复盘任务请求（空 body，复用 query 参数）"""


@router.post("/review/run")
async def run_review_manually(
    request: Request,
    target_date: Optional[str] = Query(
        None,
        description="复盘日期 YYYY-MM-DD，默认昨天",
    ),
):
    """手动触发工作成果复盘任务（仅管理员）

    用于运维或管理员手动补跑复盘任务。默认复盘昨天，可指定日期。
    返回 review_batch_id，可关联查询本次复盘产出的所有成果。
    """
    user = _resolve_user(request)
    tenant_id = _resolve_tenant_id(request, user)

    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="仅管理员可手动触发复盘任务")

    # 解析 target_date
    if target_date:
        td = _parse_date(target_date)
    else:
        td = date.today() - timedelta(days=1)

    try:
        from src.reports.work_outcome_review import run_daily_review
        batch_id = await run_daily_review(td)
        logger.info(
            f"手动触发工作成果复盘: batch_id={batch_id}, date={td}, "
            f"tenant={tenant_id}, operator={user.get('user_id')}"
        )
        return {
            "success": True,
            "data": {
                "review_batch_id": batch_id,
                "target_date": td.isoformat(),
            },
        }
    except Exception as e:
        logger.error(f"手动触发工作成果复盘失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="手动触发工作成果复盘失败")
