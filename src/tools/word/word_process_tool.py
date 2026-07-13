"""
Word 文档处理工具 — Agent 唯一入口

Agent 优先传 instruction（用户目的）+ content（待处理正文）+ file_paths（附件），
旧版 context（用户需求 + 相关内容）继续兼容。工具内部自动判断操作类型和参数，
执行 pipeline 后返回结果。
"""

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger
from pydantic import BaseModel, Field

from src.tools.base import BaseTool


class TaskType:
    """有效操作类型常量"""
    READ = "read"
    ANALYZE = "analyze"
    WORD_TO_MD = "word_to_md"
    MD_TO_WORD = "md_to_word"
    MODIFY = "modify"
    FORMAT = "format"
    FILL_TEMPLATE = "fill_template"
    LIST_TEMPLATES = "list_templates"
    DIFF = "diff"

    ALL = {READ, ANALYZE, WORD_TO_MD, MD_TO_WORD, MODIFY, FORMAT,
           FILL_TEMPLATE, LIST_TEMPLATES, DIFF}


class WordProcessInput(BaseModel):
    instruction: Optional[str] = Field(
        None,
        description="用户目的描述，如生成Word、读取、修改、格式化、对比文档。"
                    "生成Word时建议只放目的，不要混入正文。"
    )
    content: Optional[str] = Field(
        None,
        description="待处理正文内容。生成Word时优先使用此字段传完整 Markdown 文本，"
                    "不要把'生成一份Word'等工具指令写入正文。"
    )
    content_type: Optional[str] = Field(
        None,
        description="content 的格式类型，可选：markdown、text、auto。默认自动识别。"
    )
    output_name: Optional[str] = Field(
        None,
        description="输出文件名，可选。生成Word时可传入业务文件名，如 安顺坝陵河3天2晚行程.docx"
    )
    context: Optional[str] = Field(
        None,
        description="兼容旧调用：用户的原始需求描述和相关内容。"
                    "如果要把对话中的内容转为Word，这里必须包含完整的 Markdown 文本；"
                    "新调用建议改用 instruction + content。"
    )
    file_paths: Optional[List[str]] = Field(
        None,
        description="附件文件路径列表（用户上传的 .docx/.md 文件等）"
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


TOOL_DESCRIPTION = """Word文档处理工具。所有与Word文档(.docx)相关的操作都通过本工具处理。

⚠️ 触发规则 — 遇到以下场景必须调用本工具：
- 用户要求生成、导出、创建Word文档
- 用户要求读取、分析、修改、格式化、对比Word文件
- 用户上传了.docx文件并要求处理
不要自己生成文件内容，一律交给本工具。

调用方式：
- 推荐使用 instruction + content：instruction 放用户目的，content 放待转换 Markdown 正文
- 兼容旧调用：也可以将用户的原始需求描述和相关内容放在 context 中
- 如果需要将对话内容转为Word，必须在 content 或 context 中包含完整的 Markdown 文本
- output_name 可传入业务文件名；不传时工具会从 Markdown 标题推断
- 用户上传的附件路径放在 file_paths 中
- 生成Word时无需指定模板路径，系统会自动使用内置默认模板。不要向用户索要模板路径。
工具会自动判断并执行合适的操作。

📦 生成文件后必须用 cp 注册下载（重要）：
当本工具产生新的 .docx 文件时（生成/修改/格式化/填充模板，返回结果中含 file_path），
必须紧接着调用 cp 工具完成交付，用户才能在前端看到并下载：
    cp(source_file_path="<本工具返回的 file_path>", display_name="<面向用户的业务文件名>")
cp 会把文件复制到下载目录、在前端对话中展示下载卡片。
display_name 必须使用用户能理解的业务文件名，不要使用工具临时文件名。
仅读取/分析/转Markdown/对比（read/analyze/word_to_md/diff）不产生新文件，无需调用 cp。"""


class WordProcessTool(BaseTool):
    name = "word_process"
    description = TOOL_DESCRIPTION
    display_name = "Word文档处理"
    category = "file"
    InputModel = WordProcessInput

    def __init__(self):
        super().__init__()
        self._router = None
        # tenant_id / user_id 注入（由 Agent 钩子调用，或通过 ContextVar 兜底）
        self._tenant_id: Optional[str] = None
        self._user_id: Optional[str] = None

    def set_tenant_id(self, tenant_id: str):
        """由 Agent 注入 tenant_id，用于 md_to_word 的 image_inliner。"""
        self._tenant_id = tenant_id

    def set_user_id(self, user_id: str):
        """由 Agent 注入 user_id。"""
        self._user_id = user_id

    def _get_router(self):
        if self._router is None:
            from src.tools.word.word_router import WordRouter
            self._router = WordRouter()
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
            return {"success": False, "error": f"Word工具无法理解需求：{error}。请在 context 中详细描述需求"}

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
                logger.error(f"Word pipeline error at {op}: {e}", exc_info=True)
                return {"success": False, "error": f"操作 {op} 执行失败: {str(e)}"}

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
        normalized_output_name = (output_name or "").strip() or None

        if normalized_context and not normalized_content:
            body = self._extract_markdown_body(normalized_context)
            if body and self._looks_like_markdown_document(body):
                normalized_content = body
                if not normalized_instruction and body != normalized_context:
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
            "analyze": self._handle_analyze,
            "word_to_md": self._handle_word_to_md,
            "md_to_word": self._handle_md_to_word,
            "modify": self._handle_modify,
            "format": self._handle_format,
            "fill_template": self._handle_fill_template,
            "list_templates": self._handle_list_templates,
            "diff": self._handle_diff,
        }
        return handlers.get(op)

    async def _resolve_task(
        self,
        context: Optional[str],
        file_paths: Optional[List[str]],
        instruction: Optional[str] = None,
        content_type: Optional[str] = None,
    ) -> Dict:
        """通过内部 LLM 路由决定操作类型和参数。

        Returns:
            {"task": "md_to_word", "params": {...}} 或 {"task": "", "error": "..."}
        """
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
            logger.error(f"[WordProcess] LLM 路由异常: {e}", exc_info=True)
            return {"task": "", "error": f"路由服务异常: {e}"}

    def _resolve_task_deterministic(
        self,
        context: Optional[str],
        file_paths: Optional[List[str]],
        instruction: Optional[str] = None,
        content_type: Optional[str] = None,
    ) -> Optional[Dict]:
        """低风险确定性路由，减少纯 Markdown 生成 Word 时对内部 LLM 的依赖。"""
        if not context:
            return None

        paths = file_paths or []
        has_word_file = any(str(p).lower().endswith((".doc", ".docx")) for p in paths)
        if has_word_file:
            return None

        markdown_body = self._extract_markdown_body(context)
        explicit_markdown_to_word = (
            (content_type or "").lower() in ("markdown", "md")
            and self._is_word_generation_instruction(instruction or context)
        )
        if not explicit_markdown_to_word and not self._looks_like_markdown_document(markdown_body):
            return None

        title = self._extract_title(markdown_body)
        params: Dict[str, Any] = {"template": "default"}
        if title:
            params["title"] = title
            params["output_name"] = f"{self._safe_file_stem(title)}.docx"

        return {
            "task": "md_to_word",
            "params": params,
            "reason": "检测到 context 已包含完整 Markdown 文档内容，直接转 Word",
        }

    @staticmethod
    def _is_word_generation_instruction(text: str) -> bool:
        if not text:
            return False
        return bool(re.search(
            r"(生成|创建|导出|制作|转成|转换|保存).{0,30}(Word|word|文档|docx)",
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
    def _extract_markdown_body(text: str) -> str:
        """从 Agent 传入的混合 context 中提取正文，剥离给工具的指令前缀。"""
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
            r"(生成|创建|导出|制作|转成|转换|保存).{0,30}(Word|word|文档|docx)|"
            r"(格式|形式).{0,20}(Markdown|markdown|表格)|"
            r"(以下|下面).{0,20}(内容|正文|行程|Markdown|markdown)",
            re.IGNORECASE,
        )
        if instruction_re.search(prefix):
            return "\n".join(lines[first_markdown_idx:]).strip()

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
        return stem[:80] or "word_document"

    @classmethod
    def _safe_output_name(cls, output_name: Optional[str]) -> Optional[str]:
        if not output_name:
            return None
        name = output_name.replace("\\", "/").split("/")[-1].strip()
        if name.lower().endswith(".docx"):
            name = name[:-5]
        return f"{cls._safe_file_stem(name)}.docx"

    def _update_context(self, ctx: PipelineContext, op: str, result: Dict) -> None:
        """根据操作结果更新 pipeline 上下文"""
        if op in ("read", "analyze", "word_to_md", "diff"):
            ctx.read_content = (
                result.get("content")
                or result.get("markdown")
                or result.get("structure")
                or result.get("diff_text")
            )

        if op in ("md_to_word", "modify", "format", "fill_template"):
            if result.get("file_path"):
                ctx.file_paths = [result["file_path"]]

    def _merge_results(self, ctx: PipelineContext) -> Dict[str, Any]:
        """合并所有步骤的结果"""
        merged = {"success": True, "steps": len(ctx.results)}

        for r in ctx.results:
            op = r["operation"]
            if op == "read":
                merged["content"] = r.get("content", "")
                merged["paragraphs"] = r.get("paragraphs", [])
                merged["tables"] = r.get("tables", [])
                merged["document_info"] = r.get("document_info", {})
            elif op == "analyze":
                merged["structure"] = r.get("structure", {})
            elif op == "word_to_md":
                merged["markdown"] = r.get("markdown", "")
            elif op == "diff":
                merged["diff_text"] = r.get("diff_text", "")
                merged["changes"] = r.get("changes", [])
                merged["change_count"] = r.get("change_count", 0)
                merged["summary"] = r.get("summary", "")
            elif op in ("md_to_word", "modify", "format", "fill_template"):
                merged["file_path"] = r.get("file_path", "")
                merged["file_size"] = r.get("file_size", 0)
                if r.get("file_id"):
                    merged["file_id"] = r["file_id"]
                if r.get("download_url"):
                    merged["download_url"] = r["download_url"]
                if op == "fill_template":
                    merged["variables_replaced"] = r.get("variables_replaced", 0)
                if op == "modify":
                    merged["operations_applied"] = r.get("operations_applied", 0)
                if op == "format":
                    merged["operations_applied"] = r.get("operations_applied", 0)
            elif op == "list_templates":
                merged["templates"] = r.get("templates", [])

        if ctx.file_paths:
            merged["final_file_path"] = ctx.file_paths[0]

        return merged

    # ── 各操作处理器 ──

    async def _handle_read(self, ctx: PipelineContext, params: Dict) -> Dict:
        from src.tools.word.word_reader import read_content

        if not ctx.file_paths:
            return {"success": False, "error": "read 操作需要 file_paths 参数"}

        file_path = ctx.file_paths[0]
        return read_content(file_path)

    async def _handle_analyze(self, ctx: PipelineContext, params: Dict) -> Dict:
        from src.tools.word.word_reader import analyze_structure

        if not ctx.file_paths:
            return {"success": False, "error": "analyze 操作需要 file_paths 参数"}

        file_path = ctx.file_paths[0]
        result = analyze_structure(file_path, detailed=params.get("detailed", False))
        if result.get("success"):
            return {"success": True, "structure": result}
        return result

    async def _handle_word_to_md(self, ctx: PipelineContext, params: Dict) -> Dict:
        from src.tools.word.word_to_md import convert

        if not ctx.file_paths:
            return {"success": False, "error": "word_to_md 操作需要 file_paths 参数"}

        file_path = ctx.file_paths[0]
        md_text = convert(file_path)
        return {
            "success": True,
            "markdown": md_text,
            "file_path": file_path,
        }

    async def _handle_md_to_word(self, ctx: PipelineContext, params: Dict) -> Dict:
        from src.tools.word.md_to_word import convert_async, save_as

        md_text = ctx.content or ctx.context
        if not md_text and ctx.file_paths:
            md_path = ctx.file_paths[0]
            if Path(md_path).exists():
                md_text = Path(md_path).read_text(encoding="utf-8")

        if not md_text:
            return {"success": False, "error": "md_to_word 需要提供 context（Markdown文本）或 file_paths（.md文件路径）"}

        md_text = self._extract_markdown_body(md_text)
        template = params.get("template")
        title = params.get("title") or self._extract_title(md_text)
        author = params.get("author", "")
        output_name = (
            self._safe_output_name(ctx.output_name)
            or self._safe_output_name(params.get("output_name"))
            or (f"{self._safe_file_stem(title)}.docx" if title else None)
        )

        # 双轨获取 tenant_id：注入优先，ContextVar 兜底（HTTP 请求场景）
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

        # 直接 await convert_async，避免 nested event loop
        doc = await convert_async(
            md_text,
            template=template,
            title=title,
            author=author,
            tenant_id=tenant_id,
            user_id=user_id,
        )
        result = save_as(doc, file_name=output_name)
        result["success"] = True
        result["message"] = "已生成Word文档"

        return result

    async def _handle_modify(self, ctx: PipelineContext, params: Dict) -> Dict:
        from src.tools.word.word_modifier import batch_modify
        from src.tools.word.word_lib import WordFileHandler

        if not ctx.file_paths:
            return {"success": False, "error": "modify 操作需要 file_paths 参数"}

        file_path = ctx.file_paths[0]
        operations = params.get("operations", [])
        if not operations:
            return {"success": False, "error": "modify 操作需要 params.operations 参数"}

        doc, info = WordFileHandler.copy_and_open(file_path)

        result = batch_modify(doc, operations)
        if not result["success"]:
            return result

        output_name = params.get("output_name") or (Path(file_path).stem + "_modified.docx")
        save_result = WordFileHandler.save_temp(doc, file_name=output_name)
        save_result["success"] = True
        save_result["operations_applied"] = result["operations_applied"]
        save_result["message"] = f"已完成{result['operations_applied']}项修改操作"

        return save_result

    async def _handle_format(self, ctx: PipelineContext, params: Dict) -> Dict:
        from src.tools.word.word_formatter import batch_format
        from src.tools.word.word_lib import WordFileHandler

        if not ctx.file_paths:
            return {"success": False, "error": "format 操作需要 file_paths 参数"}

        file_path = ctx.file_paths[0]
        operations = params.get("format_operations") or params.get("operations", [])
        if not operations:
            return {"success": False, "error": "format 操作需要 params.format_operations 参数"}

        doc, info = WordFileHandler.copy_and_open(file_path)

        result = batch_format(doc, operations)
        if not result["success"]:
            return result

        output_name = params.get("output_name") or (Path(file_path).stem + "_formatted.docx")
        save_result = WordFileHandler.save_temp(doc, file_name=output_name)
        save_result["success"] = True
        save_result["operations_applied"] = result["operations_applied"]
        save_result["message"] = f"已完成{result['operations_applied']}项格式化操作"

        return save_result

    async def _handle_fill_template(self, ctx: PipelineContext, params: Dict) -> Dict:
        from src.tools.word.template_manager import fill_template
        from src.tools.word.word_lib import WordFileHandler

        if not ctx.file_paths:
            return {"success": False, "error": "fill_template 操作需要 file_paths 参数"}

        file_path = ctx.file_paths[0]
        variables = params.get("variables", {})
        if not variables:
            return {"success": False, "error": "fill_template 操作需要 params.variables 参数"}

        doc, info = WordFileHandler.copy_and_open(file_path)

        count = fill_template(doc, variables)

        output_name = params.get("output_name") or (Path(file_path).stem + "_filled.docx")
        save_result = WordFileHandler.save_temp(doc, file_name=output_name)
        save_result["success"] = True
        save_result["variables_replaced"] = count
        save_result["message"] = f"已替换{count}个模板变量"

        return save_result

    async def _handle_list_templates(self, ctx: PipelineContext, params: Dict) -> Dict:
        from src.tools.word.template_manager import list_templates

        templates = list_templates()
        return {"success": True, "templates": templates}

    async def _handle_diff(self, ctx: PipelineContext, params: Dict) -> Dict:
        from src.tools.word.word_differ import diff

        if not ctx.file_paths or len(ctx.file_paths) < 2:
            return {"success": False, "error": "diff 操作需要 file_paths 参数（两个文件路径：[旧版本, 新版本]）"}

        output_format = params.get("output_format", "text")
        return diff(ctx.file_paths[0], ctx.file_paths[1], output_format=output_format)
