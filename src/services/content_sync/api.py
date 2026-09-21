"""内容同步通用 API：租户已开通的数据源清单。

只读、无租户专名：module 取自 bs_content_sync_sources 行（授权记录），
前端连接中心菜单据此门控显示；页面路由与调度仍归属各租户模块。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from fastapi import APIRouter, Depends

from src.db.database import get_db_connection
from src.saas.api.tenant_auth import require_admin

router = APIRouter(prefix="/api/saas/connection-sources", tags=["connection-sources"])


@router.get("")
async def list_my_sources(admin: dict = Depends(require_admin)):
    """当前租户已开通的数据源（module 维度）；失败时前端按未授权兜底。"""

    def _query() -> List[Dict[str, Any]]:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT module, enabled
                FROM bs_content_sync_sources
                WHERE tenant_id = %s
                ORDER BY module
                """,
                (admin["tenant_id"],),
            )
            return [dict(r) for r in cursor.fetchall()]

    rows = await asyncio.to_thread(_query)
    return {
        "success": True,
        "sources": [
            {"module": r["module"], "enabled": bool(r["enabled"])}
            for r in rows
        ],
    }
