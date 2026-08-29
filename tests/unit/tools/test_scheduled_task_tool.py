"""
CreateScheduledTaskTool 单元测试

验证 time_config 嵌套 dict 内部数值字段（interval_hours/hour/minute/day）
以字符串传入时被强转为 int，防止字符串乘法与 :02d 格式化崩溃。
"""
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio

import pytest

pytestmark = pytest.mark.tools

from src.tools.scheduler.scheduled_task_tool import (
    CreateScheduledTaskTool,
    ManageScheduledTaskTool,
)
from src.tools.context import ToolExecutionContext, tool_execution_scope
from src.tools.executor import ToolExecutor
from src.tools.registry import ToolRegistry


def _make_tool():
    return CreateScheduledTaskTool()


def _patch_deps(task_dict):
    """mock DB 与试执行依赖，返回 (patches, create_mock, dry_run_mock) 供断言"""
    from src.scheduler.executor import ScheduledTaskExecutor

    create_mock = MagicMock(return_value=task_dict)
    dry_run_mock = AsyncMock(return_value={"success": True, "result": "ok"})
    patches = [
        patch("src.scheduler.db.ScheduledTaskDB.count_by_user", return_value=0),
        patch("src.scheduler.db.ScheduledTaskDB.create", create_mock),
        patch.object(
            ScheduledTaskExecutor,
            "dry_run",
            dry_run_mock,
        ),
    ]
    for p in patches:
        p.start()
    return patches, create_mock, dry_run_mock


@pytest.mark.asyncio
async def test_interval_hours_string_coerced_to_int():
    """interval_hours="2"（字符串）被强转为 int，interval_seconds=7200 而非字符串乘法"""
    tool = _make_tool()
    task_dict = {"task_id": "t1", "next_run_at": None}
    patches, create_mock, dry_run_mock = _patch_deps(task_dict)

    try:
        with tool_execution_scope(ToolExecutionContext(user_id="u1", session_id="session_1")):
            result = await tool.execute(
                name="定时检查", task_prompt="执行检查",
                schedule_type="interval", time_config={"interval_hours": "2"},
            )
    finally:
        for p in patches:
            p.stop()

    assert result["success"] is True
    # create 收到的 interval_seconds 必须是 7200
    call_kwargs = create_mock.call_args.kwargs
    assert call_kwargs["interval_seconds"] == 7200
    assert isinstance(call_kwargs["interval_seconds"], int)
    # 试执行身份租户与创建落库租户一致（非 SaaS 部署无租户语义 → ''）
    assert dry_run_mock.await_args.kwargs["tenant_id"] == call_kwargs["tenant_id"] == ""
    # 调度描述不再出现字符串乘法痕迹
    assert "每隔 2 小时" in result["schedule_description"]


@pytest.mark.asyncio
async def test_hour_minute_string_does_not_crash_format():
    """hour/minute 以字符串传入时 :02d 格式化不再抛 ValueError"""
    tool = _make_tool()
    task_dict = {"task_id": "t2", "next_run_at": None}
    patches, _, _ = _patch_deps(task_dict)

    try:
        with tool_execution_scope(ToolExecutionContext(user_id="u1", session_id="session_1")):
            result = await tool.execute(
                name="每日报告", task_prompt="生成日报",
                schedule_type="daily", time_config={"hour": "9", "minute": "30"},
            )
    finally:
        for p in patches:
            p.stop()

    assert result["success"] is True
    assert "每天 09:30" in result["schedule_description"]


@pytest.mark.asyncio
async def test_invalid_numeric_value_falls_back_to_default():
    """非法数值（hour="abc"）回退默认 9，不崩溃"""
    tool = _make_tool()
    task_dict = {"task_id": "t3", "next_run_at": None}
    patches, _, _ = _patch_deps(task_dict)

    try:
        with tool_execution_scope(ToolExecutionContext(user_id="u1", session_id="session_1")):
            result = await tool.execute(
                name="每日报告", task_prompt="生成日报",
                schedule_type="daily", time_config={"hour": "abc"},
            )
    finally:
        for p in patches:
            p.stop()

    assert result["success"] is True
    assert "每天 09:00" in result["schedule_description"]


