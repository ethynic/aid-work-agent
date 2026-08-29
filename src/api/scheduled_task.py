"""定时任务 REST API"""

from typing import Optional
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel

from src.scheduler.db import ScheduledTaskDB, ScheduledTaskLogDB
from src.scheduler.error_sanitizer import sanitize_scheduled_task_error
from src.api.auth import get_current_user
from src.saas.context import get_current_tenant_id


router = APIRouter(prefix="/api/scheduled-tasks", tags=["定时任务"])


def _sanitize_error(error_msg: str) -> str:
    """过滤敏感信息（统一委托独立脱敏模块，含引号/未闭合/截断保守遮蔽）"""
    return sanitize_scheduled_task_error(error_msg)


def _get_request_identity(request: Request) -> tuple[str, str]:
    """返回请求级 user/tenant 身份；租户来源缺失或无法验证时 fail-closed。

    租户优先取请求上下文（TenantMiddleware 注入），SaaS 部署下缺失时回退认证
    用户行上的 tenant_id（''=平台管理员/公共用户），两处来源都缺失视为上下文
    不可信，403 拒绝，绝不把未知身份隐式当成公共租户。
    非 SaaS 部署无租户语义，统一解析为 ''（与工具层 _resolve_runtime_tenant_id
    一致），否则 demo 等用户行自带租户时，工具创建的 '' 任务在 API 视图不可见。
    """
    user = get_current_user(request)
    if not user or not user.get("user_id"):
        raise HTTPException(status_code=401, detail="未登录")
    tenant_id = get_current_tenant_id()
    if tenant_id is None:
        from src.config.settings import settings
        if not settings.saas.enabled:
            tenant_id = ""
        elif "tenant_id" in user:
            tenant_id = user.get("tenant_id") or ""
        else:
            raise HTTPException(status_code=403, detail="租户上下文缺失")
    return str(user["user_id"]), str(tenant_id)


# ==================== CRUD ====================

@router.get("")
async def list_tasks(request: Request, status: Optional[str] = None):
    """获取当前用户的定时任务列表"""
    try:
        user_id, tenant_id = _get_request_identity(request)
        # 租户过滤：tenant_id='' 的遗留未回填行在具体租户视图 fail-closed 不可见
        tasks = ScheduledTaskDB.list_by_user(user_id, status=status, tenant_id=tenant_id)

        # 获取每个任务的统计
        for task in tasks:
            stats = ScheduledTaskLogDB.get_stats(
                task["task_id"], tenant_id=tenant_id, user_id=user_id
            )
            task["stats"] = stats

        return {"success": True, "data": {"tasks": tasks, "total": len(tasks)}}
    except HTTPException:
        raise
    except Exception as e:
        logger.opt(exception=True).error(f"后端日志：获取定时任务列表失败 {e}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": "获取定时任务列表失败", "debug": _sanitize_error(str(e))}
        )


@router.get("/stats")
async def get_user_stats(request: Request):
    """获取当前用户的定时任务统计"""
    try:
        user_id, tenant_id = _get_request_identity(request)
        tasks = ScheduledTaskDB.list_by_user(user_id, tenant_id=tenant_id)

        active_count = sum(1 for t in tasks if t["status"] == "active")
        paused_count = sum(1 for t in tasks if t["status"] == "paused")
        total_runs = sum(t["total_runs"] for t in tasks)
        total_success = sum(t["success_count"] for t in tasks)
        total_fail = sum(t["fail_count"] for t in tasks)
        success_rate = round(total_success / total_runs * 100, 1) if total_runs > 0 else 0

        return {
            "success": True,
            "data": {
                "active_tasks": active_count,
                "paused_tasks": paused_count,
                "total_tasks": len(tasks),
                "total_runs": total_runs,
                "total_success": total_success,
                "total_fail": total_fail,
                "success_rate": success_rate
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.opt(exception=True).error(f"后端日志：获取定时任务统计失败 {e}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": "获取统计失败", "debug": _sanitize_error(str(e))}
        )


@router.get("/{task_id}")
async def get_task(request: Request, task_id: str):
    """获取任务详情"""
    try:
        user_id, tenant_id = _get_request_identity(request)
        # 属主与租户条件进 SQL：跨租户/跨用户任务直接查不到（不泄露存在性）
        task = ScheduledTaskDB.get_by_id(task_id, tenant_id=tenant_id, user_id=user_id)

        if not task:
            return {"success": False, "error": "任务不存在"}

        stats = ScheduledTaskLogDB.get_stats(
            task_id, tenant_id=tenant_id, user_id=user_id
        )
        task["stats"] = stats

        return {"success": True, "data": task}
    except HTTPException:
        raise
    except Exception as e:
        logger.opt(exception=True).error(f"后端日志：获取定时任务详情失败 task_id={task_id}, {e}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": "获取任务详情失败", "debug": _sanitize_error(str(e))}
        )


