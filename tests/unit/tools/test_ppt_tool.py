"""PPT 工具 Phase 0 回归测试。"""

import json
import os
from pathlib import Path

import pytest
from pptx import Presentation

pytestmark = pytest.mark.tools


class FakePlanner:
    def __init__(self, plan):
        self.plan = plan
        self.topic_calls = []
        self.content_calls = []
        self.images_seen = []

    async def plan_from_topic(self, topic, slide_count=None, theme_id=None, images=None):
        self.topic_calls.append(topic)
        self.images_seen.append(images)
        return self.plan

    async def plan_from_content(self, content, theme_id=None, images=None):
        self.content_calls.append(content)
        self.images_seen.append(images)
        return self.plan


def _sample_plan(title="测试演示文稿"):
    return {
        "title": title,
        "theme_id": 14,
        "style": "soft",
        "slides": [
            {
                "type": "cover",
                "title": title,
                "subtitle": "Phase 0 回归",
                "presenter": "AID",
                "date": "2026-06-30",
            },
            {
                "type": "content",
                "layout": "bullets",
                "title": "核心要点",
                "points": ["稳定生成", "可编辑 PPTX", "保持旧路径"],
            },
            {
                "type": "summary",
                "title": "总结",
                "takeaways": ["回归测试覆盖旧能力"],
                "next_steps": ["继续 Phase 1"],
            },
        ],
    }


def _assert_pptx(path, expected_slides):
    pptx_path = Path(path)
    assert pptx_path.exists()
    assert pptx_path.stat().st_size > 0
    prs = Presentation(str(pptx_path))
    assert len(prs.slides) == expected_slides


def _slide_texts(path):
    prs = Presentation(str(path))
    return [
        "\n".join(
            shape.text
            for shape in slide.shapes
            if hasattr(shape, "text") and shape.text
        )
        for slide in prs.slides
    ]


@pytest.mark.asyncio
async def test_topic_generates_ppt_with_python_pptx(tmp_path, monkeypatch):
    from src.tools.ppt.generator import PPTGenerator
    from src.tools.ppt.ppt_process_tool import PptProcessTool

    monkeypatch.setenv("PPT_RENDERER", "python_pptx")
    monkeypatch.setattr(PPTGenerator, "_get_output_dir", lambda self: tmp_path)

    tool = PptProcessTool()
    planner = FakePlanner(_sample_plan("企业 AI 应用"))
    tool._planner = planner

    result = await tool.execute(context="企业 AI 应用")

    assert result["success"] is True
    assert result["renderer"] == "python_pptx"
    assert result["slide_count"] == 3
    assert planner.topic_calls == ["企业 AI 应用"]
    _assert_pptx(result["file_path"], 3)
    assert "企业 AI 应用" in _slide_texts(result["file_path"])[0]


@pytest.mark.asyncio
async def test_markdown_outline_generates_ppt_with_python_pptx(tmp_path, monkeypatch):
    from src.tools.ppt.generator import PPTGenerator
    from src.tools.ppt.ppt_process_tool import PptProcessTool

    monkeypatch.setenv("PPT_RENDERER", "python_pptx")
    monkeypatch.setattr(PPTGenerator, "_get_output_dir", lambda self: tmp_path)

    markdown = "# 数据治理方案\n\n## 背景\n\n- 数据分散\n- 口径不一\n\n## 路线\n\n1. 标准化\n2. 自动化"
    tool = PptProcessTool()
    planner = FakePlanner(_sample_plan("数据治理方案"))
    tool._planner = planner

    result = await tool.execute(context=markdown)

    assert result["success"] is True
    assert planner.content_calls == [markdown]
    _assert_pptx(result["file_path"], 3)
    assert "核心要点" in _slide_texts(result["file_path"])[1]


