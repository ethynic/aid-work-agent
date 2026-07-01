"""Tests for PPT input normalization and deterministic routing."""

import pytest
from pydantic import ValidationError

from src.tools.ppt.input_normalizer import PptInputNormalizer
from src.tools.ppt.ppt_process_tool import (
    TOOL_DESCRIPTION,
    PptProcessInput,
    PptProcessTool,
)


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


def test_tool_contract_documents_structured_html_and_workspace_flow():
    schema = PptProcessInput.model_json_schema()

    assert "workspace" in schema["properties"]["file_paths"]["description"]
    assert "instruction" in TOOL_DESCRIPTION
    assert "content" in TOOL_DESCRIPTION
    assert 'content_type="html"' in TOOL_DESCRIPTION
    assert all(mode in TOOL_DESCRIPTION for mode in ("high_fidelity", "editable", "both"))
    assert "register_download=false" in TOOL_DESCRIPTION
    assert "alternate_file_path" in TOOL_DESCRIPTION
    assert "返回失败时不得声称文件已生成" in TOOL_DESCRIPTION


def test_user_errors_are_specific_and_do_not_leak_internal_paths():
    from src.tools.ppt.html_exporter import HtmlExportError
    from src.tools.ppt.renderer import PptRendererError
    from src.tools.ppt.template_analyzer import TemplateAnalysisError

    tool = PptProcessTool()
    html_error = tool._format_user_error(
        HtmlExportError(r"api_key=secret C:\tenant\private.html")
    )
    renderer_error = tool._format_user_error(
        PptRendererError(r"token=secret C:\tenant\private.pptx")
    )
    template_error = tool._format_user_error(
        TemplateAnalysisError("模板缺少可用版式，无法安全生成")
    )
    unsafe_template_error = tool._format_user_error(
        TemplateAnalysisError(r"token=secret C:\tenant\private.pptx")
    )

    assert html_error == "HTML 转 PPTX 失败，请检查页面内容或 workspace 文件"
    assert renderer_error == "PPTX 渲染器不可用或生成失败，请稍后重试"
    assert template_error == "模板缺少可用版式，无法安全生成"
    assert unsafe_template_error == "PPTX 模板处理失败，请检查模板文件"
    combined = html_error + renderer_error + template_error + unsafe_template_error
    assert "secret" not in combined
    assert "private" not in combined


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (
            {"content": "季度复盘", "content_type": r"C:\tenant\secret.xml"},
            "content_type 不受支持",
        ),
        (
            {"content": "季度复盘", "export_mode": "secret-mode"},
            "export_mode 不受支持",
        ),
    ],
)
async def test_validation_errors_are_specific_and_redacted(payload, expected):
    result = await PptProcessTool().execute(**payload)

    assert result["success"] is False
    assert result["error"].startswith(expected)
    assert "tenant" not in result["error"]
    assert "secret" not in result["error"]
