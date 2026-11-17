"""水城创业网租户后台 API（照 lyjy_employment/api.py 同构，无分类范围开关）。

差异点：sccy 仅单表全量（每个 run 即全量比对），无 selected_ids/范围开关；
其余（源设置/立即运行/账本/admin 开通停用/连接加密不回显）与 lyjy 同构。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.db.database import get_db_connection
from src.saas.api.tenant_auth import require_admin

router = APIRouter(prefix="/api/saas/sccy-policy", tags=["sccy-policy"])

MODULE = "sccy_policy"

_INTERVAL_MIN, _INTERVAL_MAX = 1, 24 * 365


class SourcePatch(BaseModel):
    enabled: Optional[bool] = None
    sync_interval_hours: Optional[int] = Field(default=None, ge=_INTERVAL_MIN, le=_INTERVAL_MAX)


def _load_source_row(tenant_id: str) -> Optional[Dict[str, Any]]:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT tenant_id, module, enabled, sync_interval_hours,
                   selection_mode, selected_ids, connection_encrypted,
                   last_sync_at, last_error, created_at, updated_at
            FROM bs_content_sync_sources
            WHERE tenant_id = %s AND module = %s
            """,
            (tenant_id, MODULE),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def _serialize_source(row: Dict[str, Any]) -> Dict[str, Any]:
    selected = row.get("selected_ids")
    if isinstance(selected, str):
        try:
            selected = json.loads(selected)
        except (TypeError, ValueError):
            selected = None
    return {
        "enabled": bool(row["enabled"]),
        "sync_interval_hours": row["sync_interval_hours"],
        "selection_mode": row["selection_mode"],
        "selected_ids": selected or [],
        "connection_configured": bool(row.get("connection_encrypted")),
        "last_sync_at": row["last_sync_at"].isoformat() if row["last_sync_at"] else None,
        "last_error": row["last_error"],
    }


@router.get("/source")
async def get_source(admin: dict = Depends(require_admin)):
    """读源配置（本租户未开通该数据源时返回 404；连接配置不回显）。"""
    tenant_id = admin["tenant_id"]
    row = await asyncio.to_thread(_load_source_row, tenant_id)
    if not row:
        raise HTTPException(status_code=404, detail="数据源未开通")
    return {"success": True, "source": _serialize_source(row)}


@router.patch("/source")
async def patch_source(patch: SourcePatch, admin: dict = Depends(require_admin)):
    """改源配置（开关/频率）；入库范围固定全量（zc_article 单表全量比对）。"""

    def _update() -> None:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            sets, params = [], []
            if patch.enabled is not None:
                sets.append("enabled = %s")
                params.append(patch.enabled)
            if patch.sync_interval_hours is not None:
                sets.append("sync_interval_hours = %s")
                params.append(patch.sync_interval_hours)
            if not sets:
                return
            sets.append("updated_at = now()")
            params.extend([admin["tenant_id"], MODULE])
            cursor.execute(
                f"""
                UPDATE bs_content_sync_sources SET {", ".join(sets)}
                WHERE tenant_id = %s AND module = %s
                """,
                params,
            )
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="数据源未开通")
            conn.commit()

    await asyncio.to_thread(_update)
    row = await asyncio.to_thread(_load_source_row, admin["tenant_id"])
    return {"success": True, "source": _serialize_source(row)}


@router.post("/source/trigger")
async def trigger_source(admin: dict = Depends(require_admin)):
    """立即运行（queued 排队，调度器领取执行；与定时同一 trigger_sync 入口）。"""
    tenant_id = admin["tenant_id"]
    row = await asyncio.to_thread(_load_source_row, tenant_id)
    if not row:
        raise HTTPException(status_code=404, detail="数据源未开通")

    from src.tenant_custom.sccy_policy.service import sccy_policy_service

    result = await asyncio.to_thread(
        sccy_policy_service.trigger_sync, tenant_id, "manual"
    )
    return {"success": True, "run": result}


_RUN_COLUMNS = """
    id, trigger_type, status, new_count, updated_count, skipped_count,
    deleted_count, restored_count, failed_count,
    embedding_tokens, credits_charged, fetch_complete, total_reported,
    error_message, started_at, completed_at, created_at
"""


def _iso(row: Dict[str, Any]) -> Dict[str, Any]:
    for key in ("started_at", "completed_at", "created_at"):
        if row.get(key):
            row[key] = row[key].isoformat()
    return row


