"""宏陶商城租户后台 API（设计 §8：源设置 / 立即运行 / 运行账本 / 产品挑选）。

- 鉴权 require_admin（platform_admin 可 X-Tenant-Id 代管）；租户一律取自
  admin 上下文，不接受请求体传租户。
- DB 访问为同步 psycopg2，经 asyncio.to_thread 包裹（假异步规范）。
- 挑选数据源是本地 bs_content_sync_records（payload 目录字段），不实时调外部 API。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.db.database import get_db_connection
from src.saas.api.tenant_auth import require_admin

router = APIRouter(prefix="/api/saas/hongtao-shop", tags=["hongtao-shop"])

MODULE = "hongtao_shop"

_INTERVAL_MIN, _INTERVAL_MAX = 1, 24 * 365


class SourcePatch(BaseModel):
    enabled: Optional[bool] = None
    sync_interval_hours: Optional[int] = Field(default=None, ge=_INTERVAL_MIN, le=_INTERVAL_MAX)
    selection_mode: Optional[str] = None
    selected_ids: Optional[List[str]] = Field(
        default=None, max_length=2000,
        description="挑选白名单（单独传等价于 selection_mode=ids）",
    )


def _load_source_row(tenant_id: str) -> Optional[Dict[str, Any]]:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT tenant_id, module, enabled, sync_interval_hours,
                   selection_mode, selected_ids, last_sync_at, last_error,
                   created_at, updated_at
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
        "last_sync_at": row["last_sync_at"].isoformat() if row["last_sync_at"] else None,
        "last_error": row["last_error"],
    }


@router.get("/source")
async def get_source(admin: dict = Depends(require_admin)):
    """读源配置（未初始化时返回 404，提示先跑 CLI --init-source 或后台初始化）。"""
    tenant_id = admin["tenant_id"]
    row = await asyncio.to_thread(_load_source_row, tenant_id)
    if not row:
        raise HTTPException(status_code=404, detail="源未初始化（请先执行初始化）")
    return {"success": True, "source": _serialize_source(row)}


@router.patch("/source")
async def patch_source(patch: SourcePatch, admin: dict = Depends(require_admin)):
    """改源配置（开关/频率/挑选）；挑选变化在下一轮 run 收尾对账生效
    （取消勾选→文档软删，重新勾选→hash 未变恢复，设计 §6.4）。"""
    tenant_id = admin["tenant_id"]
    # 契约：单独传 selected_ids 等价于 selection_mode=ids（避免静默忽略）
    if patch.selected_ids is not None and patch.selection_mode is None:
        patch.selection_mode = "ids"
    if patch.selection_mode is not None and patch.selection_mode not in ("all", "ids"):
        raise HTTPException(status_code=400, detail="selection_mode 仅支持 all/ids")
    if patch.selection_mode == "ids" and patch.selected_ids is None:
        raise HTTPException(status_code=400, detail="selection_mode=ids 需同时提供 selected_ids")

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
            if patch.selection_mode is not None:
                sets.append("selection_mode = %s")
                params.append(patch.selection_mode)
                # 切回 all 时清空白名单，避免残留误导
                if patch.selection_mode == "all":
                    sets.append("selected_ids = NULL")
                else:
                    sets.append("selected_ids = %s")
                    params.append(json.dumps(patch.selected_ids or []))
            if not sets:
                return
            sets.append("updated_at = now()")
            params.extend([tenant_id, MODULE])
            cursor.execute(
                f"""
                UPDATE bs_content_sync_sources SET {", ".join(sets)}
                WHERE tenant_id = %s AND module = %s
                """,
                params,
            )
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="源未初始化")
            conn.commit()

    await asyncio.to_thread(_update)
    row = await asyncio.to_thread(_load_source_row, tenant_id)
    return {"success": True, "source": _serialize_source(row)}


@router.post("/source/trigger")
async def trigger_source(admin: dict = Depends(require_admin)):
    """立即运行（queued 排队，调度器/本进程执行；与定时同路径）。"""
    tenant_id = admin["tenant_id"]
    row = await asyncio.to_thread(_load_source_row, tenant_id)
    if not row:
        raise HTTPException(status_code=404, detail="源未初始化")

    from src.tenant_custom.hongtao_shop.service import hongtao_shop_service

    result = await asyncio.to_thread(
        hongtao_shop_service.trigger_sync, tenant_id, "manual"
    )
    return {"success": True, "run": result}


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
                """
                SELECT id, trigger_type, status, new_count, updated_count, skipped_count,
                       deleted_count, restored_count, failed_count, vl_parsed_count,
                       vl_billed_count, embedding_tokens, credits_charged,
                       fetch_complete, total_reported, error_message,
                       started_at, completed_at, created_at
                FROM bs_content_sync_runs
                WHERE tenant_id = %s AND module = %s
                ORDER BY id DESC LIMIT %s OFFSET %s
                """,
                (tenant_id, MODULE, limit, offset),
            )
            return [dict(r) for r in cursor.fetchall()]

    rows = await asyncio.to_thread(_query)
    for r in rows:
        for key in ("started_at", "completed_at", "created_at"):
            if r.get(key):
                r[key] = r[key].isoformat()
    return {"success": True, "runs": rows, "limit": limit, "offset": offset}


@router.get("/runs/{run_id}")
async def get_run(run_id: int, admin: dict = Depends(require_admin)):
    """单 run 详情 + items 概要。"""
    tenant_id = admin["tenant_id"]

    def _query():
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, trigger_type, status, new_count, updated_count, skipped_count,
                       deleted_count, restored_count, failed_count, vl_parsed_count,
                       vl_billed_count, embedding_tokens, credits_charged,
                       fetch_complete, total_reported, error_message,
                       started_at, completed_at, created_at
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
                       vl_images, vl_billed, embedding_tokens, billing_status,
                       credits_charged, started_at, completed_at
                FROM bs_content_sync_items
                WHERE run_id = %s ORDER BY id LIMIT 500
                """,
                (run_id,),
            )
            return dict(run), [dict(r) for r in cursor.fetchall()]

    run, items = await asyncio.to_thread(_query)
    if not run:
        raise HTTPException(status_code=404, detail="run 不存在")
    for row in [run] + items:
        for key in ("started_at", "completed_at", "created_at"):
            if row.get(key):
                row[key] = row[key].isoformat()
    return {"success": True, "run": run, "items": items}


