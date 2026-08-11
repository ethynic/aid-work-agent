"""LLM Gateway 按模型区分默认 max_tokens 单元测试

覆盖：
- LLMGateway._resolve_max_tokens：显式传值 / 模型在配置表 / 模型不在表回退默认
- qwen._clamp_max_tokens：上限从 config.yaml llm.model_max_tokens 读取
"""

from unittest.mock import patch

from src.llm.gateway import DEFAULT_MAX_TOKENS, LLMGateway
from src.llm.providers.qwen import _clamp_max_tokens


def _new_gateway() -> LLMGateway:
    """绕过 __init__ 构造，避免拉起 provider/KeyPool 创建"""
    return LLMGateway.__new__(LLMGateway)


class TestResolveMaxTokens:
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
            with patch.object(gw, "get_model_name", return_value="qwen-plus"):
                assert gw._resolve_max_tokens(None) == DEFAULT_MAX_TOKENS


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
            assert _clamp_max_tokens("qwen-plus", 16384) == 16384
