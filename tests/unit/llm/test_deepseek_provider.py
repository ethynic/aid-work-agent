"""DeepSeekProvider 思考开关单测。

语义（2026-08-27 起）：默认开启思考——Agent 主链路依赖思考能力，网关单例
所有 deepseek 调用共用同一实例，实例级默认即主链路默认。例外是导航选航/
事实提取这类小任务：思考耗时 20~95s/次且会烧穿小 max_tokens 导致 content
为空（真机教训，2026-08 人口学会探针），由调用点显式传 enable_thinking=False
关闭，调用级优先于实例默认。

关闭思考必须走官方参数 `thinking: {"type": "disabled"}`——
`chat_template_kwargs` 是 vLLM 本地部署用法，官方 API 不识别（真机实测
软提示、不保证生效）。
"""

from src.llm.providers.deepseek import DeepSeekProvider


def _make_provider(**kwargs) -> DeepSeekProvider:
    return DeepSeekProvider(api_key="sk-test", model="deepseek-flash", **kwargs)


def test_thinking_enabled_by_default():
    """默认开启思考：请求体不注入任何思考控制参数（交给模型默认行为）。"""
    provider = _make_provider()
    body: dict = {}
    provider._apply_thinking_control(body)
    assert body == {}


def test_thinking_disabled_via_instance_flag():
    """实例级显式关闭：请求体必须带官方参数 thinking.type=disabled。"""
    provider = _make_provider(enable_thinking=False)
    body: dict = {}
    provider._apply_thinking_control(body)
    assert body == {"thinking": {"type": "disabled"}}


def test_call_level_false_overrides_instance_true():
    """调用级优先：实例开思考、单次调用传 False → 关闭。"""
    provider = _make_provider(enable_thinking=True)
    body: dict = {}
    provider._apply_thinking_control(body, enable_thinking=False)
    assert body == {"thinking": {"type": "disabled"}}


def test_call_level_true_overrides_instance_false():
    """调用级优先：实例关思考、单次调用传 True → 开启（无控制参数）。"""
    provider = _make_provider(enable_thinking=False)
    body: dict = {}
    provider._apply_thinking_control(body, enable_thinking=True)
    assert body == {}
