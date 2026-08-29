"""
定时任务工具

提供创建和管理定时任务的能力，包含两个工具：
- CreateScheduledTaskTool: 创建定时执行的任务（含 dry-run 验证）
- ManageScheduledTaskTool: 管理已创建的定时任务（列表、暂停、恢复、取消、查看日志）
"""

import asyncio
from typing import Any, Dict, Optional

from loguru import logger
from pydantic import BaseModel, Field

from src.tools.base import BaseTool
from src.tools.context import (
    ToolExecutionContext,
    current_tool_execution_context,
)


def _resolve_runtime_tenant_id(context: Optional[ToolExecutionContext]) -> Optional[str]:
    """解析工具调用的可信租户；未知身份绝不降级成公共租户。

    优先取请求级工具执行上下文的 tenant_id（''=明确公共用户，由可信边界构造时
    从请求租户解析注入）；非 SaaS 部署无租户语义，视为公共租户；
    SaaS 部署下上下文缺失（None）时 fail-closed 返回 None，由调用方拒绝操作。
    """
    tenant_id = context.tenant_id if context else None
    if tenant_id is not None:
        return tenant_id
    try:
        from src.config.settings import settings

        if not settings.saas.enabled:
            return ""
    except Exception:
        pass
    return None


class CreateScheduledTaskInput(BaseModel):
    """创建定时任务参数"""
    name: str = Field(..., description="任务名称，简短描述（如：每日邮件检查）")
    description: Optional[str] = Field("", description="任务的详细描述")
    task_prompt: str = Field(..., description=(
        "独立可执行的提示词，不依赖对话上下文。应包含完整的任务指令、所有必要信息（收件人、文件路径、操作步骤等）。"
        "如需专业领域能力，可在提示词中指示委派给子智能体。"
        "示例：「1. 使用 email_process 读取未读邮件（action=read, unseen_only=true） 2. 将邮件列表汇总为文本 3. 使用 email_process 发送汇总到 zhangsan@company.com（action=send）」"
    ))
    schedule_type: str = Field(..., description="调度类型：daily每天, weekly每周, monthly每月, interval间隔, once一次性")
    time_config: Dict[str, Any] = Field(..., description="时间配置，必须从用户话语中解析，所有时间为北京时间")


class ManageScheduledTaskInput(BaseModel):
    """管理定时任务参数"""
    action: str = Field(..., description="操作类型：list查看列表, pause暂停, resume恢复, cancel取消, view_logs查看执行日志")
    task_id: Optional[str] = Field(None, description="任务ID（list 操作不需要）")


def generate_cron_expression(schedule_type: str, time_config: dict) -> Optional[str]:
    """根据调度类型和时间配置生成 cron 表达式或执行时间"""
    if not time_config:
        return None

    if schedule_type == "daily":
        hour = time_config.get("hour", 9)
        minute = time_config.get("minute", 0)
        return f"{minute} {hour} * * *"
    elif schedule_type == "weekly":
        day_map = {"mon": "1", "tue": "2", "wed": "3", "thu": "4", "fri": "5", "sat": "6", "sun": "0"}
        day_of_week = day_map.get(time_config.get("day_of_week", "mon").lower(), "1")
        hour = time_config.get("hour", 9)
        minute = time_config.get("minute", 0)
        return f"{minute} {hour} * * {day_of_week}"
    elif schedule_type == "monthly":
        day = time_config.get("day", 1)
        hour = time_config.get("hour", 9)
        minute = time_config.get("minute", 0)
        return f"{minute} {hour} {day} * *"
    elif schedule_type == "once":
        return time_config.get("run_at", None)
    elif schedule_type == "interval":
        # interval 类型不需要 cron 表达式，用 interval_seconds
        return None
    return None