@router.get("/runs")
async def list_runs(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    admin: dict = Depends(require_admin),
):
    """运行账本列表（倒序分页）。"""
    tenant_id = admin["tenant_id"]

    def _query():
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT {_RUN_COLUMNS}
                FROM bs_content_sync_runs
                WHERE tenant_id = %s AND module = %s
                ORDER BY id DESC LIMIT %s OFFSET %s
                """,
                (tenant_id, MODULE, limit, offset),
            )
            return [_iso(dict(r)) for r in cursor.fetchall()]

    rows = await asyncio.to_thread(_query)
    return {"success": True, "runs": rows, "limit": limit, "offset": offset}


@router.get("/runs/{run_id}")
async def get_run(run_id: int, admin: dict = Depends(require_admin)):
    """单 run 详情 + items 概要。"""
    tenant_id = admin["tenant_id"]

    def _query():
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT {_RUN_COLUMNS}
                FROM bs_content_sync_runs
                WHERE id = %s AND tenant_id = %s AND module = %s
                """,
                (run_id, tenant_id, MODULE),
            )
            run = cursor.fetchone()
            if not run:
                return None, []
            cursor.execute(
                """
                SELECT native_id, action, status, error_code, error_message,
                       embedding_tokens, billing_status, credits_charged,
                       started_at, completed_at
                FROM bs_content_sync_items
                WHERE run_id = %s ORDER BY id LIMIT 500
                """,
                (run_id,),
            )
            return _iso(dict(run)), [_iso(dict(r)) for r in cursor.fetchall()]

    run, items = await asyncio.to_thread(_query)
    if not run:
        raise HTTPException(status_code=404, detail="run 不存在")
    return {"success": True, "run": run, "items": items}


# ==================== 平台管理员：数据源开通/停用（portal 企业管理） ====================


class GrantRequest(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=64)
    db_host: str = Field(min_length=1, max_length=255)
    db_port: int = Field(default=3306, ge=1, le=65535)
    db_database: str = Field(min_length=1, max_length=64)
    db_user: str = Field(min_length=1, max_length=64)
    db_password: str = Field(min_length=1, max_length=256, description="必填，Fernet 加密落库")


class RevokeRequest(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=64)


def _require_platform_admin(admin: dict) -> None:
    if admin.get("role") != "platform_admin":
        raise HTTPException(status_code=403, detail="仅平台管理员可管理数据源授权")


@router.get("/admin/source")
async def admin_get_source(
    tenant_id: str = Query(..., min_length=1, max_length=64),
    admin: dict = Depends(require_admin),
):
    """平台管理员查某租户的数据源开通状态与当前配置（连接配置不回显）。"""
    _require_platform_admin(admin)
    row = await asyncio.to_thread(_load_source_row, tenant_id)
    return {
        "success": True,
        "granted": row is not None,
        "source": _serialize_source(row) if row else None,
    }


@router.post("/admin/grant")
async def admin_grant_source(body: GrantRequest, admin: dict = Depends(require_admin)):
    """开通：建源行（幂等）+ 连接配置 Fernet 整包加密写 connection_encrypted。

    源行已存在时不覆盖租户运营配置（enabled/频率），仅更新连接配置。
    计费无模块种子（embedding 走平台既有单价行，同 lyjy）。
    """
    _require_platform_admin(admin)

    def _grant():
        from src.saas.db.tenant_db import TenantDB
        from src.tenant_custom.sccy_policy.bootstrap import init_source

        if not TenantDB.get_by_id(body.tenant_id):
            raise HTTPException(status_code=404, detail="租户不存在")
        try:
            init_source(body.tenant_id, {
                "host": body.db_host,
                "port": body.db_port,
                "database": body.db_database,
                "user": body.db_user,
                "password": body.db_password,
            })
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return _load_source_row(body.tenant_id)

    row = await asyncio.to_thread(_grant)
    return {"success": True, "granted": True, "source": _serialize_source(row)}


@router.post("/admin/revoke")
async def admin_revoke_source(body: RevokeRequest, admin: dict = Depends(require_admin)):
    """停用开通：删源行 + 同事务取消在队 run（宏陶 revoke 同款事务模式）。"""
    _require_platform_admin(admin)

    def _revoke() -> int:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM bs_content_sync_sources WHERE tenant_id = %s AND module = %s",
                (body.tenant_id, MODULE),
            )
            cursor.execute(
                """
                UPDATE bs_content_sync_runs
                SET status = 'interrupted', completed_at = now(),
                    error_message = '数据源已停用，取消排队中的同步'
                WHERE tenant_id = %s AND module = %s AND status = 'queued'
                """,
                (body.tenant_id, MODULE),
            )
            cancelled = cursor.rowcount
            conn.commit()
            return cancelled

    cancelled = await asyncio.to_thread(_revoke)
    return {"success": True, "granted": False, "cancelled_runs": cancelled}
