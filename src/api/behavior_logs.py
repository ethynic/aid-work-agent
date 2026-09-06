"""
用户行为审计日志查询 API（Phase 4）

设计文档：docs/system/user-behavior-audit-log-design.md §7
- GET /api/admin/behavior-logs        租户管理员 / 平台管理员代租户查询本租户行为日志
- GET /api/saas/behavior-logs         仅平台管理员全局查询（可按 tenant_id 筛选）

模式参照 admin_error_logs.py：手写两段式鉴权 + 动态 WHERE + COUNT + LIMIT/OFFSET，
SQL 全参数化（时间范围用固定分支常量，禁 f-string 拼值）。
"""

from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Request, Query, HTTPException
from loguru import logger
from pydantic import BaseModel

from src.api.auth import get_current_user
from src.db import get_db_connection
from src.saas.models.enums import BehaviorAction, BehaviorResourceType
from src.saas.permissions.checker import is_platform_admin, is_tenant_admin


router_admin = APIRouter(prefix="/api/admin/behavior-logs", tags=["用户行为日志"])
router_platform = APIRouter(prefix="/api/saas/behavior-logs", tags=["用户行为日志"])


# ============== 请求/响应模型 ==============

class BehaviorLogListResponse(BaseModel):
    """行为日志列表响应（data 为完整行 dict，detail/error_msg 已在写入侧过滤敏感键，原样返回）"""
    success: bool
    data: List[Dict[str, Any]]
    total: int
    page: int
    page_size: int
    total_pages: int
    message: Optional[str] = None


# ============== 工具函数 ==============

# 时间范围 -> 固定 SQL 片段（常量映射，不含任何用户输入，无注入面）
_TIME_RANGE_FRAGMENTS = {
    "today": "created_at >= CURRENT_DATE",
    "7d": "created_at >= NOW() - INTERVAL '7 days'",
    "30d": "created_at >= NOW() - INTERVAL '30 days'",
}

# 行查询列（与 user_behavior_logs 表列一致）
_ROW_COLUMNS = (
    "id, tenant_id, user_id, user_role, action, resource_type, resource_id, resource_name, "
    "detail, client_ip, user_agent, success, error_msg, entry, login_method, channel, "
    "channel_user_id, token_id, request_id, http_method, path, device_type, device_info, created_at"
)