@pytest.mark.asyncio
async def test_instruction_does_not_pollute_markdown_content_or_title(tmp_path, monkeypatch):
    from src.tools.ppt.generator import PPTGenerator
    from src.tools.ppt.ppt_process_tool import PptProcessTool

    monkeypatch.setenv("PPT_RENDERER", "python_pptx")
    monkeypatch.setattr(PPTGenerator, "_get_output_dir", lambda self: tmp_path)
    markdown = "# 数据治理方案\n\n## 背景\n\n- 数据分散"
    tool = PptProcessTool()
    planner = FakePlanner(_sample_plan("规划器标题"))
    tool._planner = planner

    result = await tool.execute(instruction="帮我生成 PPT", content=markdown)

    assert result["success"] is True
    assert planner.content_calls == [markdown]
    assert Path(result["file_path"]).name == "数据治理方案.pptx"
    assert "帮我生成" not in Path(result["file_path"]).name


@pytest.mark.asyncio
async def test_output_name_has_highest_title_priority(tmp_path, monkeypatch):
    from src.tools.ppt.generator import PPTGenerator
    from src.tools.ppt.ppt_process_tool import PptProcessTool

    monkeypatch.setenv("PPT_RENDERER", "python_pptx")
    monkeypatch.setattr(PPTGenerator, "_get_output_dir", lambda self: tmp_path)
    tool = PptProcessTool()
    tool._planner = FakePlanner(_sample_plan("规划器标题"))

    result = await tool.execute(
        instruction="生成 PPT",
        content="# Markdown 标题\n\n## 内容",
        output_name="董事会汇报.pptx",
    )

    assert result["success"] is True
    assert Path(result["file_path"]).name == "董事会汇报.pptx"


@pytest.mark.asyncio
async def test_legacy_mixed_context_remains_compatible(tmp_path, monkeypatch):
    from src.tools.ppt.generator import PPTGenerator
    from src.tools.ppt.ppt_process_tool import PptProcessTool

    monkeypatch.setenv("PPT_RENDERER", "python_pptx")
    monkeypatch.setattr(PPTGenerator, "_get_output_dir", lambda self: tmp_path)
    markdown = "# 兼容标题\n\n## 内容\n\n- 要点"
    tool = PptProcessTool()
    planner = FakePlanner(_sample_plan("规划器标题"))
    tool._planner = planner

    result = await tool.execute(context=f"请生成 PPT\n\n{markdown}")

    assert result["success"] is True
    assert planner.content_calls == [markdown]
    assert Path(result["file_path"]).name == "兼容标题.pptx"


@pytest.mark.asyncio
async def test_html_export_feature_gate_returns_stable_error(monkeypatch):
    from src.tools.ppt.ppt_process_tool import PptProcessTool

    monkeypatch.setenv("PPT_ENABLE_HTML_EXPORT", "false")
    result = await PptProcessTool().execute(
        instruction="转换为 PPTX",
        content="<h1>网页演示</h1>",
        content_type="html",
    )

    assert result["success"] is False
    assert "PPT_ENABLE_HTML_EXPORT=false" in result["error"]


@pytest.mark.asyncio
async def test_slide_deck_spec_renders_directly_without_planner(tmp_path, monkeypatch):
    from src.tools.ppt.ppt_process_tool import PptProcessTool
    from src.tools.ppt.renderer import NodePptRenderer

    def fake_render(self, spec, output_path):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"pptx")
        return {
            "file_path": str(output_path),
            "qa": {"slide_count": 1, "node_count": 1},
        }

    monkeypatch.setattr(NodePptRenderer, "render", fake_render)
    monkeypatch.setattr(PptProcessTool, "_get_output_dir", lambda self: tmp_path)
    tool = PptProcessTool()

    result = await tool.execute(
        content_type="slide_deck_spec",
        output_name="直接规格.pptx",
        content=json.dumps({
            "title": "原始标题",
            "slides": [{
                "id": "s1",
                "nodes": [{"type": "text", "x": 1, "y": 1, "w": 3, "h": 1, "text": "内容"}],
            }],
        }, ensure_ascii=False),
    )

    assert result["success"] is True
    assert result["renderer"] == "pptxgenjs"
    assert Path(result["file_path"]).name == "直接规格.pptx"
    assert tool._planner is None


