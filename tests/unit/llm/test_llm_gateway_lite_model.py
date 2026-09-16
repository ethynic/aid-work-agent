"""
LLMGateway.chat_lite 与 LLMConfig.get_lite_target 单测

覆盖 chat_lite 三种路由：
1. "provider/model" 跨 provider：指定即专用，构建独立 key_pool + provider 直连，不走主链路 failover
2. 纯模型名（同 provider）：走 self.chat 完整链路（含 failover），显式传 model 覆盖
3. 未配置：fallback 到主模型（同 provider 路径）

及 get_lite_target 解析边界：
- provider/model 合法解析
- 非法 provider 降级到主 provider
- 纯模型名 / 未配置 / 空字符串 fallback 主模型
"""

from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from src.config.settings import LLMConfig
from src.llm.gateway import LLMGateway


class _FakeKeyAcquire:
    """模拟 KeyPool.acquire() 返回的异步上下文管理器（yield 一个 api_key）"""

    def __init__(self, key: str):
        self._key = key

    async def __aenter__(self):
        return self._key

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeProvider:
    """模拟 Provider：async chat 返回固定响应"""

    def __init__(self, content="ok"):
        self.chat = AsyncMock(
            return_value={"content": content, "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
        )


@pytest.fixture(autouse=True)
def _patch_llm_env(monkeypatch):
    """所有用例统一：关闭 failover + mock _build_key_pool，构造/调用不依赖真实 Key 配置"""
    from src.config.settings import settings as _settings
    monkeypatch.setattr(_settings.llm.failover, "enabled", False)
    pool = MagicMock()
    pool.acquire.return_value = _FakeKeyAcquire("fake-key")
    with patch("src.llm.gateway._build_key_pool", return_value=pool) as mock_factory:
        yield mock_factory


class TestChatLite:
    """chat_lite 三种路由"""

    @pytest.mark.asyncio
    async def test_cross_provider_builds_independent_provider(self, _patch_llm_env):
        """provider/model 跨 provider：指定即专用，独立 key_pool + provider 直连"""
        fake_provider = _FakeProvider()
        with patch("src.llm.gateway._build_provider", return_value=fake_provider) as mock_build_provider, \
             patch.object(LLMConfig, "get_lite_target", return_value=("qwen", "qwen3.7-flash")):
            gw = LLMGateway(provider_name="deepseek")
            result = await gw.chat_lite(
                messages=[{"role": "user", "content": "hi"}],
                temperature=0.1,
                max_tokens=1024,
            )

        assert result["content"] == "ok"
        # 用 qwen 的 key 构建 qwen provider（指定即专用，不用主 provider 的 pool）
        provider_args = mock_build_provider.call_args.args
        assert provider_args[0] == "qwen"
        assert provider_args[1] == "fake-key"
        assert mock_build_provider.call_args.kwargs["model"] == "qwen3.7-flash"
        built_pools = [c.args[0] for c in _patch_llm_env.call_args_list]
        assert "qwen" in built_pools
        # 直接调 provider.chat，不经过 gw.chat（不参与主链路 failover）
        fake_provider.chat.assert_awaited_once()
        call_kwargs = fake_provider.chat.call_args.kwargs
        assert call_kwargs["messages"] == [{"role": "user", "content": "hi"}]
        assert call_kwargs["temperature"] == 0.1
        assert call_kwargs["max_tokens"] == 1024
        # qwen target 自动关思考（enable_thinking=False，三通道统一语义）
        assert call_kwargs["enable_thinking"] is False
        assert "thinking" not in call_kwargs

    @pytest.mark.asyncio
    async def test_same_provider_goes_through_self_chat(self):
        """纯模型名（同 provider）：走 self.chat 完整链路，显式传 model 覆盖"""
        gw = LLMGateway(provider_name="deepseek")
        with patch.object(LLMConfig, "get_lite_target", return_value=("deepseek", "deepseek-flash")), \
             patch.object(gw, "chat", new=AsyncMock(return_value={"content": "ok"})) as mock_chat:
            result = await gw.chat_lite(
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=1024,
            )

        assert result["content"] == "ok"
        mock_chat.assert_awaited_once()
        call_kwargs = mock_chat.call_args.kwargs
        assert call_kwargs["model"] == "deepseek-flash"
        assert call_kwargs["max_tokens"] == 1024
        # deepseek target 自动关思考（统一收口到 chat_lite）
        assert call_kwargs["thinking"] == {"type": "disabled"}

    @pytest.mark.asyncio
    async def test_zhipu_target_disables_thinking_via_effort(self, _patch_llm_env):
        """zhipu target 跨 provider 直连：reasoning_effort=low（GLM Flash 始终思考，low 档归零）"""
        fake_provider = _FakeProvider()
        with patch("src.llm.gateway._build_provider", return_value=fake_provider), \
             patch.object(LLMConfig, "get_lite_target", return_value=("zhipu", "GLM-5.3-Flash")):
            gw = LLMGateway(provider_name="deepseek")
            await gw.chat_lite(messages=[{"role": "user", "content": "hi"}], max_tokens=1024)

        call_kwargs = fake_provider.chat.call_args.kwargs
        assert call_kwargs["reasoning_effort"] == "low"

    @pytest.mark.asyncio
    async def test_same_provider_qwen_disables_thinking(self):
        """qwen 同 provider 路径：enable_thinking=False 经主链路传入"""
        gw = LLMGateway(provider_name="qwen")
        with patch.object(LLMConfig, "get_lite_target", return_value=("qwen", "qwen3.8-flash")), \
             patch.object(gw, "chat", new=AsyncMock(return_value={"content": "ok"})) as mock_chat:
            await gw.chat_lite(messages=[{"role": "user", "content": "hi"}], max_tokens=1024)

        call_kwargs = mock_chat.call_args.kwargs
        assert call_kwargs["enable_thinking"] is False
        assert call_kwargs["model"] == "qwen3.8-flash"

    @pytest.mark.asyncio
    async def test_unconfigured_falls_back_to_main_model(self):
        """未配置 lite_model：get_lite_target fallback 到主模型，同 provider 走 self.chat"""
        gw = LLMGateway(provider_name="deepseek")
        with patch.object(LLMConfig, "get_lite_target", return_value=("deepseek", "deepseek-v4-pro")), \
             patch.object(gw, "chat", new=AsyncMock(return_value={"content": "ok"})) as mock_chat:
            result = await gw.chat_lite(messages=[{"role": "user", "content": "hi"}])

        assert result["content"] == "ok"
        mock_chat.assert_awaited_once()
        assert mock_chat.call_args.kwargs["model"] == "deepseek-v4-pro"


class TestChatNoThinking:
    """chat_no_thinking：沿用主链路模型（不切 lite_model），仅关思考"""

    @pytest.mark.asyncio
    async def test_keeps_main_model_and_disables_thinking(self):
        """走 self.chat 完整链路，不传 model（沿用主模型），deepseek 关思考"""
        gw = LLMGateway(provider_name="deepseek")
        with patch.object(gw, "chat", new=AsyncMock(return_value={"content": "ok"})) as mock_chat:
            result = await gw.chat_no_thinking(
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=1024,
            )

        assert result["content"] == "ok"
        mock_chat.assert_awaited_once()
        call_kwargs = mock_chat.call_args.kwargs
        # 关键：不切模型（与 chat_lite 的区别），计费按主模型单价与实际消耗一致
        assert "model" not in call_kwargs
        assert call_kwargs["thinking"] == {"type": "disabled"}
        assert call_kwargs["max_tokens"] == 1024

    @pytest.mark.asyncio
    async def test_qwen_disables_thinking_and_drops_caller_model(self):
        """qwen 主链路：enable_thinking=False；调用方误传的 model 被丢弃"""
        gw = LLMGateway(provider_name="qwen")
        with patch.object(gw, "chat", new=AsyncMock(return_value={"content": "ok"})) as mock_chat:
            await gw.chat_no_thinking(
                messages=[{"role": "user", "content": "hi"}],
                model="qwen3.8-flash",  # 误传应被丢弃
            )

        call_kwargs = mock_chat.call_args.kwargs
        assert "model" not in call_kwargs
        assert call_kwargs["enable_thinking"] is False


class TestGetLiteTarget:
    """LLMConfig.get_lite_target 解析边界"""

    def _cfg(self, lite_model=None, provider="deepseek"):
        cfg = LLMConfig(provider=provider)
        cfg.lite_model = lite_model
        cfg.deepseek.model = "deepseek-flash"
        cfg.qwen.model = "qwen3.7-plus"
        return cfg

    def test_provider_model_parsed(self):
        cfg = self._cfg("qwen/qwen3.7-flash")
        assert cfg.get_lite_target() == ("qwen", "qwen3.7-flash")

    def test_provider_model_with_spaces(self):
        cfg = self._cfg("  qwen / qwen3.7-flash  ")
        assert cfg.get_lite_target() == ("qwen", "qwen3.7-flash")

    def test_invalid_provider_falls_back_to_current_provider(self):
        cfg = self._cfg("unknown/qwen3.7-flash")
        assert cfg.get_lite_target() == ("deepseek", "deepseek-flash")

    def test_empty_model_falls_back_to_current_provider(self):
        cfg = self._cfg("qwen/  ")
        assert cfg.get_lite_target() == ("deepseek", "deepseek-flash")

    def test_pure_model_name_uses_current_provider(self):
        cfg = self._cfg("deepseek-v4-pro")
        assert cfg.get_lite_target() == ("deepseek", "deepseek-v4-pro")

    def test_unconfigured_falls_back_to_main_model(self):
        cfg = self._cfg(None)
        assert cfg.get_lite_target() == ("deepseek", "deepseek-flash")

    def test_empty_string_falls_back_to_main_model(self):
        cfg = self._cfg("   ")
        assert cfg.get_lite_target() == ("deepseek", "deepseek-flash")
