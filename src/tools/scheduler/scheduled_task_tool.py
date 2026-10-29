"""定时任务工具（v2 Runner 化重建，设计 §3/§10，Phase 4）

两个 chat_only 工具（仅 web 对话可见，渠道与 scheduled 执行滤除）：
- CreateScheduledTaskTool：创建自包含定时任务（无试跑、无同步等待 Runner，
  创建即 active，首次执行在下一个调度点；由 background_runner 的 dispatch
  循环认领并提交）；
- ManageScheduledTaskTool：list/pause/resume/cancel/view/set_notify_target，
  适配新状态机（pause 仅 active、resume 仅 paused、cancel 走事务守卫硬删）。

本模块同时承载工具与 REST API 共用的纯校验/计算函数（generate_cron_expression /
build_schedule_fields / compute_resume_due / format_schedule_description）。
"""

import asyncio
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger
from pydantic import BaseModel, Field

from src.scheduler.db import (ScheduledTaskBusyError, ScheduledTaskQuotaError,
                             ScheduledTaskDB)
from src.tools.base import BaseTool
from src.tools.context import ToolExecutionContext, current_tool_execution_context

# ==================== 共享领域函数（scheduler.schedule） ====================
# 评审修正：共享调度纯函数统一放 scheduler.schedule，工具与 API 共用，
# 避免 API 反向依赖工具实现。本模块只保留工具侧行为编排。

from src.scheduler.schedule import (  # noqa: F401（部分名称供既有测试/调用方直接导入）
    ScheduleConfigError,
    build_schedule_fields,
    compute_resume_due,
    format_schedule_description,
    generate_cron_expression,
    parse_run_at,
    validate_task_attachments,
)


def _resolve_runtime_tenant_id(context) -> "str|None":
    """解析工具调用的可信租户；未知身份绝不降级成公共租户（fail-closed）"""
    return context.tenant_id if context else None


# 任务行中不进对话记录的调度侧内部字段（与 API _INTERNAL_FIELDS 同口径）
_INTERNAL_FIELDS = ("pending_snapshot",)


def _public_task(task):
    """任务行对外投影：剥离调度侧内部交接字段（chat 记录不落库内部快照）"""
    return {key: value for key, value in task.items() if key not in _INTERNAL_FIELDS}


# ==================== R56 路由约束：微信营销定时任务引导专用工具 ====================
# 设计原则是保守拦截：只有「微信营销域 + 调度词 + 发送动作」三类关键词同时命中，
# 且没有明确指向其他渠道（邮件/短信/钉钉/飞书/企业微信）的词时才拒绝创建；
# 任何不确定情况（如仅提"微信"、或发送目标是邮箱）一律放行走通用定时任务。
# （沿用 R56 定稿口径，与旧实现一致）

_WEIXIN_MARKETING_DOMAIN_MARKERS = (
    "微信群发", "微信营销", "微信推送", "微信自动发送", "weixin_automation",
)
_WEIXIN_SCHEDULE_MARKERS = (
    "定时", "每天", "每日", "每周", "每月", "定期", "自动", "每隔",
)
_WEIXIN_SEND_MARKERS = (
    "群发", "发送", "推送", "发消息", "发到群", "发群", "发到微信群",
)
_NON_WEIXIN_CHANNEL_MARKERS = (
    # 明确指向其他渠道的发送意图 → 不拦截（保守放行）
    # "企微" 单列：防 "企微信群发" 因含子串 "微信群发" 被误拦（简写不含 "企业微信" 全称）
    "邮件", "邮箱", "email", "短信", "sms", "钉钉", "飞书", "企业微信", "企微",
)


def _is_definite_weixin_marketing_task(*texts: str) -> bool:
    """判定任务描述是否确定为微信营销定时发送任务（保守：不确定返回 False）"""
    text = " ".join(t for t in texts if t)
    if not text:
        return False
    has_domain = any(m in text for m in _WEIXIN_MARKETING_DOMAIN_MARKERS)
    has_schedule = any(m in text for m in _WEIXIN_SCHEDULE_MARKERS)
    has_send = any(m in text for m in _WEIXIN_SEND_MARKERS)
    has_other_channel = any(m in text for m in _NON_WEIXIN_CHANNEL_MARKERS)
    return has_domain and has_schedule and has_send and not has_other_channel


