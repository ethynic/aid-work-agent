"""视频生成 Provider 工厂。

根据 settings.video_gen.provider 构造对应 provider 实例。独立 factory 便于后续扩展第 3 个
provider，也便于测试（不直接读 settings，api_keys 由调用方注入）。
"""
from __future__ import annotations

from src.video_gen.base import BaseVideoProvider
from src.video_gen.minimax_provider import MiniMaxProvider
from src.video_gen.wanx_provider import WanxProvider


def build_provider(cfg, qwen_api_keys: list[str] | None = None) -> BaseVideoProvider:
    """根据 cfg.provider 构造 provider 实例。

    - wanx：api_key 优先读 cfg.wanx.api_key，空则回退 qwen_api_keys[0]（同百炼账号通用）
    - minimax：必须读 cfg.minimax.api_key（与万相独立）
    - 其它：抛 ValueError
    """
    if cfg.provider == "wanx":
        api_key = cfg.wanx.api_key or (qwen_api_keys[0] if qwen_api_keys else "")
        return WanxProvider(api_key=api_key, model=cfg.wanx.model)
    if cfg.provider == "minimax":
        return MiniMaxProvider(api_key=cfg.minimax.api_key, model=cfg.minimax.model)
    raise ValueError(f"未知 video_gen.provider: {cfg.provider}")