def format_schedule_description(schedule_type: str, time_config: dict) -> str:
    """生成调度描述文本"""
    if not time_config:
        return schedule_type
    if schedule_type == "daily":
        return f"每天 {time_config.get('hour', 9):02d}:{time_config.get('minute', 0):02d}"
    elif schedule_type == "weekly":
        day_names = {"mon": "周一", "tue": "周二", "wed": "周三", "thu": "周四", "fri": "周五", "sat": "周六", "sun": "周日"}
        day = day_names.get(time_config.get("day_of_week", "mon").lower(), "周一")
        return f"每{day} {time_config.get('hour', 9):02d}:{time_config.get('minute', 0):02d}"
    elif schedule_type == "monthly":
        return f"每月{time_config.get('day', 1)}日 {time_config.get('hour', 9):02d}:{time_config.get('minute', 0):02d}"
    elif schedule_type == "interval":
        return f"每隔 {time_config.get('interval_hours', 1)} 小时"
    elif schedule_type == "once":
        return f"一次性执行 {time_config.get('run_at', '未指定')}"
    return schedule_type


class CreateScheduledTaskTool(BaseTool):
    """创建定时任务工具"""

    name = "create_scheduled_task"
    assembly_order = 100  # 兼容历史 Agent 注册顺序：定时工具位于普通工具之后
    description = (
        "为用户创建定时执行的任务。当用户说「每天/每周/每月/定期/定时/每隔X小时」+ 某个操作时使用。"
        "创建前会先执行一次验证，只有验证通过才会创建定时任务。"
        "支持每天、每周、每月、间隔执行、一次性等模式。"
        "如果用户询问已创建的定时任务，使用 manage_scheduled_task 工具查看。"
        "【重要】必须从用户话语中解析出具体的调度时间（北京时间）。"
        "如果用户没有给出具体时间，必须先向用户确认后再调用，禁止自行猜测默认时间。"
    )
    usage_guide = """"""
    display_name = "创建定时任务"
    category = "scheduler"
    InputModel = CreateScheduledTaskInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        """执行创建定时任务"""
        from src.scheduler.executor import ScheduledTaskExecutor
        from src.scheduler.db import ScheduledTaskDB

        name = kwargs.get("name", "")
        task_prompt = kwargs.get("task_prompt", "")
        schedule_type = kwargs.get("schedule_type", "daily")
        time_config = kwargs.get("time_config") or {}
        description = kwargs.get("description", "")

        # LLM 解析时间时可能把数值字段以字符串传出（如 interval_hours="2"、hour="9"）。
        # time_config 是 Dict[str, Any]，executor 的 InputModel 不会递归强转内部值，
        # 且本工具由 agent 直接调用（绕过 executor），这里统一强转数值字段，
        # 防止字符串乘法（interval_hours）与 :02d 格式化崩溃（hour/minute/day）
        for key, default in (("interval_hours", 1), ("hour", 9), ("minute", 0), ("day", 1)):
            if key in time_config:
                try:
                    time_config[key] = int(time_config[key])
                except (TypeError, ValueError):
                    time_config[key] = default

        context = current_tool_execution_context()
        user_id = context.user_id if context else None
        session_id = context.session_id if context else None
        tenant_id = _resolve_runtime_tenant_id(context)

        # 必须有用户信息才能创建定时任务
        if not user_id:
            return {"success": False, "error": "用户未登录，无法创建定时任务", "debug": "user is None, cannot determine user_id"}
        if tenant_id is None:
            return {"success": False, "error": "租户上下文缺失，无法创建定时任务"}

        # 参数校验
        if not name or not task_prompt:
            return {"success": False, "error": "缺少必要参数（name 或 task_prompt）", "debug": "参数校验失败"}

        if not time_config:
            return {"success": False, "error": "缺少时间配置（time_config），请明确指定调度时间", "debug": "time_config is empty"}

        if len(task_prompt) > 2000:
            return {"success": False, "error": "task_prompt 长度超过限制（最大2000字符）", "debug": f"task_prompt 长度: {len(task_prompt)}"}

        # 检查用户最大任务数限制
        max_tasks = 20
        try:
            from src.config.settings import settings
            if hasattr(settings, 'scheduler') and hasattr(settings.scheduler, 'max_tasks_per_user'):
                max_tasks = settings.scheduler.max_tasks_per_user
        except Exception:
            pass

        current_count = await asyncio.to_thread(
            ScheduledTaskDB.count_by_user, user_id, "active", tenant_id=tenant_id
        )
        if current_count >= max_tasks:
            return {
                "success": False,
                "error": f"您的定时任务已达上限（{max_tasks}个），请先取消不需要的任务",
                "debug": f"当前活跃任务数: {current_count}, 最大限制: {max_tasks}"
            }

        # 生成 cron 表达式
        cron_expression = generate_cron_expression(schedule_type, time_config)
        interval_seconds = None
        if schedule_type == "interval":
            interval_seconds = time_config.get("interval_hours", 1) * 3600

        # 试执行（dry run）：执行身份租户与随后创建的任务行租户保持一致
        executor = ScheduledTaskExecutor()
        try:
            dry_run_result = await executor.dry_run(
                user_id=user_id,
                task_prompt=task_prompt,
                user_input=description or name,
                tenant_id=tenant_id,
            )
        except Exception as e:
            logger.opt(exception=True).error(f"后端日志：定时任务试执行异常 user_id={user_id}, error={e}")
            return {
                "success": False,
                "error": "任务验证过程出错，无法创建定时任务",
                "debug": str(e)
            }

        if not dry_run_result.get("success"):
            return {
                "success": False,
                "error": "任务验证失败，当前无法完成此类定时任务",
                "debug": dry_run_result.get("error", "未知错误")[:500]
            }

        # 试执行成功，创建定时任务
        # 创建时必须写入租户（''=公共用户/无租户上下文），否则新任务在租户视图不可见
        task = await asyncio.to_thread(
            ScheduledTaskDB.create,
            user_id=user_id,
            name=name,
            description=description,
            task_prompt=task_prompt,
            schedule_type=schedule_type,
            cron_expression=cron_expression,
            interval_seconds=interval_seconds,
            session_id=session_id,
            tenant_id=tenant_id,
        )

        if not task:
            return {"success": False, "error": "创建定时任务失败（数据库错误）", "debug": "ScheduledTaskDB.create 返回 None"}

        # 注册到调度器由 background runner 的 reconcile 对账负责（≤30s 内自动注册），
        # 本工具只写 DB，不做跨进程 manager 调用。

        schedule_description = format_schedule_description(schedule_type, time_config)
        dry_run_preview = (dry_run_result.get("result") or "")[:200]

        logger.info(f"后端日志：定时任务创建成功 task_id={task['task_id']}, user_id={user_id}, name={name}")

        return {
            "success": True,
            "task_id": task["task_id"],
            "name": name,
            "schedule_description": schedule_description,
            "next_run_at": task.get("next_run_at"),
            "dry_run_result_preview": dry_run_preview,
            "message": f"定时任务已创建！名称：{name}，调度：{schedule_description}。首次验证执行结果：{dry_run_preview}..."
        }


