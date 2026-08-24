"""DeepSeekProvider 思考开关单测。

背景（2026-08 人口学会探针教训）：v4 系混合模型默认带思考链，导航选航
这类小任务思考耗时 20~95s/次，且会烧穿小 max_tokens 导致 content 为空、
逐层导航静默退化到词表兜底。关闭思考必须走官方参数
`thinking: {"type": "disabled"}`——`chat_template_kwargs` 是 vLLM 本地部署
用法，官方 API 不识别（真机实测软提示、不保证生效）。
"""

from src.llm.providers.deepseek import DeepSeekProvider


def _make_provider(**kwargs) -> DeepSeekProvider:
    return DeepSeekProvider(api_key="sk-test", model="deepseek-v4-flash", **kwargs)


def test_thinking_disabled_by_default_via_official_param():
    """默认关闭思考：请求体必须带官方参数 thinking.type=disabled。"""
    provider = _make_provider()
    body: dict = {}
    provider._apply_thinking_control(body)
    assert body == {"thinking": {"type": "disabled"}}


def test_thinking_enabled_omits_param():
    """显式开启思考时不注入任何思考控制参数（交给模型默认行为）。"""
    provider = _make_provider(enable_thinking=True)
    body: dict = {}
    provider._apply_thinking_control(body)
    assert body == {}
