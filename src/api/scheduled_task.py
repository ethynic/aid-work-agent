"""定时任务 REST API（v2 Runner 化重建，设计 §10，Phase 4）

读写全部 tenant+user 归属条件进 SQL（从请求鉴权上下文取，越权行直接查不到）。
端点：列表/统计/详情/暂停/恢复/删除（事务守卫）/立即执行一次（manual 槽）/
改期（重算 due）/编辑（内容与调度解耦）。执行历史不在本 API——由任务视图
经 Runner 网关读取（agent_runners + 任务会话消息）。
"""

import asyncio
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pydantic import BaseModel, Field

from src.api.web_subject import fresh_web_user
from src.scheduler.db import (ScheduledTaskBusyError, ScheduledTaskQuotaError,
                              ScheduledTaskDB)
from src.scheduler.schedule import (
    ScheduleConfigError,
    build_schedule_fields,
    max_tasks_per_user,
    validate_task_attachments,
)

router = APIRouter(prefix="/api/scheduled-tasks", tags=["定时任务"])

# 任务行中不对外暴露的字段（调度侧内部交接数据，含完整提交快照）
_INTERNAL_FIELDS = ("pending_snapshot",)


async def _request_identity(request: Request):
    """请求级 (user_id, tenant_id) 身份；fresh 校验 + 租户解析 fail-closed。

    租户取法与 owned_web_session 同口径：platform_admin 可带 X-Tenant-Id 跨租户，
    普通用户必须与自身租户一致；租户来源缺失（非 platform 且无 tenant_id）→ 403。
    身份解析经 to_thread 执行（fresh_web_user → fresh_web_subject 内含同步
    DB 查询，直接在 async handler 中运行会阻塞事件循环——评审 P2）。
    """
    user = await asyncio.to_thread(
        fresh_web_user, request.headers.get("Authorization", ""))
    platform = user.get("role") == "platform_admin"
    requested = request.headers.get("X-Tenant-Id") or None
    tenant_id = (requested or None) if platform else user.get("tenant_id")
    if (not platform and requested and requested != tenant_id) or (
            tenant_id is None and not platform):
        raise HTTPException(status_code=403, detail="租户上下文缺失或无权访问")
    return str(user["user_id"]), tenant_id


def _public_task(task: Dict[str, Any]) -> Dict[str, Any]:
    """任务行公开投影：剥离调度侧内部字段"""
    return {key: value for key, value in task.items() if key not in _INTERNAL_FIELDS}


