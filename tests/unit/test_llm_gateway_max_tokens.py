"""LLM Gateway 按模型区分默认 max_tokens 单元测试

覆盖：
- LLMGateway._resolve_max_tokens：显式传值 / 模型在配置表 / 模型不在表回退默认
- qwen._clamp_max_tokens：上限从 config.yaml llm.model_max_tokens 读取
"""

from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml

from src.llm.gateway import DEFAULT_MAX_TOKENS, LLMGateway
from src.llm.providers.qwen import _clamp_max_tokens


def _new_gateway() -> LLMGateway:
    """绕过 __init__ 构造，避免拉起 provider/KeyPool 创建"""
    return LLMGateway.__new__(LLMGateway)


class TestResolveMaxTokens:
    @pytest.mark.parametrize("model,explicit,expected", [
        ("deepseek-v4-pro", None, 393216),
        ("deepseek-v4-pro", 4000, 4000),
        ("unknown-probe-model", None, DEFAULT_MAX_TOKENS),
    ])
    async def test_shipped_model_limits_reach_tool_call_provider(self, model, explicit, expected):
        """Pro reasoning must not hit the unknown-model 16K fallback before producing a tool call."""
        config_path = Path(__file__).resolve().parents[2] / "configs" / "config.yaml"
        limits = yaml.safe_load(config_path.read_text(encoding="utf-8"))["llm"]["model_max_tokens"]
        captured = {}

        @asynccontextmanager
        async def acquire():
            yield "test-key"

        async def provider_chat(**kwargs):
            captured.update(kwargs)
            return {"content": "ok", "finish_reason": "stop"}

        gw = _new_gateway()
        gw.provider_name = "deepseek"
        gw._model_codes = {"deepseek": model}
        gw._failover_enabled = False
        gw._key_pool = SimpleNamespace(acquire=acquire)
        provider = SimpleNamespace(model=model, chat=provider_chat)
        with patch("src.llm.gateway.settings") as mock_settings, \
             patch("src.llm.gateway._build_provider", return_value=provider):
            mock_settings.llm.model_max_tokens = limits
            await gw.chat_with_tools(
                [{"role": "user", "content": "continue research"}], tools=[], max_tokens=explicit,
            )
        assert captured["max_tokens"] == expected

    def test_explicit_value_returned(self):
        gw = _new_gateway()
        assert gw._resolve_max_tokens(4000) == 4000

    def test_uses_config_limit_for_known_model(self):
        gw = _new_gateway()
        with patch("src.llm.gateway.settings") as mock_settings:
            mock_settings.llm.model_max_tokens = {"qwen-vl-plus": 8192}
            with patch.object(gw, "get_model_name", return_value="qwen-vl-plus"):
                assert gw._resolve_max_tokens(None) == 8192

    def test_fallback_default_for_unknown_model(self):
        gw = _new_gateway()
        with patch("src.llm.gateway.settings") as mock_settings:
            mock_settings.llm.model_max_tokens = {}
            with patch.object(gw, "get_model_name", return_value="qwen3.7-flash"):
                assert gw._resolve_max_tokens(None) == DEFAULT_MAX_TOKENS


class TestChatLiteDefaultMaxTokens:
    """chat_lite 未显式指定 max_tokens 时用 DEFAULT_MAX_TOKENS 兜底，不查能力上限表

    背景（2026-10-12 事故）：推送循环 chat_lite 未指定 max_tokens，
    _resolve_max_tokens 查表返回 deepseek-flash 能力上限 393216，
    模型退化独白跑满上限，单次调用烧 177 积分。
    """

    def _gw(self) -> LLMGateway:
        gw = _new_gateway()
        gw.provider_name = "deepseek"
        return gw

    async def test_default_is_not_capability_limit(self):
        gw = self._gw()
        captured = {}

        async def fake_chat(**kwargs):
            captured.update(kwargs)
            return {"content": "ok"}

        with patch("src.config.settings.settings") as mock_settings, \
             patch.object(gw, "chat", side_effect=fake_chat):
            mock_settings.llm.get_lite_target.return_value = ("deepseek", "deepseek-flash")
            mock_settings.llm.model_max_tokens = {"deepseek-flash": 393216}
            await gw.chat_lite([{"role": "user", "content": "hi"}])
        assert captured["max_tokens"] == DEFAULT_MAX_TOKENS

    async def test_explicit_value_passed_through(self):
        gw = self._gw()
        captured = {}

        async def fake_chat(**kwargs):
            captured.update(kwargs)
            return {"content": "ok"}

        with patch("src.config.settings.settings") as mock_settings, \
             patch.object(gw, "chat", side_effect=fake_chat):
            mock_settings.llm.get_lite_target.return_value = ("deepseek", "deepseek-flash")
            await gw.chat_lite([{"role": "user", "content": "hi"}], max_tokens=8192)
        assert captured["max_tokens"] == 8192

    async def test_default_on_cross_provider_branch(self):
        import contextlib

        gw = _new_gateway()
        gw.provider_name = "deepseek"
        captured = {}

        async def fake_provider_chat(**kwargs):
            captured.update(kwargs)
            return {"content": "ok"}

        class _FakePool:
            def acquire(self):
                return contextlib.nullcontext("fake-key")

        with patch("src.config.settings.settings") as mock_settings, \
             patch("src.llm.gateway._build_key_pool", return_value=_FakePool()), \
             patch("src.llm.gateway._build_provider") as mock_build:
            mock_settings.llm.get_lite_target.return_value = ("zhipu", "GLM-5.3-Flash")
            mock_settings.llm.model_max_tokens = {"GLM-5.3-Flash": 131072}
            mock_build.return_value = type("P", (), {"chat": staticmethod(fake_provider_chat)})()
            await gw.chat_lite([{"role": "user", "content": "hi"}])
        assert captured["max_tokens"] == DEFAULT_MAX_TOKENS


class TestClampMaxTokens:
    def test_clamp_to_config_limit(self):
        with patch("src.llm.providers.qwen.settings") as mock_settings:
            mock_settings.llm.model_max_tokens = {"qwen-vl-plus": 8192}
            assert _clamp_max_tokens("qwen-vl-plus", 16384) == 8192

    def test_below_limit_keeps_value(self):
        with patch("src.llm.providers.qwen.settings") as mock_settings:
            mock_settings.llm.model_max_tokens = {"qwen-vl-plus": 8192}
            assert _clamp_max_tokens("qwen-vl-plus", 4096) == 4096

    def test_unknown_model_keeps_value(self):
        with patch("src.llm.providers.qwen.settings") as mock_settings:
            mock_settings.llm.model_max_tokens = {}
            assert _clamp_max_tokens("qwen3.7-flash", 16384) == 16384
