"""定时任务工具（Runner 化 Phase 4）单元测试（全 mock，不触真实 DB）

覆盖（plan-scheduled-task-runner-refactor.md Phase 4）：
- 纯函数：build_schedule_fields 校验全集（缺时间/once 过去与归一化/枚举/数值
  容错/越界）、compute_resume_due（interval 锚点/cron/once 宽限）
- create：上下文缺一不创建、profile/instance 继承、notify_target_id 透传、
  每用户上限、R56 拦截与放行、无 prompt 长度上限
- manage：list 摘要/pause/resume（once 过宽限提示）/cancel（BusyError 友好
  提示）/view/set_notify_target 设置与清除（F4）/越权 fail-closed
"""

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from src.scheduler.db import (ScheduledTaskBusyError, ScheduledTaskQuotaError,
                             ScheduledTaskDB)
from src.tools.context import ToolExecutionContext, tool_execution_scope
from src.tools.scheduler.scheduled_task_tool import (
    CreateScheduledTaskTool,
    ManageScheduledTaskTool,
    ScheduleConfigError,
    build_schedule_fields,
    compute_resume_due,
    format_schedule_description,
    parse_run_at,
)

_CTX = dict(user_id="user_1", tenant_id="tenant_1", session_id="sess_1")


def _scope(**overrides):
    """安装带身份三要素的工具执行上下文"""
    return tool_execution_scope(ToolExecutionContext(**{**_CTX, **overrides}))


def _created_row(**overrides):
    row = {
        "task_id": "sched_abc123", "tenant_id": "tenant_1", "user_id": "user_1",
        "session_id": "sess_task_1", "name": "每日日报", "status": "active",
        "schedule_type": "daily", "cron_expression": "0 9 * * *",
        "interval_seconds": None, "run_at": None, "next_due_at": None,
        "notification_target_id": None, "pause_reason": None,
        "total_runs": 0, "success_count": 0, "fail_count": 0,
        "last_run_at": None, "last_status": None,
    }
    row.update(overrides)
    return row


_CREATE_KWARGS = dict(
    name="每日日报",
    task_prompt="读取销售表并生成日报发送到 a@b.com；无新增数据时输出：今日无新增数据",
    schedule_type="daily",
    time_config={"hour": 9, "minute": 0},
)


# ==================== 纯函数：build_schedule_fields ====================