@pytest.mark.asyncio
async def test_concurrent_sessions_do_not_cross_identity():
    registry = ToolRegistry()
    registry.register(CreateScheduledTaskTool())
    executor = ToolExecutor(registry)
    barrier = asyncio.Barrier(2)
    created = []

    async def dry_run(**kwargs):
        await barrier.wait()
        return {"success": True, "result": "ok"}

    def create(**kwargs):
        created.append((kwargs["user_id"], kwargs["session_id"]))
        return {"task_id": kwargs["user_id"], "next_run_at": None}

    with (
        patch("src.scheduler.db.ScheduledTaskDB.count_by_user", return_value=0),
        patch("src.scheduler.db.ScheduledTaskDB.create", side_effect=create),
        patch("src.scheduler.executor.ScheduledTaskExecutor.dry_run", side_effect=dry_run),
    ):
        await asyncio.gather(*(
            executor.execute(
                "create_scheduled_task",
                {"name": user, "task_prompt": "检查", "schedule_type": "daily",
                 "time_config": {"hour": 9}},
                context=ToolExecutionContext(user_id=user, session_id=session),
            )
            for user, session in (("user-a", "session-a"), ("user-b", "session-b"))
        ))

    assert set(created) == {
        ("user-a", "session-a"), ("user-b", "session-b")
    }


@pytest.mark.asyncio
async def test_manage_task_rejects_other_user_before_update():
    tool = ManageScheduledTaskTool()
    with (
        patch(
            "src.scheduler.db.ScheduledTaskDB.get_by_id",
            return_value={"task_id": "task-b", "user_id": "user-b"},
        ),
        patch("src.scheduler.db.ScheduledTaskDB.update_status") as update_status,
        tool_execution_scope(ToolExecutionContext(user_id="user-a")),
    ):
        result = await tool.execute(action="pause", task_id="task-b")

    assert result["permission_denied"] is True
    update_status.assert_not_called()


@pytest.mark.asyncio
async def test_manage_task_does_not_expose_internal_exception():
    tool = ManageScheduledTaskTool()
    with (
        patch(
            "src.scheduler.db.ScheduledTaskDB.list_by_user",
            side_effect=RuntimeError("database password leaked"),
        ),
        tool_execution_scope(ToolExecutionContext(user_id="user-a")),
    ):
        result = await tool.execute(action="list")

    assert result == {"success": False, "error": "操作失败"}


@pytest.mark.asyncio
async def test_create_dry_run_failure_debug_sanitized():
    """试执行失败：dry_run error 中的凭据在 debug 返回前被遮蔽（工具结果直达用户会话）"""
    from src.scheduler.executor import ScheduledTaskExecutor

    tool = _make_tool()
    with (
        patch("src.scheduler.db.ScheduledTaskDB.count_by_user", return_value=0),
        patch.object(
            ScheduledTaskExecutor,
            "dry_run",
            AsyncMock(return_value={
                "success": False, "result": "",
                "error": "认证失败 api_key=sk-leak-99 已拒绝",
            }),
        ),
        patch("src.scheduler.db.ScheduledTaskDB.create") as create_mock,
        tool_execution_scope(ToolExecutionContext(user_id="user-a")),
    ):
        result = await tool.execute(
            name="每日报告", task_prompt="生成日报",
            schedule_type="daily", time_config={"hour": 9},
        )

    assert result["success"] is False
    assert "sk-leak-99" not in result["debug"]
    assert "api_key=***" in result["debug"]
    create_mock.assert_not_called()


