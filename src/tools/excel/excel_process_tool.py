"""
Excel 电子表格处理工具 — Agent 唯一入口

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
    ANALYZE = "analyze"
    TO_MD = "to_md"
    EXPORT = "export"
    MODIFY = "modify"
    FORMAT = "format"
    CHART = "chart"
    FILL_TEMPLATE = "fill_template"
    LIST_TEMPLATES = "list_templates"
    MERGE = "merge"
    CONVERT = "convert"

    ALL = {READ, ANALYZE, TO_MD, EXPORT, MODIFY, FORMAT, CHART,
           FILL_TEMPLATE, LIST_TEMPLATES, MERGE, CONVERT}


class ExcelProcessInput(BaseModel):
    context: Optional[str] = Field(
        None,
        description="用户的原始需求描述，包含所有相关内容。"
                    "如果要把对话中的表格数据转为Excel，context 中必须包含表格数据（Markdown表格或JSON数组）；"
                    "如果涉及附件操作，附件路径通过 file_paths 传入"
    )
    file_paths: Optional[List[str]] = Field(
        None,
        description="用户上传的附件文件路径列表（.xlsx/.csv/.json 文件等）"
    )


class PipelineContext:
    """Pipeline 执行过程中的上下文，在步骤间传递"""

    def __init__(self, file_paths: Optional[List[str]] = None,
                 context: Optional[str] = None):
        self.file_paths: List[str] = list(file_paths) if file_paths else []
        self.original_file_paths: List[str] = list(file_paths) if file_paths else []
        self.context: Optional[str] = context
        self.read_data: Optional[Dict] = None
        self.analysis_result: Optional[Dict] = None
        self.markdown_content: Optional[str] = None
        self.results: List[Dict] = []


TOOL_DESCRIPTION = """Excel电子表格处理工具。所有与Excel(.xlsx)相关的操作都通过本工具处理。

⚠️ 触发规则 — 遇到以下场景必须调用本工具：
- 用户要求导出、生成、创建Excel文件
- 用户要求读取、分析、修改Excel文件
- 用户要求将数据(表格、CSV、JSON)转为Excel
- 用户要求将Excel转为其他格式(Markdown、CSV)
- 用户要求基于模板填充数据生成Excel报告
- 用户要求对Excel数据进行统计分析、生成图表
- 用户上传了.xlsx/.csv文件并要求处理

