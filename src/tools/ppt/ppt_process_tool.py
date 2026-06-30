"""
PPT 生成工具 — Agent 唯一入口

支持模板、HTML、规格、大纲和主题五种确定性路由。
"""

from pathlib import Path
import json
import re
from typing import Any, Dict, List, Literal, Optional

from loguru import logger
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from src.tools.base import BaseTool
from src.tools.ppt.input_normalizer import NormalizedPptInput, PptInputNormalizer
from src.tools.ppt.ppt_config import get_ppt_config


class PptProcessInput(BaseModel):
    instruction: Optional[str] = Field(
        None, description="用户目的或操作指令，如“生成PPT”或“基于模板生成”"
    )
    content: Optional[str] = Field(
        None, description="PPT 主题、Markdown 大纲、HTML 或 SlideDeckSpec JSON 正文"
    )
    content_type: Optional[
        Literal["auto", "text", "markdown", "html", "slide_deck_spec", "md", "json", "spec"]
    ] = Field(
        None, description="内容类型：auto/text/markdown/html/slide_deck_spec"
    )
    output_name: Optional[str] = Field(
        None, description="输出业务文件名，可含或不含 .pptx 扩展名"
    )
    export_mode: Optional[Literal["editable", "high_fidelity", "both"]] = Field(
        None, description="HTML 导出模式：editable/high_fidelity/both"
    )
    context: Optional[str] = Field(
        None,
        description="兼容旧调用的混合输入；新调用请优先使用 instruction + content"
    )
    file_paths: Optional[List[str]] = Field(
        None,
        description="附件文件路径列表（用户上传的 .pptx 模板文件等）"
    )

    @field_validator(
        "instruction", "content", "content_type", "output_name", "export_mode", "context",
        mode="before",
    )
    @classmethod
    def strip_optional_strings(cls, value):
        if isinstance(value, str):
            return value.strip() or None
        return value

    @field_validator("file_paths", mode="before")
    @classmethod
    def clean_file_paths(cls, value):
        if value is None:
            return None
        if not isinstance(value, list):
            return value
        paths = [
            path.strip()
            for path in value
            if isinstance(path, str) and path.strip()
        ]
        return paths or None

    @model_validator(mode="after")
    def require_content_or_file(self):
        if not self.content and not self.context and not self.file_paths:
            raise ValueError("请提供 content、context 或 file_paths")
        return self


TOOL_DESCRIPTION = """PPT生成工具。根据用户需求生成可编辑的 PowerPoint 演示文稿(.pptx)。

⚠️ 触发规则 — 遇到以下场景必须调用本工具：
- 用户要求生成、制作、创建 PPT/PPTX/演示文稿/幻灯片
- 用户提供主题或内容，要求生成 PPT
- 用户上传了 .pptx 模板并要求基于模板生成
不要自己生成文件内容，一律交给本工具。

调用方式：
- 推荐将操作要求放在 instruction，将主题、Markdown 大纲或其他正文放在 content
- 可用 content_type 明确正文类型；output_name 指定业务文件名
- context 仅用于兼容旧调用，工具会尝试自动拆分指令和正文
- 用户上传的模板文件路径放在 file_paths 中
工具会自动判断模式并生成PPT。

📦 生成文件后必须用 cp 注册下载（重要）：
本工具生成 .pptx 文件后（返回结果中含 file_path），必须紧接着调用 cp 工具完成交付，
用户才能在前端看到并下载：
    cp(source_file_path="<本工具返回的 file_path>", display_name="<面向用户的业务文件名>")
cp 会把文件复制到下载目录、在前端对话中展示下载卡片。
display_name 必须使用用户能理解的业务文件名，不要使用工具临时文件名。"""


