"""
PDF 文档处理工具 — Agent 唯一入口

Agent 只需传 context（用户需求 + 相关内容）和 file_paths（附件），
内部 LLM 自动判断操作类型和参数，执行 pipeline 后返回结果。
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger
from pydantic import BaseModel, Field

from src.tools.base import BaseTool


class TaskType:
    """有效操作类型常量"""
    READ = "read"
    READ_TABLES = "read_tables"
    OCR = "ocr"
    PDF_TO_MD = "pdf_to_md"
    MD_TO_PDF = "md_to_pdf"
    HTML_TO_PDF = "html_to_pdf"
    DOCX_TO_PDF = "docx_to_pdf"
    MERGE = "merge"
    SPLIT = "split"
    EXTRACT_PAGES = "extract_pages"

    ALL = {READ, READ_TABLES, OCR, PDF_TO_MD, MD_TO_PDF,
           HTML_TO_PDF, DOCX_TO_PDF, MERGE, SPLIT, EXTRACT_PAGES}


class PdfProcessInput(BaseModel):
    context: Optional[str] = Field(
        None,
        description="用户的原始需求描述和相关内容。"
                    "如果要把对话中的内容转为PDF，这里必须包含完整的 Markdown 或 HTML 文本；"
                    "如果涉及附件操作，附件路径通过 file_paths 传入"
    )
    file_paths: Optional[List[str]] = Field(
        None,
        description="附件文件路径列表（用户上传的 .pdf/.docx/.md/.html 文件等）"
    )


class PipelineContext:
    """Pipeline 执行过程中的上下文，在步骤间传递"""

    def __init__(self, file_paths: Optional[List[str]] = None,
                 context: Optional[str] = None):
        self.file_paths: List[str] = list(file_paths) if file_paths else []
        self.original_file_paths: List[str] = list(file_paths) if file_paths else []
        self.context: Optional[str] = context
        self.read_content: Optional[str] = None
        self.results: List[Dict] = []


TOOL_DESCRIPTION = """PDF文档处理工具。所有与PDF文件相关的操作都通过本工具处理。

⚠️ 触发规则 — 遇到以下场景必须调用本工具：
- 用户要求读取、查看PDF文件内容
- 用户要求提取PDF中的表格
- 用户要求OCR识别PDF（扫描件）
- 用户要求将PDF转为Markdown
- 用户要求将Markdown/HTML/Word转为PDF
- 用户要求合并、拆分、提取PDF页面
- 用户上传了.pdf文件并要求处理
不要自己处理PDF文件，一律交给本工具。

