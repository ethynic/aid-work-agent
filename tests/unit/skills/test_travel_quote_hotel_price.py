"""
travel-quote 酒店价格选价单元测试

当前策略：LLM 主路径按"行序号"选价格行；规则只做候选压缩，不做最终决策。
新六列价格表格式（首行为表头）：房型 | 散客价 | 团客价 | 含早 | 适用日期 | 备注
三个选价函数自 43cd50f0 起返回结构化 dict：{"price", "breakfast", "room_type"}。
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = PROJECT_ROOT / 'src' / 'skills' / 'travel-quote' / 'scripts'
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


# 六列格式（首行表头会被解析器跳过）；团客价即对外团队价
PINGTANG_TABLE = """房型 | 散客价 | 团客价 | 含早 | 适用日期 | 备注
观景房型 | 420 | 360 | 未知 | 5月1日-6月30日 | 平季
观景房型 | 460 | 400 | 未知 | 5月1日-6月30日 | 平季
观景房型 | 800 | 728 | 未知 | 5月1日-5月5日（五一） | 节假日
观景房型 | 860 | 788 | 未知 | 5月1日-5月5日（五一） | 节假日
观景房型 | 720 | 658 | 未知 | 7月1日-8月31日 | 暑期
观景房型 | 820 | 758 | 未知 | 7月1日-8月31日 | 暑期
特殊政策：司陪房400元/间"""


class TestExtractFirstTeamPrice:
    def test_pingtang_first_team_is_offseason_360(self):
        import hotel
        result = hotel._extract_first_team_price(PINGTANG_TABLE)
        assert result == {"price": 360.0, "breakfast": "未知", "room_type": "观景房型"}

    def test_empty_table(self):
        import hotel
        zero = {"price": 0, "breakfast": "", "room_type": ""}
        assert hotel._extract_first_team_price('') == zero
        assert hotel._extract_first_team_price(None) == zero

    def test_special_policy_row_not_misread(self):
        import hotel
        assert hotel._extract_first_team_price("特殊政策：司陪房400元/间") == {
            "price": 0, "breakfast": "", "room_type": ""
        }

    def test_team_price_missing_falls_back_to_retail(self):
        import hotel
        # 团客价列缺失时回退散客价（"单价格同时填两列"约定的容错）
        table = "观景房型 | 500 |  | 未知 | 5月1日-6月30日 |"
        assert hotel._extract_first_team_price(table)["price"] == 500.0


class TestParseHotelPriceRows:
    def test_parse_rows_for_candidate_compression(self):
        import hotel
        rows = hotel._parse_hotel_price_rows(PINGTANG_TABLE)
        # 表头跳过、特殊政策行无管道符被忽略，其余 6 行均含团客价
        assert len(rows) == 6
        assert all(row["is_team"] for row in rows)
        assert rows[4]["price"] == 658.0
        assert rows[4]["date_text"] == "7月1日-8月31日"


class TestSelectTeamPriceByLlm:
    def test_row_index_selection(self, monkeypatch):
        import hotel
        import llm_client
        # LLM 按新契约输出行序号：暑期适用日期 → 序号 5（团客价 658）
        monkeypatch.setattr(llm_client, 'call_llm', lambda prompt, **kwargs: '5')
        result = hotel._select_team_price_by_llm(PINGTANG_TABLE, '2026-07-01')
        assert result == {"price": 658.0, "breakfast": "未知", "room_type": "观景房型"}

    def test_price_output_backward_compat(self, monkeypatch):
        import hotel
        import llm_client
        # 兼容 LLM 仍输出价格：序号越界时按价格反查首条
        monkeypatch.setattr(llm_client, 'call_llm', lambda prompt, **kwargs: '728')
        result = hotel._select_team_price_by_llm(PINGTANG_TABLE, '2026-05-03')
        assert result["price"] == 728.0

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
            return "5"

        monkeypatch.setattr(llm_client, 'call_llm', fake_call)
        result = hotel._select_team_price_by_llm(
            PINGTANG_TABLE,
            '2026-07-01',
            total_people=2,
            couples=1,
            teacher_count=0,
        )
        assert result["price"] == 658.0
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
        result = hotel._parse_team_price(PINGTANG_TABLE)
        assert result == {"price": 360.0, "breakfast": "未知", "room_type": "观景房型"}

    def test_empty_table_with_date(self):
        import hotel
        zero = {"price": 0, "breakfast": "", "room_type": ""}
        assert hotel._parse_team_price('', '2026-07-01') == zero
        assert hotel._parse_team_price(None, '2026-07-01') == zero

    def test_date_uses_llm_primary_even_when_rule_could_match(self, monkeypatch):
        import hotel
        import llm_client

        called = {"count": 0}

        def fake_call(prompt, **kwargs):
            called["count"] += 1
            return "5"

        monkeypatch.setattr(llm_client, 'call_llm', fake_call)
        result = hotel._parse_team_price(
            PINGTANG_TABLE,
            '2026-07-01',
            total_people=2,
            couples=1,
        )
        assert result["price"] == 658.0
        assert called["count"] == 1

    def test_llm_failure_falls_back(self, monkeypatch):
        import hotel
        import llm_client

        def boom(prompt, **kwargs):
            raise RuntimeError("API down")

        monkeypatch.setattr(llm_client, 'call_llm', boom)
        assert hotel._parse_team_price(PINGTANG_TABLE, '2026-07-01')["price"] == 360.0

    def test_llm_non_positive_falls_back(self, monkeypatch):
        import hotel
        import llm_client
        monkeypatch.setattr(llm_client, 'call_llm', lambda prompt, **kwargs: '0')
        assert hotel._parse_team_price(PINGTANG_TABLE, '2026-07-01')["price"] == 360.0