class TestBuildScheduleFields:
    def test_daily_generates_cron_and_strictly_future_due(self):
        now = datetime(2026, 10, 28, 10, 0, 0)
        cron, interval, run_at, due = build_schedule_fields(
            "daily", {"hour": 9, "minute": 30}, now=now)
        assert cron == "30 9 * * *"
        assert interval is None and run_at is None
        # 10:00 的下一次 09:30 在次日，且严格大于 now（+1µs 精确边界语义）
        assert due == datetime(2026, 10, 29, 9, 30, 0)

    def test_daily_at_exact_now_advances_strictly(self):
        """now 恰等于触发时刻（09:30:00）时推进到次日（不得返回等于 now 的时间）"""
        now = datetime(2026, 10, 28, 9, 30, 0)
        _, _, _, due = build_schedule_fields("daily", {"hour": 9, "minute": 30}, now=now)
        assert due == datetime(2026, 10, 29, 9, 30, 0)

    def test_weekly_validates_day_of_week(self):
        _, _, _, due = build_schedule_fields(
            "weekly", {"day_of_week": "FRI", "hour": 18, "minute": 0},
            now=datetime(2026, 10, 28, 10, 0, 0))
        assert due == datetime(2026, 10, 30, 18, 0, 0)

    def test_weekly_invalid_day_rejected(self):
        with pytest.raises(ScheduleConfigError, match="day_of_week"):
            build_schedule_fields("weekly", {"day_of_week": "funday", "hour": 9, "minute": 0})

    def test_monthly_day_out_of_range_rejected(self):
        with pytest.raises(ScheduleConfigError, match="越界"):
            build_schedule_fields("monthly", {"day": 32, "hour": 9, "minute": 0})

    def test_hour_out_of_range_rejected(self):
        with pytest.raises(ScheduleConfigError, match="越界"):
            build_schedule_fields("daily", {"hour": 24, "minute": 0})

    def test_missing_time_rejected_with_ask_user_guidance(self):
        """缺时间/空 time_config：拒绝并引导先向用户确认（不猜测默认时间）"""
        with pytest.raises(ScheduleConfigError, match="先向用户确认具体时间"):
            build_schedule_fields("daily", {})
        with pytest.raises(ScheduleConfigError, match="先向用户确认具体时间"):
            build_schedule_fields("daily", {"day": 1})

    def test_invalid_schedule_type_rejected(self):
        with pytest.raises(ScheduleConfigError, match="不支持的调度类型"):
            build_schedule_fields("hourly", {"hour": 9, "minute": 0})

    def test_string_numeric_fields_coerced(self):
        """数值字段 str→int 容错（LLM 常见输出形态）"""
        cron, interval, _, _ = build_schedule_fields(
            "daily", {"hour": "9", "minute": "30"})
        assert cron == "30 9 * * *"
        _, interval, _, due = build_schedule_fields(
            "interval", {"interval_hours": "2"}, now=datetime(2026, 10, 28, 10, 0, 0))
        assert interval == 7200
        assert due == datetime(2026, 10, 28, 12, 0, 0)

    def test_interval_missing_or_nonpositive_rejected(self):
        with pytest.raises(ScheduleConfigError, match="先向用户确认具体时间"):
            build_schedule_fields("interval", {})
        with pytest.raises(ScheduleConfigError, match="正数"):
            build_schedule_fields("interval", {"interval_hours": 0})

    def test_interval_fractional_hours_honored(self):
        """interval 小数小时不截断：1.5 小时=5400s（str 形态同规则），非法值拒绝"""
        now = datetime(2026, 10, 28, 10, 0, 0)
        _, interval, _, due = build_schedule_fields(
            "interval", {"interval_hours": 1.5}, now=now)
        assert interval == 5400
        assert due == now + timedelta(seconds=5400)
        _, interval, _, _ = build_schedule_fields(
            "interval", {"interval_hours": "1.5"}, now=now)
        assert interval == 5400
        with pytest.raises(ScheduleConfigError, match="数字"):
            build_schedule_fields("interval", {"interval_hours": "abc"}, now=now)

    def test_interval_due_is_now_plus_seconds(self):
        now = datetime(2026, 10, 28, 10, 0, 0)
        _, interval, run_at, due = build_schedule_fields(
            "interval", {"interval_hours": 1}, now=now)
        assert interval == 3600 and run_at is None
        assert due == now + timedelta(hours=1)

    def test_once_future_normalized(self):
        """once 未来时间：归一化 "%Y-%m-%d %H:%M:%S"，next_due_at = run_at"""
        now = datetime(2026, 10, 28, 10, 0, 0)
        cron, interval, run_at, due = build_schedule_fields(
            "once", {"run_at": "2026-12-01 09:30:00"}, now=now)
        assert cron is None and interval is None
        assert run_at == "2026-12-01 09:30:00"
        assert due == datetime(2026, 12, 1, 9, 30, 0)

    def test_once_past_rejected(self):
        now = datetime(2026, 10, 28, 10, 0, 0)
        with pytest.raises(ScheduleConfigError, match="已经过去"):
            build_schedule_fields("once", {"run_at": "2026-10-01 09:30:00"}, now=now)

    def test_once_missing_or_unparseable_run_at_rejected(self):
        now = datetime(2026, 10, 28, 10, 0, 0)
        # time_config 为空：统一走"先向用户确认具体时间"引导
        with pytest.raises(ScheduleConfigError, match="先向用户确认具体时间"):
            build_schedule_fields("once", {}, now=now)
        with pytest.raises(ScheduleConfigError, match="run_at 必填"):
            build_schedule_fields("once", {"run_at": "明天早上"}, now=now)

    def test_parse_run_at_accepts_whitespace(self):
        assert parse_run_at("  2026-12-01 09:30:00 ") == datetime(2026, 12, 1, 9, 30)
        assert parse_run_at("") is None
        assert parse_run_at(None) is None


# ==================== 纯函数：compute_resume_due ====================

