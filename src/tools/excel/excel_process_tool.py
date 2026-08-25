"""
Excel 电子表格处理工具 — Agent 唯一入口

Agent 优先传 instruction（用户目的）+ content（待导出数据）+ file_paths（附件），
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
    TO_MD = "to_md"
    EXPORT = "export"
    MODIFY = "modify"
    FORMAT = "format"
    FILL_TEMPLATE = "fill_template"
    LIST_TEMPLATES = "list_templates"
    MERGE = "merge"
    CONVERT = "convert"

    ALL = {READ, TO_MD, EXPORT, MODIFY, FORMAT,
           FILL_TEMPLATE, LIST_TEMPLATES, MERGE, CONVERT}


class ExcelProcessInput(BaseModel):
    instruction: Optional[str] = Field(
        None,
        description="用户目的描述，如导出Excel、读取、修改、格式化、合并、转换。"
                    "导出Excel时建议只放目的，不要混入表格数据。"
    )
    content: Optional[str] = Field(
        None,
        description="待导出的表格数据。导出Excel时优先使用此字段传 Markdown表格、CSV 或 JSON。"
    )
    content_type: Optional[str] = Field(
        None,
        description="content 的格式类型，可选：markdown、csv、json、auto。默认自动识别。"
    )
    output_name: Optional[str] = Field(
        None,
        description="输出文件名，可选。导出Excel时可传入业务文件名，如 报价单.xlsx"
    )
    context: Optional[str] = Field(
        None,
        description="兼容旧调用：用户的原始需求描述，包含所有相关内容。"
                    "如果要把对话中的表格数据转为Excel，context 中必须包含表格数据（Markdown表格或JSON数组）；"
                    "新调用建议改用 instruction + content。"
    )
    file_paths: Optional[List[str]] = Field(
        None,
        description="用户上传的附件文件路径列表（.xlsx/.csv/.json 文件等）"
    )
    data: Optional[Dict[str, Any]] = Field(
        None,
        description="按样例版式填充的结构化数据（AI 模板填充模式）。"
                    "形如 {meta:{...}, rows:[{...}], group_subtotals:{...}, totals:{...}}。"
                    "**所有值必须是标量（str/int/float/bool），一格一值，不得嵌套 dict/list**："
                    "totals 形如 {\"grand_total\": 9520, \"per_capita\": {\"成人人均\": 238}}；"
                    "按列/人数档位分列的合计须拆成独立标量键（如 合计总价_40人: 9520），"
                    "多值明细转为多行放入 rows。"
                    "提供 data + 样例附件(file_paths) 时走智能模板填充：AI 分析样例结构并按版式填入，"
                    "自动处理行数多/少/相等、保留样例样式。"
                    "**模板填充时数据必须放本字段（不要写进 instruction 文本）；普通数据导出不用本字段。**"
    )


class PipelineContext:
    """Pipeline 执行过程中的上下文，在步骤间传递"""

    def __init__(self, file_paths: Optional[List[str]] = None,
                 context: Optional[str] = None,
                 instruction: Optional[str] = None,
                 content: Optional[str] = None,
                 content_type: Optional[str] = None,
                 output_name: Optional[str] = None,
                 data: Optional[Dict[str, Any]] = None):
        self.file_paths: List[str] = list(file_paths) if file_paths else []
        self.original_file_paths: List[str] = list(file_paths) if file_paths else []
        self.context: Optional[str] = context
        self.instruction: Optional[str] = instruction
        self.content: Optional[str] = content
        self.content_type: Optional[str] = content_type
        self.output_name: Optional[str] = output_name
        self.data: Optional[Dict[str, Any]] = data
        self.read_data: Optional[Dict] = None
        self.markdown_content: Optional[str] = None
        self.results: List[Dict] = []


TOOL_DESCRIPTION = """Excel电子表格处理工具。处理Excel(.xlsx/.csv)文件的读取、创建、修改、格式化、模板填充、格式转换等操作。

⚠️ 触发规则 — 遇到以下场景必须调用本工具：
- 用户要求导出、生成、创建Excel文件
- 用户要求读取、修改、格式化Excel文件
- 用户要求将数据(表格、CSV、JSON)转为Excel
- 用户要求将Excel转为其他格式(Markdown、CSV)
- 用户要求基于模板填充数据生成Excel报告
- 用户上传了.xlsx/.csv文件并要求处理

