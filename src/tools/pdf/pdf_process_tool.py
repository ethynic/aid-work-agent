"""
PDF 文档处理工具 — Agent 唯一入口

Agent 优先传 instruction（用户目的）+ content（待处理正文）+ file_paths（附件），
旧版 context（用户需求 + 相关内容）继续兼容。工具内部自动判断操作类型和参数，
执行 pipeline 后返回结果。
"""

import asyncio
import re
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from loguru import logger
from pydantic import BaseModel, Field

from src.tools._helpers import sanitize_error, truncate_text
from src.tools._spill import spill_large_content
from src.tools.base import BaseTool


class TaskType:
    """有效操作类型常量"""
    READ = "read"
    READ_TABLES = "read_tables"
    OCR = "ocr"
    PDF_TO_MD = "pdf_to_md"
    MD_TO_PDF = "md_to_pdf"
    HTML_TO_PDF = "html_to_pdf"
    MERGE = "merge"
    SPLIT = "split"
    EXTRACT_PAGES = "extract_pages"
    INSPECT = "inspect"
    RENDER_PAGES = "render_pages"
    VALIDATE = "validate"
    CLEAN_METADATA = "clean_metadata"
    ADD_WATERMARK = "add_watermark"
    PROTECT = "protect"
    COMPRESS = "compress"
    EXTRACT_IMAGES = "extract_images"
    ROTATE = "rotate"

    ALL = {READ, READ_TABLES, OCR, PDF_TO_MD, MD_TO_PDF,
           HTML_TO_PDF, MERGE, SPLIT, EXTRACT_PAGES,
           INSPECT, RENDER_PAGES, VALIDATE, CLEAN_METADATA,
           ADD_WATERMARK, PROTECT, COMPRESS, EXTRACT_IMAGES, ROTATE}


class PdfProcessInput(BaseModel):
    instruction: Optional[str] = Field(
        None,
        description="用户目的描述，如生成PDF、读取、验证、合并、拆分、加水印。"
                    "生成PDF时建议只放目的，不要混入正文。"
    )
    content: Optional[str] = Field(
        None,
        description="待处理正文内容。Markdown/HTML 转 PDF 时优先使用此字段传完整正文，"
                    "不要把'生成一份PDF'等工具指令写入正文。"
    )
    content_type: Optional[Literal["markdown", "html", "text", "auto"]] = Field(
        None,
        description="content 的格式类型，可选：markdown、html、text、auto。默认自动识别。"
    )
    output_name: Optional[str] = Field(
        None,
        description="输出文件名，可选。生成PDF时可传入业务文件名，如 报告.pdf"
    )
    context: Optional[str] = Field(
        None,
        description="兼容旧调用：用户的原始需求描述和相关内容。"
                    "如果要把对话中的内容转为PDF，这里必须包含完整的 Markdown 或 HTML 文本；"
                    "新调用建议改用 instruction + content。"
    )
    file_paths: Optional[List[str]] = Field(
        None,
        description="附件文件路径列表（用户上传的 .pdf/.docx/.md/.html 文件等）"
    )


class PipelineContext:
    """Pipeline 执行过程中的上下文，在步骤间传递"""

    def __init__(self, file_paths: Optional[List[str]] = None,
                 context: Optional[str] = None,
                 instruction: Optional[str] = None,
                 content: Optional[str] = None,
                 content_type: Optional[str] = None,
                 output_name: Optional[str] = None):
        self.file_paths: List[str] = list(file_paths) if file_paths else []
        self.original_file_paths: List[str] = list(file_paths) if file_paths else []
        self.context: Optional[str] = context
        self.instruction: Optional[str] = instruction
        self.content: Optional[str] = content
        self.content_type: Optional[str] = content_type
        self.output_name: Optional[str] = output_name
        self.read_content: Optional[str] = None
        self.results: List[Dict] = []


TOOL_DESCRIPTION = (
    "处理PDF：读取/转Markdown/OCR/提取表格，以及生成PDF/合并/拆分/页面操作。不支持Word转PDF，请基于内容用md_to_pdf/html_to_pdf生成。"
    "操作类型（工具自动判断）：read/read_tables/ocr/pdf_to_md/md_to_pdf/html_to_pdf/merge/split/extract_pages/"
    "inspect/render_pages/validate/clean_metadata/add_watermark/protect/compress/extract_images/rotate。"
    "推荐 instruction+content 调用（instruction 放用户目的，content 放 Markdown/HTML 正文），附件路径放 file_paths。"
    "产生新文件的操作（转PDF/合并/拆分/提取页面/水印/加密/压缩/旋转）必须紧接着调 cp 注册下载；"
    "read/read_tables/ocr/pdf_to_md 不产生新文件无需 cp。"
)

TOOL_USAGE_GUIDE = ""