def _weixin_routing_rejection() -> Dict[str, Any]:
    """确定的微信营销定时任务 → 结构化引导到专用工具（不创建）"""
    return {
        "success": False,
        "error": (
            "微信营销定时群发任务不适用通用定时任务，请改用专用工具创建"
            "（weixin_automation_prepare 准备草稿 → 用户确认 → weixin_automation_publish 发布）"
        ),
        "code": "USE_DEDICATED_WEIXIN_TOOL",
        "routed_tool": "weixin_automation_prepare",
        "guidance": (
            "微信营销任务请使用 weixin_automation_prepare 创建草稿"
            "（无发送副作用），经用户明确确认后用 weixin_automation_publish 发布；"
            "运行/试发/重试/暂停等用 weixin_automation_manage。"
        ),
    }


# 上限为领域函数（schedule.py），API 与工具共用同一配置口径
from src.scheduler.schedule import max_tasks_per_user as _max_tasks_per_user


# ==================== 工具一：创建 ====================

class CreateScheduledTaskInput(BaseModel):
    """创建定时任务参数"""
    name: str = Field(..., description="任务名称，简短描述（如：每日邮件检查）")
    task_prompt: str = Field(..., description=(
        "自包含的任务定义（执行提示词），不依赖创建时的对话上下文。"
        "必须包含完整任务指令、所有必要参数（收件人/路径/关键词等）、资料来源与输出要求；"
        "文件等资料用 file_id 等受权引用固化；实时数据约定执行时获取；"
        "可预见的无数据/空结果场景写明默认行为约定（如：无新增时输出「今日无新增数据」）。"
        "如需专业领域能力，可在提示词中指示委派给对应子智能体。"
    ))
    schedule_type: str = Field(..., description="调度类型：daily每天, weekly每周, monthly每月, interval间隔, once一次性")
    time_config: Dict[str, Any] = Field(..., description=(
        "时间配置，必须从用户话语中解析，所有时间为北京时间。"
        "once 类型必填 run_at（格式 YYYY-MM-DD HH:MM:SS，必须是未来时间）；"
        "daily/weekly/monthly 必填 hour/minute（weekly 另需 day_of_week，monthly 另需 day）；"
        "interval 用 interval_hours"
    ))
    attachments: Optional[List[Dict[str, Any]]] = Field(None, description=(
        "受权资源引用（可选，数组）：任务依赖的文件等资料，每项含 "
        "file_id/name/type(file|image)；随每次执行提交，由执行侧在属主存储"
        "范围内解析。不要把附件信息塞进提示词文字来代替本字段"
    ))
    description: Optional[str] = Field("", description="任务的补充描述（可选）")
    notify_target_id: Optional[str] = Field(None, description=(
        "通知目标 ID（可选）：任务完成/失败/暂停通知投递到指定目标"
        "（个人 Web Push 或租户级群机器人/邮件目标）；不填=站内信+个人偏好渠道"
    ))


