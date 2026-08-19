"""qwen3.7-flash enable_thinking / 显式缓存开关单元测试

覆盖（plan-qwen3-7-flash-context-cache-optimization.md 测试方案）：
- _format_messages 缓存开：最后一条消息转数组 + cache_control，首条 system 保持字符串 content
- _format_messages 缓存关：全部走原逻辑（字符串 content）
- 仅最后一条消息被标记（末尾标记覆盖整个消息数组）
- chat() enable_thinking true / false / None 三态，request_body 正确写/不写该键
- 非 qwen 系 model（百炼第三方）即便 context_cache=True 也不加 cache_control，且 enable_thinking 不写入
"""

from unittest.mock import AsyncMock, MagicMock, patch

from src.llm.providers.base import BaseLLMProvider
from src.llm.providers.qwen import QwenProvider


class _StubProvider(BaseLLMProvider):
    """去掉抽象方法的测试桩，直接测 _format_messages"""
    async def chat(self, *args, **kwargs):
        raise NotImplementedError

    async def stream_chat(self, *args, **kwargs):
        raise NotImplementedError


def _mock_response(content: str = "hi") -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = {
        "id": "req-1",
        "choices": [{
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }
    return resp


class TestFormatMessagesCache:
    def test_cache_on_last_message_becomes_array(self):
        # 缓存开：最后一条消息（user）被转数组 + cache_control，首条 system 保持字符串 content
        p = _StubProvider(api_key="k", model="qwen3.7-flash")
        messages = [
            {"role": "system", "content": "你是助手"},
            {"role": "user", "content": "你好"},
        ]
        formatted = p._format_messages(messages, use_cache=True)
        # 首条 system 保持字符串 content，不被数组化
        assert formatted[0] == {"role": "system", "content": "你是助手"}
        # 最后一条消息被转数组 + cache_control
        assert formatted[1]["role"] == "user"
        assert isinstance(formatted[1]["content"], list)
        assert formatted[1]["content"][0]["type"] == "text"
        assert formatted[1]["content"][0]["text"] == "你好"
        assert formatted[1]["content"][0]["cache_control"] == {"type": "ephemeral"}

    def test_cache_off_all_string_content(self):
        p = _StubProvider(api_key="k", model="qwen3.7-flash")
        messages = [
            {"role": "system", "content": "你是助手"},
            {"role": "user", "content": "你好"},
        ]
        formatted = p._format_messages(messages, use_cache=False)
        assert formatted == [
            {"role": "system", "content": "你是助手"},
            {"role": "user", "content": "你好"},
        ]

    def test_only_last_message_cached(self):
        # 仅最后一条消息被标记（末尾标记覆盖整个消息数组），首条 user 保持字符串 content
        p = _StubProvider(api_key="k", model="qwen3.7-flash")
        messages = [
            {"role": "user", "content": "你好"},
            {"role": "system", "content": "第二条 system"},
        ]
        formatted = p._format_messages(messages, use_cache=True)
        assert formatted[0] == {"role": "user", "content": "你好"}
        assert formatted[1]["role"] == "system"
        assert isinstance(formatted[1]["content"], list)
        assert formatted[1]["content"][0]["text"] == "第二条 system"
        assert formatted[1]["content"][0]["cache_control"] == {"type": "ephemeral"}

    def test_cache_empty_messages_returns_empty(self):
        # 空消息列表：末尾标记逻辑的 `if formatted` 守卫，返回 [] 不崩
        p = _StubProvider(api_key="k", model="qwen3.7-flash")
        formatted = p._format_messages([], use_cache=True)
        assert formatted == []

    def test_cache_on_list_content_appends_to_last_block(self):
        # 最后一条已是 list content（多模态/已数组化）：对最后一个 content 块追加
        # cache_control，且不覆盖原字段
        p = _StubProvider(api_key="k", model="qwen3.7-flash")
        messages = [
            {"role": "system", "content": "你是助手"},
            {"role": "user", "content": [
                {"type": "text", "text": "看图说话"},
                {"type": "image_url", "image_url": {"url": "http://x/img.png"}},
            ]},
        ]
        formatted = p._format_messages(messages, use_cache=True)
        assert formatted[0] == {"role": "system", "content": "你是助手"}
        last_content = formatted[1]["content"]
        assert isinstance(last_content, list) and len(last_content) == 2
        # 最后一个 content 块追加 cache_control，原字段保留
        assert last_content[-1]["type"] == "image_url"
        assert last_content[-1]["image_url"] == {"url": "http://x/img.png"}
        assert last_content[-1]["cache_control"] == {"type": "ephemeral"}
        # 前一个 content 块不受影响
        assert "cache_control" not in last_content[0]

    def test_cache_on_agent_loop_tool_result(self):
        # 模拟 agent 循环：system + user + assistant tool_calls + tool 结果。
        # 关键约束：tool 消息 content 必须保持纯字符串（OpenAI 兼容规范），
        # 若把 tool 消息转成 content 数组，qwen3.7-flash 会把 tool_result 回显为文本。
        # 因此缓存标记要跳过 tool / assistant(tool_calls)，落在前面的 user 消息上
        p = _StubProvider(api_key="k", model="qwen3.7-flash")
        messages = [
            {"role": "system", "content": "你是助手"},
            {"role": "user", "content": "查一下天气"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": "weather", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "call_1", "content": {"city": "北京", "temp": 25}},
        ]
        formatted = p._format_messages(messages, use_cache=True)
        # 首条 system 保持字符串 content
        assert formatted[0] == {"role": "system", "content": "你是助手"}
        # assistant tool_calls 消息 content 保持字符串（空串），不带 cache_control
        assert formatted[2]["role"] == "assistant"
        assert "tool_calls" in formatted[2]
        assert formatted[2]["content"] == ""
        # tool 消息 content 保持纯字符串，绝不能被数组化
        last = formatted[-1]
        assert last["role"] == "tool"
        assert last["tool_call_id"] == "call_1"
        assert last["content"] == '{"city": "北京", "temp": 25}'
        # 缓存标记落在前面的 user 消息"查一下天气"上
        assert formatted[1]["role"] == "user"
        assert isinstance(formatted[1]["content"], list)
        assert formatted[1]["content"][0]["text"] == "查一下天气"
        assert formatted[1]["content"][0]["cache_control"] == {"type": "ephemeral"}

    def test_non_qwen_model_not_cached(self):
        # 百炼第三方模型（deepseek 前缀）即便 use_cache=True 也不加 cache_control
        p = _StubProvider(api_key="k", model="deepseek-v4-flash")
        messages = [{"role": "system", "content": "你是助手"}]
        formatted = p._format_messages(messages, use_cache=True)
        assert formatted == [{"role": "system", "content": "你是助手"}]

    def test_vl_model_not_cached(self):
        # 视觉模型（qwen-vl-plus，视频提示词默认）不在显式缓存支持列表，
        # 即便 use_cache=True 也不加 cache_control，避免 400
        for model in ("qwen-vl-plus", "qwen-vl-max", "qwen3-vl-flash"):
            p = _StubProvider(api_key="k", model=model)
            messages = [{"role": "system", "content": "你是助手"}]
            formatted = p._format_messages(messages, use_cache=True)
            assert formatted == [{"role": "system", "content": "你是助手"}], model


class TestChatEnableThinking:
    @staticmethod
    async def _run_chat(model: str, enable_thinking, context_cache: bool = True):
        provider = QwenProvider(api_key="test-key", model=model)
        with patch("src.llm.providers.qwen.settings") as mock_settings, \
             patch("src.llm.providers.qwen.log_llm_invoke") as mock_log, \
             patch("httpx.AsyncClient") as MockClient:
            mock_settings.llm.enable_thinking = enable_thinking
            mock_settings.llm.context_cache = context_cache
            mock_settings.llm.model_max_tokens = {}
            client_instance = MockClient.return_value.__aenter__.return_value
            client_instance.post = AsyncMock(return_value=_mock_response())
            await provider.chat([
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "hi"},
            ])
            call = client_instance.post.call_args
            return call.kwargs["json"] if call.kwargs else call[0][1]

    async def test_enable_thinking_true(self):
        body = await self._run_chat("qwen3.7-flash", True)
        assert body["enable_thinking"] is True

    async def test_enable_thinking_false(self):
        body = await self._run_chat("qwen3.7-flash", False)
        assert body["enable_thinking"] is False

    async def test_enable_thinking_none_not_written(self):
        body = await self._run_chat("qwen3.7-flash", None)
        assert "enable_thinking" not in body

    async def test_context_cache_wired_to_format_messages(self):
        # 缓存开关开启时，qwen 系模型最后一条消息应为数组 + cache_control
        body = await self._run_chat("qwen3.7-flash", False, context_cache=True)
        last = body["messages"][-1]
        assert last["role"] == "user"
        assert isinstance(last["content"], list)
        assert last["content"][0]["cache_control"] == {"type": "ephemeral"}

    async def test_context_cache_disabled_plain_string(self):
        body = await self._run_chat("qwen3.7-flash", False, context_cache=False)
        assert body["messages"][0] == {"role": "system", "content": "sys"}

    async def test_non_qwen_model_no_thinking_no_cache(self):
        # 百炼第三方模型（deepseek 前缀）：context_cache=True 但 model 非 qwen 系，
        # 不写 enable_thinking、不加 cache_control
        body = await self._run_chat("deepseek-v4-flash", False, context_cache=True)
        assert "enable_thinking" not in body
        assert body["messages"][0] == {"role": "system", "content": "sys"}

    async def test_vl_model_no_thinking_no_cache(self):
        # 视觉模型（qwen-vl-plus，视频提示词默认）：虽为 qwen 前缀，但无思考模式且
        # 不在显式缓存支持列表，不写 enable_thinking、不加 cache_control（防 400）
        body = await self._run_chat("qwen-vl-plus", False, context_cache=True)
        assert "enable_thinking" not in body
        assert body["messages"][0] == {"role": "system", "content": "sys"}


class TestParseResponseCacheCreation:
    """_parse_response 需暴露 cache_creation_tokens，供计费端按输入价 125% 核算"""

    def test_parse_response_exposes_cache_creation(self):
        provider = QwenProvider(api_key="k", model="qwen3.7-flash")
        raw = {
            "id": "req-1",
            "choices": [{"message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": 1000,
                "completion_tokens": 5,
                "total_tokens": 1005,
                "prompt_tokens_details": {
                    "cached_tokens": 400,
                    "cache_creation_input_tokens": 200,
                },
            },
        }
        parsed = provider._parse_response(raw)
        assert parsed["usage"]["cached_tokens"] == 400
        assert parsed["usage"]["cache_creation_tokens"] == 200

    def test_parse_response_cache_creation_absent_defaults_zero(self):
        # 无 prompt_tokens_details / 无显式缓存创建字段时默认为 0，不崩
        provider = QwenProvider(api_key="k", model="qwen3.7-flash")
        raw = {
            "id": "req-1",
            "choices": [{"message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        parsed = provider._parse_response(raw)
        assert parsed["usage"]["cache_creation_tokens"] == 0
        assert parsed["usage"]["cached_tokens"] == 0

    def test_parse_response_top_level_cache_creation_fallback(self):
        # 兼容 API 直接把 cache_creation_input_tokens 放在 usage 顶层（非 details）的情况
        provider = QwenProvider(api_key="k", model="qwen3.7-flash")
        raw = {
            "id": "req-1",
            "choices": [{"message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
                      "cache_creation_input_tokens": 30},
        }
        parsed = provider._parse_response(raw)
        assert parsed["usage"]["cache_creation_tokens"] == 30
