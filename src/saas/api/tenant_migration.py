"""
租户数据迁移 API

路由：/api/saas/tenants/{tenant_id}/migration/*
- POST /preview — 预览迁移数据量
- POST /execute — 执行迁移
"""

import asyncio
import os
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pydantic import BaseModel, Field

from src.saas.api.tenant_auth import require_admin

router = APIRouter(prefix="/api/saas/tenants", tags=["租户数据迁移"])


class MigrationRequest(BaseModel):
    source_db: str = Field(default="aid_work_agent2", description="源数据库名")
    source_tenant: str = Field(..., description="源租户ID")
    tables: List[str] = Field(
        default=["kb", "knowledge_categories", "travel_quote"],
        description="要迁移的表组",
    )
    mode: str = Field(default="replace", description="迁移模式: replace / merge")
    source_storage: str = Field(default="/app/source_storage", description="源存储根路径")


def _sanitize_error(error_msg: str) -> str:
    import re
    patterns = [
        r'password["\s:=]+\S+',
        r'passwd["\s:=]+\S+',
        r'secret["\s:=]+\S+',
        r'token["\s:=]+\S+',
        r'api[_-]?key["\s:=]+\S+',
    ]
    for p in patterns:
        error_msg = re.sub(p, lambda m: m.group(0).split('=')[0] + '=***', error_msg, flags=re.IGNORECASE)
    return error_msg


@router.post("/{tenant_id}/migration/preview")
async def preview_migration(tenant_id: str, request: MigrationRequest, req: Request):
    """预览迁移数据量（dry-run 模式）"""
    require_admin(req, tenant_id)

    from scripts.tenant_migrate_kb import run_migration

    target_db = _get_target_db()
    target_storage = _get_target_storage()

    try:
        result = await asyncio.to_thread(
            run_migration,
            source_db=request.source_db,
            target_db=target_db,
            source_tenant=request.source_tenant,
            target_tenant=tenant_id,
            tables=request.tables,
            mode=request.mode,
            source_storage=request.source_storage,
            target_storage=target_storage,
            dry_run=True,
        )
        return {"success": result.get("success", True), "summary": result.get("summary", {}),
                "errors": result.get("errors", [])}
    except Exception as e:
        logger.error(f"预览迁移失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail={"success": False, "error": _sanitize_error(str(e))})


@router.post("/{tenant_id}/migration/execute")
async def execute_migration(tenant_id: str, request: MigrationRequest, req: Request):
    """执行数据迁移"""
    require_admin(req, tenant_id)

    from scripts.tenant_migrate_kb import run_migration

    target_db = _get_target_db()
    target_storage = _get_target_storage()

    try:
        result = await asyncio.to_thread(
            run_migration,
            source_db=request.source_db,
            target_db=target_db,
            source_tenant=request.source_tenant,
            target_tenant=tenant_id,
            tables=request.tables,
            mode=request.mode,
            source_storage=request.source_storage,
            target_storage=target_storage,
            dry_run=False,
        )
        if not result.get("success"):
            raise HTTPException(
                status_code=500,
                detail={"success": False, "error": "; ".join(result.get("errors", ["未知错误"])),
                        "summary": result.get("summary", {})},
            )
        return {"success": True, "summary": result.get("summary", {}), "errors": result.get("errors", [])}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"执行迁移失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail={"success": False, "error": _sanitize_error(str(e))})


def _get_target_db() -> str:
    return os.getenv("DATABASE_URL", "").split("/")[-1] if "/" in os.getenv("DATABASE_URL", "") else "aid_work_agent"


def _get_target_storage() -> str:
    try:
        from src.config.settings import settings
        return str(settings.storage.uploads_dir) if hasattr(settings, 'storage') else "storage/uploads"
    except Exception:
        return "storage/uploads"