@pytest.mark.asyncio
async def test_template_pptx_with_content_generates_ppt(tmp_path, monkeypatch):
    from src.tools.ppt.ppt_process_tool import PptProcessTool
    from src.tools.ppt.template_analyzer import TemplateAnalyzer

    monkeypatch.setenv("PPT_RENDERER", "python_pptx")
    monkeypatch.setattr(TemplateAnalyzer, "_get_output_dir", lambda self: tmp_path)

    template_path = tmp_path / "template.pptx"
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = "模板标题"
    prs.save(str(template_path))

    tool = PptProcessTool()
    planner = FakePlanner(_sample_plan("模板生成"))
    tool._planner = planner

    result = await tool.execute(
        context="# 模板生成\n\n- 使用模板布局\n- 填充内容",
        file_paths=[str(template_path)],
    )

    assert result["success"] is True
    assert "基于模板" in result["message"]
    assert result["qa_summary"]["template_audit_created"] is True
    assert result["qa_summary"]["frame_map_created"] is True
    assert result["qa_summary"]["deviation_log_created"] is True
    _assert_pptx(result["file_path"], 3)
    slide_texts = _slide_texts(result["file_path"])
    assert "模板生成" in slide_texts[0]
    assert "稳定生成" in slide_texts[1]


@pytest.mark.asyncio
async def test_empty_input_returns_clear_error():
    from src.tools.ppt.ppt_process_tool import PptProcessTool

    result = await PptProcessTool().execute(context="  ")

    assert result["success"] is False
    assert "请提供主题或内容" in result["error"]


@pytest.mark.asyncio
async def test_blank_file_paths_are_treated_as_empty_input():
    from src.tools.ppt.ppt_process_tool import PptProcessTool

    result = await PptProcessTool().execute(context=None, file_paths=["", "  "])

    assert result["success"] is False
    assert "请提供主题或内容" in result["error"]


@pytest.mark.asyncio
async def test_blank_content_and_instruction_cannot_bypass_execute_validation():
    from src.tools.ppt.ppt_process_tool import PptProcessTool

    result = await PptProcessTool().execute(
        instruction="  ", content=" ", context="\n", file_paths=[]
    )

    assert result == {
        "success": False,
        "error": "请提供主题或内容（content/context），或提供模板文件（file_paths）",
    }


@pytest.mark.asyncio
async def test_markdown_file_is_merged_before_routing_and_title_selection(
    tmp_path, monkeypatch
):
    from src.tools.ppt.generator import PPTGenerator
    from src.tools.ppt.ppt_process_tool import PptProcessTool

    monkeypatch.setenv("PPT_RENDERER", "python_pptx")
    monkeypatch.setattr(PPTGenerator, "_get_output_dir", lambda self: tmp_path)
    markdown_path = tmp_path / "outline.md"
    markdown_path.write_text("# 附件标题\n\n## 内容\n\n- 要点", encoding="utf-8")
    tool = PptProcessTool()
    planner = FakePlanner(_sample_plan("规划器标题"))
    tool._planner = planner

    result = await tool.execute(file_paths=[str(markdown_path)])

    assert result["success"] is True
    assert planner.content_calls == ["# 附件标题\n\n## 内容\n\n- 要点"]
    assert Path(result["file_path"]).name == "附件标题.pptx"


