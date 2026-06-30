"""PPT 工具运行时配置。"""

import os
from dataclasses import dataclass


SUPPORTED_RENDERERS = {"pptxgenjs", "python_pptx"}


@dataclass(frozen=True)
class PPTConfig:
    renderer: str
    renderer_fallback: bool
    enable_html_export: bool
    qa_strict: bool
    html_viewport_width: int
    html_viewport_height: int
    html_image_format: str
    html_jpeg_quality: int
    html_timeout_ms: int


def get_ppt_config() -> PPTConfig:
    """从环境变量读取 PPT 工具开关。"""
    renderer = os.getenv("PPT_RENDERER", "pptxgenjs").strip().lower()
    if renderer not in SUPPORTED_RENDERERS:
        renderer = "python_pptx"

    return PPTConfig(
        renderer=renderer,
        renderer_fallback=_env_bool("PPT_RENDERER_FALLBACK", True),
        enable_html_export=_env_bool("PPT_ENABLE_HTML_EXPORT", False),
        qa_strict=_env_bool("PPT_QA_STRICT", False),
        html_viewport_width=_env_int("PPT_HTML_VIEWPORT_WIDTH", 1920, 320, 7680),
        html_viewport_height=_env_int("PPT_HTML_VIEWPORT_HEIGHT", 1080, 240, 4320),
        html_image_format=_env_choice("PPT_HTML_IMAGE_FORMAT", "png", {"png", "jpeg"}),
        html_jpeg_quality=_env_int("PPT_HTML_JPEG_QUALITY", 90, 1, 100),
        html_timeout_ms=_env_int("PPT_HTML_TIMEOUT_MS", 30000, 1000, 120000),
    )


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return min(max(value, minimum), maximum)


def _env_choice(name: str, default: str, choices: set[str]) -> str:
    value = os.getenv(name, default).strip().lower()
    return value if value in choices else default