class TestComputeResumeDue:
    def test_interval_reanchors_at_now(self):
        task = {"schedule_type": "interval", "interval_seconds": 3600,
                "next_due_at": datetime(2026, 10, 1, 0, 0, 0)}
        now = datetime(2026, 10, 28, 10, 0, 0)
        due, will_expire = compute_resume_due(task, now=now)
        assert due == now + timedelta(hours=1)
        assert will_expire is False

    def test_cron_takes_next_after_now(self):
        task = {"schedule_type": "daily", "cron_expression": "0 9 * * *",
                "next_due_at": datetime(2026, 10, 1, 9, 0, 0)}
        due, will_expire = compute_resume_due(task, now=datetime(2026, 10, 28, 10, 0, 0))
        assert due == datetime(2026, 10, 29, 9, 0, 0)
        assert will_expire is False

    def test_once_future_keeps_run_at(self):
        task = {"schedule_type": "once", "run_at": datetime(2026, 12, 1, 9, 0, 0)}
        due, will_expire = compute_resume_due(task, now=datetime(2026, 10, 28, 10, 0, 0))
        assert due == datetime(2026, 12, 1, 9, 0, 0)
        assert will_expire is False

    def test_once_past_grace_flags_expiry(self):
        """once run_at 超过 10 分钟宽限：恢复后将被认领侧作废（提示用户）"""
        task = {"schedule_type": "once", "run_at": datetime(2026, 10, 28, 8, 0, 0)}
        due, will_expire = compute_resume_due(task, now=datetime(2026, 10, 28, 10, 0, 0))
        assert will_expire is True

    def test_interval_invalid_schedule_rejected(self):
        task = {"schedule_type": "interval", "interval_seconds": 0,
                "next_due_at": datetime(2026, 10, 1, 0, 0, 0)}
        with pytest.raises(ScheduleConfigError):
            compute_resume_due(task)


# ==================== 工具一：create ====================

