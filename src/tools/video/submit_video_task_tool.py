"""视频创作提交工具

video-agent 子智能体的"视频生成"入口。LLM 在 agent loop 中识别到用户要做视频创作时
调用此工具；工具从请求级工具执行上下文读取前端工具栏选择的参数，调用
VideoChatService.handle_user_message 完成视频生成。

精修模式两阶段调用（参考 subagents/video-agent/SUBAGENT.md）：
- 阶段一/二（draft_only=True）：生成提示词草稿，存 Redis，返回草稿 Markdown，不提交视频
- 阶段三（draft_only=False）：优先用 Redis 草稿提交视频生成 API，无草稿则重新生成

设计依据：docs/plans/plan-video-agent-phase1.md §3.4
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger
from pydantic import BaseModel, Field

from src.tools.base import BaseTool


class SubmitVideoTaskInput(BaseModel):
    """视频创作提交参数"""
    user_input: str = Field(..., description="用户的视频创作需求描述（中文）")
    image_file_ids: Optional[List[str]] = Field(
        None,
        description="用户上传的参考图片 file_id 列表（产品图 / 模特图），可为空",
    )
    draft_only: bool = Field(
        False,
        description=(
            "True=只生成提示词草稿不提交视频生成（精修模式阶段一/二，预览草稿等用户确认）；"
            "False=提交视频生成任务（精修模式阶段三，优先用上一轮确认的草稿提交）。"
            "敏捷模式固定 False"
        ),
    )


class SubmitVideoTaskTool(BaseTool):
    """视频创作提交工具（video-agent 子智能体专用）"""

    name = "submit_video_task"
    description = (
        "提交视频创作任务。根据用户需求和参考图片，结合前端工具栏选择的创作参数"
        "（模式/时长/比例/分辨率/抽卡数/提示词模型），调用提示词引擎生成结构化提示词"
        "后提交视频生成 API。精修模式支持两阶段调用：draft_only=True 只生成草稿供用户预览，"
        "draft_only=False 提交视频生成（优先用上一轮草稿）。"
    )
    usage_guide = (
        "精修模式：用户提需求/调整意见时调用 draft_only=True 生成草稿；用户明确确认后才调用 draft_only=False 提交视频。"
        "敏捷模式：直接调用 draft_only=False 提交 N 条。"
        "创作参数由前端工具栏提供，你无需关心；只需把用户的文字需求和上传图片传入即可。"
    )
    display_name = "提交视频任务"
    category = "video"
    InputModel = SubmitVideoTaskInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        user_input: str = kwargs.get("user_input", "")
        image_file_ids: Optional[List[str]] = kwargs.get("image_file_ids")
        draft_only: bool = bool(kwargs.get("draft_only", False))
        from src.tools.context import current_tool_execution_context
        from src.video_request_context import VIDEO_REQUEST_DATA_KEY

        context = current_tool_execution_context()
        tenant_id: Optional[str] = context.tenant_id if context else None
        user_id: Optional[str] = context.user_id if context else None
        session_id: Optional[str] = context.session_id if context else None
        # 身份、会话和视频业务参数都只读可信边界构造的不可变上下文。
        # kwargs 中即使伪造同名内部参数也不会生效。
        video_params = (
            context.request_data.get(VIDEO_REQUEST_DATA_KEY, {})
            if context else {}
        )

        logger.info(
            f"[submit_video_task] 收到视频创作请求: draft_only={draft_only}, "
            f"images={len(image_file_ids or [])}"
        )

        if not user_input.strip():
            return {
                "success": False,
                "error": "视频创作需求不能为空，请描述想要的视频内容",
            }
        if not session_id:
            return {
                "success": False,
                "error": "缺少会话上下文（session_id），无法创建视频任务",
            }
        if VIDEO_REQUEST_DATA_KEY not in context.request_data:
            return {
                "success": False,
                "error": "缺少视频创作请求上下文，无法创建视频任务",
            }

        try:
            from src.video_agent.chat_integration import handle_user_message_via_chat

            result = await handle_user_message_via_chat(
                session_id=session_id,
                tenant_id=tenant_id,
                user_id=user_id,
                user_input=user_input,
                image_file_ids=image_file_ids,
                mode=video_params.get("mode", "refine"),
                duration_sec=int(video_params.get("duration_sec", 5)),
                ratio=video_params.get("ratio", "9:16"),
                resolution=video_params.get("resolution", "720P"),
                card_count=int(video_params.get("card_count", 1)),
                prompt_model=video_params.get("prompt_model"),
                draft_only=draft_only,
            )
            # 附加 success 标记，便于 LLM 读取
            result["success"] = result.get("error") is None
            return result
        except Exception as e:
            logger.opt(exception=True).error(f"[submit_video_task] 视频创作失败: {e}")
            return {
                "success": False,
                "error": "视频创作失败，请稍后重试",
                "debug": str(e)[:500],
            }