async def _owned_task(request: Request, task_id: str,
                      identity=None) -> Dict[str, Any]:
    """按归属条件取任务行；查不到（不存在/越权）统一 404 不泄露存在性。

    identity：调用方已解析的 (user_id, tenant_id)——每请求只做一次
    fresh_web_user 认证（评审 P2：此前 _request_identity 与 _owned_task
    各调一次造成重复同步认证）。"""
    if identity is None:
        identity = await _request_identity(request)
    user_id, tenant_id = identity
    task = await asyncio.to_thread(
        ScheduledTaskDB.get_by_id, task_id, tenant_id=tenant_id, user_id=user_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在或无权操作")
    return task


def _task_response(task: Dict[str, Any], message: Optional[str] = None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"success": True, "data": _public_task(task)}
    if message:
        payload["message"] = message
    return payload


# ==================== 列表 / 统计 / 详情 ====================

@router.get("")
async def list_tasks(request: Request, status: Optional[str] = None):
    """获取当前用户的定时任务列表（归属过滤进 SQL）"""
    user_id, tenant_id = await _request_identity(request)
    tasks = await asyncio.to_thread(
        ScheduledTaskDB.list_by_user, user_id, status=status, tenant_id=tenant_id)
    return {"success": True, "data": {"tasks": [_public_task(t) for t in tasks],
                                      "total": len(tasks)}}


@router.get("/stats")
async def get_user_stats(request: Request):
    """获取当前用户的定时任务统计摘要"""
    user_id, tenant_id = await _request_identity(request)
    tasks = await asyncio.to_thread(
        ScheduledTaskDB.list_by_user, user_id, tenant_id=tenant_id)
    total_runs = sum(t.get("total_runs", 0) or 0 for t in tasks)
    total_success = sum(t.get("success_count", 0) or 0 for t in tasks)
    total_fail = sum(t.get("fail_count", 0) or 0 for t in tasks)
    return {"success": True, "data": {
        "total_tasks": len(tasks),
        "active_tasks": sum(1 for t in tasks if t["status"] == "active"),
        "paused_tasks": sum(1 for t in tasks if t["status"] == "paused"),
        "total_runs": total_runs,
        "total_success": total_success,
        "total_fail": total_fail,
        "success_rate": round(total_success / total_runs * 100, 1) if total_runs > 0 else 0,
    }}


@router.get("/{task_id}")
async def get_task(request: Request, task_id: str):
    """获取任务详情"""
    identity = await _request_identity(request)
    task = await _owned_task(request, task_id, identity)
    return _task_response(task)


# ==================== 生命周期操作 ====================

@router.post("/{task_id}/pause")
async def pause_task(request: Request, task_id: str):
    """暂停任务（仅 active；清空 pending 槽；已受理的执行继续到终态）"""
    identity = await _request_identity(request)
    user_id, tenant_id = identity
    task = await _owned_task(request, task_id, identity)
    updated = await asyncio.to_thread(
        ScheduledTaskDB.pause, task_id, tenant_id=tenant_id, user_id=user_id)
    if not updated:
        raise HTTPException(
            status_code=409,
            detail=f"任务当前状态为 {task['status']}，仅执行中（active）的任务可暂停")
    return {"success": True, "message": "任务已暂停"}


@router.post("/{task_id}/resume")
async def resume_task(request: Request, task_id: str):
    """恢复任务（统一走 resume_recompute：事务内锁行重算 + once 过宽限统一
    转 expired + 配额检查 + 恢复回合重置——与工具同一转换路径）"""
    identity = await _request_identity(request)
    task = await _owned_task(request, task_id, identity)
    try:
        updated = await asyncio.to_thread(
            ScheduledTaskDB.resume_recompute, task_id,
            max_active=max_tasks_per_user(),
            tenant_id=identity[1], user_id=identity[0])
    except (ScheduleConfigError, ValueError) as e:
        logger.error(f"后端日志：恢复定时任务重算 due 失败 task_id={task_id}")
        raise HTTPException(status_code=409, detail=str(e) or "任务调度配置非法，无法恢复")
    except ScheduledTaskQuotaError as e:
        raise HTTPException(status_code=409, detail=str(e))
    if not updated:
        raise HTTPException(
            status_code=409,
            detail=f"任务当前状态为 {task['status']}，仅已暂停（paused）的任务可恢复")
    if updated["status"] == "expired":
        # once 过宽限的统一转换与回执（与工具同一路径，不再分叉）
        return {"success": True, "expired": True,
                "message": "该一次性任务的执行时间已超过宽限，恢复操作已将其作废"
                           "（expired）；如仍需执行请重新创建或改期"}
    return {"success": True, "message": "任务已恢复",
            "data": {"next_due_at": updated.get("next_due_at")}}


@router.delete("/{task_id}")
async def delete_task(request: Request, task_id: str):
    """删除任务（硬删任务+任务会话+消息；事务守卫：非终态 runner 存在时 409）"""
    identity = await _request_identity(request)
    user_id, tenant_id = identity
    await _owned_task(request, task_id, identity)
    try:
        deleted = await asyncio.to_thread(
            ScheduledTaskDB.delete_with_session, task_id,
            tenant_id=tenant_id, user_id=user_id)
    except ScheduledTaskBusyError:
        raise HTTPException(status_code=409, detail="任务正在执行，稍后再试")
    if not deleted:
        # 守卫锁行未命中：并发删除或 platform_admin 未带 X-Tenant-Id
        # （tenant NULL 不匹配任何任务行）——如实 404，不谎报已删除
        raise HTTPException(status_code=404, detail="任务不存在或无权操作")
    return {"success": True, "message": "任务已删除（含任务会话与消息）"}


@router.post("/{task_id}/trigger")
async def trigger_task(request: Request, task_id: str):
    """立即执行一次（manual pending 槽；不改 next_due_at，≤poll 间隔由循环提交）"""
    from src.scheduler.dispatch import NON_INTERACTIVE_AUGMENTATION
    identity = await _request_identity(request)
    await _owned_task(request, task_id, identity)
    updated = await asyncio.to_thread(
        ScheduledTaskDB.submit_manual, task_id,
        occurrence_key=uuid.uuid4().hex,
        prompt_augmentations=[NON_INTERACTIVE_AUGMENTATION])
    if not updated:
        raise HTTPException(
            status_code=409, detail="任务不在可执行状态（非 active 或已有待提交执行）")
    return {"success": True, "message": "已触发执行（稍后由调度循环提交，可在任务视图查看进度）"}


# ==================== 编辑（内容与调度解耦） ====================

class UpdateScheduleRequest(BaseModel):
    """改期请求：仅调度字段变更（重算 next_due_at），不动任务内容"""
    schedule_type: str = Field(..., description="调度类型：daily/weekly/monthly/interval/once")
    time_config: Dict[str, Any] = Field(..., description=(
        "时间配置：once 必填 run_at（未来时间）；daily/weekly/monthly 用 hour/minute"
        "（weekly 另需 day_of_week，monthly 另需 day）；interval 用 interval_hours"
    ))


@router.put("/{task_id}/schedule")
async def update_task_schedule(request: Request, task_id: str, body: UpdateScheduleRequest):
    """改期：更新调度字段 + 重算 next_due_at（paused 任务改期后经 resume 生效）"""
    identity = await _request_identity(request)
    user_id, tenant_id = identity
    await _owned_task(request, task_id, identity)
    try:
        cron_expression, interval_seconds, run_at, next_due_at = build_schedule_fields(
            body.schedule_type, body.time_config or {})
    except ScheduleConfigError as e:
        raise HTTPException(status_code=422, detail=str(e))
    updated = await asyncio.to_thread(
        ScheduledTaskDB.update_schedule, task_id,
        schedule_type=body.schedule_type, cron_expression=cron_expression,
        interval_seconds=interval_seconds, run_at=run_at,
        next_due_at=next_due_at, tenant_id=tenant_id, user_id=user_id)
    if not updated:
        raise HTTPException(status_code=404, detail="任务不存在或无权操作")
    logger.info(f"后端日志：定时任务调度已更新 task_id={task_id}, "
                f"schedule_type={body.schedule_type}, next_due_at={next_due_at}")
    return {"success": True, "message": "调度时间已更新",
            "data": {"schedule_type": body.schedule_type,
                     "cron_expression": cron_expression,
                     "interval_seconds": interval_seconds,
                     "run_at": run_at,
                     "next_due_at": next_due_at}}


class UpdateDefinitionRequest(BaseModel):
    """编辑任务内容（不动调度字段、不重算 due；已认领执行的快照不受影响）"""
    name: Optional[str] = Field(None, description="任务名称")
    task_prompt: Optional[str] = Field(None, description="自包含任务定义")
    attachments: Optional[List[Dict[str, Any]]] = Field(None, description="受权资源引用（file_id 等）")
    notification_target_id: Optional[str] = Field(None, description="通知目标 ID")
    clear_notification_target: bool = Field(
        False, description="显式清除通知目标（恢复默认：站内信+个人偏好渠道）")


@router.put("/{task_id}")
async def update_task(request: Request, task_id: str, body: UpdateDefinitionRequest):
    """编辑任务内容/附件/通知目标（F4：clear_notification_target=true 显式清空）"""
    identity = await _request_identity(request)
    user_id, tenant_id = identity
    await _owned_task(request, task_id, identity)
    clear_fields = ["notification_target_id"] if body.clear_notification_target else None
    if body.clear_notification_target and body.notification_target_id:
        raise HTTPException(
            status_code=422,
            detail="notification_target_id 与 clear_notification_target 互斥")
    if body.name is not None and not body.name.strip():
        raise HTTPException(status_code=422, detail="任务名称不能为空白")
    if body.task_prompt is not None and not body.task_prompt.strip():
        raise HTTPException(status_code=422, detail="任务定义（task_prompt）不能为空白")
    if body.attachments is not None:
        try:
            body.attachments = validate_task_attachments(body.attachments)
        except ScheduleConfigError as e:
            raise HTTPException(status_code=422, detail=str(e))
    if body.notification_target_id and not await asyncio.to_thread(
            ScheduledTaskDB.notify_target_visible, body.notification_target_id,
            tenant_id=tenant_id, user_id=user_id):
        # 归属校验（设计 §3.3/§8.3 同口径）：只允许本人个人/本租户租户级目标
        raise HTTPException(status_code=422, detail="通知目标不存在或不可用")
    updated = await asyncio.to_thread(
        ScheduledTaskDB.update_definition, task_id,
        name=body.name, task_prompt=body.task_prompt,
        attachments=body.attachments,
        notification_target_id=body.notification_target_id,
        clear_fields=clear_fields,
        tenant_id=tenant_id, user_id=user_id)
    if not updated:
        raise HTTPException(status_code=422, detail="无可更新字段")
    task = await asyncio.to_thread(
        ScheduledTaskDB.get_by_id, task_id, tenant_id=tenant_id, user_id=user_id)
    if not task:
        # 更新与删除并发：行已被任务管理删除（不泄露存在性，统一 404）
        raise HTTPException(status_code=404, detail="任务不存在或无权操作")
    return _task_response(task, message="任务已更新")