调用方式：
- 将用户的原始需求描述和相关内容放在 context 中
- 如果需要将对话内容转为PDF，context 中必须包含完整的 Markdown 或 HTML 文本
- 用户上传的附件路径放在 file_paths 中
工具会自动判断并执行合适的操作。"""


class PdfProcessTool(BaseTool):
    name = "pdf_process"
    description = TOOL_DESCRIPTION
    display_name = "PDF文档处理"
    category = "file"
    InputModel = PdfProcessInput

    def __init__(self):
        super().__init__()
        self._router = None

    def _get_router(self):
        if self._router is None:
            from src.tools.pdf.pdf_router import PdfRouter
            self._router = PdfRouter()
        return self._router

    async def execute(self, **kwargs) -> Dict[str, Any]:
        context = kwargs.get("context")
        file_paths = kwargs.get("file_paths")

        # 内部 LLM 路由决定操作类型和参数
        route_result = await self._resolve_task(context, file_paths)
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
        ctx = PipelineContext(file_paths=file_paths, context=context)

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
                logger.error(f"PDF pipeline error at {op}: {e}", exc_info=True)
                return {"success": False, "error": f"操作 {op} 执行失败: {str(e)}"}

            if not step_result.get("success", True):
                step_result["failed_at"] = op
                return step_result

            step_result["operation"] = op
            ctx.results.append(step_result)
            self._update_context(ctx, op, step_result)

        return self._merge_results(ctx)

    def _get_handler(self, op: str):
        handlers = {
            "read": self._handle_read,
            "read_tables": self._handle_read_tables,
            "ocr": self._handle_ocr,
            "pdf_to_md": self._handle_pdf_to_md,
            "md_to_pdf": self._handle_md_to_pdf,
            "html_to_pdf": self._handle_html_to_pdf,
            "docx_to_pdf": self._handle_docx_to_pdf,
            "merge": self._handle_merge,
            "split": self._handle_split,
            "extract_pages": self._handle_extract_pages,
        }
        return handlers.get(op)

    async def _resolve_task(self, context: Optional[str], file_paths: Optional[List[str]]) -> Dict:
        if not context and not file_paths:
            return {"task": "", "error": "缺少 context 和 file_paths"}

        try:
            router = self._get_router()
            return await router.route(context, file_paths)
        except Exception as e:
            logger.error(f"[PdfProcess] LLM 路由异常: {e}", exc_info=True)
            return {"task": "", "error": f"路由服务异常: {e}"}

    def _update_context(self, ctx: PipelineContext, op: str, result: Dict) -> None:
        """根据操作结果更新 pipeline 上下文"""
        if op in ("read", "pdf_to_md", "ocr"):
            ctx.read_content = (
                result.get("content")
                or result.get("markdown")
            )

        if op in ("md_to_pdf", "html_to_pdf", "docx_to_pdf", "merge", "split", "extract_pages"):
            if result.get("file_path"):
                ctx.file_paths = [result["file_path"]]
            elif result.get("files"):
                ctx.file_paths = [f["file_path"] for f in result["files"] if f.get("file_path")]

    def _merge_results(self, ctx: PipelineContext) -> Dict[str, Any]:
        """合并所有步骤的结果"""
        merged = {"success": True, "steps": len(ctx.results)}

        for r in ctx.results:
            op = r["operation"]
            if op == "read":
                merged["content"] = r.get("content", "")
                merged["pages"] = r.get("pages", [])
                merged["metadata"] = r.get("metadata", {})
            elif op == "read_tables":
                merged["tables"] = r.get("tables", [])
                merged["table_count"] = r.get("count", 0)
            elif op == "ocr":
                merged["content"] = r.get("content", "")
                merged["page_count"] = r.get("page_count", 0)
            elif op == "pdf_to_md":
                merged["markdown"] = r.get("markdown", "")
                merged["source"] = r.get("source", "text_extract")
            elif op in ("md_to_pdf", "html_to_pdf", "docx_to_pdf"):
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

        if ctx.file_paths:
            merged["final_file_path"] = ctx.file_paths[0]

        return merged

    # ── 各操作处理器 ──

    async def _handle_read(self, ctx: PipelineContext, params: Dict) -> Dict:
        from src.tools.pdf.pdf_reader import read_text

        if not ctx.file_paths:
            return {"success": False, "error": "read 操作需要 file_paths 参数"}

        file_path = self._resolve_file(ctx.file_paths[0])
        pages = params.get("pages")
        return read_text(file_path, pages=pages)

    async def _handle_read_tables(self, ctx: PipelineContext, params: Dict) -> Dict:
        from src.tools.pdf.pdf_reader import extract_tables

        if not ctx.file_paths:
            return {"success": False, "error": "read_tables 操作需要 file_paths 参数"}

        file_path = self._resolve_file(ctx.file_paths[0])
        pages = params.get("pages")
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

        import asyncio
        return await asyncio.to_thread(convert_smart, file_path)

    async def _handle_md_to_pdf(self, ctx: PipelineContext, params: Dict) -> Dict:
        from src.tools.pdf.pdf_writer import md_to_pdf

        md_text = ctx.context
        if not md_text and ctx.file_paths:
            md_path = ctx.file_paths[0]
            if Path(md_path).exists():
                md_text = Path(md_path).read_text(encoding="utf-8")

        if not md_text:
            return {"success": False, "error": "md_to_pdf 需要提供 context（Markdown文本）或 file_paths（.md文件路径）"}

        import asyncio
        result = await asyncio.to_thread(
            md_to_pdf,
            md_text=md_text,
            output_name=params.get("output_name"),
            css=params.get("css"),
            title=params.get("title", ""),
        )

        if result.get("success") and result.get("file_path"):
            download_info = await self._register_download(result["file_path"], params.get("output_name") or "document.pdf")
            if download_info:
                result["file_id"] = download_info["file_id"]
                result["download_url"] = download_info["download_url"]

        return result

    async def _handle_html_to_pdf(self, ctx: PipelineContext, params: Dict) -> Dict:
        from src.tools.pdf.pdf_writer import html_to_pdf

        html_text = ctx.context
        if not html_text and ctx.file_paths:
            html_path = ctx.file_paths[0]
            if Path(html_path).exists():
                html_text = Path(html_path).read_text(encoding="utf-8")

        if not html_text:
            return {"success": False, "error": "html_to_pdf 需要提供 context（HTML文本）或 file_paths（.html文件路径）"}

        import asyncio
        result = await asyncio.to_thread(
            html_to_pdf,
            html_text=html_text,
            output_name=params.get("output_name"),
        )

        if result.get("success") and result.get("file_path"):
            download_info = await self._register_download(result["file_path"], params.get("output_name") or "document.pdf")
            if download_info:
                result["file_id"] = download_info["file_id"]
                result["download_url"] = download_info["download_url"]

        return result

    async def _handle_docx_to_pdf(self, ctx: PipelineContext, params: Dict) -> Dict:
        from src.tools.pdf.pdf_writer import docx_to_pdf

        if not ctx.file_paths:
            return {"success": False, "error": "docx_to_pdf 操作需要 file_paths 参数"}

        file_path = self._resolve_file(ctx.file_paths[0])

        import asyncio
        result = await asyncio.to_thread(
            docx_to_pdf,
            file_path=file_path,
            output_name=params.get("output_name"),
        )

        if result.get("success") and result.get("file_path"):
            download_info = await self._register_download(result["file_path"], params.get("output_name") or "document.pdf")
            if download_info:
                result["file_id"] = download_info["file_id"]
                result["download_url"] = download_info["download_url"]

        return result

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

        if result.get("success") and result.get("file_path"):
            download_info = await self._register_download(result["file_path"], params.get("output_name") or "merged.pdf")
            if download_info:
                result["file_id"] = download_info["file_id"]
                result["download_url"] = download_info["download_url"]

        return result

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

        # 为每个拆分文件注册下载
        if result.get("success") and result.get("files"):
            for f in result["files"]:
                if f.get("file_path"):
                    download_info = await self._register_download(f["file_path"], Path(f["file_path"]).name)
                    if download_info:
                        f["file_id"] = download_info["file_id"]
                        f["download_url"] = download_info["download_url"]

        return result

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

        if result.get("success") and result.get("file_path"):
            download_info = await self._register_download(result["file_path"], params.get("output_name") or "extracted.pdf")
            if download_info:
                result["file_id"] = download_info["file_id"]
                result["download_url"] = download_info["download_url"]

        return result

    # ── 辅助方法 ──

    def _resolve_file(self, file_path: str) -> str:
        """解析文件路径"""
        from src.tools.pdf.pdf_lib import PdfFileHandler
        resolved = PdfFileHandler.resolve_path(file_path)
        if not Path(resolved).exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")
        return resolved

    async def _register_download(self, file_path: str, display_name: str) -> Dict[str, str]:
        """自动注册文件到下载系统。"""
        try:
            from src.core.redis_client import redis_client
            import uuid

            src = Path(file_path)
            if not src.exists():
                logger.warning(f"注册下载文件失败: 文件不存在 {file_path}")
                return {}

            file_id = f"file_{uuid.uuid4().hex[:12]}"

            suffix = src.suffix.lower()
            mime_type_map = {
                '.pdf': 'application/pdf',
                '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                '.txt': 'text/plain',
                '.png': 'image/png',
                '.jpg': 'image/jpeg',
            }
            mime_type = mime_type_map.get(suffix, 'application/octet-stream')

            if not display_name.lower().endswith(suffix):
                display_name += suffix

            file_size = src.stat().st_size

            file_info = {
                "file_id": file_id,
                "name": display_name,
                "path": str(src.absolute()),
                "size": file_size,
                "mime_type": mime_type,
                "type": "image" if mime_type.startswith("image/") else "file",
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