class ManageScheduledTaskTool(BaseTool):
    """管理定时任务工具"""

    name = "manage_scheduled_task"
    assembly_order = 100  # 兼容历史 Agent 注册顺序：定时工具位于普通工具之后
    description = "管理用户的定时任务：查看列表、暂停、恢复、取消、查看执行日志。"
    display_name = "管理定时任务"
    category = "scheduler"
    InputModel = ManageScheduledTaskInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        """执行管理定时任务"""
        from src.scheduler.db import ScheduledTaskDB, ScheduledTaskLogDB

        action = kwargs.get("action", "list")
        task_id = kwargs.get("task_id")
        context = current_tool_execution_context()
        user_id = context.user_id if context else None
        tenant_id = _resolve_runtime_tenant_id(context)

        if not user_id:
            return {"success": False, "error": "用户未登录，无法操作定时任务", "debug": "user is None, cannot determine user_id"}
        if tenant_id is None:
            return {"success": False, "error": "租户上下文缺失，无法操作定时任务"}

        try:
            if action == "list":
                # 与 API 列表一致的租户过滤：tenant_id='' 遗留行在具体租户视图不可见
                tasks = await asyncio.to_thread(
                    ScheduledTaskDB.list_by_user, user_id, tenant_id=tenant_id
                )
                if not tasks:
                    return {"success": True, "message": "您还没有创建任何定时任务"}

                task_list = []
                for t in tasks:
                    task_list.append(
                        f"- **{t['name']}** (ID: {t['task_id']})\n"
                        f"  状态: {t['status']} | 调度: {t['schedule_type']} | "
                        f"执行: {t['total_runs']}次 (成功{t['success_count']}, 失败{t['fail_count']})\n"
                        f"  创建时间: {t.get('created_at', '-')}"
                    )
                return {
                    "success": True,
                    "message": f"您共有 {len(tasks)} 个定时任务：\n\n" + "\n".join(task_list),
                    "tasks": tasks
                }

            elif action in {"pause", "resume", "cancel", "view_logs"}:
                if not task_id:
                    action_labels = {
                        "pause": "暂停", "resume": "恢复",
                        "cancel": "取消", "view_logs": "查看日志",
                    }
                    return {
                        "success": False,
                        "error": f"请指定要{action_labels[action]}的任务ID",
                    }

                # task_id 可由模型/用户提供，所有读写前必须用当前执行上下文的
                # user_id+tenant_id 校验所有权（条件进 SQL，越权行直接查不到），
                # 不能只依赖不可枚举的 ID 作为权限边界。
                task = await asyncio.to_thread(
                    ScheduledTaskDB.get_by_id,
                    task_id, tenant_id=tenant_id, user_id=user_id,
                )
                if not task or task.get("user_id") != user_id:
                    return {
                        "success": False,
                        "permission_denied": True,
                        "error": "任务不存在或无权操作",
                    }

                if action == "pause":
                    # 只写 DB，background reconcile ≤30s 内同步到调度器；
                    # 租户+用户条件进 UPDATE 本身，防止校验与写入之间的 TOCTOU
                    updated = await asyncio.to_thread(
                        ScheduledTaskDB.update_status,
                        task_id, "paused", tenant_id=tenant_id, user_id=user_id,
                    )
                    if updated:
                        return {"success": True, "message": f"任务 {task_id} 已暂停（≤30s 生效）"}
                    return {"success": False, "error": "暂停失败，任务可能不存在或已暂停"}

                if action == "resume":
                    updated = await asyncio.to_thread(
                        ScheduledTaskDB.update_status,
                        task_id, "active", tenant_id=tenant_id, user_id=user_id,
                    )
                    if updated:
                        return {"success": True, "message": f"任务 {task_id} 已恢复（≤30s 生效）"}
                    return {"success": False, "error": "恢复失败，任务可能不存在或未暂停"}

                if action == "cancel":
                    deleted = await asyncio.to_thread(
                        ScheduledTaskDB.delete,
                        task_id, tenant_id=tenant_id, user_id=user_id,
                    )
                    if deleted:
                        return {"success": True, "message": f"任务 {task_id} 已取消"}
                    return {"success": False, "error": "取消失败，任务可能不存在"}

                # view_logs：日志查询同样带租户+用户条件，与任务校验双层隔离
                logs = await asyncio.to_thread(
                    ScheduledTaskLogDB.list_by_task,
                    task_id, 20, tenant_id=tenant_id, user_id=user_id,
                )
                if not logs:
                    return {"success": True, "message": f"任务 {task_id} 暂无执行日志"}
                log_list = []
                for log in logs:
                    status_icon = "✅" if log["status"] == "success" else "❌"
                    log_list.append(
                        f"{status_icon} [{log['started_at']}] {log['status']} | "
                        f"耗时: {log.get('duration_ms', 0)}ms | "
                        f"触发: {log.get('trigger_type', '-')}"
                        + (f" | 错误: {log.get('error_message', '')[:100]}" if log.get("error_message") else "")
                    )
                return {
                    "success": True,
                    "message": f"任务 {task_id} 最近 {len(logs)} 条执行日志：\n\n" + "\n".join(log_list)
                }

            else:
                return {"success": False, "error": f"不支持的操作: {action}"}

        except Exception as e:
            logger.opt(exception=True).error(f"后端日志：管理定时任务失败 action={action}, error={e}")
            return {"success": False, "error": "操作失败"}