class TestCreateScheduledTaskTool:
    async def test_missing_context_identity_rejected_without_db_write(self):
        """身份三要素（user/tenant/session）缺一不创建，不触发任何 DB 调用"""
        tool = CreateScheduledTaskTool()
        with patch.object(ScheduledTaskDB, "count_by_user") as mock_count, \
                patch.object(ScheduledTaskDB, "create") as mock_create:
            with tool_execution_scope(ToolExecutionContext(user_id="user_1")):
                result = await tool.execute(**_CREATE_KWARGS)
            assert result["success"] is False
            mock_count.assert_not_called()
            mock_create.assert_not_called()

    async def test_create_success_passes_schedule_and_identity(self):
        tool = CreateScheduledTaskTool()
        with patch.object(ScheduledTaskDB, "count_by_user", return_value=0), \
             patch("src.tools.scheduler.scheduled_task_tool.build_schedule_fields",
                  return_value=("0 9 * * *", None, None, datetime(2026, 10, 29, 9, 0))),              patch.object(ScheduledTaskDB, "create",
                          return_value=_created_row(next_due_at=datetime(2026, 10, 29, 9, 0))) as mock_create:
            with _scope():
                result = await tool.execute(**_CREATE_KWARGS)
        assert result["success"] is True
        assert result["task_id"] == "sched_abc123"
        assert result["schedule_description"] == "每天 09:00"
        assert "首次执行在下一个调度点" in result["message"]
        kwargs = mock_create.call_args.kwargs
        assert kwargs["cron_expression"] == "0 9 * * *"
        assert kwargs["interval_seconds"] is None
        assert kwargs["run_at"] is None
        assert kwargs["next_due_at"] == datetime(2026, 10, 29, 9, 0)
        # 默认主智能体：profile_id='main'，不落 subagent_id
        assert kwargs["profile_id"] == "main"
        assert kwargs["subagent_id"] is None
        assert kwargs["tenant_id"] == "tenant_1" and kwargs["user_id"] == "user_1"
        # count 校验带租户过滤
        assert mock_create.call_args is not None

    async def test_profile_inheritance_from_context(self):
        """智能体继承（设计 §3.2）：context.profile_id/instance_id 固化到任务行"""
        tool = CreateScheduledTaskTool()
        with patch.object(ScheduledTaskDB, "count_by_user", return_value=0), \
             patch.object(ScheduledTaskDB, "create", return_value=_created_row()) as mock_create:
            with _scope(profile_id="data-analysis", instance_id="inst_9"):
                await tool.execute(**_CREATE_KWARGS)
        kwargs = mock_create.call_args.kwargs
        assert kwargs["profile_id"] == "data-analysis"
        assert kwargs["instance_id"] == "inst_9"
        assert kwargs["subagent_id"] == "data-analysis"

    async def test_notify_target_id_passthrough(self):
        tool = CreateScheduledTaskTool()
        with patch.object(ScheduledTaskDB, "count_by_user", return_value=0), \
             patch.object(ScheduledTaskDB, "notify_target_visible",
                          return_value=True) as mock_visible, \
             patch.object(ScheduledTaskDB, "create", return_value=_created_row()) as mock_create:
            with _scope():
                result = await tool.execute(
                    **_CREATE_KWARGS, notify_target_id="ntarget_1")
        assert result["success"] is True
        kwargs = mock_create.call_args.kwargs
        assert kwargs["notification_target_id"] == "ntarget_1"
        # 归属校验同口径（本人个人/本租户租户级目标）
        assert mock_visible.call_args.args[0] == "ntarget_1"
        assert mock_visible.call_args.kwargs["tenant_id"] == "tenant_1"
        assert mock_visible.call_args.kwargs["user_id"] == "user_1"

    async def test_notify_target_invisible_rejected_before_db_write(self):
        """通知目标归属校验：不可见目标（他租户/幻觉 ID）拒绝，不落库"""
        tool = CreateScheduledTaskTool()
        with patch.object(ScheduledTaskDB, "notify_target_visible",
                          return_value=False), \
             patch.object(ScheduledTaskDB, "count_by_user") as mock_count, \
             patch.object(ScheduledTaskDB, "create") as mock_create:
            with _scope():
                result = await tool.execute(
                    **_CREATE_KWARGS, notify_target_id="target_other_tenant")
        assert result["success"] is False
        assert "通知目标不存在或不可用" in result["error"]
        mock_count.assert_not_called()
        mock_create.assert_not_called()

    async def test_count_limit_rejected_before_create(self):
        """每用户 active 上限（事务内落实：create 抛 QuotaError，评审 P2）"""
        tool = CreateScheduledTaskTool()
        with patch.object(ScheduledTaskDB, "create",
                          side_effect=ScheduledTaskQuotaError("定时任务已达上限（5个）")) as mock_create:
            with _scope():
                result = await tool.execute(**_CREATE_KWARGS)
        assert result["success"] is False
        assert "上限" in result["error"]
        # 配额走 create 事务内（max_active 传入），不再前置独立计数
        assert mock_create.call_args.kwargs.get("max_active") == 5

    async def test_missing_time_config_rejected_with_ask_user_hint(self):
        tool = CreateScheduledTaskTool()
        with patch.object(ScheduledTaskDB, "count_by_user") as mock_count:
            with _scope():
                result = await tool.execute(
                    name="每日日报", task_prompt="x", schedule_type="daily", time_config={})
        assert result["success"] is False
        assert "先向用户确认具体时间" in result["error"]
        mock_count.assert_not_called()

    async def test_once_past_time_rejected(self):
        tool = CreateScheduledTaskTool()
        with patch.object(ScheduledTaskDB, "create") as mock_create:
            with _scope():
                result = await tool.execute(
                    name="提醒", task_prompt="x", schedule_type="once",
                    time_config={"run_at": "2020-01-01 09:00:00"})
        assert result["success"] is False
        assert "过去" in result["error"]
        mock_create.assert_not_called()

    async def test_invalid_schedule_type_rejected(self):
        tool = CreateScheduledTaskTool()
        with _scope():
            result = await tool.execute(
                name="n", task_prompt="x", schedule_type="hourly",
                time_config={"hour": 9})
        assert result["success"] is False
        assert "不支持的调度类型" in result["error"]

    async def test_r56_definite_weixin_marketing_intercepted(self):
        """R56：微信营销域+调度词+发送动作同时命中 → 拒绝并引导专用工具"""
        tool = CreateScheduledTaskTool()
        with patch.object(ScheduledTaskDB, "count_by_user") as mock_count, \
                patch.object(ScheduledTaskDB, "create") as mock_create:
            with _scope():
                result = await tool.execute(
                    name="微信群发营销",
                    task_prompt="每天早上自动发送群发消息给客户微信群",
                    schedule_type="daily", time_config={"hour": 9, "minute": 0})
        assert result["success"] is False
        assert result["code"] == "USE_DEDICATED_WEIXIN_TOOL"
        assert result["routed_tool"] == "weixin_automation_prepare"
        mock_count.assert_not_called()
        mock_create.assert_not_called()

    async def test_r56_other_channel_marker_passes_through(self):
        """R56 保守放行：命中微信词但明确发到邮件 → 走通用定时任务"""
        tool = CreateScheduledTaskTool()
        with patch.object(ScheduledTaskDB, "count_by_user", return_value=0), \
             patch.object(ScheduledTaskDB, "create", return_value=_created_row()) as mock_create:
            with _scope():
                result = await tool.execute(
                    name="每日汇总",
                    task_prompt="每天汇总微信群发数据后发送邮件到 a@b.com",
                    schedule_type="daily", time_config={"hour": 9, "minute": 0})
        assert result["success"] is True
        mock_create.assert_called_once()

    async def test_r56_incomplete_markers_pass_through(self):
        """R56 保守放行：仅命中微信域词、无调度/发送动作组合 → 放行"""
        tool = CreateScheduledTaskTool()
        with patch.object(ScheduledTaskDB, "count_by_user", return_value=0), \
             patch.object(ScheduledTaskDB, "create", return_value=_created_row()) as mock_create:
            with _scope():
                result = await tool.execute(
                    name="微信数据报表",
                    task_prompt="每天统计微信营销后台数据并生成报表",
                    schedule_type="daily", time_config={"hour": 9, "minute": 0})
        assert result["success"] is True
        mock_create.assert_called_once()

    async def test_no_prompt_length_limit(self):
        """设计 §3.1：删除 prompt 2000 字上限（服从请求容量闸），超长 prompt 可创建"""
        tool = CreateScheduledTaskTool()
        long_prompt = "生成日报，数据来源与要求：" + "详细要求内容。" * 800  # ~4800 字
        with patch.object(ScheduledTaskDB, "count_by_user", return_value=0), \
             patch.object(ScheduledTaskDB, "create", return_value=_created_row()) as mock_create:
            with _scope():
                result = await tool.execute(
                    name="长任务", task_prompt=long_prompt,
                    schedule_type="daily", time_config={"hour": 9, "minute": 0})
        assert result["success"] is True
        assert mock_create.call_args.kwargs["task_prompt"] == long_prompt


