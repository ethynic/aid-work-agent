"""防滥用中心 API（Phase 1 观察闭环）

路由：/api/saas/abuse/*
- presets：规则目录（代码内置，前端勾选界面渲染用）
- policies：绑定整存整取（scope_type=tenant 兜底 / user / channel）
- events：触发记录分页
- usage：窗口内会话积分消耗 top 用户 + 单用户逐条明细

全部走 require_admin（租户管理员），租户隔离由 admin["tenant_id"] 保证；
平台管理员代操作走 X-Tenant-Id（require_admin 内处理）。
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from loguru import logger
from pydantic import BaseModel, Field

from src.config.settings import settings
from src.saas.api.tenant_auth import require_admin
from src.db.database import get_db_connection
from src.services.anti_abuse import get_anti_abuse_service
from src.services.anti_abuse.rules import (
    RULE_CATALOG,
    SESSION_SOURCE_TYPES,
    validate_policy_params,
)

router = APIRouter(prefix="/api/saas/abuse", tags=["SaaS 防滥用中心"])

_SCOPE_TYPES = ("tenant", "user", "channel")
_USAGE_WINDOWS = {"hour": "1 hour", "day": "1 day"}
_TOP_USERS_LIMIT = 20
_DETAIL_LIMIT = 200


def _resolve_scope(request_admin: dict, scope_type: str, scope_id: Optional[str]) -> tuple:
    """校验作用域参数并解析 (tenant_id, scope_id)。

    tenant 兜底可省 scope_id（默认本租户）；user/channel 必填且必须属于本租户。
    """
    tenant_id = request_admin.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=400, detail="缺少租户上下文")
    if scope_type not in _SCOPE_TYPES:
        raise HTTPException(status_code=400, detail=f"非法 scope_type: {scope_type}")
    if scope_type == "tenant":
        if scope_id and scope_id != tenant_id:
            raise HTTPException(status_code=403, detail="无权访问其他租户的绑定")
        return tenant_id, tenant_id
    if not scope_id:
        raise HTTPException(status_code=400, detail="user/channel 作用域必须提供 scope_id")
    _assert_scope_in_tenant(tenant_id, scope_type, scope_id)
    return tenant_id, scope_id


def _assert_scope_in_tenant(tenant_id: str, scope_type: str, scope_id: str) -> None:
    """防止管理员给其他租户的用户/渠道配置写绑定"""
    with get_db_connection() as cursor:
        if scope_type == "user":
            cursor.execute(
                "SELECT 1 FROM users WHERE user_id = %s AND tenant_id = %s",
                (scope_id, tenant_id),
            )
        else:
            cursor.execute(
                "SELECT 1 FROM tenant_channel_configs WHERE config_id = %s AND tenant_id = %s",
                (scope_id, tenant_id),
            )
        row = cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="作用域对象不存在或不属于当前租户")


# ============== 规则目录 ==============

@router.get("/presets")
async def get_presets(request: Request):
    """规则目录（id/名称/说明/参数 schema/默认值）"""
    require_admin(request)
    return {"success": True, "rules": RULE_CATALOG}


# ============== 绑定读写 ==============

@router.get("/policies")
async def get_policies(request: Request, scope_type: str = "tenant",
                       scope_id: Optional[str] = None):
    """读绑定；无自定义行时返回平台默认生效值（供 UI 展示当前生效）"""
    admin = require_admin(request)
    tenant_id, sid = _resolve_scope(admin, scope_type, scope_id)
    row = get_anti_abuse_service().load_policy_row(tenant_id, scope_type, sid)
    if row:
        return {"success": True, "exists": True, **row}
    default = settings.anti_abuse.tenant_default
    return {
        "success": True,
        "exists": False,
        "tenant_id": tenant_id,
        "scope_type": scope_type,
        "scope_id": sid,
        "mode": default.mode,
        "policies": default.policies,
        "is_platform_default": True,
    }


class AbusePolicyPutRequest(BaseModel):
    mode: str = Field("observe", pattern=r"^(observe|enforce)$")
    policies: list = Field(default_factory=list, max_length=10)


@router.put("/policies")
async def put_policies(request: Request, scope_type: str = "tenant",
                       scope_id: Optional[str] = None, body: AbusePolicyPutRequest = None):
    """整存整取绑定（mode + policies 数组），三种作用域统一"""
    admin = require_admin(request)
    if body is None:
        body = AbusePolicyPutRequest()
    tenant_id, sid = _resolve_scope(admin, scope_type, scope_id)
    seen_rules = set()
    for policy in body.policies:
        if not isinstance(policy, dict):
            raise HTTPException(status_code=400, detail="policies 元素必须为对象")
        rule_id = policy.get("rule_id", "")
        if rule_id in seen_rules:
            raise HTTPException(status_code=400, detail=f"规则重复勾选: {rule_id}")
        seen_rules.add(rule_id)
        ok, err = validate_policy_params(rule_id, policy.get("params"))
        if not ok:
            raise HTTPException(status_code=400, detail=err)
    try:
        get_anti_abuse_service().upsert_policy(
            tenant_id, scope_type, sid, body.mode, body.policies,
            updated_by=admin.get("user_id", ""),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.opt(exception=True).error(f"防滥用绑定保存失败 tenant={tenant_id}: {e}")
        raise HTTPException(status_code=500, detail="保存失败，请稍后重试")
    return {"success": True}


# ============== 触发记录 ==============

@router.get("/events")
async def list_events(request: Request, scope_type: Optional[str] = None,
                      scope_id: Optional[str] = None, user_id: Optional[str] = None,
                      page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    """触发记录分页查询（created_at DESC）"""
    admin = require_admin(request)
    tenant_id = admin.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=400, detail="缺少租户上下文")
    conditions = ["tenant_id = %s"]
    params: list = [tenant_id]
    if scope_type:
        if scope_type not in _SCOPE_TYPES:
            raise HTTPException(status_code=400, detail=f"非法 scope_type: {scope_type}")
        conditions.append("scope_type = %s")
        params.append(scope_type)
    if scope_id:
        conditions.append("scope_id = %s")
        params.append(scope_id)
    if user_id:
        conditions.append("user_id = %s")
        params.append(user_id)
    where = " AND ".join(conditions)
    offset = (page - 1) * page_size
    with get_db_connection() as cursor:
        cursor.execute(
            f"SELECT COUNT(*) AS cnt FROM abuse_events WHERE {where}", tuple(params))
        total = cursor.fetchone()["cnt"]
        cursor.execute(
            f"""SELECT * FROM abuse_events WHERE {where}
                ORDER BY created_at DESC LIMIT %s OFFSET %s""",
            tuple(params + [page_size, offset]),
        )
        items = [dict(row) for row in cursor.fetchall()]
    return {"success": True, "items": items, "total": total, "page": page, "page_size": page_size}


# ============== 用量监控 ==============

@router.get("/usage")
async def usage(request: Request, window: str = "day", user_id: Optional[str] = None):
    """窗口内会话积分消耗：无 user_id 返回 top 用户汇总；带 user_id 返回逐条明细"""
    admin = require_admin(request)
    tenant_id = admin.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=400, detail="缺少租户上下文")
    interval = _USAGE_WINDOWS.get(window)
    if interval is None:
        raise HTTPException(status_code=400, detail=f"非法 window: {window}")
    source_types = list(SESSION_SOURCE_TYPES)
    with get_db_connection() as cursor:
        if not user_id:
            cursor.execute(
                f"""SELECT r.user_id,
                           COALESCE(u.username, '') AS username,
                           COALESCE(u.nickname, '') AS nickname,
                           u.source AS channel_source,
                           u.status AS user_status,
                           SUM(r.credit_cost) AS total_credits,
                           COUNT(*) AS request_count,
                           MAX(r.created_at) AS last_request_at
                    FROM chat_records r
                    LEFT JOIN users u ON u.user_id = r.user_id
                    WHERE r.tenant_id = %s AND r.credit_cost > 0
                      AND r.user_id IS NOT NULL
                      AND r.source_type = ANY(%s)
                      AND r.created_at >= NOW() - interval '{interval}'
                    GROUP BY r.user_id, u.username, u.nickname, u.source, u.status
                    ORDER BY total_credits DESC
                    LIMIT %s""",
                (tenant_id, source_types, _TOP_USERS_LIMIT),
            )
            items = [dict(row) for row in cursor.fetchall()]
            return {"success": True, "window": window, "items": items}
        # 单用户明细
        cursor.execute(
            f"""SELECT id, session_id, user_message, response_message, credit_cost,
                       model, source_type, created_at
                FROM chat_records
                WHERE tenant_id = %s AND user_id = %s AND credit_cost > 0
                  AND source_type = ANY(%s)
                  AND created_at >= NOW() - interval '{interval}'
                ORDER BY created_at DESC
                LIMIT %s""",
            (tenant_id, user_id, source_types, _DETAIL_LIMIT),
        )
        items = [dict(row) for row in cursor.fetchall()]
        cursor.execute(
            f"""SELECT COALESCE(SUM(credit_cost), 0) AS total FROM chat_records
                WHERE tenant_id = %s AND user_id = %s AND credit_cost > 0
                  AND source_type = ANY(%s)
                  AND created_at >= NOW() - interval '{interval}'""",
            (tenant_id, user_id, source_types),
        )
        total_credits = float(cursor.fetchone()["total"] or 0)
    return {
        "success": True, "window": window, "user_id": user_id,
        "total_credits": total_credits, "items": items,
    }
