"""视频创作智能体与主聊天循环的集成层

设计依据：docs/plans/plan-video-agent-phase1.md §3.4

集成方式：
- video-agent 已通过 subagents/video-agent/SUBAGENT.md 自动注册到 SubagentRegistry
- 主智能体触发 delegate_to_subagent 时，SubagentExecutor 实例化子 Agent（is_master=False），
  并加载 video-agent 的 system_prompt 作为提示词
- 子 Agent 在执行过程中可通过工具调用 video_agent 服务（提交视频生成 / 留用 / 不喜欢）

第一阶段简化：不实现专门的 SSE 视频卡片推送（复用现有 message/images 事件），
视频生成结果通过工具返回值的 file_id 字段在聊天中以文件卡片形式展示。
后续 Phase 5 前端集成时，前端识别 source_type=video_gen 的 chat_records 渲染为视频卡片。

本模块对外暴露的接口：
- get_video_chat_service: 获取 VideoChatService 单例
- handle_user_message_via_chat: 给子 Agent 工具调用的统一入口

设计原则：本模块不修改 src/core/agent.py 的主循环，避免对主链路造成回归风险。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger

from src.video_agent.service import (
    VideoChatService,
    VideoCard,
    VideoGenParams,
    get_video_chat_service,
)


async def handle_user_message_via_chat(
    session_id: str,
    tenant_id: Optional[str],
    user_id: Optional[str],
    user_input: str,
    image_file_ids: Optional[List[str]] = None,
    mode: str = "refine",
    duration_sec: int = 5,
    ratio: str = "9:16",
    resolution: str = "720P",
    card_count: int = 1,
    prompt_model: Optional[str] = None,
) -> Dict[str, Any]:
    """子 Agent 工具调用入口：处理用户视频创作消息

    Args:
        session_id: 会话 ID
        tenant_id: 租户 ID
        user_id: 用户 ID
        user_input: 用户中文需求描述
        image_file_ids: 用户上传的参考图片 file_id 列表
        mode: 创作模式（refine / agile）
        duration_sec: 视频时长（秒）
        ratio: 视频比例（9:16 / 16:9 / 1:1 / 4:3 / 3:4）
        resolution: 分辨率（720P / 1080P / 768P / 2K）
        card_count: 敏捷模式生成条数（1-3，精修模式固定 1）
        prompt_model: 提示词模型（覆盖 SUBAGENT.md 默认值），如 'qwen-vl-max'

    Returns:
        VideoChatService.handle_user_message 的返回值（dict）
    """
    service = get_video_chat_service(prompt_model_code=prompt_model)
    params = VideoGenParams(
        mode=mode,
        duration_sec=duration_sec,
        ratio=ratio,
        resolution=resolution,
        card_count=card_count,
        prompt_model=prompt_model,
    )
    logger.info(
        f"[chat_integration] 处理视频创作消息: session={session_id}, mode={mode}, "
        f"prompt_model={prompt_model}, images={len(image_file_ids or [])}, user={user_id}"
    )
    return await service.handle_user_message(
        session_id=session_id,
        tenant_id=tenant_id,
        user_id=user_id,
        user_input=user_input,
        image_file_ids=image_file_ids,
        params=params,
    )


def keep_video_via_chat(
    session_id: str,
    tenant_id: str,
    user_id: str,
    card_id: str,
    video_file_id: str,
    video_display_name: str,
    save_prompt_to_library: bool = True,
) -> Dict[str, Any]:
    """子 Agent 工具调用入口：留用视频

    注：card_id 对应的 VideoCard 由调用方在前一次 handle_user_message 返回中保存，
    本函数通过 service 内部状态查询；第一阶段简化为不维护内存状态，由调用方提供完整 card。
    本函数保留接口，实际调用方应直接调 service.keep_video(card=...)。
    """
    raise NotImplementedError(
        "请直接调用 VideoChatService.keep_video(card=VideoCard(...))；"
        "card_id 仅用于前端展示，后端不维护 card 内存状态"
    )


__all__ = [
    "handle_user_message_via_chat",
    "keep_video_via_chat",
    "get_video_chat_service",
    "VideoChatService",
    "VideoCard",
    "VideoGenParams",
]
