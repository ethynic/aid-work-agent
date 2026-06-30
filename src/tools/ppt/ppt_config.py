"""PPT 工具运行时配置。"""

import os
from dataclasses import dataclass


SUPPORTED_RENDERERS = {"pptxgenjs", "python_pptx"}


@dataclass(frozen=True)
class PPTConfig:
    renderer: str
    enable_html_export: bool
    qa_strict: bool


def get_ppt_config() -> PPTConfig:
    """从环境变量读取 PPT 工具开关。"""
    renderer = os.getenv("PPT_RENDERER", "python_pptx").strip().lower()
    if renderer not in SUPPORTED_RENDERERS:
        renderer = "python_pptx"

    return PPTConfig(
        renderer=renderer,
        enable_html_export=_env_bool("PPT_ENABLE_HTML_EXPORT", False),
        qa_strict=_env_bool("PPT_QA_STRICT", False),
    )


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}
