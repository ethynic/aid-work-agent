"""
travel-quote 景点门票/项目 LLM 调用优化测试。
"""
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = PROJECT_ROOT / 'src' / 'skills' / 'travel-quote' / 'scripts'
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def test_ticket_extract_uses_disabled_thinking_and_team_ticket_priority(monkeypatch):
    import attraction

    captured = {}

    def fake_call(prompt, **kwargs):
        captured["prompt"] = prompt
        captured["kwargs"] = kwargs
        return """
        {
          "confirmed": true,
          "name": "天龙屯堡",
          "tickets": [
            {"name": "古镇+天台山+摆渡车+标餐", "unit_price": 104, "ticket_type": "adult", "remark": "挂牌价"},
            {"name": "古镇+摆渡车（短程游）", "unit_price": 45, "ticket_type": "adult", "remark": "挂牌价"},
            {"name": "古镇+天台山+观光车（团队）", "unit_price": 55, "ticket_type": "adult", "remark": "团队结算价"}
          ]
        }
        """

    monkeypatch.setattr(attraction, 'call_llm', fake_call)
    monkeypatch.setattr(attraction, '_llm_kwargs', lambda task, max_tokens, timeout=20.0: {
        "timeout": timeout,
        "max_tokens": max_tokens,
        "task": task,
        "extra_body": {"thinking": {"type": "disabled"}},
    })

    result = attraction._llm_extract_tickets(
        "天龙屯堡",
        "",
        "古镇+天台山+摆渡车+标餐 | 成人 | 挂牌价:104元\n"
        "古镇+摆渡车（短程游） | 成人 | 挂牌价:45元\n"
        "古镇+天台山+观光车（团队） | 团队 | 团队结算价:55元",
        adults=2,
        children_half=0,
        students=0,
        elders=0,
    )

    assert captured["kwargs"]["task"] == "attraction_ticket_extract"
    assert captured["kwargs"]["max_tokens"] == 1024
    assert captured["kwargs"]["extra_body"] == {"thinking": {"type": "disabled"}}
    assert len(result["tickets"]) == 1
    assert result["tickets"][0]["unit_price"] == 55
    assert "团队" in result["tickets"][0]["name"]


def test_project_match_uses_disabled_thinking(monkeypatch):
    import attraction

    captured = {}

    def fake_call(prompt, **kwargs):
        captured["prompt"] = prompt
        captured["kwargs"] = kwargs
        return '{"projects":[{"name":"卧龙潭漂游","unit_price":50,"billing_method":"per_person","matched_activity":"卧龙潭漂游"}]}'

    monkeypatch.setattr(attraction, 'call_llm', fake_call)
    monkeypatch.setattr(attraction, '_llm_kwargs', lambda task, max_tokens, timeout=20.0: {
        "timeout": timeout,
        "max_tokens": max_tokens,
        "task": task,
        "extra_body": {"thinking": {"type": "disabled"}},
    })

    result = attraction._llm_extract_projects(
        "小七孔景区",
        "",
        "卧龙潭漂游 | 游玩项目 | 50元",
        ["卧龙潭漂游"],
        total_people=2,
    )

    assert captured["kwargs"]["task"] == "attraction_project_match"
    assert captured["kwargs"]["max_tokens"] == 2048
    assert captured["kwargs"]["extra_body"] == {"thinking": {"type": "disabled"}}
    assert result[0]["unit_price"] == 50


def test_team_ticket_priority_uses_source_table_when_llm_misses_team_ticket():
    import attraction

    result = attraction._prefer_team_ticket_rows(
        {
            "confirmed": True,
            "name": "天龙屯堡",
            "tickets": [
                {"name": "古镇+天台山+摆渡车+标餐", "unit_price": 104, "ticket_type": "adult", "remark": "挂牌价"}
            ],
        },
        "古镇+天台山+摆渡车+标餐 | 成人 | 挂牌价:104元\n"
        "古镇+天台山+观光车（团队） | 团队 | 团队结算价:55元",
    )

    assert len(result["tickets"]) == 1
    assert result["tickets"][0]["unit_price"] == 55
    assert "团队票" in result["tickets"][0]["name"]