class PptProcessTool(BaseTool):
    name = "ppt_process"
    description = TOOL_DESCRIPTION
    display_name = "PPT生成"
    category = "file"
    InputModel = PptProcessInput

    def __init__(self):
        super().__init__()
        self._planner = None
        self._normalizer = PptInputNormalizer()

    def _get_planner(self):
        if self._planner is None:
            from src.tools.ppt.planner import PPTPlanner
            self._planner = PPTPlanner()
        return self._planner

    async def execute(self, **kwargs) -> Dict[str, Any]:
        try:
            payload = self.InputModel.model_validate(kwargs)
        except ValidationError:
            return {
                "success": False,
                "error": "请提供主题或内容（content/context），或提供模板文件（file_paths）",
            }

        normalized = self._normalizer.normalize(
            **payload.model_dump()
        )

        if not normalized.content and not normalized.file_paths:
            return {"success": False, "error": "请提供主题或内容（content），或提供模板文件（file_paths）"}

        mode = self._detect_mode(normalized)

        try:
            if mode == "template":
                return await self._handle_template(normalized)
            if mode == "html_to_pptx":
                return {"success": False, "error": "HTML 转 PPTX 暂不支持，将在 Phase 4 实现"}
            if mode == "spec_to_pptx":
                return await self._handle_spec(normalized)
            return await self._handle_auto(normalized, mode)
        except Exception as e:
            logger.error(f"[PptProcess] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": self._format_user_error(e)}

    def _detect_mode(self, normalized: NormalizedPptInput) -> str:
        """检测生成模式。"""
        if normalized.file_paths:
            for fp in normalized.file_paths:
                if Path(fp).suffix.lower() == ".pptx":
                    return "template"
        if normalized.content_type == "html":
            return "html_to_pptx"
        if normalized.content_type == "slide_deck_spec":
            return "spec_to_pptx"
        if normalized.content and self._looks_like_outline(normalized.content):
            return "outline_to_pptx"
        return "topic_to_pptx"

    async def _handle_auto(
        self, normalized: NormalizedPptInput, mode: str
    ) -> Dict[str, Any]:
        """一键生成模式。"""
        planner = self._get_planner()
        content = normalized.content

        if normalized.file_paths:
            # 有文件但不是 pptx（可能是 md/txt），读取内容
            for fp in normalized.file_paths:
                if Path(fp).suffix.lower() in (".md", ".txt"):
                    try:
                        file_content = Path(fp).read_text(encoding="utf-8")
                        content = f"{content}\n\n{file_content}" if content else file_content
                    except Exception:
                        pass

        merged_mode = (
            "outline_to_pptx"
            if content and self._looks_like_outline(content)
            else mode
        )
        if merged_mode == "outline_to_pptx":
            plan = await planner.plan_from_content(content)
        else:
            plan = await planner.plan_from_topic(content or "演示文稿")

        if "error" in plan:
            return {"success": False, "error": plan["error"]}

        plan["title"] = (
            self._normalizer.output_title(normalized.output_name)
            or self._normalizer.extract_title(content, normalized.content_type)
            or plan.get("title")
            or "演示文稿"
        )
        plan.setdefault("style", "soft")

        # 生成 PPT
        return await self._generate_ppt(plan)

    async def _handle_template(self, normalized: NormalizedPptInput) -> Dict[str, Any]:
        """模板生成模式。"""
        from src.tools.ppt.template_analyzer import TemplateAnalyzer

        if not normalized.file_paths:
            return {"success": False, "error": "模板模式需要提供 .pptx 模板文件"}

        template_path = None
        for fp in normalized.file_paths:
            if Path(fp).suffix.lower() == ".pptx":
                template_path = fp
                break

        if not template_path:
            return {"success": False, "error": "未找到 .pptx 模板文件"}

        # LLM 规划内容大纲
        planner = self._get_planner()
        plan = await planner.plan_from_content(normalized.content or "演示文稿")
        if "error" in plan:
            return {"success": False, "error": plan["error"]}
        plan["title"] = (
            self._normalizer.output_title(normalized.output_name)
            or normalized.extracted_title
            or plan.get("title")
            or "演示文稿"
        )

        # 分析模板 + 匹配内容 + 生成
        analyzer = TemplateAnalyzer()
        analysis = analyzer.analyze(template_path)
        matches = analyzer.match_content_to_layouts(plan, analysis)

        output_path = analyzer.generate_from_template(template_path, matches, plan)
        result = {
            "success": True,
            "file_path": output_path,
            "message": f"已基于模板生成PPT，共 {len(matches)} 页",
        }

        return result

    async def _handle_spec(self, normalized: NormalizedPptInput) -> Dict[str, Any]:
        """Validate and render a SlideDeckSpec without invoking the planner."""
        from src.tools.ppt.spec import SlideDeckSpec

        try:
            raw_spec = json.loads(normalized.content or "")
            spec = SlideDeckSpec.model_validate(raw_spec)
        except (json.JSONDecodeError, ValidationError):
            return {"success": False, "error": "SlideDeckSpec 格式或内容无效"}

        output_title = self._normalizer.output_title(normalized.output_name)
        if output_title:
            spec = spec.model_copy(update={"title": output_title})
        return self._render_node_spec(spec)

    async def _generate_ppt(self, plan: dict) -> Dict[str, Any]:
        """根据大纲生成 PPT 文件。"""
        from src.tools.ppt.theme import get_theme
        from src.tools.ppt.generator import PPTGenerator

        config = get_ppt_config()
        if config.renderer == "pptxgenjs":
            from src.tools.ppt.spec_builder import SlideDeckSpecBuilder

            try:
                if not self._check_node_renderer_ready():
                    raise RuntimeError("node renderer unavailable")
                spec = SlideDeckSpecBuilder().from_planner(plan)
                return self._render_node_spec(spec)
            except Exception as e:
                logger.error(f"[PptProcess] PptxGenJS 渲染失败: {e}", exc_info=True)
                if not config.renderer_fallback:
                    return {"success": False, "error": "PPT渲染服务暂不可用，请稍后重试"}
                warning = (
                    "PptxGenJS 渲染器依赖不可用，已回退到 python-pptx 路径"
                    if not self._check_node_renderer_ready()
                    else "PptxGenJS 渲染失败，已回退到 python-pptx 路径"
                )

        theme = get_theme(plan.get("theme_id"), plan.get("style", "soft"))
        generator = PPTGenerator(theme)
        output_path = generator.generate(plan)

        result = {
            "success": True,
            "file_path": output_path,
            "slide_count": len(plan.get("slides", [])),
            "renderer": "python_pptx",
            "message": f"已生成PPT，共 {len(plan.get('slides', []))} 页",
        }
        if config.renderer == "pptxgenjs":
            result["warnings"] = [warning]

        return result

    def _render_node_spec(self, spec) -> Dict[str, Any]:
        from src.tools.ppt.renderer import NodePptRenderer

        output_dir = self._get_output_dir()
        safe_name = "".join(
            character if character.isalnum() or character in "._- " else "_"
            for character in spec.title
        ).strip() or "演示文稿"
        output_path = output_dir / f"{safe_name}.pptx"
        rendered = NodePptRenderer().render(spec, output_path)
        qa = rendered["qa"]
        return {
            "success": True,
            "file_path": rendered["file_path"],
            "slide_count": qa.get("slide_count", len(spec.slides)),
            "renderer": "pptxgenjs",
            "qa_summary": {
                "slide_count": qa.get("slide_count", len(spec.slides)),
                "node_count": qa.get("node_count", 0),
            },
            "message": f"已生成PPT，共 {len(spec.slides)} 页",
        }

    def _get_output_dir(self) -> Path:
        try:
            from src.main import _get_tenant_upload_dir
            return _get_tenant_upload_dir()
        except (ImportError, AttributeError):
            return Path("storage/ppt")

    def _check_node_renderer_ready(self) -> bool:
        """返回 Node 渲染器依赖是否可用。"""
        from src.tools.ppt.ppt_capabilities import get_capabilities

        try:
            caps = get_capabilities()
            return bool(caps.get("node_renderer", {}).get("available"))
        except Exception as e:
            logger.warning(f"[PptProcess] Node 渲染器依赖探测失败: {e}")
            return False

    def _format_user_error(self, error: Exception) -> str:
        """生成用户可读错误，避免泄漏异常堆栈。"""
        return "PPT生成失败，请检查输入内容或稍后重试"

    def _looks_like_outline(self, text: str) -> bool:
        """判断文本是否像结构化大纲。"""
        # 包含 markdown 标题或编号列表
        has_headings = bool(
            re.search(r"^#{1,3}\s", text, re.MULTILINE)
        )
        has_numbering = bool(
            re.search(r"^(\d+[\.\)、]|[-*]\s)", text, re.MULTILINE)
        )
        return has_headings or has_numbering or len(text) > 200
