"""
travel-quote 酒店价格选价单元测试

当前策略：LLM 主路径选价；规则只做候选压缩，不做最终决策。
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = PROJECT_ROOT / 'src' / 'skills' / 'travel-quote' / 'scripts'
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


PINGTANG_TABLE = """观景房型 | 团队 | 360 | 未知 | 5月1日-6月30日
观景房型 | 团散 | 400 | 未知 | 5月1日-6月30日
观景房型 | 团队 | 728 | 未知 | 5月1日-5月5日（五一）
观景房型 | 团散 | 788 | 未知 | 5月1日-5月5日（五一）
观景房型 | 团队 | 658 | 未知 | 7月1日-8月31日
观景房型 | 团散 | 758 | 未知 | 7月1日-8月31日
特殊政策：司陪房400元/间（7月1日-8月31日）"""


class TestExtractFirstTeamPrice:
    def test_pingtang_first_team_is_offseason_360(self):
        import hotel
        assert hotel._extract_first_team_price(PINGTANG_TABLE) == 360.0

    def test_empty_table(self):
        import hotel
        assert hotel._extract_first_team_price('') == 0
        assert hotel._extract_first_team_price(None) == 0

    def test_special_policy_row_not_misread(self):
        import hotel
        assert hotel._extract_first_team_price("特殊政策：司陪房400元/间") == 0

    def test_no_team_row_fallback_to_any_price(self):
        import hotel
        assert hotel._extract_first_team_price("观景房型 | 团散 | 500 | 未知 | 5月1日-6月30日") == 500.0


class TestParseHotelPriceRows:
    def test_parse_rows_for_candidate_compression(self):
        import hotel
        rows = hotel._parse_hotel_price_rows(PINGTANG_TABLE)
        team_rows = [row for row in rows if row["is_team"]]
        assert len(team_rows) == 3
        assert team_rows[2]["price"] == 658.0
        assert team_rows[2]["date_text"] == "7月1日-8月31日"


class TestSelectTeamPriceByLlm:
    def test_plain_number(self, monkeypatch):
        import hotel
        import llm_client
        monkeypatch.setattr(llm_client, 'call_llm', lambda prompt, **kwargs: '658')
        assert hotel._select_team_price_by_llm(PINGTANG_TABLE, '2026-07-01') == 658.0

    def test_number_with_surrounding_text(self, monkeypatch):
        import hotel
        import llm_client
        monkeypatch.setattr(llm_client, 'call_llm', lambda prompt, **kwargs: '团队价是728元')
        assert hotel._select_team_price_by_llm(PINGTANG_TABLE, '2026-05-03') == 728.0

    def test_unparseable_raises(self, monkeypatch):
        import hotel
        import llm_client
        monkeypatch.setattr(llm_client, 'call_llm', lambda prompt, **kwargs: '无法确定')
        with pytest.raises(ValueError, match="无法解析"):
            hotel._select_team_price_by_llm(PINGTANG_TABLE, '2026-07-01')

    def test_prompt_includes_team_context_and_uses_short_task_settings(self, monkeypatch):
        import hotel
        import llm_client

        captured = {}

        def fake_call(prompt, **kwargs):
            captured["prompt"] = prompt
            captured["kwargs"] = kwargs
            return "658"

        monkeypatch.setattr(llm_client, 'call_llm', fake_call)
        assert hotel._select_team_price_by_llm(
            PINGTANG_TABLE,
            '2026-07-01',
            total_people=2,
            couples=1,
            teacher_count=0,
        ) == 658.0
        assert "入住日期：2026-07-01（周三）" in captured["prompt"]
        assert "团队构成：总人数2人，夫妻1对" in captured["prompt"]
        assert "团队构成只用于判断客户类型" in captured["prompt"]
        assert captured["kwargs"]["timeout"] == 8.0
        assert captured["kwargs"]["max_tokens"] == 64
        assert captured["kwargs"]["task"] == "hotel_price_select"
        assert captured["kwargs"]["extra_body"] == {"thinking": {"type": "disabled"}}


class TestParseTeamPriceDispatch:
    def test_no_check_in_date_returns_fallback(self):
        import hotel
        assert hotel._parse_team_price(PINGTANG_TABLE) == 360.0

    def test_empty_table_with_date(self):
        import hotel
        assert hotel._parse_team_price('', '2026-07-01') == 0
        assert hotel._parse_team_price(None, '2026-07-01') == 0

    def test_date_uses_llm_primary_even_when_rule_could_match(self, monkeypatch):
        import hotel
        import llm_client

        called = {"count": 0}

        def fake_call(prompt, **kwargs):
            called["count"] += 1
            return "658"

        monkeypatch.setattr(llm_client, 'call_llm', fake_call)
        assert hotel._parse_team_price(
            PINGTANG_TABLE,
            '2026-07-01',
            total_people=2,
            couples=1,
        ) == 658.0
        assert called["count"] == 1

    def test_llm_failure_falls_back(self, monkeypatch):
        import hotel
        import llm_client

        def boom(prompt, **kwargs):
            raise RuntimeError("API down")

        monkeypatch.setattr(llm_client, 'call_llm', boom)
        assert hotel._parse_team_price(PINGTANG_TABLE, '2026-07-01') == 360.0

    def test_llm_non_positive_falls_back(self, monkeypatch):
        import hotel
        import llm_client
        monkeypatch.setattr(llm_client, 'call_llm', lambda prompt, **kwargs: '0')
        assert hotel._parse_team_price(PINGTANG_TABLE, '2026-07-01') == 360.0