@router.get("/products")
async def list_products(
    keyword: str = Query("", max_length=64),
    selected: Optional[bool] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    admin: dict = Depends(require_admin),
):
    """产品挑选列表：本地 records 目录缓存（payload 字段），不实时调外部 API。"""
    tenant_id = admin["tenant_id"]

    def _query():
        # ILIKE 通配符转义（%/_/\ 按字面匹配；非注入面，仅防语义放大）
        kw = keyword.strip()
        for ch in ("\\", "%", "_"):
            kw = kw.replace(ch, "\\" + ch)
        like = f"%{kw}%" if kw else None
        with get_db_connection() as conn:
            cursor = conn.cursor()
            where = "r.tenant_id = %s AND r.module = %s"
            params: list = [tenant_id, MODULE]
            if like:
                where += (
                    " AND (payload->>'name' ILIKE %s OR payload->>'model' ILIKE %s"
                    " OR payload->>'procode' ILIKE %s)"
                )
                params.extend([like, like, like])
            if selected is not None:
                # all 模式视为全选；ids 模式按 JSONB 数组元素命中（? 检查数组元素）
                where += (
                    " AND (COALESCE(s.selection_mode, 'all') = 'all'"
                    " OR s.selected_ids ? r.native_id::text) = %s"
                )
                params.append(selected)
            cursor.execute(
                f"""
                SELECT COUNT(*) AS cnt FROM bs_content_sync_records r
                LEFT JOIN bs_content_sync_sources s
                  ON s.tenant_id = r.tenant_id AND s.module = r.module
                WHERE {where}
                """,
                params,
            )
            total = cursor.fetchone()["cnt"]
            cursor.execute(
                f"""
                SELECT r.native_id, r.external_id, r.payload, r.doc_id,
                       r.user_deleted, r.miss_streak, r.processing_status,
                       r.last_synced_at,
                       (s.selected_ids ? r.native_id::text) AS selected
                FROM bs_content_sync_records r
                LEFT JOIN bs_content_sync_sources s
                  ON s.tenant_id = r.tenant_id AND s.module = r.module
                WHERE {where}
                ORDER BY r.payload->>'name' ASC, r.native_id ASC
                LIMIT %s OFFSET %s
                """,
                params + [page_size, (page - 1) * page_size],
            )
            return total, [dict(r) for r in cursor.fetchall()]

    total, rows = await asyncio.to_thread(_query)
    products = []
    for r in rows:
        payload = r.get("payload") or {}
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (TypeError, ValueError):
                payload = {}
        products.append({
            "native_id": r["native_id"],
            "name": payload.get("name"),
            "model": payload.get("model"),
            "procode": payload.get("procode"),
            "status": payload.get("status"),
            "has_doc": r["doc_id"] is not None,
            "user_deleted": r["user_deleted"],
            "selected": bool(r["selected"]) if r["selected"] is not None else True,
            "processing_status": r["processing_status"],
        })
    return {
        "success": True,
        "products": products,
        "total": total,
        "page": page,
        "page_size": page_size,
    }
