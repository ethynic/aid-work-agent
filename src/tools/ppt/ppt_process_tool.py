"""
PPT 生成工具 — Agent 唯一入口

支持两种模式：
- auto（一键生成）: 用户输入主题或内容，LLM 规划大纲后自动生成 PPT
- template（模板生成）: 分析用户上传的 .pptx 模板，匹配内容后生成
"""

from pathlib import Path
import re
from typing import Any, Dict, List, Optional

from loguru import logger
from pydantic import BaseModel, Field

from src.tools.base import BaseTool
from src.tools.ppt.ppt_config import get_ppt_config


class PptProcessInput(BaseModel):
    context: Optional[str] = Field(
        None,
        description="用户的原始需求描述。可以是一句话主题（如'AI在企业中的应用'），"
                    "也可以是 Markdown 格式的完整内容大纲。"
                    "如果涉及附件操作，附件路径通过 file_paths 传入。"
    )
    file_paths: Optional[List[str]] = Field(
        None,
        description="附件文件路径列表（用户上传的 .pptx 模板文件等）"
    )


TOOL_DESCRIPTION = """PPT生成工具。根据用户需求生成可编辑的 PowerPoint 演示文稿(.pptx)。

⚠️ 触发规则 — 遇到以下场景必须调用本工具：
- 用户要求生成、制作、创建 PPT/PPTX/演示文稿/幻灯片
- 用户提供主题或内容，要求生成 PPT
- 用户上传了 .pptx 模板并要求基于模板生成
不要自己生成文件内容，一律交给本工具。

调用方式：
- 将用户的原始需求描述和相关内容放在 context 中
- context 可以是一句话主题，也可以是 Markdown 格式的完整大纲
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

    def _get_planner(self):
        if self._planner is None:
            from src.tools.ppt.planner import PPTPlanner
            self._planner = PPTPlanner()
        return self._planner

    async def execute(self, **kwargs) -> Dict[str, Any]:
        context = kwargs.get("context")
        file_paths = [
            path
            for path in (kwargs.get("file_paths") or [])
            if isinstance(path, str) and path.strip()
        ]

        if not (context and str(context).strip()) and not file_paths:
            return {"success": False, "error": "请提供主题或内容（context）或模板文件（file_paths）"}

        # 判断模式
        mode = self._detect_mode(context, file_paths)

        try:
            if mode == "template":
                return await self._handle_template(context, file_paths)
            else:
                return await self._handle_auto(context, file_paths)
        except Exception as e:
            logger.error(f"[PptProcess] 执行失败: {e}", exc_info=True)
            return {"success": False, "error": self._format_user_error(e)}

    def _detect_mode(self, context: Optional[str], file_paths: Optional[List[str]]) -> str:
        """检测生成模式。"""
        if file_paths:
            for fp in file_paths:
                if Path(fp).suffix.lower() == ".pptx":
                    return "template"
        return "auto"

    async def _handle_auto(self, context: Optional[str],
                           file_paths: Optional[List[str]]) -> Dict[str, Any]:
        """一键生成模式。"""
        planner = self._get_planner()

        # LLM 规划大纲
        if file_paths:
            # 有文件但不是 pptx（可能是 md/txt），读取内容
            for fp in file_paths:
                if Path(fp).suffix.lower() in (".md", ".txt"):
                    try:
                        content = Path(fp).read_text(encoding="utf-8")
                        context = f"{context or ''}\n\n{content}" if context else content
                    except Exception:
                        pass

        # 判断是否已有结构化大纲
        plan = None
        if context and self._looks_like_outline(context):
            plan = await planner.plan_from_content(context)
        else:
            plan = await planner.plan_from_topic(context or "演示文稿")

        if "error" in plan:
            return {"success": False, "error": plan["error"]}

        # 补充默认值
        plan.setdefault("title", context[:30] if context else "演示文稿")
        plan.setdefault("style", "soft")

        # 生成 PPT
        return await self._generate_ppt(plan)

    async def _handle_template(self, context: Optional[str],
                               file_paths: Optional[List[str]]) -> Dict[str, Any]:
        """模板生成模式。"""
        from src.tools.ppt.template_analyzer import TemplateAnalyzer

        if not file_paths:
            return {"success": False, "error": "模板模式需要提供 .pptx 模板文件"}

        template_path = None
        for fp in file_paths:
            if Path(fp).suffix.lower() == ".pptx":
                template_path = fp
                break

        if not template_path:
            return {"success": False, "error": "未找到 .pptx 模板文件"}

        # LLM 规划内容大纲
        planner = self._get_planner()
        plan = await planner.plan_from_content(context or "演示文稿")
        if "error" in plan:
            return {"success": False, "error": plan["error"]}

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

    async def _generate_ppt(self, plan: dict) -> Dict[str, Any]:
        """根据大纲生成 PPT 文件。"""
        from src.tools.ppt.theme import get_theme
        from src.tools.ppt.generator import PPTGenerator

        config = get_ppt_config()
        warnings = []
        renderer = config.renderer
        if config.renderer == "pptxgenjs":
            renderer_ready = self._check_node_renderer_ready()
            if renderer_ready:
                warnings.append("PptxGenJS 渲染器尚未接入，已使用 python-pptx 路径生成")
            else:
                warnings.append("PptxGenJS 渲染器依赖不可用，已回退到 python-pptx 路径")
            renderer = "python_pptx"

        theme = get_theme(plan.get("theme_id"), plan.get("style", "soft"))
        generator = PPTGenerator(theme)
        output_path = generator.generate(plan)

        result = {
            "success": True,
            "file_path": output_path,
            "slide_count": len(plan.get("slides", [])),
            "renderer": renderer,
            "message": f"已生成PPT，共 {len(plan.get('slides', []))} 页",
        }
        if warnings:
            result["warnings"] = warnings

        return result

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