def _query_behavior_logs(
    tenant_id: Optional[str] = None,
    action: Optional[str] = None,
    resource_type: Optional[str] = None,
    success: Optional[bool] = None,
    keyword: Optional[str] = None,
    time_range: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> Dict[str, Any]:
    """
    查询用户行为日志（动态 WHERE + COUNT + LIMIT/OFFSET，全参数化）

    Args:
        tenant_id: 租户过滤（None 表示全局，仅平台管理员全局视图使用）
        action: 行为类型过滤（BehaviorAction 枚举值）
        resource_type: 资源类型过滤（BehaviorResourceType 枚举值）
        success: 结果过滤（True 成功 / False 失败 / None 全部）
        keyword: 关键字（ILIKE 匹配 user_id / resource_name / resource_id）
        time_range: 时间范围（today / 7d / 30d / None 全部）
        page: 页码（从 1 开始）
        page_size: 每页数量

    Returns:
        dict 包含 data（完整行 dict 列表）和 total
    """
    offset = (page - 1) * page_size

    conditions = []
    params: List[Any] = []

    # 租户过滤
    if tenant_id:
        conditions.append("tenant_id = %s")
        params.append(tenant_id)

    # 行为类型 / 资源类型
    if action:
        conditions.append("action = %s")
        params.append(action)
    if resource_type:
        conditions.append("resource_type = %s")
        params.append(resource_type)

    # 结果过滤
    if success is not None:
        conditions.append("success = %s")
        params.append(bool(success))

    # 关键字：ILIKE 匹配 user_id / resource_name / resource_id
    if keyword:
        like = f"%{keyword}%"
        conditions.append("(user_id ILIKE %s OR resource_name ILIKE %s OR resource_id ILIKE %s)")
        params.extend([like, like, like])

    # 时间范围（固定分支常量，不拼用户输入）
    if time_range:
        time_fragment = _TIME_RANGE_FRAGMENTS.get(time_range)
        if time_fragment:
            conditions.append(time_fragment)

    where_clause = " AND ".join(conditions) if conditions else "1=1"

    with get_db_connection() as conn:
        cursor = conn.cursor()

        # 查询总数
        cursor.execute(
            f"SELECT COUNT(*) AS cnt FROM user_behavior_logs WHERE {where_clause}",
            params,
        )
        total = cursor.fetchone()["cnt"]

        # 查询数据（按时间倒序，LIMIT/OFFSET 同样参数化）
        cursor.execute(
            f"""
            SELECT {_ROW_COLUMNS}
            FROM user_behavior_logs
            WHERE {where_clause}
            ORDER BY created_at DESC, id DESC
            LIMIT %s OFFSET %s
            """,
            [*params, page_size, offset],
        )
        rows = cursor.fetchall()

    data = []
    for row in rows:
        row_dict = dict(row)
        # 时间字段转字符串（后端本地时间，无时区）
        if row_dict.get("created_at"):
            row_dict["created_at"] = str(row_dict["created_at"])
        data.append(row_dict)

    return {"data": data, "total": total}


def _validate_enum_param(
    value: Optional[str],
    valid_values: List[str],
    label: str,
) -> None:
    """校验枚举类查询参数合法性，非法值返回 400"""
    if value and value not in valid_values:
        raise HTTPException(status_code=400, detail=f"无效的{label}值")


def _validate_time_range(time_range: Optional[str]) -> None:
    """校验时间范围参数合法性，非法值返回 400"""
    if time_range and time_range not in _TIME_RANGE_FRAGMENTS:
        raise HTTPException(status_code=400, detail="无效的时间范围值")


# ============== API 端点 ==============

@router_admin.get("", response_model=BehaviorLogListResponse)
async def list_behavior_logs(
    request: Request,
    action: Optional[str] = Query(None, description="行为类型（BehaviorAction 枚举值）"),
    resource_type: Optional[str] = Query(None, description="资源类型（BehaviorResourceType 枚举值）"),
    success: Optional[bool] = Query(None, description="结果过滤：true 成功 / false 失败"),
    keyword: Optional[str] = Query(None, description="关键字（匹配用户ID/资源名称/资源ID）"),
    time_range: Optional[str] = Query(None, description="时间范围：today / 7d / 30d，空为全部"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
):
    """
    查询本租户用户行为日志

    租户管理员 / 平台管理员（需 X-Tenant-Id 指定目标租户）可访问。
    租户从 request.state.tenant_id 取（TenantContextMiddleware 已处理 X-Tenant-Id 优先级），
    为空（如 platform_admin 未带 X-Tenant-Id）返回 400。
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")

    # is_tenant_admin 对 platform_admin 也返回 True（代租户管理语义），双条件显式可读
    if not (is_tenant_admin(user) or is_platform_admin(user)):
        raise HTTPException(status_code=403, detail="仅租户管理员或平台管理员可访问")

    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(
            status_code=400,
            detail="无法确定租户上下文，请通过 X-Tenant-Id 指定目标租户",
        )

    _validate_enum_param(action, [a.value for a in BehaviorAction], "行为类型")
    _validate_enum_param(resource_type, [r.value for r in BehaviorResourceType], "资源类型")
    _validate_time_range(time_range)

    result = _query_behavior_logs(
        tenant_id=tenant_id,
        action=action,
        resource_type=resource_type,
        success=success,
        keyword=keyword,
        time_range=time_range,
        page=page,
        page_size=page_size,
    )

    total_pages = (result["total"] + page_size - 1) // page_size
    logger.info(
        f"后端日志：行为日志查询 tenant_id={tenant_id} user_id={user.get('user_id')} "
        f"共 {result['total']} 条（page={page}, page_size={page_size}）"
    )

    return BehaviorLogListResponse(
        success=True,
        data=result["data"],
        total=result["total"],
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        message=f"共 {result['total']} 条记录",
    )


@router_platform.get("", response_model=BehaviorLogListResponse)
async def list_behavior_logs_platform(
    request: Request,
    tenant_id: Optional[str] = Query(None, description="租户ID筛选（空为全部租户）"),
    action: Optional[str] = Query(None, description="行为类型（BehaviorAction 枚举值）"),
    resource_type: Optional[str] = Query(None, description="资源类型（BehaviorResourceType 枚举值）"),
    success: Optional[bool] = Query(None, description="结果过滤：true 成功 / false 失败"),
    keyword: Optional[str] = Query(None, description="关键字（匹配用户ID/资源名称/资源ID）"),
    time_range: Optional[str] = Query(None, description="时间范围：today / 7d / 30d，空为全部"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
):
    """
    全局查询用户行为日志（仅平台管理员）

    额外支持 tenant_id 查询参数筛选指定租户。
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")

    if not is_platform_admin(user):
        raise HTTPException(status_code=403, detail="仅平台管理员可访问")

    _validate_enum_param(action, [a.value for a in BehaviorAction], "行为类型")
    _validate_enum_param(resource_type, [r.value for r in BehaviorResourceType], "资源类型")
    _validate_time_range(time_range)

    result = _query_behavior_logs(
        tenant_id=tenant_id,
        action=action,
        resource_type=resource_type,
        success=success,
        keyword=keyword,
        time_range=time_range,
        page=page,
        page_size=page_size,
    )

    total_pages = (result["total"] + page_size - 1) // page_size
    logger.info(
        f"后端日志：行为日志全局查询（平台管理员 {user.get('user_id')}，"
        f"tenant_id 筛选={tenant_id or '全部'}）共 {result['total']} 条"
    )

    return BehaviorLogListResponse(
        success=True,
        data=result["data"],
        total=result["total"],
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        message=f"共 {result['total']} 条记录",
    )
