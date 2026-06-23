"""
travel-quote 行程解析 LLM 调用优化测试。
"""
import sys
from pathlib import Path


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