调用方式（重要）：
- context 参数必须包含**完整的表格数据**，不能只传用户意图描述
- 如果需要将数据导出为Excel，context 中必须包含完整的 Markdown表格 或 JSON数组 数据
- 如果当前对话中已有表格数据（由其他工具生成或用户提供），必须将其完整放入 context 中
- 如果还没有表格数据，Agent 应先通过其他方式准备好数据，再调用本工具
- 用户上传的附件路径放在 file_paths 中
工具会自动判断并执行合适的操作。"""


class ExcelProcessTool(BaseTool):
    name = "excel_process"
    description = TOOL_DESCRIPTION
    display_name = "Excel电子表格处理"
    category = "file"
    InputModel = ExcelProcessInput

    def __init__(self):
        super().__init__()
        self._router = None

    def _get_router(self):
        if self._router is None:
            from src.tools.excel.excel_router import ExcelRouter
            self._router = ExcelRouter()
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
                logger.error(f"[ExcelProcess] pipeline error at {op}: {e}", exc_info=True)
                return {"success": False, "error": f"操作 {op} 执行失败: {e}"}

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
            "analyze": self._handle_analyze,
            "to_md": self._handle_to_md,
            "export": self._handle_export,
            "modify": self._handle_modify,
            "format": self._handle_format,
            "chart": self._handle_chart,
            "fill_template": self._handle_fill_template,
            "list_templates": self._handle_list_templates,
            "merge": self._handle_merge,
            "convert": self._handle_convert,
        }
        return handlers.get(op)

    async def _resolve_task(self, context: Optional[str], file_paths: Optional[List[str]]) -> Dict:
        if not context and not file_paths:
            return {"task": "", "error": "缺少 context 和 file_paths"}

        try:
            router = self._get_router()
            return await router.route(context, file_paths)
        except Exception as e:
            logger.error(f"[ExcelProcess] LLM 路由异常: {e}", exc_info=True)
            return {"task": "", "error": f"路由服务异常: {e}"}

    def _update_context(self, ctx: PipelineContext, op: str, result: Dict) -> None:
        """根据操作结果更新 pipeline 上下文"""
        if op == "read":
            ctx.read_data = result
        elif op == "analyze":
            ctx.analysis_result = result
        elif op == "to_md":
            ctx.markdown_content = result.get("markdown", "")
            ctx.read_data = {"headers": [], "rows": [], "markdown": result.get("markdown", "")}

        if op in ("export", "modify", "format", "chart", "fill_template", "merge", "convert"):
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
            elif op == "analyze":
                merged["analysis"] = r.get("result", {})
                merged["analysis_type"] = r.get("analysis_type", "summary")
            elif op in ("export", "modify", "format", "chart", "fill_template", "merge", "convert"):
                merged["file_path"] = r.get("file_path", "")
                merged["file_name"] = r.get("file_name", "")
                merged["file_size"] = r.get("file_size", 0)
                if r.get("file_id"):
                    merged["file_id"] = r["file_id"]
                if r.get("download_url"):
                    merged["download_url"] = r["download_url"]
                if op == "fill_template":
                    merged["variables_replaced"] = r.get("variables_replaced", 0)
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
        from src.tools.excel.excel_reader import read_sheet

        if not ctx.file_paths:
            return {"success": False, "error": "read 操作需要 file_paths 参数"}

        file_path = ctx.file_paths[0]
        return read_sheet(
            file_path,
            sheet_name=params.get("sheet_name"),
            cell_range=params.get("range"),
            include_formulas=params.get("include_formulas", False),
        )

    async def _handle_analyze(self, ctx: PipelineContext, params: Dict) -> Dict:
        from src.tools.excel.excel_analyzer import analyze_data

        if not ctx.file_paths:
            return {"success": False, "error": "analyze 操作需要 file_paths 参数"}

        file_path = ctx.file_paths[0]
        return analyze_data(
            file_path,
            sheet_name=params.get("sheet_name"),
            analysis_type=params.get("analysis_type", "summary"),
            group_by=params.get("group_by"),
            aggregations=params.get("aggregations"),
        )

    async def _handle_to_md(self, ctx: PipelineContext, params: Dict) -> Dict:
        from src.tools.excel.excel_to_md import excel_to_markdown

        if not ctx.file_paths:
            return {"success": False, "error": "to_md 操作需要 file_paths 参数"}

        file_path = ctx.file_paths[0]
        return excel_to_markdown(
            file_path,
            sheet_name=params.get("sheet_name"),
            max_rows=params.get("max_rows", 100),
        )

    async def _handle_export(self, ctx: PipelineContext, params: Dict) -> Dict:
        from src.tools.excel.excel_writer import create_excel

        # 数据来源优先级：params.data > ctx.context
        data = params.get("data") or ctx.context

        # 检查 context 是否包含实际表格数据（而非纯意图描述）
        if data:
            data_type = params.get("data_type")
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
                "error": "context 中未包含有效的表格数据。Agent 需要先将完整的表格内容（Markdown表格、JSON数组或CSV文本）放入 context 参数中，再调用本工具导出。不能只传用户意图描述。",
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
            file_name=params.get("file_name"),
            sheet_name=params.get("sheet_name", "Sheet1"),
            auto_format=params.get("auto_format", True),
        )

        if not result.get("success"):
            return result

        # 注册下载
        output_name = params.get("file_name") or "export.xlsx"
        download_info = await self._register_download(result["file_path"], output_name)
        if download_info:
            result["file_id"] = download_info["file_id"]
            result["download_url"] = download_info["download_url"]

        return result

    async def _handle_modify(self, ctx: PipelineContext, params: Dict) -> Dict:
        from src.tools.excel.excel_modifier import batch_modify
        from src.tools.excel.excel_lib import ExcelFileHandler

        if not ctx.file_paths:
            return {"success": False, "error": "modify 操作需要 file_paths 参数"}

        file_path = ctx.file_paths[0]
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

        download_info = await self._register_download(save_result["file_path"], output_name)
        if download_info:
            save_result["file_id"] = download_info["file_id"]
            save_result["download_url"] = download_info["download_url"]
        return save_result

    async def _handle_format(self, ctx: PipelineContext, params: Dict) -> Dict:
        from src.tools.excel.excel_formatter import batch_format
        from src.tools.excel.excel_lib import ExcelFileHandler

        if not ctx.file_paths:
            return {"success": False, "error": "format 操作需要 file_paths 参数"}

        file_path = ctx.file_paths[0]
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

        download_info = await self._register_download(save_result["file_path"], output_name)
        if download_info:
            save_result["file_id"] = download_info["file_id"]
            save_result["download_url"] = download_info["download_url"]
        return save_result

    async def _handle_chart(self, ctx: PipelineContext, params: Dict) -> Dict:
        from src.tools.excel.excel_chart import create_chart
        from src.tools.excel.excel_lib import ExcelFileHandler

        if not ctx.file_paths:
            return {"success": False, "error": "chart 操作需要 file_paths 参数"}

        file_path = ctx.file_paths[0]
        wb, info = ExcelFileHandler.copy_and_open(file_path)

        result = create_chart(
            wb,
            chart_type=params.get("chart_type", "bar"),
            x_column=params.get("x_column", ""),
            y_columns=params.get("y_columns", []),
            title=params.get("title"),
            sheet_name=params.get("sheet_name"),
            **{k: v for k, v in params.items() if k not in (
                "chart_type", "x_column", "y_columns", "title", "sheet_name")},
        )

        if not result["success"]:
            wb.close()
            return result

        output_name = params.get("output_name") or (Path(file_path).stem + "_chart.xlsx")
        save_result = ExcelFileHandler.save_temp(wb, file_name=output_name)
        wb.close()

        save_result["success"] = True
        save_result["chart_type"] = result.get("chart_type", "")
        save_result["chart_title"] = result.get("chart_title", "")

        download_info = await self._register_download(save_result["file_path"], output_name)
        if download_info:
            save_result["file_id"] = download_info["file_id"]
            save_result["download_url"] = download_info["download_url"]
        return save_result

    async def _handle_fill_template(self, ctx: PipelineContext, params: Dict) -> Dict:
        from src.tools.excel.excel_template import fill_template
        from src.tools.excel.excel_lib import ExcelFileHandler

        template_name = params.get("template_name")
        template_file = params.get("template_file")
        variables = params.get("variables", {})

        if not variables:
            return {"success": False, "error": "fill_template 操作需要 params.variables 参数"}

        # 确定模板来源
        if template_file:
            template_path = template_file
        elif template_name:
            template_path = str(Path("storage/excel_templates") / f"{template_name}.xlsx")
        elif ctx.file_paths:
            # 默认使用第一个附件作为模板
            template_path = ctx.file_paths[0]
        else:
            return {"success": False, "error": "fill_template 需要指定模板（template_name 或 template_file）"}

        result = fill_template(template_path, variables=variables,
                               output_name=params.get("output_name"))

        if not result.get("success"):
            return result

        output_name = params.get("output_name") or "filled_template.xlsx"
        download_info = await self._register_download(result["file_path"], output_name)
        if download_info:
            result["file_id"] = download_info["file_id"]
            result["download_url"] = download_info["download_url"]
        return result

    async def _handle_list_templates(self, ctx: PipelineContext, params: Dict) -> Dict:
        from src.tools.excel.excel_template import list_templates
        templates = list_templates()
        return {"success": True, "templates": templates}

    async def _handle_merge(self, ctx: PipelineContext, params: Dict) -> Dict:
        from src.tools.excel.excel_writer import merge_files

        if not ctx.file_paths or len(ctx.file_paths) < 2:
            return {"success": False, "error": "merge 操作需要 file_paths 中包含至少 2 个文件"}

        result = merge_files(
            ctx.file_paths,
            output_name=params.get("output_name"),
            merge_mode=params.get("merge_mode", "sheets"),
        )

        if not result.get("success"):
            return result

        output_name = params.get("output_name") or "merged.xlsx"
        download_info = await self._register_download(result["file_path"], output_name)
        if download_info:
            result["file_id"] = download_info["file_id"]
            result["download_url"] = download_info["download_url"]
        return result

    async def _handle_convert(self, ctx: PipelineContext, params: Dict) -> Dict:
        from src.tools.excel.excel_writer import convert_format

        if not ctx.file_paths:
            return {"success": False, "error": "convert 操作需要 file_paths 参数"}

        file_path = ctx.file_paths[0]
        target_format = params.get("target_format", "xlsx")

        result = convert_format(
            file_path,
            target_format=target_format,
            output_name=params.get("output_name"),
        )

        if not result.get("success"):
            return result

        output_name = params.get("output_name") or "converted"
        download_info = await self._register_download(result["file_path"], output_name)
        if download_info:
            result["file_id"] = download_info["file_id"]
            result["download_url"] = download_info["download_url"]
        return result

    async def _register_download(self, file_path: str, display_name: str) -> Dict[str, str]:
        """自动注册文件到下载系统"""
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
                '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                '.xls': 'application/vnd.ms-excel',
                '.csv': 'text/csv',
                '.pdf': 'application/pdf',
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