@router.post("/{task_id}/pause")
async def pause_task(request: Request, task_id: str):
    """暂停任务（只写 DB，background reconcile ≤30s 内同步到调度器）"""
    try:
        user_id, tenant_id = _get_request_identity(request)
        task = ScheduledTaskDB.get_by_id(task_id, tenant_id=tenant_id, user_id=user_id)
        if not task:
            return {"success": False, "error": "任务不存在或无权操作"}

        if task["status"] != "active":
            return {"success": False, "error": f"任务状态异常（当前: {task['status']}），无法暂停"}

        if ScheduledTaskDB.update_status(
            task_id, "paused", tenant_id=tenant_id, user_id=user_id
        ):
            return {"success": True, "message": "任务已暂停（≤30s 生效）"}
        return {"success": False, "error": "暂停失败"}
    except HTTPException:
        raise
    except Exception as e:
        logger.opt(exception=True).error(f"后端日志：暂停定时任务失败 task_id={task_id}, {e}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": "暂停失败", "debug": _sanitize_error(str(e))}
        )


@router.post("/{task_id}/resume")
async def resume_task(request: Request, task_id: str):
    """恢复任务（只写 DB，background reconcile ≤30s 内同步到调度器）"""
    try:
        user_id, tenant_id = _get_request_identity(request)
        task = ScheduledTaskDB.get_by_id(task_id, tenant_id=tenant_id, user_id=user_id)
        if not task:
            return {"success": False, "error": "任务不存在或无权操作"}

        if task["status"] != "paused":
            return {"success": False, "error": f"任务状态异常（当前: {task['status']}），无法恢复"}

        if ScheduledTaskDB.update_status(
            task_id, "active", tenant_id=tenant_id, user_id=user_id
        ):
            return {"success": True, "message": "任务已恢复（≤30s 生效）"}
        return {"success": False, "error": "恢复失败"}
    except HTTPException:
        raise
    except Exception as e:
        logger.opt(exception=True).error(f"后端日志：恢复定时任务失败 task_id={task_id}, {e}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": "恢复失败", "debug": _sanitize_error(str(e))}
        )


@router.delete("/{task_id}")
async def cancel_task(request: Request, task_id: str):
    """取消任务（只写 DB，background reconcile ≤30s 内从调度器移除）"""
    try:
        user_id, tenant_id = _get_request_identity(request)
        task = ScheduledTaskDB.get_by_id(task_id, tenant_id=tenant_id, user_id=user_id)
        if not task:
            return {"success": False, "error": "任务不存在或无权操作"}

        if ScheduledTaskDB.delete(task_id, tenant_id=tenant_id, user_id=user_id):
            return {"success": True, "message": "任务已取消"}
        return {"success": False, "error": "取消失败"}
    except HTTPException:
        raise
    except Exception as e:
        logger.opt(exception=True).error(f"后端日志：取消定时任务失败 task_id={task_id}, {e}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": "取消失败", "debug": _sanitize_error(str(e))}
        )


@router.post("/{task_id}/trigger_task")
async def trigger_task(request: Request, task_id: str):
    """手动触发执行一次（写 manual_trigger_at=NOW()，background reconcile ≤30s 内执行）"""
    try:
        user_id, tenant_id = _get_request_identity(request)
        task = ScheduledTaskDB.get_by_id(task_id, tenant_id=tenant_id, user_id=user_id)
        if not task:
            return {"success": False, "error": "任务不存在或无权操作"}

        if ScheduledTaskDB.request_manual_trigger(
            task_id, tenant_id=tenant_id, user_id=user_id
        ):
            return {"success": True, "message": "已触发执行（≤30s 内生效）"}
        return {"success": False, "error": "触发失败（任务可能不存在或非 active/paused 状态）"}
    except HTTPException:
        raise
    except Exception as e:
        logger.opt(exception=True).error(f"后端日志：手动触发定时任务失败 task_id={task_id}, {e}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": "触发失败", "debug": _sanitize_error(str(e))}
        )