def test_ppt_config_reads_switches(monkeypatch):
    from src.tools.ppt.ppt_config import get_ppt_config

    monkeypatch.setenv("PPT_RENDERER", "pptxgenjs")
    monkeypatch.setenv("PPT_ENABLE_HTML_EXPORT", "true")
    monkeypatch.setenv("PPT_QA_STRICT", "1")
    monkeypatch.setenv("PPT_HTML_VIEWPORT_WIDTH", "1600")
    monkeypatch.setenv("PPT_HTML_IMAGE_FORMAT", "jpeg")

    config = get_ppt_config()

    assert config.renderer == "pptxgenjs"
    assert config.enable_html_export is True
    assert config.qa_strict is True
    assert config.html_viewport_width == 1600
    assert config.html_viewport_height == 1080
    assert config.html_image_format == "jpeg"


def test_ppt_config_defaults_to_node_renderer_with_fallback(monkeypatch):
    from src.tools.ppt.ppt_config import get_ppt_config

    monkeypatch.delenv("PPT_RENDERER", raising=False)
    monkeypatch.delenv("PPT_RENDERER_FALLBACK", raising=False)

    config = get_ppt_config()

    assert config.renderer == "pptxgenjs"
    assert config.renderer_fallback is True


def test_ppt_config_invalid_renderer_falls_back(monkeypatch):
    from src.tools.ppt.ppt_config import get_ppt_config

    monkeypatch.setenv("PPT_RENDERER", "unknown")

    assert get_ppt_config().renderer == "python_pptx"


@pytest.mark.asyncio
def test_ppt_capabilities_reports_missing_node(monkeypatch, tmp_path):
    from src.tools.ppt import ppt_capabilities

    monkeypatch.setattr(ppt_capabilities.shutil, "which", lambda command: None)

    caps = ppt_capabilities.get_capabilities(renderer_dir=tmp_path / "renderer-node")

    assert caps["node_renderer"]["available"] is False
    assert "Node.js" in caps["node_renderer"]["reason"]
    assert caps["render_validation"]["available"] is False


def test_ppt_capabilities_reports_missing_playwright_browser(monkeypatch):
    from src.tools.ppt import ppt_capabilities

    monkeypatch.setattr(
        ppt_capabilities,
        "probe_playwright",
        lambda package_available: {
            "available": False,
            "browser": "chromium",
            "reason": "Playwright Chromium 未安装或不可用",
        },
    )

    caps = ppt_capabilities.get_capabilities()

    assert caps["html_export"]["available"] is False
    assert "Chromium" in caps["html_export"]["reason"]


@pytest.mark.asyncio
async def test_pptxgenjs_renderer_missing_node_falls_back_without_stack(tmp_path, monkeypatch):
    from src.tools.ppt import ppt_capabilities
    from src.tools.ppt.generator import PPTGenerator
    from src.tools.ppt.ppt_process_tool import PptProcessTool

    monkeypatch.setenv("PPT_RENDERER", "pptxgenjs")
    monkeypatch.setattr(ppt_capabilities.shutil, "which", lambda command: None)
    monkeypatch.setattr(PPTGenerator, "_get_output_dir", lambda self: tmp_path)

    tool = PptProcessTool()
    tool._planner = FakePlanner(_sample_plan("Node 缺失回退"))

    result = await tool.execute(context="Node 缺失回退")

    assert result["success"] is True
    assert result["renderer"] == "python_pptx"
    assert "PptxGenJS 渲染器依赖不可用" in result["warnings"][0]
    assert "Traceback" not in str(result)
    _assert_pptx(result["file_path"], 3)


@pytest.mark.asyncio
async def test_unexpected_value_error_does_not_leak_details(monkeypatch):
    from src.tools.ppt.ppt_process_tool import PptProcessTool

    tool = PptProcessTool()

    async def raise_sensitive_error(normalized, mode, images=None):
        raise ValueError("api_key=secret-value C:\\tenant\\private.pptx")

    monkeypatch.setattr(tool, "_handle_auto", raise_sensitive_error)

    result = await tool.execute(context="测试异常")

    assert result["success"] is False
    assert result["error"] == "PPT生成失败，请检查输入内容或稍后重试"
    assert "secret-value" not in str(result)
    assert "private.pptx" not in str(result)


