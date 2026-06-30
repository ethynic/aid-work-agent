"""Tests for PPT input normalization and deterministic routing."""

import pytest
from pydantic import ValidationError

from src.tools.ppt.input_normalizer import PptInputNormalizer
from src.tools.ppt.ppt_process_tool import PptProcessInput, PptProcessTool


def test_structured_fields_take_priority_over_legacy_context():
    result = PptInputNormalizer().normalize(
        instruction="生成 PPT",
        content="# 新标题\n\n## 内容",
        context="旧指令\n# 旧标题",
    )

    assert result.instruction == "生成 PPT"
    assert result.content == "# 新标题\n\n## 内容"
    assert result.extracted_title == "新标题"


def test_legacy_context_splits_instruction_from_markdown():
    result = PptInputNormalizer().normalize(
        context="帮我生成一份 PPT\n\n# 数据治理\n\n## 背景"
    )

    assert result.instruction == "帮我生成一份 PPT"
    assert result.content == "# 数据治理\n\n## 背景"
    assert result.extracted_title == "数据治理"


def test_extracts_html_and_json_titles():
    normalizer = PptInputNormalizer()

    html = normalizer.normalize(content="<html><head><title>经营分析</title></head></html>")
    spec = normalizer.normalize(
        content='{"version":"1.0","title":"产品路线","slides":[]}'
    )

    assert html.content_type == "html"
    assert html.extracted_title == "经营分析"
    assert spec.content_type == "slide_deck_spec"
    assert spec.extracted_title == "产品路线"


def test_html_title_decodes_entities_and_beats_markdown_like_text():
    result = PptInputNormalizer().normalize(
        content="<title>研发 &amp; 交付</title><pre># 不是标题</pre>",
        content_type="html",
    )

    assert result.extracted_title == "研发 & 交付"


def test_legacy_plain_text_context_splits_instruction():
    normalizer = PptInputNormalizer()

    multiline = normalizer.normalize(context="请生成 PPT\n季度经营复盘")
    inline = normalizer.normalize(context="请生成 PPT：季度经营复盘")

    assert (multiline.instruction, multiline.content) == ("请生成 PPT", "季度经营复盘")
    assert (inline.instruction, inline.content) == ("请生成 PPT", "季度经营复盘")


@pytest.mark.parametrize(
    ("output_name", "expected"),
    [
        (r"..\..\敏感目录\董事会汇报.pptx", "董事会汇报"),
        ("/tmp/季度复盘.PPTX", "季度复盘"),
        ("非法<>名称.pptx", "非法__名称"),
    ],
)
def test_output_title_removes_extension_and_unsafe_path(output_name, expected):
    assert PptInputNormalizer.output_title(output_name) == expected


def test_pydantic_contract_rejects_blank_payload_and_invalid_enums():
    with pytest.raises(ValidationError):
        PptProcessInput(instruction="  ", content=" ", context="", file_paths=[" "])
    with pytest.raises(ValidationError):
        PptProcessInput(content="主题", content_type="xml")
    with pytest.raises(ValidationError):
        PptProcessInput(content="主题", export_mode="unknown")


def test_detects_all_phase_one_modes():
    normalizer = PptInputNormalizer()
    tool = PptProcessTool()

    assert tool._detect_mode(
        normalizer.normalize(content="季度复盘")
    ) == "topic_to_pptx"
    assert tool._detect_mode(
        normalizer.normalize(content="# 季度复盘\n## 结果")
    ) == "outline_to_pptx"
    assert tool._detect_mode(
        normalizer.normalize(content="<h1>季度复盘</h1>")
    ) == "html_to_pptx"
    assert tool._detect_mode(
        normalizer.normalize(content='{"title":"季度复盘","slides":[]}')
    ) == "spec_to_pptx"
    assert tool._detect_mode(
        normalizer.normalize(content="季度复盘", file_paths=["template.pptx"])
    ) == "template"