class UpdateScheduleRequest(BaseModel):
    schedule_type: str
    time_config: dict


@router.put("/{task_id}/schedule")
async def update_task_schedule(request: Request, task_id: str, body: UpdateScheduleRequest):
    """更新任务调度时间"""
    from src.tools.scheduler.scheduled_task_tool import generate_cron_expression
    try:
        user_id, tenant_id = _get_request_identity(request)
        task = ScheduledTaskDB.get_by_id(task_id, tenant_id=tenant_id, user_id=user_id)
        if not task:
            return {"success": False, "error": "任务不存在或无权操作"}

        time_config = body.time_config or {}
        schedule_type = body.schedule_type or task["schedule_type"]

        # time_config 为空时使用默认值
        if not time_config:
            default_configs = {
                "daily": {"hour": 9, "minute": 0},
                "weekly": {"day_of_week": "mon", "hour": 9, "minute": 0},
                "monthly": {"day": 1, "hour": 9, "minute": 0},
                "interval": {"interval_hours": 1},
            }
            time_config = default_configs.get(schedule_type, {})

        cron_expression = generate_cron_expression(schedule_type, time_config)
        interval_seconds = time_config.get("interval_hours", 1) * 3600 if schedule_type == "interval" else None

        # 更新数据库（background reconcile ≤30s 内按 updated_at 变化自动重注册）；
        # 租户+用户条件进 UPDATE 本身，防止校验与写入之间的 TOCTOU
        if not ScheduledTaskDB.update_schedule(
            task_id, cron_expression=cron_expression,
            interval_seconds=interval_seconds,
            tenant_id=tenant_id, user_id=user_id,
        ):
            return {"success": False, "error": "任务不存在或无权操作"}

        logger.info(f"后端日志：定时任务调度已更新 task_id={task_id}, cron={cron_expression}, "
                    f"schedule_type={schedule_type}, time_config={time_config}")

        return {"success": True, "message": "调度时间已更新", "data": {
            "cron_expression": cron_expression,
            "schedule_type": schedule_type,
            "interval_seconds": interval_seconds,
        }}
    except HTTPException:
        raise
    except Exception as e:
        logger.opt(exception=True).error(f"后端日志：更新定时任务调度失败 task_id={task_id}, {e}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": "更新调度时间失败", "debug": _sanitize_error(str(e))}
        )


# ==================== 执行日志 ====================

@router.get("/{task_id}/logs")
async def get_task_logs(request: Request, task_id: str, limit: int = 20):
    """获取任务的执行日志"""
    try:
        user_id, tenant_id = _get_request_identity(request)
        task = ScheduledTaskDB.get_by_id(task_id, tenant_id=tenant_id, user_id=user_id)
        if not task:
            return {"success": False, "error": "任务不存在或无权操作"}

        logs = ScheduledTaskLogDB.list_by_task(
            task_id, limit=limit, tenant_id=tenant_id, user_id=user_id
        )
        return {"success": True, "data": {"logs": logs, "total": len(logs)}}
    except HTTPException:
        raise
    except Exception as e:
        logger.opt(exception=True).error(f"后端日志：获取定时任务日志失败 task_id={task_id}, {e}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": "获取日志失败", "debug": _sanitize_error(str(e))}
        )


@router.get("/get_user_logs")
async def get_user_logs(request: Request, limit: int = 50):
    """获取当前用户的所有执行日志"""
    try:
        user_id, tenant_id = _get_request_identity(request)
        # 租户过滤：tenant_id='' 的遗留未回填行在具体租户视图 fail-closed 不可见
        logs = ScheduledTaskLogDB.list_by_user(user_id, limit=limit, tenant_id=tenant_id)
        return {"success": True, "data": {"logs": logs, "total": len(logs)}}
    except HTTPException:
        raise
    except Exception as e:
        logger.opt(exception=True).error(f"后端日志：获取用户执行日志失败 {e}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": "获取日志失败", "debug": _sanitize_error(str(e))}
        )