@pytest.mark.asyncio
async def test_create_dry_run_exception_debug_sanitized():
    """试执行抛异常：str(e) 中的凭据在 debug 返回前被遮蔽"""
    from src.scheduler.executor import ScheduledTaskExecutor

    tool = _make_tool()
    with (
        patch("src.scheduler.db.ScheduledTaskDB.count_by_user", return_value=0),
        patch.object(
            ScheduledTaskExecutor,
            "dry_run",
            AsyncMock(side_effect=RuntimeError('连接失败 password="hunter-xy 未闭合')),
        ),
        patch("src.scheduler.db.ScheduledTaskDB.create") as create_mock,
        tool_execution_scope(ToolExecutionContext(user_id="user-a")),
    ):
        result = await tool.execute(
            name="每日报告", task_prompt="生成日报",
            schedule_type="daily", time_config={"hour": 9},
        )

    assert result["success"] is False
    assert "hunter-xy" not in result["debug"]
    assert "password=***" in result["debug"]
    create_mock.assert_not_called()


@pytest.mark.asyncio
async def test_create_passes_tenant_to_dry_run_and_create():
    """上下文租户透传：dry_run 执行身份与 create 落库租户一致且来自请求上下文"""
    tool = _make_tool()
    task_dict = {"task_id": "t4", "next_run_at": None}
    patches, create_mock, dry_run_mock = _patch_deps(task_dict)

    try:
        with tool_execution_scope(
            ToolExecutionContext(user_id="u1", session_id="session_1", tenant_id="tenant_a")
        ):
            result = await tool.execute(
                name="每日报告", task_prompt="生成日报",
                schedule_type="daily", time_config={"hour": 9},
            )
    finally:
        for p in patches:
            p.stop()

    assert result["success"] is True
    assert create_mock.call_args.kwargs["tenant_id"] == "tenant_a"
    assert dry_run_mock.await_args.kwargs["tenant_id"] == "tenant_a"


@pytest.mark.asyncio
async def test_create_rejects_when_tenant_context_missing_in_saas():
    """SaaS 部署下租户上下文缺失（None）时 fail-closed：拒绝创建且不触达 DB"""
    from src.config.settings import settings as app_settings

    tool = _make_tool()
    create_mock = MagicMock()
    count_mock = MagicMock()
    with (
        patch.object(app_settings.saas, "enabled", True),
        patch("src.scheduler.db.ScheduledTaskDB.count_by_user", count_mock),
        patch("src.scheduler.db.ScheduledTaskDB.create", create_mock),
        tool_execution_scope(ToolExecutionContext(user_id="u1", session_id="session_1")),
    ):
        result = await tool.execute(
            name="每日报告", task_prompt="生成日报",
            schedule_type="daily", time_config={"hour": 9},
        )

    assert result["success"] is False
    assert "租户上下文缺失" in result["error"]
    count_mock.assert_not_called()
    create_mock.assert_not_called()


@pytest.mark.asyncio
async def test_manage_task_rejects_when_tenant_context_missing_in_saas():
    """SaaS 部署下租户上下文缺失（None）时 fail-closed：拒绝管理操作"""
    from src.config.settings import settings as app_settings

    tool = ManageScheduledTaskTool()
    list_mock = MagicMock()
    with (
        patch.object(app_settings.saas, "enabled", True),
        patch("src.scheduler.db.ScheduledTaskDB.list_by_user", list_mock),
        tool_execution_scope(ToolExecutionContext(user_id="user-a")),
    ):
        result = await tool.execute(action="list")

    assert result["success"] is False
    assert "租户上下文缺失" in result["error"]
    list_mock.assert_not_called()


@pytest.mark.asyncio
async def test_manage_task_update_carries_tenant_and_user_conditions():
    """pause 等写操作把租户+用户条件传进 UPDATE 本身（防 TOCTOU）"""
    tool = ManageScheduledTaskTool()
    update_mock = MagicMock(return_value=True)
    with (
        patch(
            "src.scheduler.db.ScheduledTaskDB.get_by_id",
            return_value={"task_id": "task-a", "user_id": "user-a"},
        ),
        patch("src.scheduler.db.ScheduledTaskDB.update_status", update_mock),
        tool_execution_scope(
            ToolExecutionContext(user_id="user-a", tenant_id="tenant_a")
        ),
    ):
        result = await tool.execute(action="pause", task_id="task-a")

    assert result["success"] is True
    assert update_mock.call_args.kwargs == {
        "tenant_id": "tenant_a", "user_id": "user-a"
    }
