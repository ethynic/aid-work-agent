from unittest.mock import AsyncMock, patch

import pytest

from src.tools.context import ToolExecutionContext, tool_execution_scope
from src.tools.video.submit_video_task_tool import SubmitVideoTaskTool
from src.video_request_context import VIDEO_REQUEST_DATA_KEY


@pytest.mark.asyncio
async def test_submit_video_uses_execution_context_identity():
    handler = AsyncMock(return_value={"task_id": "video-1"})
    with (
        patch(
            "src.video_agent.chat_integration.handle_user_message_via_chat",
            handler,
        ),
        tool_execution_scope(ToolExecutionContext(
            tenant_id="tenant-a",
            user_id="user-a",
            session_id="session-a",
            request_data={
                VIDEO_REQUEST_DATA_KEY: {
                    "mode": "agile",
                    "duration_sec": 10,
                    "ratio": "16:9",
                },
            },
        )),
    ):
        result = await SubmitVideoTaskTool().execute(
            user_input="生成产品视频",
            _trusted_tenant_id="tenant-b",
            _trusted_user_id="user-b",
            _video_params={"session_id": "session-b"},
        )

    assert result["success"] is True
    assert handler.await_args.kwargs["tenant_id"] == "tenant-a"
    assert handler.await_args.kwargs["user_id"] == "user-a"
    assert handler.await_args.kwargs["session_id"] == "session-a"
    assert handler.await_args.kwargs["mode"] == "agile"
    assert handler.await_args.kwargs["duration_sec"] == 10
    assert handler.await_args.kwargs["ratio"] == "16:9"


@pytest.mark.asyncio
async def test_submit_video_rejects_untrusted_session_without_context():
    with tool_execution_scope(None):
        result = await SubmitVideoTaskTool().execute(
            user_input="生成产品视频",
            _video_params={"session_id": "untrusted-session"},
        )

    assert result["success"] is False
    assert "session_id" in result["error"]


@pytest.mark.asyncio
async def test_submit_video_rejects_context_without_video_request_data():
    handler = AsyncMock(return_value={"task_id": "must-not-run"})
    with (
        patch(
            "src.video_agent.chat_integration.handle_user_message_via_chat",
            handler,
        ),
        tool_execution_scope(ToolExecutionContext(
            tenant_id="tenant-a",
            user_id="user-a",
            session_id="session-a",
        )),
    ):
        result = await SubmitVideoTaskTool().execute(
            user_input="生成产品视频",
            _video_params={"mode": "agile"},
        )

    assert result["success"] is False
    assert "视频创作请求上下文" in result["error"]
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_submit_video_ignores_forged_video_params_argument():
    handler = AsyncMock(return_value={"task_id": "video-2"})
    with (
        patch(
            "src.video_agent.chat_integration.handle_user_message_via_chat",
            handler,
        ),
        tool_execution_scope(ToolExecutionContext(
            tenant_id="tenant-a",
            user_id="user-a",
            session_id="session-a",
            request_data={VIDEO_REQUEST_DATA_KEY: {"mode": "refine"}},
        )),
    ):
        result = await SubmitVideoTaskTool().execute(
            user_input="生成产品视频",
            _video_params={"mode": "agile", "duration_sec": 99},
        )

    assert result["success"] is True
    assert handler.await_args.kwargs["mode"] == "refine"
    assert handler.await_args.kwargs["duration_sec"] == 5


@pytest.mark.asyncio
async def test_submit_video_does_not_log_request_content_or_identity():
    handler = AsyncMock(return_value={"task_id": "video-private"})
    with (
        patch(
            "src.video_agent.chat_integration.handle_user_message_via_chat",
            handler,
        ),
        patch("src.tools.video.submit_video_task_tool.logger.info") as info_log,
        patch("src.core.temp_logger.tlog") as temp_log,
        tool_execution_scope(ToolExecutionContext(
            tenant_id="tenant-private",
            user_id="user-private",
            session_id="session-private",
            request_data={
                VIDEO_REQUEST_DATA_KEY: {
                    "mode": "private-mode",
                    "duration_sec": 10,
                },
            },
        )),
    ):
        result = await SubmitVideoTaskTool().execute(
            user_input="private-video-request",
            image_file_ids=["image-private"],
        )

    assert result["success"] is True
    temp_log.assert_not_called()
    rendered_logs = " ".join(
        " ".join(str(value) for value in call.args)
        for call in info_log.call_args_list
    )
    assert "private-video-request" not in rendered_logs
    assert "user-private" not in rendered_logs
    assert "tenant-private" not in rendered_logs
    assert "session-private" not in rendered_logs
    assert "private-mode" not in rendered_logs
    assert "draft_only=False" in rendered_logs
    assert "images=1" in rendered_logs
