"""管理后台系统健康监控 API（/portal/system-health 数据源）。

设计文档：docs/system/system-health-monitor-design.md §5

- GET /api/admin/monitor/overview：当前快照 + active 告警聚合 + AI 研判结论 + 心跳；
  采集器状态三态判定（正常 / 采集中断 / 存储不可用）见设计 §4.5：
  Redis 不可用（含内存降级态）必须显式返回 storage_unavailable，**不读降级内存
  快照充当数据**；探测成功但快照/心跳缺失为 collector_down（含未部署 background
  容器的环境）
- GET /api/admin/monitor/runners：运行中/排队任务明细（runner_id/租户/数字员工/
  状态/时长），不含用户消息内容与凭证
"""

import time
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from loguru import logger

from src.api.auth import get_current_user
from src.core.error_log_sink import sanitize_error_info
from src.core.redis_client import redis_client
from src.saas.permissions.checker import is_platform_admin
from src.services.system_health.collector import heartbeat_key, snapshot_key
from src.services.system_health.assessment import assessment_key

router = APIRouter(prefix="/api/admin/monitor", tags=["系统健康监控"])


def _check_admin(user) -> None:
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    if not is_platform_admin(user):
        raise HTTPException(status_code=403, detail="需要平台管理员权限")


def _error_response(message: str, exc: Exception) -> Dict[str, Any]:
    return {
        "success": False,
        "error": message,
        "debug": sanitize_error_info(str(exc)),
    }


def _collectors_status() -> Dict[str, Any]:
    """判定采集器状态三态（设计 §4.5 口径）。

    - Redis 探测失败或处于内存降级态 -> storage_unavailable（横幅「监控存储不可用」）
    - 探测成功但快照/心跳缺失 -> collector_down（含 sim 私有云未部署 background 容器）
    - 其余 -> ok
    """
    server_time = datetime.now().isoformat(timespec="seconds")
    if not redis_client.is_available():
        return {
            "status": "storage_unavailable",
            "snapshot": None,
            "assessment": None,
            "heartbeat": None,
            "server_time": server_time,
        }

    snapshot = redis_client.get(snapshot_key())
    heartbeat = redis_client.get(heartbeat_key())
    if not isinstance(snapshot, dict) or not isinstance(heartbeat, dict):
        return {
            "status": "collector_down",
            "snapshot": None,
            "assessment": None,
            "heartbeat": None,
            "server_time": server_time,
        }

    assessment = redis_client.get(assessment_key())
    if not isinstance(assessment, dict):
        assessment = None

    collected_at = snapshot.get("collected_at")
    age_seconds: Optional[float] = None
    try:
        age_seconds = round(time.time() - datetime.fromisoformat(str(collected_at)).timestamp(), 1)
    except (TypeError, ValueError):
        pass

    return {
        "status": "ok",
        "snapshot": snapshot,
        "assessment": assessment,
        "heartbeat": {"collected_at": heartbeat.get("collected_at"), "age_seconds": age_seconds},
        "server_time": server_time,
    }


@router.get("/overview")
async def get_monitor_overview(request: Request):
    """当前健康快照 + AI 研判结论 + 采集器状态（platform_admin）。"""
    user = get_current_user(request)
    _check_admin(user)
    try:
        return {"success": True, **_collectors_status()}
    except Exception as e:
        logger.opt(exception=True).error(f"后端日志：健康监控 overview 查询失败: {e}")
        raise HTTPException(status_code=500, detail=_error_response("健康监控查询失败", e))


@router.get("/runners")
async def list_monitor_runners(request: Request):
    """运行中/排队任务明细（platform_admin）。

    只含 runner_id/租户/数字员工/状态/时长/attempt，不含输入内容与凭证。
    """
    user = get_current_user(request)
    _check_admin(user)
    try:
        from src.db.database import get_db_connection

        running: list = []
        queued: list = []
        tenant_ids: set = set()
        with get_db_connection() as cur:
            cur.execute(
                "SELECT runner_id, tenant_id, profile_id, status, attempt, "
                "EXTRACT(EPOCH FROM (clock_timestamp()-execution_started_at))::bigint AS elapsed_seconds "
                "FROM agent_runners "
                "WHERE status IN ('running','finalizing') AND execution_started_at IS NOT NULL "
                "ORDER BY execution_started_at ASC LIMIT 200"
            )
            running = [dict(r) for r in cur.fetchall()]
            cur.execute(
                "SELECT runner_id, tenant_id, profile_id, status, attempt, "
                "EXTRACT(EPOCH FROM (clock_timestamp()-accepted_at))::bigint AS elapsed_seconds "
                "FROM agent_runners WHERE status = 'queued' "
                "ORDER BY accepted_at ASC LIMIT 200"
            )
            queued = [dict(r) for r in cur.fetchall()]

        tenant_ids = {r["tenant_id"] for r in running + queued if r.get("tenant_id")}
        tenant_names: Dict[str, str] = {}
        if tenant_ids:
            placeholders = ",".join(["%s"] * len(tenant_ids))
            with get_db_connection() as cur:
                cur.execute(
                    f"SELECT tenant_id, company_name FROM tenants WHERE tenant_id IN ({placeholders})",
                    list(tenant_ids),
                )
                tenant_names = {r["tenant_id"]: r.get("company_name") for r in cur.fetchall()}

        for item in running + queued:
            item["tenant_name"] = tenant_names.get(item.get("tenant_id"))

        return {"success": True, "running": running, "queued": queued}
    except Exception as e:
        logger.opt(exception=True).error(f"后端日志：健康监控 runners 查询失败: {e}")
        raise HTTPException(status_code=500, detail=_error_response("任务明细查询失败", e))