🚫 以下场景不要调用本工具，请使用对应专用工具：
- 用户要求对数据进行**统计分析、趋势分析、对比分析、异常检测**等 → 调用 analyze_data 工具
- 用户要求**生成图表**（柱状图、折线图、饼图等） → 调用 analyze_data 工具
- 用户要求从多个数据源关联分析 → 调用 analyze_data 工具

调用方式（重要）：
- 推荐使用 instruction + content：instruction 放用户目的，content 放待导出的完整表格数据
- 兼容旧调用：也可以将用户的原始需求描述和相关内容放在 context 中
- 如果需要将数据导出为Excel，content 或 context 中必须包含完整的 Markdown表格、CSV 或 JSON数组 数据
- 如果当前对话中已有表格数据（由其他工具生成或用户提供），必须将其完整放入 content 中
- output_name 可传入业务文件名
- 如果还没有表格数据，Agent 应先通过其他方式准备好数据，再调用本工具
- file_paths 支持直接传 file_id（如 file_xxx，推荐，工具自动解析为真实路径）或文件相对路径，禁止自行拼接 storage/uploads 等目录路径
工具会自动判断并执行合适的操作。

📦 生成文件后必须用 cp 注册下载（重要）：
当本工具产生新的 Excel 文件时（导出/修改/格式化/填充模板/合并/转换，返回结果中含 file_path），
必须紧接着调用 cp 工具完成交付，用户才能在前端看到并下载：
    cp(source_file_path="<本工具返回的 file_path>", display_name="<面向用户的业务文件名>")
