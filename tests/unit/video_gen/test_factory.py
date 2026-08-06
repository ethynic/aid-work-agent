"""视频生成 Provider Factory 单元测试。

验证 build_provider 根据 cfg.provider 正确构造对应 provider 实例。
"""

from __future__ import annotations

import pytest

from src.video_gen.base import BaseVideoProvider
from src.video_gen.factory import build_provider
from src.video_gen.minimax_provider import MiniMaxProvider
from src.video_gen.wanx_provider import WanxProvider, WanxProviderError

pytestmark = [pytest.mark.unit]


def _wanx_cfg(**overrides):
    """构造 wanx provider 的 VideoGenConfig-like 对象。"""
    from dataclasses import dataclass
    from typing import Optional

    @dataclass
    class _W:
        api_key: str = ""
        model: str = "wan2.7-r2v-2026-06-12"

    @dataclass
    class _M:
        api_key: str = ""
        model: str = "MiniMax-H3"
        base_url: str = "https://api.minimaxi.com"

    @dataclass
    class _Cfg:
        provider: str = "wanx"
        wanx: _W = None
        minimax: _M = None

    w = _W(api_key=overrides.get("wanx_api_key", ""), model=overrides.get("wanx_model", "wan2.7-r2v-2026-06-12"))
    m = _M(api_key=overrides.get("minimax_api_key", ""), model=overrides.get("minimax_model", "MiniMax-H3"))
    return _Cfg(provider=overrides.get("provider", "wanx"), wanx=w, minimax=m)


class TestBuildProvider:
    def test_wanx_with_explicit_api_key(self):
        cfg = _wanx_cfg(provider="wanx", wanx_api_key="sk-wanx")
        p = build_provider(cfg, qwen_api_keys=None)
        assert isinstance(p, WanxProvider)
        assert p.name == "wanx"

    def test_wanx_fallback_to_qwen_keys(self):
        """wanx api_key 空时，回退到 qwen_api_keys[0]（同百炼账号通用）。"""
        cfg = _wanx_cfg(provider="wanx", wanx_api_key="")
        p = build_provider(cfg, qwen_api_keys=["sk-qwen", "sk-qwen2"])
        assert isinstance(p, WanxProvider)

    def test_wanx_no_api_key_raises(self):
        """wanx 既无 api_key 又无 qwen_api_keys 应抛 WanxProviderError。"""
        cfg = _wanx_cfg(provider="wanx", wanx_api_key="")
        with pytest.raises(WanxProviderError):
            build_provider(cfg, qwen_api_keys=None)

    def test_minimax(self):
        cfg = _wanx_cfg(provider="minimax", minimax_api_key="sk-mm")
        p = build_provider(cfg, qwen_api_keys=None)
        assert isinstance(p, MiniMaxProvider)
        assert p.name == "minimax"

    def test_minimax_missing_api_key_raises(self):
        """MiniMax api_key 必须显式配置，不回退 qwen_api_keys。"""
        from src.video_gen.minimax_provider import MiniMaxProviderError
        cfg = _wanx_cfg(provider="minimax", minimax_api_key="")
        with pytest.raises(MiniMaxProviderError):
            build_provider(cfg, qwen_api_keys=["sk-qwen"])

    def test_invalid_provider_raises_value_error(self):
        cfg = _wanx_cfg(provider="unknown")
        with pytest.raises(ValueError, match="未知"):
            build_provider(cfg)

    def test_returns_base_provider(self):
        """返回值应实现 BaseVideoProvider ABC。"""
        cfg = _wanx_cfg(provider="wanx", wanx_api_key="sk-x")
        p = build_provider(cfg, qwen_api_keys=None)
        assert isinstance(p, BaseVideoProvider)
