"""视频创作提交工具

video-agent 子智能体的"视频生成"入口。LLM 在 agent loop 中识别到用户要做视频创作时
调用此工具；工具从 `_video_params`（agent 注入的上下文）读取前端工具栏选择的参数，
调用 VideoChatService.handle_user_message 完成视频生成。

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


class SubmitVideoTaskTool(BaseTool):
    """视频创作提交工具（video-agent 子智能体专用）"""

    name = "submit_video_task"
    description = (
        "提交视频创作任务。根据用户需求和参考图片，结合前端工具栏选择的创作参数"
        "（模式/时长/比例/分辨率/抽卡数/提示词模型），调用提示词引擎生成结构化提示词"
        "后提交视频生成 API。"
    )
    usage_guide = (
        "当用户明确要求生成视频 / 创作视频时调用。"
        "创作参数由前端工具栏提供，你无需关心；只需把用户的文字需求和上传图片传入即可。"
    )
    display_name = "提交视频任务"
    category = "video"
    InputModel = SubmitVideoTaskInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        user_input: str = kwargs.get("user_input", "")
        image_file_ids: Optional[List[str]] = kwargs.get("image_file_ids")
        # 视频参数由 agent 注入（前端工具栏选择 → ChatRequest.video_params → _current_video_params → _video_params）
        video_params: Dict[str, Any] = kwargs.get("_video_params") or {}
        # 调用方上下文：tenant_id / user_id / session_id 由 trusted 注入或 agent 状态提供
        tenant_id: Optional[str] = kwargs.get("_trusted_tenant_id")
        user_id: Optional[str] = kwargs.get("_trusted_user_id")
        # session_id 没有专门的 trusted 注入；第一阶段从 video_params 中透传（前端 attach）
        session_id: Optional[str] = video_params.get("session_id")

        logger.info(
            f"[submit_video_task] 收到视频创作请求: user_input={user_input[:50]}, "
            f"images={len(image_file_ids or [])}, video_params={video_params}, user={user_id}"
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
            )
            # 附加 success 标记，便于 LLM 读取
            result["success"] = result.get("error") is None
            return result
        except Exception as e:
            logger.error(f"[submit_video_task] 视频创作失败: {e}", exc_info=True)
            return {
                "success": False,
                "error": "视频创作失败，请稍后重试",
                "debug": str(e)[:500],
            }