cp 会把文件复制到下载目录、在前端对话中展示下载卡片。
display_name 必须使用用户能理解的业务文件名，不要使用工具临时文件名。
仅读取/转Markdown（read/to_md）不产生新文件，无需调用 cp。"""


class ExcelProcessTool(BaseTool):
    name = "excel_process"
    description = TOOL_DESCRIPTION
    display_name = "Excel电子表格处理"
    category = "file"
    InputModel = ExcelProcessInput

    def __init__(self):
        super().__init__()
        self._router = None
        # tenant_id / user_id 注入（由 Agent 主循环 hasattr 钩子自动调用，或 ContextVar 兜底）
        self._tenant_id: Optional[str] = None
        self._user_id: Optional[str] = None

    def set_tenant_id(self, tenant_id: str):
        """由 Agent 注入 tenant_id（与 Word/Pdf 工具一致）。"""
        self._tenant_id = tenant_id

    def set_user_id(self, user_id: str):
        """由 Agent 注入 user_id。"""
        self._user_id = user_id

    def _resolve_tenant_user(self):
        """双轨获取 tenant_id/user_id：注入优先，ContextVar 兜底（HTTP 请求场景）。"""
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

    def _resolve_output_dir(self) -> Optional[str]:
        """注入的 tenant 优先构造输出目录；未注入返回 None（让 save_temp 走 ContextVar）。

        路径: storage/tenants/{tenant_id}/conversation/
        无 tenant_id: storage/tenants/_anonymous/conversation/
        """
        if not self._tenant_id and not self._user_id:
            return None
        try:
            from src.core.storage import ensure_tenant_storage_dir
            tid = self._tenant_id or "_anonymous"
            # user_id 不进路径，仅作元数据
            return str(ensure_tenant_storage_dir(tid, "conversation"))
        except Exception as e:
            logger.warning(f"[ExcelProcess] 解析输出目录失败，回退默认: {e}")
            return None

    def _resolve_file(self, file_path: str) -> str:
        """解析 file_id 或相对路径为磁盘绝对路径

        Agent 传的 file_paths 可能是 file_id（如 file_e300d0d5befc）或相对路径，
        不能直接当磁盘路径用。通过 ExcelFileHandler.resolve_path 走 Redis 元数据
        + 新旧目录兜底扫描，找不到抛 FileNotFoundError。
        """
        from src.tools.excel.excel_lib import ExcelFileHandler
        resolved = ExcelFileHandler.resolve_path(file_path)
        if not Path(resolved).exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")
        return resolved

    def _get_router(self):
        if self._router is None:
            from src.tools.excel.excel_router import ExcelRouter
            self._router = ExcelRouter()
        return self._router

    async def execute(self, **kwargs) -> Dict[str, Any]:
        context = kwargs.get("context")
        instruction = kwargs.get("instruction")
        content = kwargs.get("content")
        content_type = kwargs.get("content_type")
        output_name = kwargs.get("output_name")
        file_paths = kwargs.get("file_paths")
        data = kwargs.get("data")
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
            data=data,
        )
        task_str = route_result.get("task", "")
        params = route_result.get("params", {})

        if not task_str:
            error = route_result.get("error", "无法确定操作类型")
            return {"success": False, "error": f"Excel工具无法理解需求：{error}。请在 context 中详细描述需求"}

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
            data=data,
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
                logger.opt(exception=True).error(f"[ExcelProcess] pipeline error at {op}: {e}")
                return {"success": False, "error": f"操作 {op} 执行失败: {e}"}

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
        """统一新旧入参，导出时让执行层只消费表格正文。"""
        normalized_instruction = (instruction or "").strip() or None
        normalized_content = (content or "").strip() or None
        normalized_context = (context or "").strip() or None
        normalized_content_type = (content_type or "auto").strip().lower()
        normalized_output_name = self._safe_output_name(output_name)

        if normalized_context and not normalized_content:
            body = self._extract_data_body(normalized_context, normalized_content_type)
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

        return {
            "instruction": normalized_instruction,
            "content": normalized_content,
            "content_type": normalized_content_type,
            "output_name": normalized_output_name,
            "route_context": "\n\n".join(route_parts).strip() or None,
        }

    def _get_handler(self, op: str):
        handlers = {
            "read": self._handle_read,
            "to_md": self._handle_to_md,
            "export": self._handle_export,
            "modify": self._handle_modify,
            "format": self._handle_format,
            "fill_template": self._handle_fill_template,
            "list_templates": self._handle_list_templates,
            "merge": self._handle_merge,
            "convert": self._handle_convert,
        }
        return handlers.get(op)

    async def _resolve_task(
        self,
        context: Optional[str],
        file_paths: Optional[List[str]],
        instruction: Optional[str] = None,
        content_type: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
    ) -> Dict:
        if not context and not file_paths and not data:
            return {"task": "", "error": "缺少 context、file_paths 和 data"}

        deterministic = self._resolve_task_deterministic(
            context,
            file_paths,
            instruction=instruction,
            content_type=content_type,
            data=data,
        )
        if deterministic:
            return deterministic

        try:
            router = self._get_router()
            return await router.route(context, file_paths)
        except Exception as e:
            logger.opt(exception=True).error(f"[ExcelProcess] LLM 路由异常: {e}")
            return {"task": "", "error": f"路由服务异常: {e}"}

    def _resolve_task_deterministic(
        self,
        context: Optional[str],
        file_paths: Optional[List[str]],
        instruction: Optional[str] = None,
        content_type: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict]:
        """低风险确定性路由，覆盖明确的数据导出 Excel 场景。"""
        # 优先：结构化 data + 样例附件 → 智能模板填充（无需 LLM 路由）
        if data and file_paths:
            return {
                "task": "fill_template",
                "params": {
                    "data": data,
                    "template_file": file_paths[0],
                    "output_name": self._safe_output_name(self._extract_output_name(context)),
                },
                "reason": "检测到 data + 样例附件，走智能模板填充",
            }

        if not context:
            return None
        intent = instruction or context or ""
        if not self._is_excel_export_instruction(intent):
            return None

        paths = file_paths or []
        if paths:
            first_path = Path(paths[0])
            first_ext = first_path.suffix.lower()
            if first_ext in (".csv", ".json"):
                return {
                    "task": "convert",
                    "params": {
                        "source_format": first_ext.lstrip("."),
                        "target_format": "excel",
                        "output_name": self._safe_output_name(self._extract_output_name(context))
                                       or first_path.with_suffix(".xlsx").name,
                    },
                    "reason": f"检测到 {first_ext} 附件并要求转换为 Excel",
                }

        body = self._extract_data_body(context, content_type)
        data_type = self._detect_data_type_for_export(body, content_type)
        if not data_type:
            return {"task": "export", "params": {"needs_data": True}, "reason": "仅检测到导出意图，缺少表格数据"}

        return {
            "task": "export",
            "params": {
                "data_type": data_type,
                "file_name": self._safe_output_name(self._extract_output_name(context)),
                "sheet_name": "Sheet1",
                "auto_format": True,
            },
            "reason": f"检测到 {data_type} 表格数据并要求导出 Excel",
        }

    def _update_context(self, ctx: PipelineContext, op: str, result: Dict) -> None:
        """根据操作结果更新 pipeline 上下文"""
        if op == "read":
            ctx.read_data = result
        elif op == "to_md":
            ctx.markdown_content = result.get("markdown", "")
            ctx.read_data = {"headers": [], "rows": [], "markdown": result.get("markdown", "")}

        if op in ("export", "modify", "format", "fill_template", "merge", "convert"):
            if result.get("file_path"):
                ctx.file_paths = [result["file_path"]]

    def _merge_results(self, ctx: PipelineContext) -> Dict[str, Any]:
        """合并所有步骤的结果"""
        merged = {"success": True, "steps": len(ctx.results)}

        for r in ctx.results:
            op = r["operation"]
            if op in ("read", "to_md"):
                if r.get("headers"):
                    merged["headers"] = r["headers"]
                    merged["rows"] = r["rows"]
                    merged["row_count"] = r.get("row_count", 0)
                    merged["column_count"] = r.get("column_count", 0)
                    merged["sheets"] = r.get("sheets", [])
                    merged["active_sheet"] = r.get("active_sheet", "")
                if r.get("markdown"):
                    merged["markdown"] = r["markdown"]
                if r.get("content"):
                    merged["content"] = r["content"]
                    merged["total_lines"] = r.get("total_lines", 0)
                    merged["document_info"] = r.get("document_info", {})
                    merged["sheet_count"] = r.get("sheet_count", 0)
                    merged["sheet_names"] = r.get("sheet_names", [])
            elif op in ("export", "modify", "format", "fill_template", "merge", "convert"):
                merged["file_path"] = r.get("file_path", "")
                merged["file_name"] = r.get("file_name", "")
                merged["file_size"] = r.get("file_size", 0)
                if r.get("file_id"):
                    merged["file_id"] = r["file_id"]
                if r.get("download_url"):
                    merged["download_url"] = r["download_url"]
                if op == "fill_template":
                    merged["variables_replaced"] = r.get("variables_replaced", 0)
                    if r.get("rows_rendered") is not None:
                        merged["rows_rendered"] = r["rows_rendered"]
                    if r.get("inferred_structure"):
                        merged["inferred_structure"] = r["inferred_structure"]
                if op in ("modify", "format"):
                    merged["operations_applied"] = r.get("operations_applied", 0)
                if op == "export":
                    merged["row_count"] = r.get("row_count", 0)
                    merged["sheet_name"] = r.get("sheet_name", "Sheet1")
            elif op == "list_templates":
                merged["templates"] = r.get("templates", [])

        if ctx.file_paths:
            merged["final_file_path"] = ctx.file_paths[0]

        return merged

    # ── 各操作处理器 ──

    async def _handle_read(self, ctx: PipelineContext, params: Dict) -> Dict:
        from src.tools.excel.excel_reader import read_sheet, read_excel_document

        if not ctx.file_paths:
            return {"success": False, "error": "read 操作需要 file_paths 参数"}

        try:
            file_path = self._resolve_file(ctx.file_paths[0])
        except FileNotFoundError as e:
            return {"success": False, "error": str(e)}

        # 精确范围读取 或 公式读取 → 使用 read_sheet（结构化数据）
        if params.get("range") or params.get("include_formulas"):
            return read_sheet(
                file_path,
                sheet_name=params.get("sheet_name"),
                cell_range=params.get("range"),
                include_formulas=params.get("include_formulas", False),
            )

        # 默认 → 使用 read_excel_document（完整文档信息 + 文本内容）
        return read_excel_document(
            file_path,
            sheet_name=params.get("sheet_name"),
        )

    async def _handle_to_md(self, ctx: PipelineContext, params: Dict) -> Dict:
        from src.tools.excel.excel_to_md import excel_to_markdown

        if not ctx.file_paths:
            return {"success": False, "error": "to_md 操作需要 file_paths 参数"}

        try:
            file_path = self._resolve_file(ctx.file_paths[0])
        except FileNotFoundError as e:
            return {"success": False, "error": str(e)}

        return excel_to_markdown(
            file_path,
            sheet_name=params.get("sheet_name"),
            max_rows=params.get("max_rows", 100),
        )

    async def _handle_export(self, ctx: PipelineContext, params: Dict) -> Dict:
        from src.tools.excel.excel_writer import create_excel

        if params.get("needs_data"):
            return {
                "success": False,
                "error": "未提供可导出的表格数据。Agent 需要先将完整的 Markdown表格、CSV 或 JSON数组 放入 content 参数中，再调用本工具导出。不能只传用户意图描述。",
                "needs_data": True,
            }

        # 数据来源优先级：params.data > ctx.content > ctx.context
        data = params.get("data") or ctx.content or ctx.context

        # 检查 context 是否包含实际表格数据（而非纯意图描述）
        if data:
            if isinstance(data, str):
                data = self._extract_data_body(data, params.get("data_type") or ctx.content_type)
                data_type = params.get("data_type") or self._detect_data_type_for_export(data, ctx.content_type)
            else:
                data_type = params.get("data_type") or "dict_list"
            if not data_type or data_type == "markdown":
                data_type = _detect_data_type(data)

            # 当检测结果为 markdown 时，验证是否真的是表格
            if data_type == "markdown" and "|" not in data:
                data = None  # 不是有效表格数据
        else:
            data_type = None

        if not data:
            return {
                "success": False,
                "error": "content/context 中未包含有效的表格数据。Agent 需要先将完整的表格内容（Markdown表格、JSON数组或CSV文本）放入 content 参数中，再调用本工具导出。不能只传用户意图描述。",
                "needs_data": True,
            }

        # JSON 字符串需要先解析为 Python 对象
        if data_type in ("json", "dict_list") and isinstance(data, str):
            import json
            try:
                data = json.loads(data)
                data_type = "dict_list"
            except json.JSONDecodeError:
                return {"success": False, "error": "JSON 数据格式不正确，请检查数据内容"}

        result = create_excel(
            data=data,
            data_type=data_type,
            file_name=ctx.output_name or self._safe_output_name(params.get("file_name") or params.get("output_name")),
            sheet_name=params.get("sheet_name", "Sheet1"),
            auto_format=params.get("auto_format", True),
        )

        if not result.get("success"):
            return result

        return result

    async def _handle_modify(self, ctx: PipelineContext, params: Dict) -> Dict:
        from src.tools.excel.excel_modifier import batch_modify
        from src.tools.excel.excel_lib import ExcelFileHandler

        if not ctx.file_paths:
            return {"success": False, "error": "modify 操作需要 file_paths 参数"}

        try:
            file_path = self._resolve_file(ctx.file_paths[0])
        except FileNotFoundError as e:
            return {"success": False, "error": str(e)}
        operations = params.get("operations", [])
        if not operations:
            return {"success": False, "error": "modify 操作需要 params.operations 参数"}

        wb, info = ExcelFileHandler.copy_and_open(file_path)
        result = batch_modify(wb, operations)

        if not result["success"]:
            wb.close()
            return result

        output_name = params.get("output_name") or (Path(file_path).stem + "_modified.xlsx")
        save_result = ExcelFileHandler.save_temp(wb, file_name=output_name)
        wb.close()

        save_result["success"] = True
        save_result["operations_applied"] = result["operations_applied"]
        save_result["message"] = f"已完成{result['operations_applied']}项修改操作"

        return save_result

    async def _handle_format(self, ctx: PipelineContext, params: Dict) -> Dict:
        from src.tools.excel.excel_formatter import batch_format
        from src.tools.excel.excel_lib import ExcelFileHandler

        if not ctx.file_paths:
            return {"success": False, "error": "format 操作需要 file_paths 参数"}

        try:
            file_path = self._resolve_file(ctx.file_paths[0])
        except FileNotFoundError as e:
            return {"success": False, "error": str(e)}
        operations = params.get("format_operations") or params.get("operations", [])
        if not operations:
            return {"success": False, "error": "format 操作需要 params.format_operations 参数"}

        wb, info = ExcelFileHandler.copy_and_open(file_path)
        result = batch_format(wb, operations)

        if not result["success"]:
            wb.close()
            return result

        output_name = params.get("output_name") or (Path(file_path).stem + "_formatted.xlsx")
        save_result = ExcelFileHandler.save_temp(wb, file_name=output_name)
        wb.close()

        save_result["success"] = True
        save_result["operations_applied"] = result["operations_applied"]
        save_result["message"] = f"已完成{result['operations_applied']}项格式化操作"

        return save_result

    async def _handle_fill_template(self, ctx: PipelineContext, params: Dict) -> Dict:
        # 确定模板来源：template_file > template_name > 附件
        template_name = params.get("template_name")
        template_file = params.get("template_file")
        if template_file:
            try:
                template_path = self._resolve_file(template_file)
            except FileNotFoundError as e:
                return {"success": False, "error": str(e)}
        elif template_name:
            template_path = str(Path("storage/excel_templates") / f"{template_name}.xlsx")
        elif ctx.file_paths:
            try:
                template_path = self._resolve_file(ctx.file_paths[0])
            except FileNotFoundError as e:
                return {"success": False, "error": str(e)}
        else:
            return {"success": False, "error": "fill_template 需要指定模板（template_file / template_name / file_paths）"}

        output_name = params.get("output_name") or ctx.output_name

        # 新路径：结构化 data → 智能模板填充（AI 分析样例结构 + 行数不匹配 + 样式保留）
        data = params.get("data") or ctx.data
        if data:
            from src.tools.excel.excel_template_ai import fill_with_sample
            return fill_with_sample(
                template_path, data,
                output_name=output_name,
                output_dir=self._resolve_output_dir(),
            )

        # 旧路径：variables 占位符替换（向后兼容）
        from src.tools.excel.excel_template import fill_template
        variables = params.get("variables", {})
        if not variables:
            return {"success": False, "error": "fill_template 需要 data（智能填充）或 variables（占位符替换）"}

        return fill_template(template_path, variables=variables, output_name=output_name)

    async def _handle_list_templates(self, ctx: PipelineContext, params: Dict) -> Dict:
        from src.tools.excel.excel_template import list_templates
        templates = list_templates()
        return {"success": True, "templates": templates}

    async def _handle_merge(self, ctx: PipelineContext, params: Dict) -> Dict:
        from src.tools.excel.excel_writer import merge_files

        if not ctx.file_paths or len(ctx.file_paths) < 2:
            return {"success": False, "error": "merge 操作需要 file_paths 中包含至少 2 个文件"}

        try:
            resolved_paths = [self._resolve_file(fp) for fp in ctx.file_paths]
        except FileNotFoundError as e:
            return {"success": False, "error": str(e)}

        result = merge_files(
            resolved_paths,
            output_name=params.get("output_name"),
            merge_mode=params.get("merge_mode", "sheets"),
        )

        if not result.get("success"):
            return result

        return result

    async def _handle_convert(self, ctx: PipelineContext, params: Dict) -> Dict:
        from src.tools.excel.excel_writer import convert_format

        if not ctx.file_paths:
            return {"success": False, "error": "convert 操作需要 file_paths 参数"}

        try:
            file_path = self._resolve_file(ctx.file_paths[0])
        except FileNotFoundError as e:
            return {"success": False, "error": str(e)}
        target_format = params.get("target_format", "xlsx")

        result = convert_format(
            file_path,
            target_format=target_format,
            output_name=ctx.output_name or self._safe_output_name(params.get("output_name")),
        )

        if not result.get("success"):
            return result

        return result

    @staticmethod
    def _is_excel_export_instruction(text: str) -> bool:
        if not text:
            return False
        return bool(re.search(
            r"(导出|生成|创建|制作|转成|转换|保存).{0,30}(Excel|excel|xlsx|电子表格|表格文件)|"
            r"(Markdown|markdown|CSV|csv|JSON|json|表格).{0,30}(转|转换|导出|生成).{0,20}(Excel|excel|xlsx)",
            text,
            re.IGNORECASE,
        ))

    @staticmethod
    def _has_markdown_table(text: str) -> bool:
        table_lines = [
            line.strip()
            for line in (text or "").splitlines()
            if line.strip().startswith("|") and line.strip().endswith("|")
        ]
        if len(table_lines) < 2:
            return False
        return any(re.match(r"^\|[\s\-:|]+\|$", line) for line in table_lines)

    def _detect_data_type_for_export(self, text: str, content_type: Optional[str] = None) -> str:
        normalized_type = (content_type or "").lower()
        if normalized_type in ("markdown", "md", "csv", "json"):
            return "markdown" if normalized_type == "md" else normalized_type
        return _detect_data_type(text)

    def _extract_data_body(self, text: str, content_type: Optional[str] = None) -> str:
        if not text:
            return ""

        stripped = text.strip()
        normalized_type = (content_type or "").lower()
        if normalized_type in ("json",) or stripped.startswith("[") or stripped.startswith("{"):
            return self._extract_json_body(stripped)
        if normalized_type == "csv":
            return self._extract_csv_body(stripped)
        if self._has_markdown_table(stripped):
            return self._extract_markdown_table_body(stripped)
        if "," in stripped and "\n" in stripped:
            return self._extract_csv_body(stripped)
        return stripped

    @staticmethod
    def _extract_markdown_table_body(text: str) -> str:
        lines = text.strip().splitlines()
        first_table_idx = None
        for idx, line in enumerate(lines):
            s = line.strip()
            if s.startswith("|") and s.endswith("|"):
                first_table_idx = idx
                break
        if first_table_idx is None or first_table_idx == 0:
            return text.strip()

        prefix = "\n".join(lines[:first_table_idx]).strip()
        instruction_re = re.compile(
            r"(导出|生成|创建|制作|转成|转换|保存).{0,30}(Excel|excel|xlsx|电子表格|表格文件)|"
            r"(以下|下面).{0,20}(表格|数据|内容|Markdown|markdown)",
            re.IGNORECASE,
        )
        if instruction_re.search(prefix):
            return "\n".join(lines[first_table_idx:]).strip()
        return text.strip()

    @staticmethod
    def _extract_csv_body(text: str) -> str:
        lines = text.strip().splitlines()
        first_csv_idx = None
        for idx, line in enumerate(lines):
            if "," in line and not re.search(r"(导出|生成|创建|转换|保存).{0,30}(Excel|excel|xlsx)", line, re.IGNORECASE):
                first_csv_idx = idx
                break
        if first_csv_idx is None or first_csv_idx == 0:
            return text.strip()
        return "\n".join(lines[first_csv_idx:]).strip()

    @staticmethod
    def _extract_json_body(text: str) -> str:
        start_candidates = [idx for idx in (text.find("["), text.find("{")) if idx >= 0]
        if not start_candidates:
            return text.strip()
        start = min(start_candidates)
        return text[start:].strip()

    @staticmethod
    def _extract_output_name(text: Optional[str]) -> Optional[str]:
        match = re.search(r"([\w\u4e00-\u9fff（）()《》+_\- ]+\.xlsx)", text or "")
        if match:
            return match.group(1).strip()
        return None

    @staticmethod
    def _safe_file_stem(title: str) -> str:
        stem = re.sub(r'[\\/:*?"<>|\r\n\t]+', "", title).strip(" .")
        return stem[:80] or "excel_export"

    @classmethod
    def _safe_output_name(cls, output_name: Optional[str]) -> Optional[str]:
        if not output_name:
            return None
        name = output_name.replace("\\", "/").split("/")[-1].strip()
        if name.lower().endswith(".xlsx"):
            name = name[:-5]
        return f"{cls._safe_file_stem(name)}.xlsx"


def _detect_data_type(text: str) -> str:
    """自动检测文本数据的格式类型"""
    stripped = text.strip()
    if stripped.startswith("[") or stripped.startswith("{"):
        return "json"
    if "|" in stripped and stripped.count("|") >= 2:
        return "markdown"
    if "," in stripped and "\n" in stripped:
        return "csv"
    return "markdown"