class PdfProcessTool(BaseTool):
    name = "pdf_process"
    description = TOOL_DESCRIPTION
    usage_guide = TOOL_USAGE_GUIDE
    display_name = "PDF文档处理"
    category = "file"
    InputModel = PdfProcessInput

    def __init__(self):
        super().__init__()
        self._router = None
        # tenant_id / user_id 注入（由 Agent 钩子调用，或通过 ContextVar 兜底）
        self._tenant_id: Optional[str] = None
        self._user_id: Optional[str] = None

    def set_tenant_id(self, tenant_id: str):
        """由 Agent 注入 tenant_id，用于 md_to_pdf/html_to_pdf 的 image_inliner。"""
        self._tenant_id = tenant_id

    def set_user_id(self, user_id: str):
        """由 Agent 注入 user_id。"""
        self._user_id = user_id

    def _resolve_tenant_user(self):
        """双轨获取 tenant_id/user_id：注入优先，ContextVar 兜底（HTTP 请求场景）。

        与 word_process_tool._handle_md_to_word 一致。
        """
        tenant_id = self._tenant_id
        if not tenant_id:
            try:
                from src.saas.context import get_current_tenant_id
                tenant_id = get_current_tenant_id()
            except Exception:
                tenant_id = None

        user_id = self._user_id
        if not user_id:
            try:
                from src.saas.context import get_current_user_id
                user_id = get_current_user_id()
            except Exception:
                user_id = None

        return tenant_id, user_id

    def _get_router(self):
        if self._router is None:
            from src.tools.pdf.pdf_router import PdfRouter
            self._router = PdfRouter()
        return self._router

    async def execute(self, **kwargs) -> Dict[str, Any]:
        context = kwargs.get("context")
        instruction = kwargs.get("instruction")
        content = kwargs.get("content")
        content_type = kwargs.get("content_type")
        output_name = kwargs.get("output_name")
        file_paths = kwargs.get("file_paths")
        normalized = self._normalize_input(
            context=context,
            instruction=instruction,
            content=content,
            content_type=content_type,
            output_name=output_name,
        )

        # 内部 LLM 路由决定操作类型和参数
        route_result = await self._resolve_task(
            normalized["route_context"],
            file_paths,
            instruction=normalized["instruction"],
            content_type=normalized["content_type"],
        )
        task_str = route_result.get("task", "")
        params = route_result.get("params", {})

        if not task_str:
            error = route_result.get("error", "无法确定操作类型")
            return {"success": False, "error": f"PDF工具无法理解需求：{error}。请在 context 中详细描述需求"}

        # 解析操作列表
        operations = [op.strip() for op in task_str.split(",") if op.strip()]
        if not operations:
            return {"success": False, "error": "任务解析异常"}

        # 校验操作类型
        for op in operations:
            if op not in TaskType.ALL:
                return {"success": False, "error": f"内部路由返回了无效操作: {op}"}

        # 初始化 pipeline 上下文
        ctx = PipelineContext(
            file_paths=file_paths,
            context=normalized["route_context"],
            instruction=normalized["instruction"],
            content=normalized["content"],
            content_type=normalized["content_type"],
            output_name=normalized["output_name"],
        )

        # 按序执行
        for op in operations:
            handler = self._get_handler(op)
            if not handler:
                return {"success": False, "error": f"操作 {op} 处理器未实现"}

            try:
                step_result = await handler(ctx, params)
            except FileNotFoundError as e:
                return {"success": False, "error": str(e)}
            except Exception as e:
                logger.opt(exception=True).error(f"PDF pipeline error at {op}: {e}")
                return {"success": False, "error": sanitize_error(e, fallback=f"操作 {op} 执行失败，请稍后重试")}

            if not step_result.get("success", True):
                step_result["failed_at"] = op
                return step_result

            step_result["operation"] = op
            ctx.results.append(step_result)
            self._update_context(ctx, op, step_result)

        return self._merge_results(ctx)

    def _normalize_input(
        self,
        *,
        context: Optional[str],
        instruction: Optional[str],
        content: Optional[str],
        content_type: Optional[str],
        output_name: Optional[str],
    ) -> Dict[str, Optional[str]]:
        """统一新旧入参，确保路由看目的，执行拿正文。"""
        normalized_instruction = (instruction or "").strip() or None
        normalized_content = (content or "").strip() or None
        normalized_context = (context or "").strip() or None
        normalized_content_type = (content_type or "auto").strip().lower()
        normalized_output_name = self._safe_output_name(output_name)

        if normalized_context and not normalized_content:
            body = self._extract_content_body(normalized_context, normalized_content_type)
            if body and body != normalized_context:
                normalized_content = body
                if not normalized_instruction:
                    prefix_end = normalized_context.find(body)
                    if prefix_end > 0:
                        normalized_instruction = normalized_context[:prefix_end].strip() or None

        route_parts = []
        if normalized_instruction:
            route_parts.append(normalized_instruction)
        if normalized_content:
            route_parts.append(normalized_content)
        elif normalized_context:
            route_parts.append(normalized_context)

        route_context = "\n\n".join(route_parts).strip() or None

        return {
            "instruction": normalized_instruction,
            "content": normalized_content,
            "content_type": normalized_content_type,
            "output_name": normalized_output_name,
            "route_context": route_context,
        }

    def _get_handler(self, op: str):
        handlers = {
            "read": self._handle_read,
            "read_tables": self._handle_read_tables,
            "ocr": self._handle_ocr,
            "pdf_to_md": self._handle_pdf_to_md,
            "md_to_pdf": self._handle_md_to_pdf,
            "html_to_pdf": self._handle_html_to_pdf,
            "merge": self._handle_merge,
            "split": self._handle_split,
            "extract_pages": self._handle_extract_pages,
            "inspect": self._handle_inspect,
            "render_pages": self._handle_render_pages,
            "validate": self._handle_validate,
            "clean_metadata": self._handle_clean_metadata,
            "add_watermark": self._handle_add_watermark,
            "protect": self._handle_protect,
            "compress": self._handle_compress,
            "extract_images": self._handle_extract_images,
            "rotate": self._handle_rotate,
        }
        return handlers.get(op)

    async def _resolve_task(
        self,
        context: Optional[str],
        file_paths: Optional[List[str]],
        instruction: Optional[str] = None,
        content_type: Optional[str] = None,
    ) -> Dict:
        if not context and not file_paths:
            return {"task": "", "error": "缺少 context 和 file_paths"}

        deterministic = self._resolve_task_deterministic(
            context,
            file_paths,
            instruction=instruction,
            content_type=content_type,
        )
        if deterministic:
            return deterministic

        try:
            router = self._get_router()
            return await router.route(context, file_paths)
        except Exception as e:
            logger.opt(exception=True).error(f"[PdfProcess] LLM 路由异常: {e}")
            return {"task": "", "error": sanitize_error(e, fallback="路由服务异常，请稍后重试")}

    def _resolve_task_deterministic(
        self,
        context: Optional[str],
        file_paths: Optional[List[str]],
        instruction: Optional[str] = None,
        content_type: Optional[str] = None,
    ) -> Optional[Dict]:
        """低风险确定性路由，减少生成 PDF 时对内部 LLM 的依赖。"""
        paths = file_paths or []
        lower_paths = [str(p).lower() for p in paths]
        intent = instruction or context or ""

        if any(p.endswith(".docx") for p in lower_paths) and self._is_pdf_generation_instruction(intent):
            return {
                "task": "",
                "error": (
                    "Word 转 PDF 已下架：LibreOffice 无法保证 Word 表格格式保真。"
                    "请基于 Word 内容直接生成 PDF（使用 md_to_pdf 或 html_to_pdf）。"
                ),
            }

        if not context:
            return None

        normalized_type = (content_type or "").lower()
        body = self._extract_content_body(context, normalized_type)
        if normalized_type in ("html", "htm") or self._looks_like_html(body):
            if self._is_pdf_generation_instruction(intent):
                return {"task": "html_to_pdf", "params": {}, "reason": "检测到 HTML 正文并要求生成 PDF"}

        if normalized_type in ("markdown", "md") or self._looks_like_markdown_document(body):
            if self._is_pdf_generation_instruction(intent):
                params: Dict[str, Any] = {}
                title = self._extract_title(body)
                if title:
                    params["title"] = title
                    params["output_name"] = f"{self._safe_file_stem(title)}.pdf"
                return {"task": "md_to_pdf", "params": params, "reason": "检测到 Markdown 正文并要求生成 PDF"}

        return None

    def _update_context(self, ctx: PipelineContext, op: str, result: Dict) -> None:
        """根据操作结果更新 pipeline 上下文"""
        if op in ("read", "pdf_to_md", "ocr"):
            ctx.read_content = (
                result.get("content")
                or result.get("markdown")
            )

        if op in (
            "md_to_pdf", "html_to_pdf", "merge", "split", "extract_pages",
            "clean_metadata", "add_watermark", "protect", "compress", "rotate",
        ):
            if result.get("file_path"):
                ctx.file_paths = [result["file_path"]]
            elif result.get("files"):
                ctx.file_paths = [f["file_path"] for f in result["files"] if f.get("file_path")]

    def _merge_results(self, ctx: PipelineContext) -> Dict[str, Any]:
        """合并所有步骤的结果。

        文本类大字段统一截断（单字段 ≤2000、全文类 ≤5000），并带 truncated 标记，
        避免把全文/完整表格灌入 LLM 上下文。规范见 tool-development-spec.md §1.4。
        """
        merged = {"success": True, "steps": len(ctx.results)}

        for r in ctx.results:
            op = r["operation"]
            if op == "read":
                # 元信息 + preview，不再回塞全文；超长落盘供 read/grep 回读
                pages = r.get("pages", [])
                merged["pages"] = pages
                merged["metadata"] = r.get("metadata", {})
                merged["page_count"] = len(pages)
                spilled = self._spill_text_field(r.get("content", ""), 2000, "pdf_read_", ".txt")
                merged["content"] = spilled["preview"]
                if spilled["truncated"]:
                    merged["content_truncated"] = True
                    merged["content_file_path"] = spilled["file_path"]
                    merged["content_full_size"] = spilled["full_size"]
            elif op == "read_tables":
                # 每张表保留元信息 + preview 行，不再回塞完整表格数据
                tables = r.get("tables", [])
                merged["table_count"] = r.get("count", len(tables))
                merged["tables"] = [self._table_preview(t) for t in tables]
            elif op == "ocr":
                spilled = self._spill_text_field(r.get("content", ""), 2000, "pdf_ocr_", ".txt")
                merged["content"] = spilled["preview"]
                merged["page_count"] = r.get("page_count", 0)
                if spilled["truncated"]:
                    merged["content_truncated"] = True
                    merged["content_file_path"] = spilled["file_path"]
                    merged["content_full_size"] = spilled["full_size"]
            elif op == "pdf_to_md":
                spilled = self._spill_text_field(r.get("markdown", ""), 5000, "pdf_to_md_", ".md")
                merged["markdown"] = spilled["preview"]
                merged["source"] = r.get("source", "text_extract")
                if spilled["truncated"]:
                    merged["markdown_truncated"] = True
                    merged["markdown_file_path"] = spilled["file_path"]
                    merged["markdown_full_size"] = spilled["full_size"]
            elif op in ("md_to_pdf", "html_to_pdf"):
                merged["file_path"] = r.get("file_path", "")
                merged["file_size"] = r.get("file_size", 0)
                if r.get("file_id"):
                    merged["file_id"] = r["file_id"]
                if r.get("download_url"):
                    merged["download_url"] = r["download_url"]
            elif op == "merge":
                merged["file_path"] = r.get("file_path", "")
                merged["page_count"] = r.get("page_count", 0)
                merged["source_count"] = r.get("source_count", 0)
                if r.get("file_id"):
                    merged["file_id"] = r["file_id"]
                if r.get("download_url"):
                    merged["download_url"] = r["download_url"]
            elif op in ("split", "extract_pages"):
                if r.get("files"):
                    merged["files"] = r["files"]
                    merged["count"] = r.get("count", 0)
                else:
                    merged["file_path"] = r.get("file_path", "")
                    merged["file_size"] = r.get("file_size", 0)
                if r.get("file_id"):
                    merged["file_id"] = r["file_id"]
                if r.get("download_url"):
                    merged["download_url"] = r["download_url"]
            elif op == "inspect":
                merged["inspection"] = self._truncate_blob(r.get("inspection", r), merged)
            elif op == "render_pages":
                merged["rendered_pages"] = r.get("pages", [])
                merged["count"] = r.get("count", 0)
                merged["renderer"] = r.get("renderer", "")
            elif op == "validate":
                merged["validation"] = self._truncate_blob(r.get("validation", r), merged)
            elif op in ("clean_metadata", "add_watermark", "protect", "compress", "rotate"):
                merged["file_path"] = r.get("file_path", "")
                merged["file_size"] = r.get("file_size", 0)
                if r.get("display_name"):
                    merged["display_name"] = r["display_name"]
                if r.get("protected"):
                    merged["protected"] = r["protected"]
                if r.get("rotated_pages"):
                    merged["rotated_pages"] = r["rotated_pages"]
                if r.get("rotation"):
                    merged["rotation"] = r["rotation"]
                if r.get("original_size"):
                    merged["original_size"] = r["original_size"]
                if r.get("compressed_size"):
                    merged["compressed_size"] = r["compressed_size"]
            elif op == "extract_images":
                merged["images"] = r.get("images", [])
                merged["count"] = r.get("count", 0)

            if r.get("validation"):
                merged["validation"] = self._truncate_blob(r["validation"], merged)
            if r.get("warnings"):
                merged.setdefault("warnings", []).extend(r["warnings"])

        if ctx.file_paths:
            merged["final_file_path"] = ctx.file_paths[0]

        return merged

    @staticmethod
    def _table_preview(table: Dict) -> Dict:
        """单张表格只保留元信息 + 有限行预览，不再回塞完整表格数据。"""
        if not isinstance(table, dict):
            return table
        preview: Dict[str, Any] = {
            "page": table.get("page"),
            "table_index": table.get("table_index"),
        }
        data = table.get("data")
        if isinstance(data, list):
            preview["row_count"] = len(data)
            preview["preview"] = data[:5]
            cell_count = sum(len(row) for row in data if isinstance(row, list))
            if cell_count > 50:
                preview["preview_truncated"] = True
        else:
            preview["data"] = data
        return preview

    @staticmethod
    def _truncate_blob(value: Any, merged: Dict) -> Any:
        """inspect/validate 等结构：序列化超长则截断为字符串 + truncated 标记。"""
        import json as _json

        if not isinstance(value, (dict, list)):
            return value
        serialized = _json.dumps(value, ensure_ascii=False)
        if len(serialized) <= 5000:
            return value
        truncated, _ = truncate_text(serialized, limit=5000)
        merged["blob_truncated"] = True
        return truncated

    @staticmethod
    def _spill_text_field(value: Optional[str], limit: int,
                          prefix: str, suffix: str) -> Dict[str, Any]:
        """文本大字段：超长则全文落盘（agent 可 read/grep 回读），否则原样。

        对齐 http_api 的落盘闭环（#34 Phase 3b），消除「截断即丢弃」的信息黑洞。
        注意：spill_large_content 内部固定 PREVIEW_LIMIT=5000，而本工具 read/ocr 的
        预览上限是 2000，因此 in-result 预览按 ``limit`` 自行截断；落盘文件始终是全文。
        返回 dict：始终含 ``preview``；超长时另含 ``truncated/file_path/full_size``，
        由调用方按字段名合并进 merged。
        """
        value = value or ""
        if len(value) <= limit:
            return {"preview": value, "truncated": False}
        spill = spill_large_content(value, prefix=prefix, suffix=suffix)
        preview, _ = truncate_text(value, limit=limit)
        return {
            "preview": preview,
            "truncated": True,
            "file_path": spill["file_path"],
            "full_size": spill["full_size"],
        }

    # ── 各操作处理器 ──

    async def _handle_read(self, ctx: PipelineContext, params: Dict) -> Dict:
        from src.tools.pdf.pdf_reader import read_text

        if not ctx.file_paths:
            return {"success": False, "error": "read 操作需要 file_paths 参数"}

        file_path = self._resolve_file(ctx.file_paths[0])
        pages = self._normalize_user_pages(file_path, params.get("pages"))
        return read_text(file_path, pages=pages)

    async def _handle_read_tables(self, ctx: PipelineContext, params: Dict) -> Dict:
        from src.tools.pdf.pdf_reader import extract_tables

        if not ctx.file_paths:
            return {"success": False, "error": "read_tables 操作需要 file_paths 参数"}

        file_path = self._resolve_file(ctx.file_paths[0])
        pages = self._normalize_user_pages(file_path, params.get("pages"))
        return extract_tables(file_path, pages=pages)

    async def _handle_ocr(self, ctx: PipelineContext, params: Dict) -> Dict:
        if not ctx.file_paths:
            return {"success": False, "error": "ocr 操作需要 file_paths 参数"}

        file_path = self._resolve_file(ctx.file_paths[0])

        import asyncio
        from src.tools.ocr.ocr_tool import paddleocr_doc_parsing
        result = await asyncio.to_thread(paddleocr_doc_parsing, file_path=file_path, file_type=0)

        if result.get("success"):
            return {
                "success": True,
                "content": result["full_text"],
                "pages": result.get("texts", []),
                "page_count": len(result.get("texts", [])),
            }
        return result

    async def _handle_pdf_to_md(self, ctx: PipelineContext, params: Dict) -> Dict:
        from src.tools.pdf.pdf_to_md import convert_smart

        if not ctx.file_paths:
            return {"success": False, "error": "pdf_to_md 操作需要 file_paths 参数"}

        file_path = self._resolve_file(ctx.file_paths[0])
        pages = self._normalize_user_pages(file_path, params.get("pages"))

        import asyncio
        return await asyncio.to_thread(convert_smart, file_path, pages=pages)

    async def _handle_md_to_pdf(self, ctx: PipelineContext, params: Dict) -> Dict:
        from src.tools.pdf.pdf_writer import md_to_pdf

        md_text = ctx.content or ctx.context
        if not md_text and ctx.file_paths:
            md_path = ctx.file_paths[0]
            if Path(md_path).exists():
                md_text = Path(md_path).read_text(encoding="utf-8")

        if not md_text:
            return {"success": False, "error": "md_to_pdf 需要提供 context（Markdown文本）或 file_paths（.md文件路径）"}

        md_text = self._extract_markdown_body(md_text)

        # 先把 ![alt](file_id:...)/![alt](https://...) 解析为本地路径，再渲染
        tenant_id, user_id = self._resolve_tenant_user()
        if tenant_id:
            from src.tools._image_inliner import inline_images
            try:
                md_text, _refs = await inline_images(
                    md_text, tenant_id=tenant_id, user_id=user_id
                )
            except Exception as e:
                logger.warning(f"[pdf_process] md_to_pdf inline_images 失败，回退原文: {e}")

        import asyncio
        result = await asyncio.to_thread(
            md_to_pdf,
            md_text=md_text,
            output_name=ctx.output_name or self._safe_output_name(params.get("output_name")),
            css=params.get("css"),
            title=params.get("title", ""),
        )

        return self._validate_generated_result(result, params)

    async def _handle_html_to_pdf(self, ctx: PipelineContext, params: Dict) -> Dict:
        from src.tools.pdf.pdf_writer import html_to_pdf

        html_text = ctx.content or ctx.context
        if not html_text and ctx.file_paths:
            html_path = ctx.file_paths[0]
            if Path(html_path).exists():
                html_text = Path(html_path).read_text(encoding="utf-8")

        if not html_text:
            return {"success": False, "error": "html_to_pdf 需要提供 context（HTML文本）或 file_paths（.html文件路径）"}

        html_text = self._extract_html_body(html_text)

        # 解析 <img src="file_id:...">/<img src="https://..."> 为本地路径，再渲染
        tenant_id, user_id = self._resolve_tenant_user()
        if tenant_id:
            from src.tools._image_inliner import inline_images
            try:
                html_text, _refs = await inline_images(
                    html_text, tenant_id=tenant_id, user_id=user_id, syntax="html"
                )
            except Exception as e:
                logger.warning(f"[pdf_process] html_to_pdf inline_images 失败，回退原文: {e}")

        import asyncio
        result = await asyncio.to_thread(
            html_to_pdf,
            html_text=html_text,
            output_name=ctx.output_name or self._safe_output_name(params.get("output_name")),
            css=params.get("css"),
            engine=params.get("engine", "auto"),
        )

        return self._validate_generated_result(result, params)

    async def _handle_merge(self, ctx: PipelineContext, params: Dict) -> Dict:
        from src.tools.pdf.pdf_merger import merge_pdfs

        if not ctx.file_paths or len(ctx.file_paths) < 2:
            return {"success": False, "error": "merge 操作需要至少 2 个 PDF 文件路径"}

        import asyncio
        result = await asyncio.to_thread(
            merge_pdfs,
            file_paths=ctx.file_paths,
            output_name=params.get("output_name"),
        )

        return self._validate_generated_result(result, params)

    async def _handle_split(self, ctx: PipelineContext, params: Dict) -> Dict:
        from src.tools.pdf.pdf_merger import split_pdf

        if not ctx.file_paths:
            return {"success": False, "error": "split 操作需要 file_paths 参数"}

        ranges = params.get("ranges", [])
        if not ranges:
            return {"success": False, "error": "split 操作需要 params.ranges 参数，格式如 ['1-3', '5-7']"}

        file_path = self._resolve_file(ctx.file_paths[0])

        import asyncio
        result = await asyncio.to_thread(
            split_pdf,
            file_path=file_path,
            ranges=ranges,
            output_name=params.get("output_name"),
        )

        return self._validate_generated_result(result, params)

    async def _handle_extract_pages(self, ctx: PipelineContext, params: Dict) -> Dict:
        from src.tools.pdf.pdf_merger import extract_pages

        if not ctx.file_paths:
            return {"success": False, "error": "extract_pages 操作需要 file_paths 参数"}

        pages = params.get("pages", [])
        if not pages:
            return {"success": False, "error": "extract_pages 操作需要 params.pages 参数，格式如 [1, 3, 5]"}

        file_path = self._resolve_file(ctx.file_paths[0])

        import asyncio
        result = await asyncio.to_thread(
            extract_pages,
            file_path=file_path,
            pages=pages,
            output_name=params.get("output_name"),
        )

        return self._validate_generated_result(result, params)

    async def _handle_inspect(self, ctx: PipelineContext, params: Dict) -> Dict:
        from src.tools.pdf.pdf_inspector import inspect_pdf

        if not ctx.file_paths:
            return {"success": False, "error": "inspect 操作需要 file_paths 参数"}

        file_path = self._resolve_file(ctx.file_paths[0])
        result = inspect_pdf(file_path)
        if result.get("success"):
            return {"success": True, "inspection": result}
        return result

    async def _handle_render_pages(self, ctx: PipelineContext, params: Dict) -> Dict:
        from src.tools.pdf.pdf_renderer import render_pages

        if not ctx.file_paths:
            return {"success": False, "error": "render_pages 操作需要 file_paths 参数"}

        file_path = self._resolve_file(ctx.file_paths[0])
        return await asyncio.to_thread(
            render_pages,
            file_path,
            pages=params.get("pages"),
            dpi=params.get("dpi", 150),
            max_pages=params.get("max_pages", 10),
            renderer=params.get("renderer", "auto"),
        )

    async def _handle_validate(self, ctx: PipelineContext, params: Dict) -> Dict:
        from src.tools.pdf.pdf_validator import validate_pdf

        if not ctx.file_paths:
            return {"success": False, "error": "validate 操作需要 file_paths 参数"}

        file_path = self._resolve_file(ctx.file_paths[0])
        result = await asyncio.to_thread(
            validate_pdf,
            file_path,
            level=params.get("level", "structural"),
            pages=params.get("pages"),
            render_dpi=params.get("dpi", 150),
            max_pages=params.get("max_pages", 10),
        )
        return {"success": result.get("success", False), "validation": result}

    async def _handle_clean_metadata(self, ctx: PipelineContext, params: Dict) -> Dict:
        from src.tools.pdf.pdf_enhancer import clean_metadata

        if not ctx.file_paths:
            return {"success": False, "error": "clean_metadata 操作需要 file_paths 参数"}

        file_path = self._resolve_file(ctx.file_paths[0])
        result = clean_metadata(file_path, output_name=params.get("output_name"))
        return self._validate_generated_result(result, params)

    async def _handle_add_watermark(self, ctx: PipelineContext, params: Dict) -> Dict:
        from src.tools.pdf.pdf_enhancer import add_watermark

        if not ctx.file_paths:
            return {"success": False, "error": "add_watermark 操作需要 file_paths 参数"}

        text = params.get("text") or params.get("watermark_text")
        file_path = self._resolve_file(ctx.file_paths[0])
        result = add_watermark(
            file_path,
            text=text,
            output_name=params.get("output_name"),
            opacity=params.get("opacity", 0.18),
            font_size=params.get("font_size", 42),
            rotate=params.get("rotate", 0),
        )
        return self._validate_generated_result(result, params)

    async def _handle_protect(self, ctx: PipelineContext, params: Dict) -> Dict:
        from src.tools.pdf.pdf_enhancer import protect_pdf

        if not ctx.file_paths:
            return {"success": False, "error": "protect 操作需要 file_paths 参数"}

        password = params.get("password")
        file_path = self._resolve_file(ctx.file_paths[0])
        result = protect_pdf(
            file_path,
            password=password,
            output_name=params.get("output_name"),
        )
        # 加密后的 PDF 无法做普通内容校验，避免误报失败。
        return result

    async def _handle_compress(self, ctx: PipelineContext, params: Dict) -> Dict:
        from src.tools.pdf.pdf_enhancer import compress_pdf

        if not ctx.file_paths:
            return {"success": False, "error": "compress 操作需要 file_paths 参数"}

        file_path = self._resolve_file(ctx.file_paths[0])
        result = compress_pdf(file_path, output_name=params.get("output_name"))
        return self._validate_generated_result(result, params)

    async def _handle_extract_images(self, ctx: PipelineContext, params: Dict) -> Dict:
        from src.tools.pdf.pdf_enhancer import extract_images

        if not ctx.file_paths:
            return {"success": False, "error": "extract_images 操作需要 file_paths 参数"}

        file_path = self._resolve_file(ctx.file_paths[0])
        return extract_images(
            file_path,
            pages=params.get("pages"),
            output_dir=params.get("output_dir"),
        )

    async def _handle_rotate(self, ctx: PipelineContext, params: Dict) -> Dict:
        from src.tools.pdf.pdf_enhancer import rotate_pages

        if not ctx.file_paths:
            return {"success": False, "error": "rotate 操作需要 file_paths 参数"}

        rotation = params.get("rotation", 90)
        file_path = self._resolve_file(ctx.file_paths[0])
        result = rotate_pages(
            file_path,
            rotation=rotation,
            pages=params.get("pages"),
            output_name=params.get("output_name"),
        )
        return self._validate_generated_result(result, params)

    # ── 辅助方法 ──

    @staticmethod
    def _is_pdf_generation_instruction(text: str) -> bool:
        if not text:
            return False
        return bool(re.search(
            r"(生成|创建|导出|制作|转成|转换|保存).{0,30}(PDF|pdf|文件)|"
            r"(Markdown|markdown|HTML|html|Word|word|docx).{0,30}(转|转换|导出|生成).{0,20}(PDF|pdf)",
            text,
            re.IGNORECASE,
        ))

    @staticmethod
    def _looks_like_markdown_document(text: str) -> bool:
        lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
        if not lines:
            return False

        has_heading = any(re.match(r"^#{1,6}\s+\S+", line) for line in lines)
        has_table = any(line.startswith("|") and line.endswith("|") for line in lines)
        has_table_separator = any(re.match(r"^\|[\s\-:—–―|]+\|$", line) for line in lines)
        has_structured_label = any(re.match(r"^\*\*[^*]+?\*\*[：:]", line) for line in lines)
        has_list = any(re.match(r"^([-*+]|\d+\.)\s+\S+", line) for line in lines)

        score = sum([has_heading, has_table and has_table_separator, has_structured_label, has_list])
        return score >= 2 or (has_heading and has_table)

    @staticmethod
    def _looks_like_html(text: str) -> bool:
        if not text:
            return False
        return bool(re.search(r"<(html|body|table|div|section|article|h[1-6]|p|ul|ol)\b", text, re.IGNORECASE))

    def _extract_content_body(self, text: str, content_type: Optional[str]) -> str:
        normalized_type = (content_type or "").lower()
        if normalized_type in ("html", "htm") or self._looks_like_html(text):
            return self._extract_html_body(text)
        return self._extract_markdown_body(text)

    @staticmethod
    def _extract_markdown_body(text: str) -> str:
        """从 Agent 传入的混合 context 中提取 Markdown 正文，剥离工具指令前缀。"""
        if not text:
            return ""

        stripped = text.strip()
        fence = re.fullmatch(r"```(?:markdown|md)?\s*\n(.*?)\n```", stripped, flags=re.DOTALL | re.IGNORECASE)
        if fence:
            stripped = fence.group(1).strip()

        lines = stripped.splitlines()
        first_markdown_idx = None
        for idx, line in enumerate(lines):
            s = line.strip()
            if re.match(r"^#{1,6}\s+\S+", s) or (s.startswith("|") and s.endswith("|")):
                first_markdown_idx = idx
                break

        if not first_markdown_idx:
            return stripped

        prefix = "\n".join(lines[:first_markdown_idx]).strip()
        if not prefix:
            return stripped

        instruction_re = re.compile(
            r"(生成|创建|导出|制作|转成|转换|保存).{0,30}(PDF|pdf|文件)|"
            r"(格式|形式).{0,20}(Markdown|markdown|表格)|"
            r"(以下|下面).{0,20}(内容|正文|报告|行程|Markdown|markdown)",
            re.IGNORECASE,
        )
        if instruction_re.search(prefix):
            return "\n".join(lines[first_markdown_idx:]).strip()

        return stripped

    @staticmethod
    def _extract_html_body(text: str) -> str:
        """从混合 context 中提取 HTML 正文，避免自然语言指令破坏 HTML 结构。"""
        if not text:
            return ""

        stripped = text.strip()
        fence = re.fullmatch(r"```(?:html)?\s*\n(.*?)\n```", stripped, flags=re.DOTALL | re.IGNORECASE)
        if fence:
            stripped = fence.group(1).strip()

        match = re.search(r"<(?:!doctype\s+html|html|body|table|div|section|article|h[1-6]|p|ul|ol)\b", stripped, re.IGNORECASE)
        if match and match.start() > 0:
            prefix = stripped[:match.start()].strip()
            instruction_re = re.compile(
                r"(生成|创建|导出|制作|转成|转换|保存).{0,30}(PDF|pdf|文件)|"
                r"(以下|下面).{0,20}(HTML|html|内容|正文)",
                re.IGNORECASE,
            )
            if instruction_re.search(prefix):
                return stripped[match.start():].strip()
        return stripped

    @staticmethod
    def _extract_title(text: str) -> str:
        for line in (text or "").splitlines():
            match = re.match(r"^#{1,6}\s+(.+?)\s*$", line.strip())
            if match:
                return match.group(1).strip()
        return ""

    @staticmethod
    def _safe_file_stem(title: str) -> str:
        stem = re.sub(r'[\\/:*?"<>|\r\n\t]+', "", title).strip(" .")
        return stem[:80] or "pdf_document"

    @classmethod
    def _safe_output_name(cls, output_name: Optional[str]) -> Optional[str]:
        if not output_name:
            return None
        name = output_name.replace("\\", "/").split("/")[-1].strip()
        if name.lower().endswith(".pdf"):
            name = name[:-4]
        return f"{cls._safe_file_stem(name)}.pdf"

    def _resolve_file(self, file_path: str) -> str:
        """解析文件路径"""
        from src.tools.pdf.pdf_lib import PdfFileHandler
        resolved = PdfFileHandler.resolve_path(file_path)
        if not Path(resolved).exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")
        return resolved

    def _normalize_user_pages(self, file_path: str, pages: Optional[List[int]]) -> Optional[List[int]]:
        """外部页码 1-based，转换为内部 PyMuPDF/pdfplumber 使用的 0-based。"""
        if not pages:
            return None

        page_count = None
        try:
            from src.tools.pdf.pdf_lib import PdfFileHandler
            page_count = PdfFileHandler.get_page_count(file_path)
        except Exception:
            pass

        normalized = []
        for page in pages:
            try:
                p = int(page)
            except (TypeError, ValueError):
                continue
            if p < 1:
                continue
            idx = p - 1
            if page_count is not None and idx >= page_count:
                continue
            if idx not in normalized:
                normalized.append(idx)
        return normalized

    def _validate_generated_result(self, result: Dict, params: Dict) -> Dict:
        """对真实存在的生成结果追加 PDF 校验；mock 路径不存在时不阻断单测。"""
        if not result.get("success"):
            return result

        file_paths = []
        if result.get("file_path"):
            file_paths.append(result["file_path"])
        for item in result.get("files", []) or []:
            if item.get("file_path"):
                file_paths.append(item["file_path"])

        if not file_paths:
            return result

        validations = []
        warnings = list(result.get("warnings", []))
        from src.tools.pdf.pdf_validator import validate_pdf

        for file_path in file_paths:
            if not Path(file_path).exists():
                warnings.append(f"跳过PDF校验，文件不存在: {file_path}")
                continue
            validation = validate_pdf(
                file_path,
                level=params.get("validate_level", "structural"),
            )
            validations.append(validation)
            if not validation.get("success"):
                result["success"] = False
                result["error"] = "PDF生成后校验失败: " + "; ".join(validation.get("errors", []))
                result["validation"] = validation
                return result

        if len(validations) == 1:
            result["validation"] = validations[0]
        elif validations:
            result["validation"] = {
                "success": all(v.get("success") for v in validations),
                "level": params.get("validate_level", "structural"),
                "files": validations,
            }
        if warnings:
            result["warnings"] = warnings
        return result