class CreateScheduledTaskTool(BaseTool):
    """创建定时任务工具"""

    name = "create_scheduled_task"
    assembly_order = 100  # 兼容历史 Agent 注册顺序：定时工具位于普通工具之后
    chat_only = True  # 仅 web 对话可见（渠道与 scheduled 执行滤除，设计 §10）
    description = (
        "为用户创建定时执行的任务。当用户说「每天/每周/每月/定期/定时/每隔X小时/某个时间点」"
        "+ 某个操作时使用。支持每天、每周、每月、间隔执行、一次性等模式。"
        "【重要】必须从用户话语中解析出具体的调度时间（北京时间）；"
        "如果用户没有给出具体时间（如只说'每天提醒我看邮件'没说几点），"
        "必须先向用户确认后再调用，禁止自行猜测默认时间。"
        "【重要】task_prompt 必须自包含、可独立执行：写入完整指令、必要参数、资料来源与输出要求；"
        "禁止依赖对话隐含上下文（如'上面那个文件''按刚才说的办'）——"
        "文件用 file_id 等受权引用写入任务；实时数据（邮件/订单等）约定执行时获取；"
        "可预见的无数据/空结果场景写明默认行为（如'无新增数据时输出：今日无新增数据'）。"
        "如需专业领域能力，在 task_prompt 中指示委派给对应子智能体。"
        "一次性任务（schedule_type=once）必须在 time_config.run_at 提供未来的具体时间，"
        "过去时间会被拒绝。可选 notify_target_id 指定完成通知投递目标。"
        "创建即生效，首次执行在下一个调度点（无试跑）。"
        "如果用户询问或管理已创建的定时任务，使用 manage_scheduled_task 工具。"
        "【限制】微信营销/微信群发类定时发送任务不适用本工具（会被拒绝），"
        "请使用 weixin_automation_prepare / weixin_automation_publish 专用工具。"
    )
    usage_guide = """"""
    display_name = "创建定时任务"
    category = "scheduler"
    InputModel = CreateScheduledTaskInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        """创建定时任务：只写 DB（含专属任务会话），不试跑、不同步等待 Runner。"""
        name = kwargs.get("name", "")
        task_prompt = kwargs.get("task_prompt", "")
        schedule_type = kwargs.get("schedule_type", "")
        time_config = dict(kwargs.get("time_config") or {})
        description = kwargs.get("description", "")
        notify_target_id = kwargs.get("notify_target_id") or None

        context = current_tool_execution_context()
        user_id = context.user_id if context else None
        session_id = context.session_id if context else None
        tenant_id = _resolve_runtime_tenant_id(context)

        # 身份三要素缺一不创建（任务创建必须发生在真实登录会话中）
        if not user_id:
            logger.warning("后端日志：创建定时任务失败，工具执行上下文缺少 user_id")
            return {"success": False, "error": "用户未登录，无法创建定时任务"}
        if tenant_id is None:
            return {"success": False, "error": "租户上下文缺失，无法创建定时任务"}
        if not session_id:
            return {"success": False, "error": "会话上下文缺失，无法创建定时任务"}
        if not name or not task_prompt:
            return {"success": False, "error": "缺少必要参数（name 或 task_prompt）"}

        # R56 路由约束：确定为微信营销定时发送任务时拒绝并引导专用工具
        # （保守拦截，在任何 DB 写入之前）
        if _is_definite_weixin_marketing_task(name, description, task_prompt):
            return _weixin_routing_rejection()

        # 调度字段校验与计算（schedule_type 枚举 / 时间必填不猜测 / once 未来时间
        # 与归一化 / 数值字段容错，全部收敛在 build_schedule_fields）
        try:
            cron_expression, interval_seconds, run_at, next_due_at = build_schedule_fields(
                schedule_type, time_config)
        except ScheduleConfigError as e:
            logger.warning(f"后端日志：创建定时任务调度校验失败 user_id={user_id}, error={e}")
            return {"success": False, "error": str(e)}

        # 附件固化（评审 P1：受权资源引用必须在创建时落任务行，随每次执行
        # 提交——写进提示词文字不能替代 Runner 的附件解析链路）
        try:
            attachments = validate_task_attachments(kwargs.get("attachments"))
        except ScheduleConfigError as e:
            return {"success": False, "error": str(e)}

        # 通知目标归属校验（设计 §3.3/§8.3 同口径）：只允许本人个人目标或
        # 本租户租户级目标——外发按 target_id 快照路由，写入口径必须校验
        # 归属（LLM 幻觉/他租户 ID 一律拒绝，不落库待投递侧失败）
        if notify_target_id and not await asyncio.to_thread(
                ScheduledTaskDB.notify_target_visible, notify_target_id,
                tenant_id=tenant_id, user_id=user_id):
            logger.warning(f"后端日志：定时任务通知目标不可用 user_id={user_id}, "
                           f"target={notify_target_id[:32]}")
            return {"success": False,
                    "error": "通知目标不存在或不可用；"
                             "可先用 manage_scheduled_task 的 list_notify_targets 查询可选目标"}

        # 每用户 active 上限：事务内落实（评审 P2：先计数后插入的两独立
        # 事务存在并发绕过窗口——advisory lock 串行化，配额检查与插入同事务）
        max_tasks = _max_tasks_per_user()

        # 智能体上下文继承（设计 §3.2）：创建会话的 profile/instance 固化到任务行，
        # 后续执行由该智能体自身执行；主智能体 profile_id='main'
        profile_id = (getattr(context, "profile_id", None) or "main")
        instance_id = getattr(context, "instance_id", None)
        subagent_id = profile_id if profile_id != "main" else None

        try:
            task = await asyncio.to_thread(
                ScheduledTaskDB.create,
                user_id=user_id, tenant_id=tenant_id, name=name,
                task_prompt=task_prompt, schedule_type=schedule_type,
                cron_expression=cron_expression, interval_seconds=interval_seconds,
                run_at=run_at, attachments=attachments,
                profile_id=profile_id, instance_id=instance_id,
                subagent_id=subagent_id, notification_target_id=notify_target_id,
                next_due_at=next_due_at, max_active=max_tasks)
        except ScheduledTaskQuotaError as e:
            return {"success": False, "error": str(e)}
        except Exception:
            # 诊断细节走日志；凭据类内容不进 tool_result
            logger.opt(exception=True).error(
                f"后端日志：定时任务落库失败 user_id={user_id}, name={name}")
            return {"success": False, "error": "创建定时任务失败（数据库错误）"}
        if not task:
            return {"success": False, "error": "创建定时任务失败（数据库错误）"}

        schedule_description = format_schedule_description(schedule_type, time_config)
        next_run_at = task.get("next_due_at") or next_due_at
        message = (f"定时任务已创建！名称：{name}，调度：{schedule_description}。"
                   f"创建即生效，首次执行在下一个调度点。")
        logger.info(f"后端日志：定时任务创建成功 task_id={task['task_id']}, "
                    f"user_id={user_id}, name={name}, next_due_at={next_run_at}")
        return {
            "success": True,
            "task_id": task["task_id"],
            "name": name,
            "schedule_description": schedule_description,
            "next_run_at": str(next_run_at) if next_run_at else None,
            "message": message,
        }


