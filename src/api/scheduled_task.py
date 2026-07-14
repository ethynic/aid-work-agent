"""定时任务 REST API"""

import re
from typing import Optional
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel

from src.scheduler.manager import scheduled_task_manager
from src.scheduler.db import ScheduledTaskDB, ScheduledTaskLogDB
from src.api.auth import get_current_user


router = APIRouter(prefix="/api/scheduled-tasks", tags=["定时任务"])


def _sanitize_error(error_msg: str) -> str:
    """过滤敏感信息"""
    if not error_msg:
        return error_msg
    patterns = [
        r'password["\s:=]+\S+', r'passwd["\s:=]+\S+',
        r'secret["\s:=]+\S+', r'token["\s:=]+\S+',
        r'api[_-]?key["\s:=]+\S+', r'access[_-]?key["\s:=]+\S+',
        r'private[_-]?key["\s:=]+\S+', r'auth[_-]?token["\s:=]+\S+',
    ]
    sanitized = error_msg
    for pattern in patterns:
        sanitized = re.sub(pattern, lambda m: m.group(0).split('=')[0] + '=***',
                          sanitized, flags=re.IGNORECASE)
    return sanitized


def _get_user_id(request: Request) -> str:
    """从请求中获取用户ID"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    return user.get("user_id", "")


# ==================== CRUD ====================

@router.get("")
async def list_tasks(request: Request, status: Optional[str] = None):
    """获取当前用户的定时任务列表"""
    try:
        user_id = _get_user_id(request)
        tasks = ScheduledTaskDB.list_by_user(user_id, status=status)

        # 获取每个任务的统计
        for task in tasks:
            stats = ScheduledTaskLogDB.get_stats(task["task_id"])
            task["stats"] = stats

        return {"success": True, "data": {"tasks": tasks, "total": len(tasks)}}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"后端日志：获取定时任务列表失败 {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": "获取定时任务列表失败", "debug": _sanitize_error(str(e))}
        )


@router.get("/stats")
async def get_user_stats(request: Request):
    """获取当前用户的定时任务统计"""
    try:
        user_id = _get_user_id(request)
        tasks = ScheduledTaskDB.list_by_user(user_id)

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
        logger.error(f"后端日志：获取定时任务统计失败 {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": "获取统计失败", "debug": _sanitize_error(str(e))}
        )


@router.post("/{task_id}/pause")
async def pause_task(request: Request, task_id: str):
    """暂停任务"""
    try:
        user_id = _get_user_id(request)
        task = ScheduledTaskDB.get_by_id(task_id)
        if not task or task["user_id"] != user_id:
            return {"success": False, "error": "任务不存在或无权操作"}

        if task["status"] != "active":
            return {"success": False, "error": f"任务状态异常（当前: {task['status']}），无法暂停"}

        if scheduled_task_manager.pause_task(task_id):
            return {"success": True, "message": "任务已暂停"}
        return {"success": False, "error": "暂停失败"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"后端日志：暂停定时任务失败 task_id={task_id}, {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": "暂停失败", "debug": _sanitize_error(str(e))}
        )


@router.post("/{task_id}/resume")
async def resume_task(request: Request, task_id: str):
    """恢复任务"""
    try:
        user_id = _get_user_id(request)
        task = ScheduledTaskDB.get_by_id(task_id)
        if not task or task["user_id"] != user_id:
            return {"success": False, "error": "任务不存在或无权操作"}

        if task["status"] != "paused":
            return {"success": False, "error": f"任务状态异常（当前: {task['status']}），无法恢复"}

        if scheduled_task_manager.resume_task(task_id):
            return {"success": True, "message": "任务已恢复"}
        return {"success": False, "error": "恢复失败"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"后端日志：恢复定时任务失败 task_id={task_id}, {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": "恢复失败", "debug": _sanitize_error(str(e))}
        )


@router.delete("/{task_id}")
async def cancel_task(request: Request, task_id: str):
    """取消任务"""
    try:
        user_id = _get_user_id(request)
        task = ScheduledTaskDB.get_by_id(task_id)
        if not task or task["user_id"] != user_id:
            return {"success": False, "error": "任务不存在或无权操作"}

        scheduled_task_manager.remove_task(task_id)
        if ScheduledTaskDB.delete(task_id):
            return {"success": True, "message": "任务已取消"}
        return {"success": False, "error": "取消失败"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"后端日志：取消定时任务失败 task_id={task_id}, {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": "取消失败", "debug": _sanitize_error(str(e))}
        )


@router.post("/{task_id}/trigger_task")
async def trigger_task(request: Request, task_id: str):
    """手动触发执行一次"""
    try:
        user_id = _get_user_id(request)
        task = ScheduledTaskDB.get_by_id(task_id)
        if not task or task["user_id"] != user_id:
            return {"success": False, "error": "任务不存在或无权操作"}

        if scheduled_task_manager.trigger_task(task_id):
            return {"success": True, "message": "已触发执行"}
        return {"success": False, "error": "触发失败"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"后端日志：手动触发定时任务失败 task_id={task_id}, {e}", exc_info=True)
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
        user_id = _get_user_id(request)
        task = ScheduledTaskDB.get_by_id(task_id)
        if not task or task["user_id"] != user_id:
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

        # 更新数据库
        ScheduledTaskDB.update_schedule(task_id, cron_expression=cron_expression,
                                        interval_seconds=interval_seconds)

        # 重新注册到调度器
        updated_task = ScheduledTaskDB.get_by_id(task_id)
        if updated_task and updated_task["status"] == "active":
            scheduled_task_manager.register_task(updated_task)

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
        logger.error(f"后端日志：更新定时任务调度失败 task_id={task_id}, {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": "更新调度时间失败", "debug": _sanitize_error(str(e))}
        )


# ==================== 执行日志 ====================

@router.get("/{task_id}/logs")
async def get_task_logs(request: Request, task_id: str, limit: int = 20):
    """获取任务的执行日志"""
    try:
        user_id = _get_user_id(request)
        task = ScheduledTaskDB.get_by_id(task_id)
        if not task or task["user_id"] != user_id:
            return {"success": False, "error": "任务不存在或无权操作"}

        logs = ScheduledTaskLogDB.list_by_task(task_id, limit=limit)
        return {"success": True, "data": {"logs": logs, "total": len(logs)}}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"后端日志：获取定时任务日志失败 task_id={task_id}, {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": "获取日志失败", "debug": _sanitize_error(str(e))}
        )
