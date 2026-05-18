"""
PPT 生成工具 — Agent 唯一入口

支持两种模式：
- auto（一键生成）: 用户输入主题或内容，LLM 规划大纲后自动生成 PPT
- template（模板生成）: 分析用户上传的 .pptx 模板，匹配内容后生成
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger
from pydantic import BaseModel, Field

from src.tools.base import BaseTool


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
工具会自动判断模式并生成PPT。"""


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
        file_paths = kwargs.get("file_paths")

        if not context and not file_paths:
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
            return {"success": False, "error": f"PPT生成失败: {e}"}

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

        download_info = await self._register_download(output_path, Path(output_path).name)
        if download_info:
            result["file_id"] = download_info["file_id"]
            result["download_url"] = download_info["download_url"]

        return result

    async def _generate_ppt(self, plan: dict) -> Dict[str, Any]:
        """根据大纲生成 PPT 文件。"""
        from src.tools.ppt.theme import get_theme
        from src.tools.ppt.generator import PPTGenerator

        theme = get_theme(plan.get("theme_id"), plan.get("style", "soft"))
        generator = PPTGenerator(theme)
        output_path = generator.generate(plan)

        result = {
            "success": True,
            "file_path": output_path,
            "slide_count": len(plan.get("slides", [])),
            "message": f"已生成PPT，共 {len(plan.get('slides', []))} 页",
        }

        download_info = await self._register_download(output_path, Path(output_path).name)
        if download_info:
            result["file_id"] = download_info["file_id"]
            result["download_url"] = download_info["download_url"]

        return result

    def _looks_like_outline(self, text: str) -> bool:
        """判断文本是否像结构化大纲。"""
        # 包含 markdown 标题或编号列表
        has_headings = bool(
            __import__("re").search(r"^#{1,3}\s", text, re.MULTILINE)
        )
        has_numbering = bool(
            __import__("re").search(r"^(\d+[\.\)、]|[-*]\s)", text, re.MULTILINE)
        )
        return has_headings or has_numbering or len(text) > 200

    async def _register_download(self, file_path: str, display_name: str) -> Dict[str, str]:
        """注册文件到下载系统。"""
        try:
            from src.core.redis_client import redis_client
            import uuid

            src = Path(file_path)
            if not src.exists():
                logger.warning(f"注册下载文件失败: 文件不存在 {file_path}")
                return {}

            file_id = f"file_{uuid.uuid4().hex[:12]}"
            file_size = src.stat().st_size

            if not display_name.lower().endswith(".pptx"):
                display_name += ".pptx"

            file_info = {
                "file_id": file_id,
                "name": display_name,
                "path": str(src.absolute()),
                "size": file_size,
                "mime_type": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                "type": "file",
            }
            key = redis_client.make_key("uploaded_file", file_id)
            for field, value in file_info.items():
                redis_client.hset(key, field, value)
            redis_client.expire(key, 86400)

            logger.info(f"文件已注册: file_id={file_id}, name={display_name}, size={file_size}")
            return {
                "file_id": file_id,
                "download_url": f"/api/files/{file_id}/download",
            }
        except Exception as e:
            logger.warning(f"注册下载文件失败: {e}")
            return {}
