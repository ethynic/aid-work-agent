"""视频创作 API 参数到通用 Agent 请求上下文的适配器。"""

from __future__ import annotations

from html import escape
from typing import Any, Mapping, Optional

from src.core.request_context import AgentRequestContext


VIDEO_REQUEST_DATA_KEY = "video_params"
VIDEO_AGENT_ID = "video-agent"
_VIDEO_MODES = frozenset({"refine", "agile"})
_VIDEO_DURATIONS = frozenset({5, 10, 15})
_VIDEO_RATIOS = frozenset({
    "9:16", "16:9", "1:1", "4:3", "3:4", "21:9", "adaptive", "auto",
})
_VIDEO_RESOLUTIONS = frozenset({"720P", "1080P", "768P", "2K"})


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    """容错解析前端整数，避免畸形业务参数在 SSE 建立前抛出异常。"""
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return parsed if minimum <= parsed <= maximum else default


def _allowed_value(value: Any, *, allowed: frozenset, default: Any) -> Any:
    try:
        return value if value in allowed else default
    except TypeError:
        return default


def _safe_prompt_value(value: Any) -> str:
    """把业务枚举显示为单行 XML 文本，避免字段值破坏提示块边界。"""
    return escape(str(value).replace("\r", " ").replace("\n", " "), quote=True)


def _format_video_params_for_llm(video_params: Mapping[str, Any]) -> str:
    """保持既有前端参数提示文案契约。"""
    mode = video_params.get("mode", "refine")
    mode_label = "精修（refine）" if mode == "refine" else "敏捷（agile）"
    duration = video_params.get("duration_sec", 5)
    ratio = _safe_prompt_value(video_params.get("ratio", "9:16"))
    resolution = _safe_prompt_value(video_params.get("resolution", "720P"))
    card_count = video_params.get("card_count", 1)
    return (
        "<video-params>\n"
        "用户已通过前端「视频生成参数」面板指定本次视频创作参数，请直接遵循，"
        "除非用户明确表示要修改，否则不要向用户重复询问以下信息：\n"
        f"- 创作模式：{mode_label}\n"
        f"- 视频时长：{duration} 秒\n"
        f"- 视频比例：{ratio}\n"
        f"- 分辨率：{resolution}\n"
        f"- 生成条数：{card_count} 条\n"
        "</video-params>"
    )


def build_video_agent_request_context(
    video_params: Optional[Mapping[str, Any]],
    *,
    agent: Any,
) -> Optional[AgentRequestContext]:
    """仅按实际路由到的视频子智能体构造上下文；其他 Agent 忽略参数。"""
    config = getattr(agent, "subagent_config", None)
    if getattr(config, "dir_name", None) != VIDEO_AGENT_ID or not video_params:
        return None
    normalized = dict(video_params)
    normalized.update({
        "mode": _allowed_value(
            video_params.get("mode", "refine"),
            allowed=_VIDEO_MODES,
            default="refine",
        ),
        "duration_sec": _allowed_value(
            _bounded_int(
                video_params.get("duration_sec", 5),
                default=5,
                minimum=1,
                maximum=15,
            ),
            allowed=_VIDEO_DURATIONS,
            default=5,
        ),
        "ratio": _allowed_value(
            video_params.get("ratio", "9:16"),
            allowed=_VIDEO_RATIOS,
            default="9:16",
        ),
        "resolution": _allowed_value(
            video_params.get("resolution", "720P"),
            allowed=_VIDEO_RESOLUTIONS,
            default="720P",
        ),
        "card_count": _bounded_int(
            video_params.get("card_count", 1),
            default=1,
            minimum=1,
            maximum=3,
        ),
        "prompt_model": video_params.get("prompt_model"),
    })
    return AgentRequestContext(
        prompt_augmentations=(_format_video_params_for_llm(normalized),),
        request_data={VIDEO_REQUEST_DATA_KEY: normalized},
    )
