"""视频创作智能体（video-agent）模块

承载 Phase 1 起新的「会话化视频创作」能力，与 src/video_gen/（MVP 原型）并列。
- db.py: 表初始化（asset_library / prompt_library）；视频库复用 work_outcomes，不新建
- prompt_engine.py: 提示词引擎（精修/敏捷双模）
- service.py: VideoChatService（会话化创作服务）
- chat_integration.py: 与主聊天循环的集成入口

设计依据：docs/plans/plan-video-agent-phase1.md
"""

from src.video_agent.db import init_video_agent_tables
from src.video_agent.prompt_engine import PromptEngine, PromptResult, get_prompt_engine
from src.video_agent.service import (
    VideoCard,
    VideoChatService,
    VideoGenParams,
    get_video_chat_service,
)

__all__ = [
    "init_video_agent_tables",
    "PromptEngine",
    "PromptResult",
    "get_prompt_engine",
    "VideoChatService",
    "VideoCard",
    "VideoGenParams",
    "get_video_chat_service",
]
