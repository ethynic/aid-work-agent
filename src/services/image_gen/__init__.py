"""DashScope 图像生成/编辑（qwen-image-edit 系列）通用服务。

平台级出图能力（2026-11-06 产品效果图助手 interior-render 整合）：
- 异步任务模式：提交（X-DashScope-Async: enable）+ 轮询 /api/v1/tasks/{task_id}，
  与 src/video_gen/wanx_provider.py 同构；复用 QWEN_API_KEYS（同百炼账号通用）。
- 模型可配置（configs/config.yaml image_gen.dashscope.model），默认 qwen-image-edit，
  支持参考图 edit（1-3 张，base64 data URL 直传）与纯文生图。
- 生成图立即下载注册到租户目录（ImageRegistry），不留 DashScope 24h 有效期外链。
"""
from typing import Any


def __getattr__(name: str) -> Any:
    # 惰性导出，避免 import 本包即拉起 settings/db 链
    if name == "get_image_gen_service":
        from .service import get_image_gen_service
        return get_image_gen_service
    if name in ("ImageGenRequest", "ImageGenProviderError"):
        from . import base as _base
        return getattr(_base, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["get_image_gen_service", "ImageGenRequest", "ImageGenProviderError"]
