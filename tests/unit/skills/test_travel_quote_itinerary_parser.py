"""
travel-quote 行程解析 LLM 调用优化测试。
"""
import importlib
import sys
from pathlib import Path

# src/config/__init__.py 里 `from .settings import settings` 会遮蔽 src.config.settings
# 模块名，必须用 importlib 从 sys.modules 拿真正的模块对象
settings_module = importlib.import_module('src.config.settings')

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = PROJECT_ROOT / 'src' / 'skills' / 'travel-quote' / 'scripts'
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def test_parse_itinerary_uses_disabled_thinking_and_hard_checks(monkeypatch):
    import itinerary_parser

    captured = {}

    def fake_call(prompt, **kwargs):
        captured["prompt"] = prompt
        captured["kwargs"] = kwargs
        return """
        {
          "region_name": "贵州",
          "total_people": 2,
          "adults": 2,
          "children_half": 0,
          "students": 0,
          "elders": 0,
          "couples": 1,
          "teacher_count": 0,
          "trip_days": 6,
          "departure_city": "贵阳",
          "destination": "荔波",
          "daily_attractions": [
            {"day": 6, "attractions": [{"name": "天龙屯堡", "activities": []}]}
          ],
          "hotel_preference": "标准",
          "hotel_stays": [],
          "daily_routes": [],
          "meal_tier": "standard",
          "guide_type": "local"
        }
        """

    monkeypatch.setattr(itinerary_parser, 'call_llm', fake_call)
    monkeypatch.setattr(itinerary_parser, '_llm_kwargs', lambda task, max_tokens, timeout=60.0: {
        "timeout": timeout,
        "max_tokens": max_tokens,
        "task": task,
        "extra_body": {"thinking": {"type": "disabled"}},
    })

    result = itinerary_parser.parse_itinerary(
        "| D6 | 上午 | 天龙屯堡 | — | 感受明代屯堡文化 |\n"
        "| D5 | 下午 | 黄果树 | 电瓶车、扶梯（单程） | |"
    )

    assert captured["kwargs"]["task"] == "itinerary_parse"
    assert captured["kwargs"]["max_tokens"] == 4096
    assert captured["kwargs"]["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "天龙屯堡 | —" in captured["prompt"]
    assert "扶梯（单程）" in captured["prompt"]
    assert "不要因为 activities 为空就删除景点" in captured["prompt"]
    assert result["daily_attractions"][0]["attractions"][0]["name"] == "天龙屯堡"


def test_llm_kwargs_uses_effective_provider_for_thinking(monkeypatch):
    """全局 provider=qwen、子智能体注入 SKILL_LLM_PROVIDER=deepseek 时，
    _llm_kwargs 必须按生效 provider 关闭思考，防止推理模型返回空白 content。"""
    import itinerary_parser

    monkeypatch.setenv('SKILL_LLM_PROVIDER', 'deepseek')

    kwargs = itinerary_parser._llm_kwargs('itinerary_parse', max_tokens=4096)
    assert kwargs.get('extra_body') == {"thinking": {"type": "disabled"}}


def test_llm_kwargs_global_qwen_keeps_compatible(monkeypatch):
    import itinerary_parser

    class _FakeLLM:
        provider = 'qwen'

    monkeypatch.delenv('SKILL_LLM_PROVIDER', raising=False)
    monkeypatch.setattr(
        settings_module, 'settings',
        type('FakeSettings', (), {'llm': _FakeLLM()})(),
    )

    kwargs = itinerary_parser._llm_kwargs('itinerary_parse', max_tokens=4096)
    assert 'extra_body' not in kwargs


def test_parse_itinerary_retries_on_empty_then_succeeds(monkeypatch):
    """第一次 LLM 返回空白，第二次成功 —— 重试应恢复，不把整个报价链路打死。"""
    import itinerary_parser

    calls = []

    def fake_call(prompt, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            return "   "
        return '{"region_name": "贵州", "total_people": 30, "adults": 0, "students": 30, "teacher_count": 0, "trip_days": 4}'

    monkeypatch.setattr(itinerary_parser, 'call_llm', fake_call)

    result = itinerary_parser.parse_itinerary("测试行程")
    assert len(calls) == 2
    assert result["region_name"] == "贵州"
    assert result["total_people"] == 30


def test_parse_itinerary_retries_on_json_error_then_succeeds(monkeypatch):
    """第一次返回非法 JSON，第二次成功。"""
    import itinerary_parser

    calls = []

    def fake_call(prompt, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            return "```json\n{这不是合法JSON"
        return '{"region_name": "贵州", "total_people": 30}'

    monkeypatch.setattr(itinerary_parser, 'call_llm', fake_call)

    result = itinerary_parser.parse_itinerary("测试行程")
    assert len(calls) == 2
    assert result["total_people"] == 30


def test_parse_itinerary_raises_clear_error_after_all_retries(monkeypatch):
    import itinerary_parser

    calls = []

    def fake_call(prompt, **kwargs):
        calls.append(1)
        return ""

    monkeypatch.setattr(itinerary_parser, 'call_llm', fake_call)

    import pytest
    with pytest.raises(ValueError, match='行程解析 LLM 返回空内容'):
        itinerary_parser.parse_itinerary("测试行程")
    assert len(calls) == 2
