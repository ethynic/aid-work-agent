"""
HotelExcelParser._parse_llm_output 单元测试

验证方案A：LLM 漏输出"酒店名称：xxx"行时，parser 强制将其前置到 info_text，
保证酒店名进入被向量化（chunk0 embedding）和 ILIKE 名称检索的文本。

背景：info_text 是唯一被向量和 ILIKE 检索的字段；若 LLM 漏写酒店名称行，
按酒店名搜索既命中不了名称也匹配不上语义（见 doc 973"天合盛景"搜不到的 bug）。
"""
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = PROJECT_ROOT / 'src' / 'skills' / 'travel-quote' / 'scripts'
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import hotel_excel_parser  # noqa: E402


def _make_parser():
    return hotel_excel_parser.HotelExcelParser()


class TestInfoTextContainsName:
    """info_text 必须含酒店名称行，否则按名搜不到（业务意图）"""

    def test_missing_name_line_is_prepended(self):
        """LLM 漏掉"酒店名称"行 → 强制以"酒店名称：{hotel_name}"开头"""
        llm_out = json.dumps([{
            "hotel_name": "西江天合盛景民宿（观景台店）",
            "region": "贵州省黔东南雷山县",
            "info_text": "所在区域：贵州省 雷山县\n星级等级：5钻\n地址：雷山西江千户苗寨",
            "price_table_text": "陌野 | 散客 | 780 | 含早",
            "metadata": {}
        }], ensure_ascii=False)
        results = _make_parser()._parse_llm_output(llm_out, "天合盛景")

        assert len(results) == 1
        info_text = results[0]["info_text"]
        # 首行必须是补上的酒店名称行
        assert info_text.splitlines()[0] == "酒店名称：西江天合盛景民宿（观景台店）"
        # 原始内容保留
        assert "星级等级：5钻" in info_text

    def test_existing_name_line_not_duplicated(self):
        """LLM 已输出"酒店名称"行 → 不重复前置"""
        llm_out = json.dumps([{
            "hotel_name": "测试酒店",
            "info_text": "酒店名称：测试酒店\n星级等级：4钻",
            "price_table_text": "房型A | 团队 | 300"
        }], ensure_ascii=False)
        results = _make_parser()._parse_llm_output(llm_out, "Sheet1")

        assert results[0]["info_text"].count("酒店名称") == 1
        assert results[0]["info_text"].startswith("酒店名称：测试酒店")

    def test_name_line_with_leading_spaces_counts_as_present(self):
        """行首带空格的"酒店名称"行仍视为已存在，不重复添加"""
        llm_out = json.dumps([{
            "hotel_name": "X酒店",
            "info_text": "  酒店名称：X酒店\n区域：A",
            "price_table_text": ""
        }], ensure_ascii=False)
        results = _make_parser()._parse_llm_output(llm_out, "S")

        assert results[0]["info_text"].count("酒店名称") == 1

    def test_markdown_fenced_json_still_prepends_name(self):
        """LLM 输出带 ```json 代码块包裹时，解析 + 名称行补全都正常"""
        inner = json.dumps([{
            "hotel_name": "Y酒店",
            "info_text": "区域：B",
            "price_table_text": "房型|团队|200"
        }], ensure_ascii=False)
        llm_out = f"```json\n{inner}\n```"
        results = _make_parser()._parse_llm_output(llm_out, "S")

        assert results[0]["info_text"].startswith("酒店名称：Y酒店")

    def test_empty_info_with_price_only_still_prepends_name(self):
        """info_text 为空、仅有价格表时，仍补名称行（保证可被名称检索）"""
        llm_out = json.dumps([{
            "hotel_name": "Z酒店",
            "info_text": "",
            "price_table_text": "房型|团队|200"
        }], ensure_ascii=False)
        results = _make_parser()._parse_llm_output(llm_out, "S")

        assert results[0]["info_text"].startswith("酒店名称：Z酒店")
