"""
CreateScheduledTaskTool 单元测试

验证 time_config 嵌套 dict 内部数值字段（interval_hours/hour/minute/day）
以字符串传入时被强转为 int，防止字符串乘法与 :02d 格式化崩溃。
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.tools

from src.tools.scheduler.scheduled_task_tool import CreateScheduledTaskTool


def _make_tool():
    """构造一个 mock 好用户上下文的工具实例"""
    tool = CreateScheduledTaskTool()
    user = MagicMock()
    user.user_id = "u1"
    tool.set_context(user, "session_1", None)
    return tool


def _patch_deps(task_dict):
    """mock DB 与试执行依赖，返回 (patches, create_mock) 供断言"""
    from src.scheduler.executor import ScheduledTaskExecutor

    create_mock = MagicMock(return_value=task_dict)
    patches = [
        patch("src.scheduler.db.ScheduledTaskDB.count_by_user", return_value=0),
        patch("src.scheduler.db.ScheduledTaskDB.create", create_mock),
        patch.object(
            ScheduledTaskExecutor,
            "dry_run",
            new_callable=AsyncMock,
            return_value={"success": True, "result": "ok"},
        ),
    ]
    for p in patches:
        p.start()
    return patches, create_mock


@pytest.mark.asyncio
async def test_interval_hours_string_coerced_to_int():
    """interval_hours="2"（字符串）被强转为 int，interval_seconds=7200 而非字符串乘法"""
    tool = _make_tool()
    task_dict = {"task_id": "t1", "next_run_at": None}
    patches, create_mock = _patch_deps(task_dict)

    try:
        result = await tool.execute(
            name="定时检查",
            task_prompt="执行检查",
            schedule_type="interval",
            time_config={"interval_hours": "2"},
        )
    finally:
        for p in patches:
            p.stop()

    assert result["success"] is True
    # create 收到的 interval_seconds 必须是 7200
    call_kwargs = create_mock.call_args.kwargs
    assert call_kwargs["interval_seconds"] == 7200
    assert isinstance(call_kwargs["interval_seconds"], int)
    # 调度描述不再出现字符串乘法痕迹
    assert "每隔 2 小时" in result["schedule_description"]


@pytest.mark.asyncio
async def test_hour_minute_string_does_not_crash_format():
    """hour/minute 以字符串传入时 :02d 格式化不再抛 ValueError"""
    tool = _make_tool()
    task_dict = {"task_id": "t2", "next_run_at": None}
    patches, _ = _patch_deps(task_dict)

    try:
        result = await tool.execute(
            name="每日报告",
            task_prompt="生成日报",
            schedule_type="daily",
            time_config={"hour": "9", "minute": "30"},
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
    patches, _ = _patch_deps(task_dict)

    try:
        result = await tool.execute(
            name="每日报告",
            task_prompt="生成日报",
            schedule_type="daily",
            time_config={"hour": "abc"},
        )
    finally:
        for p in patches:
            p.stop()

    assert result["success"] is True
    assert "每天 09:00" in result["schedule_description"]