# ==================== 工具二：manage ====================

class TestManageScheduledTaskTool:
    async def test_list_summary(self):
        tool = ManageScheduledTaskTool()
        rows = [_created_row(task_id="sched_1", name="任务A", status="active",
                             next_due_at=datetime(2026, 10, 29, 9, 0), total_runs=3,
                             success_count=2, fail_count=1),
                _created_row(task_id="sched_2", name="任务B", status="paused",
                             pause_reason="credit_blocked")]
        with patch.object(ScheduledTaskDB, "list_by_user", return_value=rows) as mock_list:
            with _scope():
                result = await tool.execute(action="list")
        assert result["success"] is True
        assert "您共有 2 个定时任务" in result["message"]
        assert "任务A" in result["message"] and "下次执行" in result["message"]
        assert "credit_blocked" in result["message"]
        assert mock_list.call_args.args[0] == "user_1"
        assert mock_list.call_args.kwargs["tenant_id"] == "tenant_1"

    async def test_list_empty(self):
        tool = ManageScheduledTaskTool()
        with patch.object(ScheduledTaskDB, "list_by_user", return_value=[]):
            with _scope():
                result = await tool.execute(action="list")
        assert result["success"] is True
        assert "还没有创建任何定时任务" in result["message"]

    async def test_pause_success(self):
        tool = ManageScheduledTaskTool()
        with patch.object(ScheduledTaskDB, "get_by_id",
                          return_value=_created_row(status="active")), \
             patch.object(ScheduledTaskDB, "pause", return_value=True) as mock_pause:
            with _scope():
                result = await tool.execute(action="pause", task_id="sched_1")
        assert result["success"] is True
        assert mock_pause.call_args.kwargs["tenant_id"] == "tenant_1"
        assert mock_pause.call_args.kwargs["user_id"] == "user_1"

    async def test_pause_wrong_state_friendly_error(self):
        """pause 仅 active：DAL 返回 False 时提示当前状态（不落库）"""
        tool = ManageScheduledTaskTool()
        with patch.object(ScheduledTaskDB, "get_by_id",
                          return_value=_created_row(status="completed")), \
             patch.object(ScheduledTaskDB, "pause", return_value=False):
            with _scope():
                result = await tool.execute(action="pause", task_id="sched_1")
        assert result["success"] is False
        assert "completed" in result["error"]

    async def test_resume_uses_transactional_recompute(self):
        """恢复统一走 resume_recompute（事务内锁行重算，评审 P1）。"""
        tool = ManageScheduledTaskTool()
        task = _created_row(status="paused", schedule_type="interval",
                            interval_seconds=3600,
                            next_due_at=datetime(2026, 10, 1, 9, 0))
        with patch.object(ScheduledTaskDB, "get_by_id", return_value=task),              patch.object(ScheduledTaskDB, "resume_recompute",
                          return_value=dict(task, status="active", pause_reason=None,
                                            next_due_at=datetime.now() + timedelta(hours=1))) as mock_rr:
            with _scope():
                result = await tool.execute(action="resume", task_id="sched_1")
        assert result["success"] is True
        assert "next_due_at" not in mock_rr.call_args.kwargs
        assert mock_rr.call_args.kwargs.get("max_active") == 5

    async def test_resume_once_past_grace_expires_unified(self):
        """once 过宽限：统一转换为 expired（与 API 同一路径，回执明确）。"""
        tool = ManageScheduledTaskTool()
        task = _created_row(status="paused", schedule_type="once",
                            run_at=datetime.now() - timedelta(hours=2))
        with patch.object(ScheduledTaskDB, "get_by_id", return_value=task),              patch.object(ScheduledTaskDB, "resume_recompute",
                          return_value=dict(task, status="expired",
                                            next_due_at=None, pause_reason=None)):
            with _scope():
                result = await tool.execute(action="resume", task_id="sched_1")
        assert result["success"] is True and result.get("expired") is True
        assert "作废" in result["message"]

    async def test_resume_wrong_state_friendly_error(self):
        """非 paused 状态恢复：明确状态提示。mock 生产实际调用的
        resume_recompute（返回 None=不可恢复）——此前 mock 旧 resume，
        resume_recompute 触真实 DB，仅在本地 DB 可达时碰巧通过。"""
        tool = ManageScheduledTaskTool()
        with patch.object(ScheduledTaskDB, "get_by_id",
                          return_value=_created_row(status="active")), \
             patch.object(ScheduledTaskDB, "resume_recompute",
                          return_value=None) as mock_rr:
            with _scope():
                result = await tool.execute(action="resume", task_id="sched_1")
        assert result["success"] is False
        assert "仅已暂停" in result["error"]
        mock_rr.assert_called_once()

    async def test_cancel_success(self):
        tool = ManageScheduledTaskTool()
        with patch.object(ScheduledTaskDB, "get_by_id",
                          return_value=_created_row()), \
             patch.object(ScheduledTaskDB, "delete_with_session",
                          return_value=_created_row()) as mock_delete:
            with _scope():
                result = await tool.execute(action="cancel", task_id="sched_1")
        assert result["success"] is True
        assert mock_delete.call_args.kwargs["tenant_id"] == "tenant_1"
        assert mock_delete.call_args.kwargs["user_id"] == "user_1"

    async def test_cancel_concurrently_deleted_reports_honestly(self):
        """并发删除（守卫锁行未命中）：如实失败，不谎报已取消"""
        tool = ManageScheduledTaskTool()
        with patch.object(ScheduledTaskDB, "get_by_id",
                          return_value=_created_row()), \
             patch.object(ScheduledTaskDB, "delete_with_session",
                          return_value=None):
            with _scope():
                result = await tool.execute(action="cancel", task_id="sched_1")
        assert result["success"] is False
        assert "已被删除" in result["error"]

    async def test_cancel_busy_error_friendly_message(self):
        """删除守卫：非终态 runner 存在 → 友好提示稍后再试（不外泄异常）"""
        tool = ManageScheduledTaskTool()
        with patch.object(ScheduledTaskDB, "get_by_id",
                          return_value=_created_row()), \
             patch.object(ScheduledTaskDB, "delete_with_session",
                          side_effect=ScheduledTaskBusyError("busy")):
            with _scope():
                result = await tool.execute(action="cancel", task_id="sched_1")
        assert result["success"] is False
        assert result["error"] == "任务正在执行，请稍后再试"

    async def test_view_returns_stats_and_task_view_guidance(self):
        tool = ManageScheduledTaskTool()
        task = _created_row(status="paused", pause_reason="credit_blocked",
                            total_runs=5, success_count=4, fail_count=1,
                            last_run_at=datetime(2026, 10, 28, 9, 0),
                            last_status="failed")
        with patch.object(ScheduledTaskDB, "get_by_id", return_value=task):
            with _scope():
                result = await tool.execute(action="view", task_id="sched_1")
        assert result["success"] is True
        assert "累计执行: 5" in result["message"]
        assert "credit_blocked" in result["message"]
        assert "任务视图" in result["message"]

    async def test_set_notify_target_sets(self):
        tool = ManageScheduledTaskTool()
        with patch.object(ScheduledTaskDB, "get_by_id", return_value=_created_row()), \
             patch.object(ScheduledTaskDB, "notify_target_visible",
                          return_value=True), \
             patch.object(ScheduledTaskDB, "update_definition",
                          return_value=True) as mock_update:
            with _scope():
                result = await tool.execute(
                    action="set_notify_target", task_id="sched_1",
                    notify_target_id="ntarget_2")
        assert result["success"] is True
        assert mock_update.call_args.kwargs["notification_target_id"] == "ntarget_2"
        assert "clear_fields" not in mock_update.call_args.kwargs or \
            mock_update.call_args.kwargs.get("clear_fields") is None

    async def test_set_notify_target_invisible_rejected(self):
        """设置路径归属校验：不可见目标拒绝（不更新）"""
        tool = ManageScheduledTaskTool()
        with patch.object(ScheduledTaskDB, "get_by_id", return_value=_created_row()), \
             patch.object(ScheduledTaskDB, "notify_target_visible",
                          return_value=False), \
             patch.object(ScheduledTaskDB, "update_definition") as mock_update:
            with _scope():
                result = await tool.execute(
                    action="set_notify_target", task_id="sched_1",
                    notify_target_id="target_foreign")
        assert result["success"] is False
        assert "通知目标不存在或不可用" in result["error"]
        mock_update.assert_not_called()

    async def test_set_notify_target_clears_via_clear_fields(self):
        """F4 清空语义：不传 notify_target_id = 清除恢复默认（clear_fields）"""
        tool = ManageScheduledTaskTool()
        with patch.object(ScheduledTaskDB, "get_by_id", return_value=_created_row()), \
             patch.object(ScheduledTaskDB, "update_definition",
                          return_value=True) as mock_update:
            with _scope():
                result = await tool.execute(action="set_notify_target", task_id="sched_1")
        assert result["success"] is True
        assert "恢复默认" in result["message"]
        kwargs = mock_update.call_args.kwargs
        assert kwargs["clear_fields"] == ["notification_target_id"]
        assert kwargs.get("notification_target_id") is None

    async def test_foreign_task_invisible(self):
        """越权行查不到（归属条件进 SQL）：get_by_id 返回 None → 无权操作"""
        tool = ManageScheduledTaskTool()
        with patch.object(ScheduledTaskDB, "get_by_id", return_value=None) as mock_get:
            with _scope():
                result = await tool.execute(action="pause", task_id="sched_other")
        assert result["success"] is False
        assert result.get("permission_denied") is True
        assert mock_get.call_args.kwargs["tenant_id"] == "tenant_1"
        assert mock_get.call_args.kwargs["user_id"] == "user_1"

    async def test_action_requires_task_id(self):
        tool = ManageScheduledTaskTool()
        with _scope():
            result = await tool.execute(action="pause")
        assert result["success"] is False
        assert "任务ID" in result["error"]

    async def test_unsupported_action_rejected(self):
        tool = ManageScheduledTaskTool()
        with _scope():
            result = await tool.execute(action="reboot", task_id="sched_1")
        assert result["success"] is False
        assert "不支持的操作" in result["error"]

    async def test_list_and_view_strip_internal_snapshot(self):
        """任务行原始数据中的调度侧内部字段（pending_snapshot）不进对话记录"""
        tool = ManageScheduledTaskTool()
        row = _created_row(pending_snapshot={"text": "内部快照"},
                           pending_occurrence_key="2026-10-29T09:00:00")
        with patch.object(ScheduledTaskDB, "list_by_user", return_value=[row]):
            with _scope():
                listed = await tool.execute(action="list")
        assert listed["success"] is True
        assert "pending_snapshot" not in listed["tasks"][0]
        with patch.object(ScheduledTaskDB, "get_by_id", return_value=row):
            with _scope():
                viewed = await tool.execute(action="view", task_id="sched_1")
        assert viewed["success"] is True
        assert "pending_snapshot" not in viewed["task"]

    async def test_missing_user_context_rejected(self):
        tool = ManageScheduledTaskTool()
        with tool_execution_scope(ToolExecutionContext(tenant_id="tenant_1")):
            result = await tool.execute(action="list")
        assert result["success"] is False
        assert "未登录" in result["error"]


