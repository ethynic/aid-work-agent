"""MoonshotProvider（Kimi）单测。

覆盖 weixin-cli 视觉定位走服务端代理的关键差异点
（docs/design/weixin/weixin-cli-billing.md §4.1）：
- 默认 base_url 指向 api.moonshot.cn
- kimi 推理模型（kimi-k3）请求体省略 temperature（官方要求必须为 1 或省略）
- 推理模型 content 为空时回退 reasoning_content
"""

from src.llm.providers.moonshot import MoonshotProvider


def _make_provider(**kwargs) -> MoonshotProvider:
    return MoonshotProvider(api_key="sk-test", model="kimi-k3", **kwargs)


def test_default_base_url_is_moonshot():
    """默认端点为 Moonshot 官方 OpenAI 兼容地址。"""
    provider = _make_provider()
    assert provider.api_url == "https://api.moonshot.cn/v1/chat/completions"
    assert provider.PROVIDER_NAME == "moonshot"


def test_kimi_model_omits_temperature():
    """kimi 系模型：调用方 temperature 被省略（kimi-k3 要求 temperature 为 1 或省略）。"""
    provider = _make_provider()
    body = {"model": "kimi-k3", "temperature": 0, "max_tokens": 4096}
    provider._adjust_request_body(body)
    assert "temperature" not in body


def test_non_kimi_model_keeps_temperature():
    """非 kimi 前缀模型（防御性分支）：temperature 保留。"""
    provider = MoonshotProvider(api_key="sk-test", model="moonshot-v1-8k")
    body = {"model": "moonshot-v1-8k", "temperature": 0.3}
    provider._adjust_request_body(body)
    assert body["temperature"] == 0.3


def test_parse_response_falls_back_to_reasoning_content():
    """推理模型 content 为空时回退 reasoning_content（与驱动直调兜底行为一致）。"""
    provider = _make_provider()
    resp = {
        "id": "cmpl-test",
        "choices": [{
            "message": {"content": "", "reasoning_content": '{"x": 1, "y": 2}'},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
    }
    parsed = provider._parse_response(resp)
    assert parsed["content"] == '{"x": 1, "y": 2}'
    assert parsed["usage"]["total_tokens"] == 120


def test_parse_response_prefers_content():
    """content 非空时不读 reasoning_content。"""
    provider = _make_provider()
    resp = {
        "choices": [{
            "message": {"content": '{"found": true}', "reasoning_content": "思考过程"},
            "finish_reason": "stop",
        }],
        "usage": {},
    }
    parsed = provider._parse_response(resp)
    assert parsed["content"] == '{"found": true}'


def test_qwen_base_provider_no_reasoning_fallback():
    """回归防护：reasoning_content 兜底仅推理模型子类开启，QwenProvider 存量行为不变
    （qwen 思考模型的 reasoning_content 不得混入 content，避免污染回复与对话历史）。"""
    from src.llm.providers.qwen import QwenProvider

    provider = QwenProvider(api_key="sk-test", model="qwen3-plus")
    resp = {
        "choices": [{
            "message": {"content": "", "reasoning_content": "思考过程"},
            "finish_reason": "stop",
        }],
        "usage": {},
    }
    parsed = provider._parse_response(resp)
    assert parsed["content"] == ""
