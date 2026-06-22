"""
travel-quote 酒店价格选价单元测试

验证 _parse_team_price 的「LLM 按入住日选价 + 兜底回退」逻辑：
- _extract_first_team_price：纯文本解析首条团队价（兜底，不依赖节日/日期）
- _select_team_price_by_llm：mock call_llm，验证返回值数字提取
- _parse_team_price：无日期 / LLM 失败 / LLM 非正 / LLM 正常 各调度路径

不依赖真实 LLM API（call_llm 全 mock）。
真实 LLM 的语义匹配正确性由手动 e2e 验证（平塘酒店 7月→658、五一→728、淡季→360）。
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = PROJECT_ROOT / 'src' / 'skills' / 'travel-quote' / 'scripts'
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


# 平塘星辰天缘大酒店真实价格表（chunks.chunk_index=1 原文）
PINGTANG_TABLE = """观景房型 | 团队 | 360 | 未知 | 5月1日-6月30日
观景房型 | 团散 | 400 | 未知 | 5月1日-6月30日
观景房型 | 团队 | 728 | 未知 | 5月1日-5月5日（五一）
观景房型 | 团散 | 788 | 未知 | 5月1日-5月5日（五一）
观景房型 | 团队 | 658 | 未知 | 7月1日-8月31日
观景房型 | 团散 | 758 | 未知 | 7月1日-8月31日
特殊政策：司陪房400元/间（7月1日-8月31日）"""


class TestExtractFirstTeamPrice:
    """兜底解析：提取首条团队价（不依赖日期/节日关键词）"""

    def test_pingtang_first_team_is_offseason_360(self):
        """平塘表第一条团队价是淡季360——这是 bug 修复前的错误返回值，
        现作为「无日期/LLM失败」时的兜底基准保留"""
        import hotel
        assert hotel._extract_first_team_price(PINGTANG_TABLE) == 360.0

    def test_empty_table(self):
        import hotel
        assert hotel._extract_first_team_price('') == 0
        assert hotel._extract_first_team_price(None) == 0

    def test_special_policy_row_not_misread(self):
        """'特殊政策：司陪房400元/间' 不含 |，不会被当作价格行误解析"""
        import hotel
        assert hotel._extract_first_team_price("特殊政策：司陪房400元/间") == 0

    def test_no_team_row_fallback_to_any_price(self):
        """没有团队行时回退到任意可解析价格"""
        import hotel
        assert hotel._extract_first_team_price("观景房型 | 团散 | 500 | 未知 | 5月1日-6月30日") == 500.0


class TestSelectTeamPriceByLlm:
    """LLM 选价的返回值数字提取（mock call_llm）"""

    def test_plain_number(self, monkeypatch):
        import hotel
        import llm_client
        monkeypatch.setattr(llm_client, 'call_llm', lambda prompt: '658')
        assert hotel._select_team_price_by_llm(PINGTANG_TABLE, '2026-07-01') == 658.0

    def test_number_with_surrounding_text(self, monkeypatch):
        """LLM 返回带文字的数字也能提取"""
        import hotel
        import llm_client
        monkeypatch.setattr(llm_client, 'call_llm', lambda prompt: '团队价是728元')
        assert hotel._select_team_price_by_llm(PINGTANG_TABLE, '2026-05-03') == 728.0

    def test_unparseable_raises(self, monkeypatch):
        """LLM 返回无数字时抛 ValueError（由上层兜底捕获）"""
        import hotel
        import llm_client
        monkeypatch.setattr(llm_client, 'call_llm', lambda prompt: '无法确定')
        with pytest.raises(ValueError, match="无法解析"):
            hotel._select_team_price_by_llm(PINGTANG_TABLE, '2026-07-01')


class TestParseTeamPriceDispatch:
    """_parse_team_price 调度：何时走 LLM、何时回退"""

    def test_no_check_in_date_returns_fallback(self):
        """无入住日期 → 首条团队价（向后兼容；update_hotel 的'是否有价'校验走此路）"""
        import hotel
        assert hotel._parse_team_price(PINGTANG_TABLE) == 360.0

    def test_empty_table_with_date(self):
        import hotel
        assert hotel._parse_team_price('', '2026-07-01') == 0
        assert hotel._parse_team_price(None, '2026-07-01') == 0

    def test_llm_returns旺季价(self, monkeypatch):
        """有入住日期 + LLM 正常 → 返回 LLM 选中的价格（修复核心：7月选 658 而非 360）"""
        import hotel
        import llm_client
        monkeypatch.setattr(llm_client, 'call_llm', lambda prompt: '658')
        assert hotel._parse_team_price(PINGTANG_TABLE, '2026-07-01') == 658.0

    def test_llm_failure_falls_back(self, monkeypatch):
        """LLM 抛异常 → 回退首条团队价，流程不中断"""
        import hotel
        import llm_client

        def boom(prompt):
            raise RuntimeError("API down")
        monkeypatch.setattr(llm_client, 'call_llm', boom)
        assert hotel._parse_team_price(PINGTANG_TABLE, '2026-07-01') == 360.0

    def test_llm_non_positive_falls_back(self, monkeypatch):
        """LLM 返回 0 → 回退"""
        import hotel
        import llm_client
        monkeypatch.setattr(llm_client, 'call_llm', lambda prompt: '0')
        assert hotel._parse_team_price(PINGTANG_TABLE, '2026-07-01') == 360.0

    def test_llm_unparseable_falls_back(self, monkeypatch):
        """LLM 返回无法解析 → 回退"""
        import hotel
        import llm_client
        monkeypatch.setattr(llm_client, 'call_llm', lambda prompt: '无价')
        assert hotel._parse_team_price(PINGTANG_TABLE, '2026-07-01') == 360.0
