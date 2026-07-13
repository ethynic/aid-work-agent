# -*- coding: utf-8 -*-
"""
AttractionExcelParser._parse_llm_output 图片字段单元测试

验证 P1.2.3：parser 从 LLM 输出中提取 cover_image_filename / gallery_image_filenames，
供 zip 包导入时拼图片绝对路径使用。

业务意图：
- cover_image_filename 缺失时结果中必须为 None（不能是空串），否则下游 _resolve_image_path
  会尝试拼路径浪费校验
- gallery 可能是分号字符串（LLM 偶发输出）或 list，两种都要规范化为 list
"""
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = PROJECT_ROOT / 'src' / 'skills' / 'travel-quote' / 'scripts'
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import attraction_excel_parser  # noqa: E402


def _make_parser():
    return attraction_excel_parser.AttractionExcelParser()


def _llm_out(item: dict) -> str:
    return json.dumps([item], ensure_ascii=False)


class TestParseLLMOutputImages:
    """从 LLM JSON 输出中提取图片字段"""

    def test_extracts_cover_filename(self):
        """LLM 输出含 cover_image_filename → 结果 dict 含该字段"""
        out = _llm_out({
            "attraction_name": "黄果树瀑布",
            "region": "贵州",
            "info_text": "景点信息",
            "ticket_table_text": "门票 | 成人 | 110元",
            "project_table_text": "",
            "cover_image_filename": "黄果树瀑布.jpg",
            "metadata": {},
        })
        results = _make_parser()._parse_llm_output(out, "黄果树")

        assert len(results) == 1
        assert results[0]["cover_image_filename"] == "黄果树瀑布.jpg"

    def test_extracts_gallery_filenames_from_string(self):
        """LLM 输出 gallery 为分号字符串 → 转为 list"""
        out = _llm_out({
            "attraction_name": "小七孔",
            "region": "贵州",
            "info_text": "景点信息",
            "ticket_table_text": "",
            "project_table_text": "",
            "gallery_image_filenames": "图1.jpg;图2.jpg;图3.jpg",
            "metadata": {},
        })
        results = _make_parser()._parse_llm_output(out, "小七孔")

        assert len(results) == 1
        assert results[0]["gallery_image_filenames"] == ["图1.jpg", "图2.jpg", "图3.jpg"]

    def test_extracts_gallery_filenames_from_list(self):
        """LLM 输出 gallery 为 list → 原样保留（去除空白/空串）"""
        out = _llm_out({
            "attraction_name": "梵净山",
            "region": "贵州",
            "info_text": "景点信息",
            "ticket_table_text": "",
            "project_table_text": "",
            "gallery_image_filenames": ["a.jpg", " b.jpg ", "", "  "],
            "metadata": {},
        })
        results = _make_parser()._parse_llm_output(out, "梵净山")

        assert len(results) == 1
        # 空串和纯空白必须被过滤掉
        assert results[0]["gallery_image_filenames"] == ["a.jpg", "b.jpg"]

    def test_no_image_fields_returns_none(self):
        """LLM 输出无图片字段 → cover_image_filename=None, gallery_image_filenames=None"""
        out = _llm_out({
            "attraction_name": "龙宫",
            "region": "贵州",
            "info_text": "景点信息",
            "ticket_table_text": "",
            "project_table_text": "",
            "metadata": {},
        })
        results = _make_parser()._parse_llm_output(out, "龙宫")

        assert len(results) == 1
        assert results[0]["cover_image_filename"] is None
        assert results[0]["gallery_image_filenames"] is None

    def test_empty_cover_filename_becomes_none(self):
        """cover_image_filename 为空串时归一化为 None"""
        out = _llm_out({
            "attraction_name": "镇远古城",
            "region": "贵州",
            "info_text": "景点信息",
            "ticket_table_text": "",
            "project_table_text": "",
            "cover_image_filename": "   ",
            "gallery_image_filenames": [],
            "metadata": {},
        })
        results = _make_parser()._parse_llm_output(out, "镇远")

        assert len(results) == 1
        assert results[0]["cover_image_filename"] is None
        # 空列表归一化为 None
        assert results[0]["gallery_image_filenames"] is None