# ==================== 工具二：管理 ====================

class ManageScheduledTaskInput(BaseModel):
    """管理定时任务参数"""
    action: str = Field(..., description=(
        "操作类型：list查看任务列表, pause暂停, resume恢复, cancel取消并删除, "
        "view查看任务统计详情, set_notify_target设置通知目标, "
        "list_notify_targets查询可选通知目标（创建任务选 notify_target_id 前先查）"
    ))
    task_id: Optional[str] = Field(None, description="任务ID（list 操作不需要）")
    notify_target_id: Optional[str] = Field(None, description=(
        "通知目标 ID（仅 set_notify_target 使用）：传入=设置该目标；"
        "不传=清除恢复默认（站内信+个人偏好渠道）"
    ))


class ManageScheduledTaskTool(BaseTool):
    """管理定时任务工具"""

    name = "manage_scheduled_task"
    assembly_order = 100  # 兼容历史 Agent 注册顺序：定时工具位于普通工具之后
    chat_only = True  # 仅 web 对话可见（渠道与 scheduled 执行滤除，设计 §10）
    description = (
        "管理用户的定时任务：查看列表（list）、暂停（pause）、恢复（resume）、"
        "取消并删除（cancel）、查看任务统计（view）、设置/清除通知目标（set_notify_target）、"
        "查询可选通知目标（list_notify_targets——创建任务选 notify_target_id 前先查询，"
        "返回 target_id/渠道/展示名）。历次执行明细请在任务视图中查看（view 结果中附有指引）。"
    )
    display_name = "管理定时任务"
    category = "scheduler"
    InputModel = ManageScheduledTaskInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        """管理定时任务（所有读写带 tenant+user 归属条件进 SQL）"""
        action = kwargs.get("action", "list")
        task_id = kwargs.get("task_id")
        notify_target_id = kwargs.get("notify_target_id") or None

        context = current_tool_execution_context()
        user_id = context.user_id if context else None
        tenant_id = _resolve_runtime_tenant_id(context)
        if not user_id:
            logger.warning("后端日志：管理定时任务失败，工具执行上下文缺少 user_id")
            return {"success": False, "error": "用户未登录，无法操作定时任务"}
        if tenant_id is None:
            return {"success": False, "error": "租户上下文缺失，无法操作定时任务"}

        try:
            if action == "list":
                return await self._list(user_id, tenant_id)
            if action == "list_notify_targets":
                return await self._list_notify_targets(tenant_id, user_id)
            if action in {"pause", "resume", "cancel", "view", "set_notify_target"}:
                if not task_id:
                    labels = {"pause": "暂停", "resume": "恢复", "cancel": "取消",
                              "view": "查看", "set_notify_target": "设置通知目标"}
                    return {"success": False,
                            "error": f"请指定要{labels[action]}的任务ID（可用 list 查询）"}
                # task_id 可由模型/用户提供：所有读写先用当前执行上下文的
                # user_id+tenant_id 校验归属（条件进 SQL，越权行直接查不到）
                task = await asyncio.to_thread(
                    ScheduledTaskDB.get_by_id, task_id,
                    tenant_id=tenant_id, user_id=user_id)
                if not task:
                    return {"success": False, "permission_denied": True,
                            "error": "任务不存在或无权操作"}
                if action == "pause":
                    return await self._pause(task, tenant_id, user_id)
                if action == "resume":
                    return await self._resume(task, tenant_id, user_id)
                if action == "cancel":
                    return await self._cancel(task, tenant_id, user_id)
                if action == "set_notify_target":
                    return await self._set_notify_target(
                        task, tenant_id, user_id, notify_target_id)
                return self._view(task)
            return {"success": False, "error": f"不支持的操作: {action}"}
        except ScheduledTaskBusyError:
            return {"success": False, "error": "任务正在执行，请稍后再试"}
        except ScheduleConfigError as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            logger.opt(exception=True).error(
                f"后端日志：管理定时任务失败 action={action}, task_id={task_id}, error={e}")
            return {"success": False, "error": "操作失败"}

    # ==================== 各操作 ====================

    @staticmethod
    async def _list(user_id: str, tenant_id: str) -> Dict[str, Any]:
        """任务列表摘要：名称/状态/调度/统计/下次执行"""
        tasks = await asyncio.to_thread(
            ScheduledTaskDB.list_by_user, user_id, tenant_id=tenant_id)
        if not tasks:
            return {"success": True, "message": "您还没有创建任何定时任务", "tasks": []}
        lines = []
        for t in tasks:
            lines.append(
                f"- **{t['name']}** (ID: {t['task_id']})\n"
                f"  状态: {t['status']}" +
                (f"（{t['pause_reason']}）" if t.get("pause_reason") else "") +
                f" | 调度: {t['schedule_type']}"
                f" | 执行: {t.get('total_runs', 0)}次"
                f"（成功{t.get('success_count', 0)}, 失败{t.get('fail_count', 0)}）"
                f" | 下次执行: {t.get('next_due_at') or '-'}"
            )
        return {
            "success": True,
            "message": f"您共有 {len(tasks)} 个定时任务：\n\n" + "\n".join(lines),
            "tasks": [_public_task(t) for t in tasks],
        }

    @staticmethod
    async def _list_notify_targets(tenant_id: str, user_id: str) -> Dict[str, Any]:
        """可选通知目标列表（评审 P2：创建对话内完成目标选择的数据来源）"""
        targets = await asyncio.to_thread(
            ScheduledTaskDB.list_visible_notify_targets, tenant_id, user_id)
        if not targets:
            return {"success": True, "targets": [],
                    "message": "暂无可用通知目标：个人目标可在通知设置中绑定浏览器推送，"
                               "租户级目标（群机器人/邮件）由租户管理员配置"}
        lines = [f"- {t['target_id']}（{t['channel']}/{t['provider']}"
                 + (f"，{t['configuration_display']}" if t.get("configuration_display") else "")
                 + "）" for t in targets]
        return {"success": True, "targets": targets,
                "message": ("可选通知目标（创建/设置任务通知时传 target_id）："
                            + chr(10) + chr(10).join(lines))}

    @staticmethod
    async def _pause(task: Dict[str, Any], tenant_id: str, user_id: str) -> Dict[str, Any]:
        """暂停任务：仅 active 可暂停（条件进 UPDATE），清空 pending 槽"""
        updated = await asyncio.to_thread(
            ScheduledTaskDB.pause, task["task_id"],
            tenant_id=tenant_id, user_id=user_id)
        if updated:
            return {"success": True,
                    "message": f"任务 {task['task_id']}（{task['name']}）已暂停"}
        return {"success": False,
                "error": f"暂停失败：任务当前状态为 {task['status']}，仅执行中（active）的任务可暂停"}

    @staticmethod
    async def _resume(task: Dict[str, Any], tenant_id: str, user_id: str) -> Dict[str, Any]:
        """恢复任务：统一走 resume_recompute（单事务锁行重算 + once 过宽限
        统一转 expired + 配额事务内检查 + 恢复回合重置，评审 P1/P2 修正）"""
        try:
            updated = await asyncio.to_thread(
                ScheduledTaskDB.resume_recompute, task["task_id"],
                max_active=_max_tasks_per_user(),
                tenant_id=tenant_id, user_id=user_id)
        except ScheduledTaskQuotaError as e:
            return {"success": False, "error": str(e)}
        except ValueError as e:
            return {"success": False, "error": str(e)}
        if not updated:
            return {"success": False,
                    "error": f"恢复失败：任务当前状态为 {task['status']}，仅已暂停（paused）的任务可恢复"}
        if updated["status"] == "expired":
            # once 过宽限的统一转换与回执（与 API 同一路径，不再分叉）
            return {"success": True, "expired": True,
                    "message": f"任务 {task['task_id']}（{task['name']}）的一次性执行时间已超过宽限，"
                               "恢复操作已将其作废（expired）；如仍需执行请重新创建或改期"}
        message = (f"任务 {task['task_id']}（{task['name']}）已恢复，"
                   f"下次执行: {updated.get('next_due_at') or '-'}")
        if task.get("pause_reason") == "interrupted":
            message += "；已开启新一轮自动恢复尝试（此前恢复失败的中断执行）"
        return {"success": True, "message": message}

    @staticmethod
    async def _cancel(task: Dict[str, Any], tenant_id: str, user_id: str) -> Dict[str, Any]:
        """取消任务 = 硬删除（任务+任务会话+消息，事务守卫：非终态 runner 存在时 409）"""
        deleted = await asyncio.to_thread(
            ScheduledTaskDB.delete_with_session, task["task_id"],
            tenant_id=tenant_id, user_id=user_id)
        if not deleted:
            # 并发删除（任务已在别处被删除）：如实提示，不谎报成功
            return {"success": False, "error": "任务不存在或已被删除"}
        return {"success": True,
                "message": f"任务 {task['task_id']}（{task['name']}）已取消并删除（含任务会话）"}

    @staticmethod
    async def _set_notify_target(task: Dict[str, Any], tenant_id: str, user_id: str,
                                 notify_target_id: Optional[str]) -> Dict[str, Any]:
        """设置/清除通知目标（F4 清空语义：不传 notify_target_id = 清除恢复默认）"""
        if notify_target_id:
            if not await asyncio.to_thread(
                    ScheduledTaskDB.notify_target_visible, notify_target_id,
                    tenant_id=tenant_id, user_id=user_id):
                return {"success": False,
                        "error": "通知目标不存在或不可用；可选目标见任务视图的通知目标选择器"}
            updated = await asyncio.to_thread(
                ScheduledTaskDB.update_definition, task["task_id"],
                notification_target_id=notify_target_id,
                tenant_id=tenant_id, user_id=user_id)
            if updated:
                return {"success": True,
                        "message": f"任务 {task['name']} 的通知目标已设置为 {notify_target_id}"}
        else:
            await asyncio.to_thread(
                ScheduledTaskDB.update_definition, task["task_id"],
                clear_fields=["notification_target_id"],
                tenant_id=tenant_id, user_id=user_id)
            return {"success": True,
                    "message": f"任务 {task['name']} 的通知目标已清除，恢复默认（站内信+个人偏好渠道）"}
        return {"success": False, "error": "设置通知目标失败"}

    @staticmethod
    def _view(task: Dict[str, Any]) -> Dict[str, Any]:
        """任务统计详情 + 任务视图指引（历次执行结果不在本工具读取）"""
        return {
            "success": True,
            "message": (
                f"任务 {task['name']}（{task['task_id']}）统计：\n"
                f"- 状态: {task['status']}" +
                (f"（暂停原因: {task['pause_reason']}）" if task.get("pause_reason") else "") + "\n"
                f"- 调度类型: {task['schedule_type']}"
                f" | 下次执行: {task.get('next_due_at') or '-'}\n"
                f"- 累计执行: {task.get('total_runs', 0)} 次"
                f"（成功 {task.get('success_count', 0)}，失败 {task.get('fail_count', 0)}）\n"
                f"- 最近执行: {task.get('last_run_at') or '-'}"
                f"（结果: {task.get('last_status') or '-'}）\n"
                "如需查看历次执行的结果明细、触发原因与费用，请打开任务计划中的任务视图。"
            ),
            "task": _public_task(task),
        }
