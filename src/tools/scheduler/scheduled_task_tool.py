"""
定时任务工具

提供创建和管理定时任务的能力，包含两个工具：
- CreateScheduledTaskTool: 创建定时执行的任务（含 dry-run 验证）
- ManageScheduledTaskTool: 管理已创建的定时任务（列表、暂停、恢复、取消、查看日志）
"""

from typing import Any, Callable, Dict, Optional

from loguru import logger

from src.tools.base import BaseTool


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
    description = (
        "为用户创建定时执行的任务。创建前会先执行一次验证，只有验证通过才会创建定时任务。"
        "支持每天、每周、每月、间隔执行、一次性等模式。"
        "task_prompt 必须是不依赖对话上下文的独立可执行提示词，"
        "如果任务需要专业领域能力，可在 task_prompt 中指示委派给子智能体。"
        "【重要】必须从用户话语中解析出具体的调度时间。所有时间默认为北京时间(Asia/Shanghai)。"
        "如果用户没有给出具体时间，必须先向用户确认时间后再调用此工具，禁止自行猜测默认时间。"
    )
    category = "scheduler"
    parameters_schema = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "任务名称，简短描述（如：每日邮件检查）"
            },
            "description": {
                "type": "string",
                "description": "任务的详细描述"
            },
            "task_prompt": {
                "type": "string",
                "description": (
                    "独立可执行的提示词，不依赖对话上下文。应包含完整的任务指令、所有必要信息。"
                    "例如：'检查邮箱中未读邮件，如有未读邮件，汇总邮件列表并发送到 user@company.com'"
                )
            },
            "schedule_type": {
                "type": "string",
                "enum": ["daily", "weekly", "monthly", "interval", "once"],
                "description": "调度类型：daily每天, weekly每周, monthly每月, interval间隔, once一次性"
            },
            "time_config": {
                "type": "object",
                "description": (
                    "【必填】时间配置，必须从用户话语中解析，所有时间为北京时间。"
                    "daily: {\"hour\": 14, \"minute\": 0} 表示每天14:00；"
                    "weekly: {\"day_of_week\": \"mon\", \"hour\": 9, \"minute\": 0} 表示每周一09:00，"
                    "day_of_week 取值: mon/tue/wed/thu/fri/sat/sun；"
                    "monthly: {\"day\": 1, \"hour\": 9, \"minute\": 0} 表示每月1日09:00；"
                    "interval: {\"interval_hours\": 2} 表示每2小时执行一次；"
                    "once: {\"run_at\": \"2026-04-01 09:00:00\"} 表示一次性在指定时间执行。"
                    "用户说'下午两点'对应 {\"hour\": 14, \"minute\": 0}，'上午九点半'对应 {\"hour\": 9, \"minute\": 30}。"
                    "注意：不允许使用默认时间，必须明确解析用户意图。"
                )
            }
        },
        "required": ["name", "description", "task_prompt", "schedule_type", "time_config"]
    }

    def __init__(self):
        """初始化工具，额外上下文（user, session_id, send_progress）通过 set_context 注入"""
        self._user = None
        self._session_id = None
        self._send_progress: Optional[Callable] = None

    def set_context(self, user, session_id: str, send_progress: Optional[Callable] = None):
        """注入运行时上下文（每次调用前由 agent 设置）"""
        self._user = user
        self._session_id = session_id
        self._send_progress = send_progress

    async def execute(self, **kwargs) -> Dict[str, Any]:
        """执行创建定时任务"""
        from src.scheduler.executor import ScheduledTaskExecutor
        from src.scheduler.manager import scheduled_task_manager
        from src.scheduler.db import ScheduledTaskDB

        name = kwargs.get("name", "")
        task_prompt = kwargs.get("task_prompt", "")
        schedule_type = kwargs.get("schedule_type", "daily")
        time_config = kwargs.get("time_config") or {}
        description = kwargs.get("description", "")

        user_id = self._user.user_id if self._user else None

        # 必须有用户信息才能创建定时任务
        if not user_id:
            return {"success": False, "error": "用户未登录，无法创建定时任务", "debug": "user is None, cannot determine user_id"}

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

        current_count = ScheduledTaskDB.count_by_user(user_id, "active")
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

        # 试执行（dry run）
        if self._send_progress:
            await self._send_progress("⏳ 正在验证任务是否可以执行...")
        executor = ScheduledTaskExecutor()
        try:
            dry_run_result = await executor.dry_run(
                user_id=user_id,
                task_prompt=task_prompt,
                user_input=description or name,
            )
        except Exception as e:
            logger.error(f"后端日志：定时任务试执行异常 user_id={user_id}, error={e}", exc_info=True)
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
        if self._send_progress:
            await self._send_progress("✅ 验证通过，正在创建定时任务...")

        task = ScheduledTaskDB.create(
            user_id=user_id,
            name=name,
            description=description,
            task_prompt=task_prompt,
            schedule_type=schedule_type,
            cron_expression=cron_expression,
            interval_seconds=interval_seconds,
            session_id=self._session_id,
        )

        if not task:
            return {"success": False, "error": "创建定时任务失败（数据库错误）", "debug": "ScheduledTaskDB.create 返回 None"}

        # 注册到调度器
        try:
            scheduled_task_manager.register_task(task)
        except Exception as e:
            logger.error(f"后端日志：注册定时任务到调度器失败 task_id={task['task_id']}, {e}", exc_info=True)
            pass

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
    description = "管理用户的定时任务：查看列表、暂停、恢复、取消、查看执行日志。"
    category = "scheduler"
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "pause", "resume", "cancel", "view_logs"],
                "description": "操作类型：list查看列表, pause暂停, resume恢复, cancel取消, view_logs查看执行日志"
            },
            "task_id": {
                "type": "string",
                "description": "任务ID（list 操作不需要）"
            }
        },
        "required": ["action"]
    }

    def __init__(self):
        """初始化工具，额外上下文（user）通过 set_context 注入"""
        self._user = None

    def set_context(self, user):
        """注入运行时上下文（每次调用前由 agent 设置）"""
        self._user = user

    async def execute(self, **kwargs) -> Dict[str, Any]:
        """执行管理定时任务"""
        from src.scheduler.manager import scheduled_task_manager
        from src.scheduler.db import ScheduledTaskDB, ScheduledTaskLogDB

        action = kwargs.get("action", "list")
        task_id = kwargs.get("task_id")
        user_id = self._user.user_id if self._user else None

        if not user_id:
            return {"success": False, "error": "用户未登录，无法操作定时任务", "debug": "user is None, cannot determine user_id"}

        try:
            if action == "list":
                tasks = ScheduledTaskDB.list_by_user(user_id)
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

            elif action == "pause":
                if not task_id:
                    return {"success": False, "error": "请指定要暂停的任务ID"}
                if scheduled_task_manager.pause_task(task_id):
                    return {"success": True, "message": f"任务 {task_id} 已暂停"}
                return {"success": False, "error": f"暂停失败，任务可能不存在或已暂停"}

            elif action == "resume":
                if not task_id:
                    return {"success": False, "error": "请指定要恢复的任务ID"}
                if scheduled_task_manager.resume_task(task_id):
                    return {"success": True, "message": f"任务 {task_id} 已恢复"}
                return {"success": False, "error": f"恢复失败，任务可能不存在或未暂停"}

            elif action == "cancel":
                if not task_id:
                    return {"success": False, "error": "请指定要取消的任务ID"}
                scheduled_task_manager.remove_task(task_id)
                if ScheduledTaskDB.delete(task_id):
                    return {"success": True, "message": f"任务 {task_id} 已取消"}
                return {"success": False, "error": f"取消失败，任务可能不存在"}

            elif action == "view_logs":
                if not task_id:
                    return {"success": False, "error": "请指定要查看日志的任务ID"}
                logs = ScheduledTaskLogDB.list_by_task(task_id, limit=20)
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
            logger.error(f"后端日志：管理定时任务失败 action={action}, error={e}", exc_info=True)
            return {"success": False, "error": "操作失败", "debug": str(e)}