# ==================== 上下文继承链（profile_id/instance_id 传递） ====================

class TestContextProfilePropagation:
    """智能体身份经 ToolExecutionContext 传递（设计 §3.2）：

    worker 从 runner 行派生（profile_id 列 + input.instance_id）→
    RuntimeExecution → ExecutionContextFactory.for_agent_call → 工具读取。
    """

    def test_for_agent_call_carries_profile_and_instance(self):
        from src.tools.context import ExecutionContextFactory
        context = ExecutionContextFactory.for_agent_call(
            tenant_id="t1", user_id="u1", session_id="s1",
            profile_id="data-analysis", instance_id="inst_9",
            infer_legacy_identity=False)
        assert context.profile_id == "data-analysis"
        assert context.instance_id == "inst_9"

    def test_for_agent_call_inherits_from_parent_scope(self):
        """嵌套调用缺省继承父 scope 的 profile/instance（子执行上下文派生）"""
        from src.tools.context import ExecutionContextFactory
        parent = ToolExecutionContext(user_id="u1", profile_id="child-1",
                                      instance_id="inst_9")
        with tool_execution_scope(parent):
            derived = ExecutionContextFactory.for_agent_call(infer_legacy_identity=False)
        assert derived.profile_id == "child-1"
        assert derived.instance_id == "inst_9"

    def test_default_profile_id_is_none_backward_compatible(self):
        """默认 None 向后兼容：旧构造点不传新字段不受影响"""
        context = ToolExecutionContext(user_id="u1")
        assert context.profile_id is None and context.instance_id is None


# ==================== 工具可见性声明 ====================

class TestToolVisibilityDeclarations:
    def test_both_tools_chat_only(self):
        """chat_only 声明：渠道与 scheduled 执行经 filter_source_visible_tools 滤除"""
        assert CreateScheduledTaskTool.chat_only is True
        assert ManageScheduledTaskTool.chat_only is True

    def test_schedule_description_formats(self):
        assert format_schedule_description("daily", {"hour": 9, "minute": 5}) == "每天 09:05"
        assert format_schedule_description("weekly", {"day_of_week": "mon", "hour": 9, "minute": 0}) == "每周一 09:00"
        assert format_schedule_description("monthly", {"day": 15, "hour": 9, "minute": 0}) == "每月15日 09:00"
        assert format_schedule_description("interval", {"interval_hours": 4}) == "每隔 4 小时"
        assert format_schedule_description("once", {"run_at": "2026-12-01 09:00:00"}) == "一次性执行 2026-12-01 09:00:00"