# ==================== 空白标题回归（生产崩溃修复）====================


def test_add_textbox_with_empty_text_returns_shape_without_error():
    from src.tools.ppt.utils import add_textbox

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])

    # 空串文本 p.runs 为空，不应触发 _Paragraph._r 的 AttributeError
    shape = add_textbox(slide, "", x=1.0, y=1.0, w=4.0, h=1.0)
    assert shape is not None
    assert shape.text_frame.text == ""

    # 纯空白文本有 run，同样不应报错
    blank_shape = add_textbox(slide, "   ", x=1.0, y=2.0, w=4.0, h=1.0)
    assert blank_shape is not None


def test_generate_with_blank_title_slides_creates_file(tmp_path, monkeypatch):
    from src.tools.ppt.generator import PPTGenerator

    monkeypatch.setattr(PPTGenerator, "_get_output_dir", lambda self: tmp_path)
    plan = {
        "title": "空白标题回归",
        "slides": [
            # 生产触发样例：缺 type 字段（按内容页渲染）且缺 title 的目录页数据
            {"layout": "toc", "sections": [{"title": "背景"}, {"title": "方案"}]},
            # 纯空白标题的内容页
            {"type": "content", "layout": "bullets", "title": "   ", "points": ["要点"]},
            # 无 title 的图表页
            {
                "type": "content",
                "layout": "chart",
                "chart": {"type": "bar", "labels": ["Q1", "Q2"], "series": [{"name": "销售额", "values": [10, 20]}]},
            },
        ],
    }

    path = PPTGenerator().generate(plan)

    _assert_pptx(path, 3)
    # 要点正文正常渲染（空白标题只跳过标题元素，不影响正文）
    assert "要点" in _slide_texts(path)[1]


# ==================== images 图片资产（分析图表嵌入）====================



def _make_image(tmp_path, name="chart.png"):
    from PIL import Image

    path = tmp_path / name
    Image.new("RGB", (40, 30), color=(30, 90, 200)).save(path)
    return str(path)


def _patch_tenant_root(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "src.tools.ppt.image_assets.resolve_tenant_root", lambda: str(tmp_path)
    )


@pytest.mark.asyncio
async def test_images_flow_to_planner_and_missing_appended_before_summary(
    tmp_path, monkeypatch
):
    from src.tools.ppt.generator import PPTGenerator
    from src.tools.ppt.ppt_process_tool import PptProcessTool

    monkeypatch.setenv("PPT_RENDERER", "python_pptx")
    monkeypatch.setattr(PPTGenerator, "_get_output_dir", lambda self: tmp_path)
    _patch_tenant_root(monkeypatch, tmp_path)
    image_path = _make_image(tmp_path)

    tool = PptProcessTool()
    planner = FakePlanner(_sample_plan("销售分析报告"))
    tool._planner = planner

    result = await tool.execute(
        content="销售分析报告",
        images=[{"path": image_path, "title": "月度销售额趋势", "caption": "Q3 环比 +23%"}],
    )

    assert result["success"] is True
    images_passed = planner.images_seen[0]
    assert images_passed and images_passed[0].path == os.path.realpath(image_path)
    assert "编排图片 1 页" in result["message"]
    assert any("补图片页" in w for w in result["warnings"])
    # 补页在 summary 前：第 3 页为图片页、第 4 页为总结
    prs = Presentation(result["file_path"])
    assert len(prs.slides) == 4
    assert any(shape.shape_type == 13 for shape in prs.slides[2].shapes)  # PICTURE
    assert "总结" in _slide_texts(result["file_path"])[3]


