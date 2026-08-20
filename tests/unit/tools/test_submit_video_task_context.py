from unittest.mock import AsyncMock, patch

import pytest

from src.tools.context import ToolExecutionContext, tool_execution_scope
from src.tools.video.submit_video_task_tool import SubmitVideoTaskTool


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


@pytest.mark.asyncio
async def test_submit_video_rejects_untrusted_session_without_context():
    with tool_execution_scope(None):
        result = await SubmitVideoTaskTool().execute(
            user_input="生成产品视频",
            _video_params={"session_id": "untrusted-session"},
        )

    assert result["success"] is False
    assert "session_id" in result["error"]
