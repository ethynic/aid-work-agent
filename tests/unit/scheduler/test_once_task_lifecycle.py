"""一次性（once）定时任务生命周期测试：创建校验 -> 注册作废 -> 执行收尾

覆盖 P1-1（apscheduler 一次性提醒任务 missed）修复：
- parse_run_datetime：格式归一化与不可解析输入
- create_scheduled_task：once 缺 run_at / 过去时间 / 非法 schedule_type 拒绝；
  未来 run_at 归一化落库
- ScheduledTaskManager._register_job：缺失/不可解析/超宽限过期的 once 任务
  显式作废（status=expired）且不注册；宽限内或未来时间正常注册
- ScheduledTaskExecutor.execute：once 任务成功执行后置 completed
"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.scheduler.manager import ScheduledTaskManager, parse_run_datetime
from src.tools.context import ToolExecutionContext, tool_execution_scope
from src.tools.scheduler.scheduled_task_tool import CreateScheduledTaskTool


# ==================== parse_run_datetime ====================


class TestParseRunDatetime:
    def test_canonical_format(self):
        assert parse_run_datetime("2026-10-20 14:40:00") == datetime(2026, 10, 20, 14, 40, 0)

    def test_without_seconds(self):
        assert parse_run_datetime("2026-10-20 14:40") == datetime(2026, 10, 20, 14, 40)

    def test_iso_t_separator(self):
        assert parse_run_datetime("2026-10-20T14:40:00") == datetime(2026, 10, 20, 14, 40)

    def test_slash_date(self):
        assert parse_run_datetime("2026/10/20 14:40") == datetime(2026, 10, 20, 14, 40)

    def test_aware_tz_converted_to_shanghai_naive(self):
        dt = parse_run_datetime("2026-10-20T14:40:00+08:00")
        assert dt == datetime(2026, 10, 20, 14, 40)
        assert dt.tzinfo is None

    def test_invalid_string_returns_none(self):
        assert parse_run_datetime("明天下午三点") is None
        assert parse_run_datetime("2026-13-99 99:99") is None

    def test_non_string_or_empty_returns_none(self):
        assert parse_run_datetime(None) is None
        assert parse_run_datetime(12345) is None
        assert parse_run_datetime("") is None
        assert parse_run_datetime("   ") is None


# ==================== create_scheduled_task 校验 ====================


def _make_context():
    return ToolExecutionContext(tenant_id="tenant_a", user_id="user_1", session_id="sess_1")


async def _run_create(time_config, schedule_type="once", **kwargs):
    tool = CreateScheduledTaskTool()
    params = {
        "name": kwargs.get("name", "电话提醒"),
        "task_prompt": kwargs.get("task_prompt", "提醒用户打电话"),
        "schedule_type": schedule_type,
        "time_config": time_config,
    }
    params.update(kwargs.get("params", {}))
    with tool_execution_scope(_make_context()):
        return tool, await tool.execute(**params)


class TestCreateOnceValidation:
    @pytest.mark.asyncio
    async def test_once_without_run_at_rejected(self):
        # time_config 非空但缺 run_at（命中 once 专项校验，而非空的 time_config 校验）
        _, result = await _run_create({"hour": 9})
        assert result["success"] is False
        assert "run_at" in result["error"]

    @pytest.mark.asyncio
    async def test_once_with_unparsable_run_at_rejected(self):
        _, result = await _run_create({"run_at": "明天下午"})
        assert result["success"] is False
        assert "run_at" in result["error"]

    @pytest.mark.asyncio
    async def test_once_with_past_run_at_rejected(self):
        past = (datetime.now() - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
        _, result = await _run_create({"run_at": past})
        assert result["success"] is False
        assert "已经过去" in result["error"]

    @pytest.mark.asyncio
    async def test_invalid_schedule_type_rejected(self):
        _, result = await _run_create({"hour": 9}, schedule_type="yearly")
        assert result["success"] is False
        assert "调度类型" in result["error"]

    @pytest.mark.asyncio
    async def test_once_future_run_at_normalized_to_db(self):
        future = (datetime.now() + timedelta(hours=2)).replace(microsecond=0, second=0)
        tool = CreateScheduledTaskTool()
        with patch("src.scheduler.db.ScheduledTaskDB.count_by_user", return_value=0), \
             patch("src.scheduler.executor.ScheduledTaskExecutor.dry_run",
                   new=AsyncMock(return_value={"success": True, "result": "ok"})), \
             patch("src.scheduler.db.ScheduledTaskDB.create",
                   return_value={"task_id": "sched_x", "next_run_at": None}) as mock_create, \
             tool_execution_scope(_make_context()):
            result = await tool.execute(
                name="电话提醒",
                task_prompt="提醒用户打电话",
                schedule_type="once",
                # 缺秒 + T 分隔的 LLM 变体输出，应被归一化
                time_config={"run_at": future.strftime("%Y-%m-%dT%H:%M")},
            )
        assert result["success"] is True
        written = mock_create.call_args.kwargs["cron_expression"]
        assert written == future.strftime("%Y-%m-%d %H:%M:%S")

    @pytest.mark.asyncio
    async def test_daily_task_still_accepted(self):
        tool = CreateScheduledTaskTool()
        with patch("src.scheduler.db.ScheduledTaskDB.count_by_user", return_value=0), \
             patch("src.scheduler.executor.ScheduledTaskExecutor.dry_run",
                   new=AsyncMock(return_value={"success": True, "result": "ok"})), \
             patch("src.scheduler.db.ScheduledTaskDB.create",
                   return_value={"task_id": "sched_x", "next_run_at": None}) as mock_create, \
             tool_execution_scope(_make_context()):
            result = await tool.execute(
                name="每日摘要",
                task_prompt="生成摘要",
                schedule_type="daily",
                time_config={"hour": 9, "minute": 30},
            )
        assert result["success"] is True
        assert mock_create.call_args.kwargs["cron_expression"] == "30 9 * * *"


# ==================== 注册端：过期 once 任务作废 ====================


def _make_task(**overrides):
    task = {
        "task_id": "task-1",
        "user_id": "user-1",
        "name": "电话提醒",
        "task_prompt": "提醒用户打电话",
        "status": "active",
        "max_retries": 0,
        "retry_count": 0,
        "tenant_id": "tenant_task",
        "schedule_type": "once",
    }
    task.update(overrides)
    return task


def _make_manager():
    mgr = ScheduledTaskManager()
    mgr._scheduler = MagicMock()
    mgr._scheduler.add_job.return_value.id = "job_task-1"
    return mgr


def _task_row(**overrides):
    row = {
        "task_id": "task-1",
        "name": "陈宇电话沟通提醒",
        "schedule_type": "once",
        "cron_expression": "2026-10-20 14:40:00",
        "interval_seconds": None,
        "status": "active",
    }
    row.update(overrides)
    return row


class TestRegisterOnceTask:
    def test_past_once_task_beyond_grace_marked_expired(self):
        mgr = _make_manager()
        past = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
        with patch("src.scheduler.manager.ScheduledTaskDB.update_status") as mock_status:
            job_id = mgr._register_job(_task_row(cron_expression=past))
        assert job_id is None
        mock_status.assert_called_once_with("task-1", "expired")
        mgr._scheduler.add_job.assert_not_called()

    def test_once_task_without_run_time_marked_expired(self):
        mgr = _make_manager()
        with patch("src.scheduler.manager.ScheduledTaskDB.update_status") as mock_status:
            job_id = mgr._register_job(_task_row(cron_expression=None))
        assert job_id is None
        mock_status.assert_called_once_with("task-1", "expired")
        mgr._scheduler.add_job.assert_not_called()

    def test_once_task_unparsable_run_time_marked_expired(self):
        mgr = _make_manager()
        with patch("src.scheduler.manager.ScheduledTaskDB.update_status") as mock_status:
            job_id = mgr._register_job(_task_row(cron_expression="not-a-datetime"))
        assert job_id is None
        mock_status.assert_called_once_with("task-1", "expired")

    def test_future_once_task_registered(self):
        mgr = _make_manager()
        future = (datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        with patch("src.scheduler.manager.ScheduledTaskDB.update_status") as mock_status:
            job_id = mgr._register_job(_task_row(cron_expression=future))
        assert job_id == "job_task-1"
        mock_status.assert_not_called()
        mgr._scheduler.add_job.assert_called_once()

    def test_once_task_within_grace_still_registered(self):
        mgr = _make_manager()
        recent = (datetime.now() - timedelta(seconds=60)).strftime("%Y-%m-%d %H:%M:%S")
        with patch("src.scheduler.manager.ScheduledTaskDB.update_status") as mock_status:
            job_id = mgr._register_job(_task_row(cron_expression=recent))
        assert job_id == "job_task-1"
        mock_status.assert_not_called()


# ==================== 执行端：once 成功后收尾 ====================


class TestExecutorOnceCompletion:
    @pytest.mark.asyncio
    async def test_once_task_marked_completed_after_success(self):
        from src.scheduler.executor import ScheduledTaskExecutor

        executor = ScheduledTaskExecutor()
        executor._agent = MagicMock()
        executor._agent.process_message_sync = AsyncMock(return_value="提醒已发送")
        task = _make_task(schedule_type="once")
        with patch("src.scheduler.executor.ScheduledTaskDB.get_by_id", return_value=task), \
             patch("src.scheduler.executor.UserDB.get_by_id",
                   return_value={"user_id": "user-1", "username": "张三",
                                 "phone": "138", "role": "employee",
                                 "tenant_id": "tenant_task"}), \
             patch("src.scheduler.executor.SessionDB.get_by_id",
                   return_value={"session_id": "cron_user-1"}), \
             patch("src.scheduler.executor.ScheduledTaskLogDB.create",
                   return_value={"log_id": "log-1"}), \
             patch("src.scheduler.executor.ScheduledTaskDB.update_after_run",
                   return_value=True) as mock_update, \
             patch("src.scheduler.executor.ScheduledTaskDB.update_status") as mock_status:
            result = await executor.execute("task-1")
        assert result["success"] is True
        mock_update.assert_called_once()
        mock_status.assert_called_once_with("task-1", "completed")

    @pytest.mark.asyncio
    async def test_recurring_task_not_marked_completed(self):
        from src.scheduler.executor import ScheduledTaskExecutor

        executor = ScheduledTaskExecutor()
        executor._agent = MagicMock()
        executor._agent.process_message_sync = AsyncMock(return_value="完成")
        task = _make_task(schedule_type="daily")
        with patch("src.scheduler.executor.ScheduledTaskDB.get_by_id", return_value=task), \
             patch("src.scheduler.executor.UserDB.get_by_id",
                   return_value={"user_id": "user-1", "username": "张三",
                                 "phone": "138", "role": "employee",
                                 "tenant_id": "tenant_task"}), \
             patch("src.scheduler.executor.SessionDB.get_by_id",
                   return_value={"session_id": "cron_user-1"}), \
             patch("src.scheduler.executor.ScheduledTaskLogDB.create",
                   return_value={"log_id": "log-1"}), \
             patch("src.scheduler.executor.ScheduledTaskDB.update_after_run",
                   return_value=True), \
             patch("src.scheduler.executor.ScheduledTaskDB.update_status") as mock_status:
            await executor.execute("task-1")
        mock_status.assert_not_called()


# ==================== 执行端：成功后尽力投递钩子 ====================


class TestExecutorDeliveryHook:
    @pytest.mark.asyncio
    async def test_success_triggers_delivery(self):
        from src.scheduler.executor import ScheduledTaskExecutor

        executor = ScheduledTaskExecutor()
        executor._agent = MagicMock()
        executor._agent.process_message_sync = AsyncMock(return_value="提醒已发送")
        task = _make_task(schedule_type="daily")
        with patch("src.scheduler.executor.ScheduledTaskDB.get_by_id", return_value=task), \
             patch("src.scheduler.executor.UserDB.get_by_id",
                   return_value={"user_id": "user-1", "username": "张三",
                                 "phone": "138", "role": "employee",
                                 "tenant_id": "tenant_task"}), \
             patch("src.scheduler.executor.SessionDB.get_by_id",
                   return_value={"session_id": "cron_user-1"}), \
             patch("src.scheduler.executor.ScheduledTaskLogDB.create",
                   return_value={"log_id": "log-1"}), \
             patch("src.scheduler.executor.ScheduledTaskDB.update_after_run",
                   return_value=True), \
             patch("src.scheduler.delivery.deliver_task_result",
                   new=AsyncMock(return_value={"delivered": True, "reason": "sent"})) as mock_delivery:
            result = await executor.execute("task-1")
        assert result["success"] is True
        mock_delivery.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delivery_exception_does_not_fail_task(self):
        from src.scheduler.executor import ScheduledTaskExecutor

        executor = ScheduledTaskExecutor()
        executor._agent = MagicMock()
        executor._agent.process_message_sync = AsyncMock(return_value="完成")
        task = _make_task(schedule_type="daily")
        with patch("src.scheduler.executor.ScheduledTaskDB.get_by_id", return_value=task), \
             patch("src.scheduler.executor.UserDB.get_by_id",
                   return_value={"user_id": "user-1", "username": "张三",
                                 "phone": "138", "role": "employee",
                                 "tenant_id": "tenant_task"}), \
             patch("src.scheduler.executor.SessionDB.get_by_id",
                   return_value={"session_id": "cron_user-1"}), \
             patch("src.scheduler.executor.ScheduledTaskLogDB.create",
                   return_value={"log_id": "log-1"}), \
             patch("src.scheduler.executor.ScheduledTaskDB.update_after_run",
                   return_value=True), \
             patch("src.scheduler.delivery.deliver_task_result",
                   new=AsyncMock(side_effect=RuntimeError("hook boom"))):
            result = await executor.execute("task-1")
        assert result["success"] is True


# ==================== 边界：宽限临界与已执行过任务 ====================


class TestRegisterGraceBoundary:
    def test_once_task_exactly_300s_ago_still_registered(self):
        # 宽限判断为严格小于：恰好 300s 前仍在宽限内，正常注册
        now = datetime(2026, 10, 26, 12, 0, 0)
        run_time = now - timedelta(seconds=300)
        mgr = _make_manager()
        with patch("src.scheduler.manager.parse_run_datetime", return_value=run_time), \
             patch("src.scheduler.manager.datetime") as mock_dt, \
             patch("src.scheduler.manager.ScheduledTaskDB.update_status") as mock_status:
            mock_dt.now.return_value = now
            job_id = mgr._register_job(_task_row(
                cron_expression=run_time.strftime("%Y-%m-%d %H:%M:%S")))
        assert job_id == "job_task-1"
        mock_status.assert_not_called()

    def test_once_task_301s_ago_expired(self):
        now = datetime(2026, 10, 26, 12, 0, 0)
        run_time = now - timedelta(seconds=301)
        mgr = _make_manager()
        with patch("src.scheduler.manager.parse_run_datetime", return_value=run_time), \
             patch("src.scheduler.manager.datetime") as mock_dt, \
             patch("src.scheduler.manager.ScheduledTaskDB.update_status") as mock_status:
            mock_dt.now.return_value = now
            job_id = mgr._register_job(_task_row(
                cron_expression=run_time.strftime("%Y-%m-%d %H:%M:%S")))
        assert job_id is None
        mock_status.assert_called_once_with("task-1", "expired")

    def test_once_task_ran_but_not_finalized_expired_within_grace(self):
        # 执行过但未收尾（失败后 status 仍 active 且 last_run_at 已刷）：
        # 宽限内不得再注册补跑，直接作废（防止 reconcile 重跑重复计费）
        now = datetime(2026, 10, 26, 12, 0, 0)
        run_time = now - timedelta(seconds=30)
        mgr = _make_manager()
        with patch("src.scheduler.manager.parse_run_datetime", return_value=run_time), \
             patch("src.scheduler.manager.datetime") as mock_dt, \
             patch("src.scheduler.manager.ScheduledTaskDB.update_status") as mock_status:
            mock_dt.now.return_value = now
            job_id = mgr._register_job(_task_row(
                cron_expression=run_time.strftime("%Y-%m-%d %H:%M:%S"),
                last_run_at="2026-10-26 11:59:31"))
        assert job_id is None
        mock_status.assert_called_once_with("task-1", "expired")

    def test_once_task_not_ran_yet_within_grace_registered(self):
        # 未执行过（如进程在触发时刻重启）：宽限内正常注册，APScheduler 补跑
        now = datetime(2026, 10, 26, 12, 0, 0)
        run_time = now - timedelta(seconds=30)
        mgr = _make_manager()
        with patch("src.scheduler.manager.parse_run_datetime", return_value=run_time), \
             patch("src.scheduler.manager.datetime") as mock_dt, \
             patch("src.scheduler.manager.ScheduledTaskDB.update_status") as mock_status:
            mock_dt.now.return_value = now
            job_id = mgr._register_job(_task_row(
                cron_expression=run_time.strftime("%Y-%m-%d %H:%M:%S"),
                last_run_at=None))
        assert job_id == "job_task-1"
        mock_status.assert_not_called()


class TestParseRunDatetimePureDate:
    def test_pure_date_rejected(self):
        # fromisoformat 接受纯日期串并解析为当天 00:00，与创建端
        # 「必须向用户确认具体时间」口径不符，应视为不可解析
        assert parse_run_datetime("2026-10-20") is None

    def test_full_formats_still_accepted(self):
        assert parse_run_datetime("2026-10-20 14:40:00") == datetime(2026, 10, 20, 14, 40)
        assert parse_run_datetime("2026-10-20T14:40:00") == datetime(2026, 10, 20, 14, 40)


# ==================== 创建端：run_at 恰好等于当前时刻 ====================


class TestCreateOnceBoundary:
    @pytest.mark.asyncio
    async def test_once_run_at_equal_now_rejected(self):
        fixed_now = datetime(2026, 10, 26, 12, 0, 0)
        tool = CreateScheduledTaskTool()
        with patch("src.scheduler.manager.parse_run_datetime", return_value=fixed_now), \
             patch("src.tools.scheduler.scheduled_task_tool.datetime") as mock_dt:
            mock_dt.now.return_value = fixed_now
            with tool_execution_scope(_make_context()):
                result = await tool.execute(
                    name="电话提醒",
                    task_prompt="提醒用户打电话",
                    schedule_type="once",
                    time_config={"run_at": "2026-10-26 12:00:00"})
        assert result["success"] is False
        assert "已经过去" in result["error"]