@pytest.mark.asyncio
async def test_images_rejected_in_template_and_spec_and_html_modes(
    tmp_path, monkeypatch
):
    from src.tools.ppt.ppt_process_tool import PptProcessTool

    _patch_tenant_root(monkeypatch, tmp_path)
    image_path = _make_image(tmp_path)
    template = tmp_path / "template.pptx"
    Presentation().save(str(template))

    tool = PptProcessTool()

    result = await tool.execute(
        file_paths=[str(template)],
        content="模板演示",
        images=[{"path": image_path, "title": "t", "caption": "c"}],
    )
    assert result["success"] is False
    assert "仅支持主题/大纲" in result["error"]

    spec = {
        "title": "spec 演示",
        "slides": [{"id": "s1", "nodes": [{"type": "text", "x": 1, "y": 1, "w": 5, "h": 1, "text": "hi"}]}],
    }
    result = await tool.execute(
        content=json.dumps(spec), content_type="slide_deck_spec",
        images=[{"path": image_path, "title": "t", "caption": "c"}],
    )
    assert result["success"] is False
    assert "仅支持主题/大纲" in result["error"]

    result = await tool.execute(
        content="<html><body><h1>页面</h1></body></html>", content_type="html",
        images=[{"path": image_path, "title": "t", "caption": "c"}],
    )
    assert result["success"] is False
    assert "仅支持主题/大纲" in result["error"]


@pytest.mark.asyncio
async def test_images_outside_tenant_root_rejected(tmp_path, monkeypatch):
    from src.tools.ppt.ppt_process_tool import PptProcessTool

    tenant_root = tmp_path / "tenant"
    tenant_root.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    outside_image = _make_image(outside_dir, "secret.png")
    _patch_tenant_root(monkeypatch, tenant_root)

    tool = PptProcessTool()
    result = await tool.execute(
        content="销售分析",
        images=[{"path": outside_image, "title": "越界图", "caption": "c"}],
    )
    assert result["success"] is False
    assert "不在当前租户存储目录内" in result["error"]


@pytest.mark.asyncio
async def test_images_missing_caption_rejected_by_schema(tmp_path, monkeypatch):
    from src.tools.ppt.ppt_process_tool import PptProcessTool

    _patch_tenant_root(monkeypatch, tmp_path)
    image_path = _make_image(tmp_path)

    tool = PptProcessTool()
    result = await tool.execute(
        content="销售分析",
        images=[{"path": image_path, "title": "有标题没说明"}],
    )
    assert result["success"] is False
    assert "images 参数无效" in result["error"]


@pytest.mark.asyncio
async def test_spec_mode_image_node_path_normalized_and_outside_rejected(
    tmp_path, monkeypatch
):
    from src.tools.ppt.ppt_process_tool import PptProcessTool

    tenant_root = tmp_path / "tenant"
    tenant_root.mkdir()
    _patch_tenant_root(monkeypatch, tenant_root)
    image_path = _make_image(tenant_root)
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    outside_image = _make_image(outside_dir, "secret.png")

    def _spec(image_path):
        return {
            "title": "spec 图片",
            "slides": [{
                "id": "s1",
                "nodes": [
                    {"type": "image", "x": 1, "y": 1, "w": 4, "h": 3, "path": image_path},
                ],
            }],
        }

    tool = PptProcessTool()
    captured = {}
    monkeypatch.setattr(
        tool,
        "_render_node_spec",
        lambda spec, output_stem=None: captured.update(spec=spec)
        or {"success": True, "file_path": str(tmp_path / "out.pptx"), "slide_count": 1},
    )
    monkeypatch.setattr(tool, "_apply_quality_validation", lambda result: result)

    # 越界路径：校验失败，返回明确错误
    result = await tool.execute(
        content=json.dumps(_spec(outside_image)), content_type="slide_deck_spec"
    )
    assert result["success"] is False
    assert "spec 图片路径校验失败" in result["error"]

    # 合法路径：校验通过，节点路径被规范化为绝对路径
    result = await tool.execute(
        content=json.dumps(_spec(image_path)), content_type="slide_deck_spec"
    )
    assert result["success"] is True
    node = captured["spec"].slides[0].nodes[0]
    assert node.path == os.path.realpath(image_path)
