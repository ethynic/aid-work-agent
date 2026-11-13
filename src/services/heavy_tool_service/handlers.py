"""重工具服务远程 handler 实现（设计 §3/§4.1）。

切分原则（§3）：
- excel_process：Router 留轻容器，handler 远程；fill_template 拆两段——
  结构分析段（LLM）留轻容器，openpyxl 渲染段（fill_template_render）远程；
- paddleocr_doc_parsing：零 LLM，整体远程；
- upload_data_file：解析远程（parse），DB 注册留轻容器；
- analyze_data：B2 接入（数据操作远程派发），本期不注册；
- image_render：留轻容器不远程（§3 观察期）。

本模块只在重容器进程内执行（server.py 调度）；轻容器不 import 本模块的
执行函数。所有函数保持同步 def，由 server 侧线程池执行（假异步规范：
async 入口层负责 to_thread，底层保持同步）。
"""

import json
import os
from typing import Any, Dict, List, Optional

from loguru import logger


# excel_process 可远程派发的 handler（与 ExcelProcessTool._get_handler 键一致）
REMOTE_EXCEL_HANDLERS = (
    "read", "to_md", "export", "modify", "format",
    "fill_template", "list_templates", "merge", "convert",
)
# fill_template 的 data（智能填充）模式在远程只允许「渲染段」：
# 结构分析（LLM）必须发生在轻容器
FILL_TEMPLATE_RENDER_HANDLER = "fill_template_render"


def dispatch(tool: str, handler: Optional[str], params: Dict[str, Any]) -> Dict[str, Any]:
    """同步 handler 分发入口（server 在线程池中调用）。"""
    if tool == "excel_process":
        return _excel_process(handler, params)
    if tool == "paddleocr_doc_parsing":
        return _paddleocr(params)
    if tool == "upload_data_file":
        return _upload_parse(handler, params)
    return {"success": False, "error": f"重工具服务未注册工具: {tool}"}


# ==================== excel_process ====================

def _excel_process(handler: Optional[str], params: Dict[str, Any]) -> Dict[str, Any]:
    if handler == FILL_TEMPLATE_RENDER_HANDLER:
        return _excel_fill_render(params)
    if handler not in REMOTE_EXCEL_HANDLERS:
        return {"success": False, "error": f"excel_process 不支持远程 handler: {handler}"}

    from src.tools.excel.excel_process_tool import ExcelProcessTool, PipelineContext

    pipeline = PipelineContext(
        file_paths=params.get("file_paths"),
        content=params.get("content"),
        content_type=params.get("content_type"),
        instruction=params.get("instruction"),
        output_name=params.get("output_name"),
        data=params.get("data"),
    )
    route_params = params.get("route_params") or {}
    # data（智能填充）模式不允许直接进入重容器：结构分析（LLM 直连链路，
    # excel_template_ai._default_llm）必须留在轻容器（设计 §3/§7 计费前提），
    # LLM 侧应先在轻容器完成结构分析后走 fill_template_render
    if handler == "fill_template" and pipeline.data:
        return {"success": False,
                "error": "fill_template data 模式需先在本地完成结构分析（fill_template_render）"}
    tool = ExcelProcessTool()
    step = tool._get_handler(handler)
    if step is None:
        return {"success": False, "error": f"操作 {handler} 处理器未实现"}

    import asyncio

    # 事件循环内调用同步 dispatch 前提：server 在无事件循环的线程池线程执行，
    # asyncio.run 在此安全（与 load_table 线程池先例一致）
    try:
        return asyncio.run(step(pipeline, route_params))
    except FileNotFoundError as error:
        return {"success": False, "error": str(error)}
    except Exception as error:  # noqa: BLE001 - 与轻容器 pipeline error 口径一致
        logger.opt(exception=True).error(f"[HeavyTool] excel handler {handler} 执行失败: {error}")
        return {"success": False, "error": f"操作 {handler} 执行失败: {error}"}


def _excel_fill_render(params: Dict[str, Any]) -> Dict[str, Any]:
    """fill_template 渲染段：结构已由轻容器分析（LLM），此处纯 openpyxl。"""
    template_file = params.get("template_file")
    data = params.get("data")
    structure = params.get("structure")
    if not template_file or not data or not structure:
        return {"success": False,
                "error": "fill_template_render 需要 template_file、data、structure"}
    try:
        from src.tools.excel.excel_template_ai import render_with_structure

        return render_with_structure(
            template_file, data, structure,
            output_name=params.get("output_name"),
        )
    except Exception as error:  # noqa: BLE001 - 渲染失败按业务失败返回
        logger.opt(exception=True).error(f"[HeavyTool] fill_template 渲染段失败: {error}")
        return {"success": False, "error": f"模板渲染失败: {error}"}


# ==================== paddleocr_doc_parsing ====================

def _paddleocr(params: Dict[str, Any]) -> Dict[str, Any]:
    file_path = params.get("file_path") or None
    file_url = params.get("file_url") or None
    file_type = params.get("file_type")
    if not file_path and not file_url:
        return {"success": False, "error": "请提供文件路径或URL"}
    if file_path and not os.path.exists(file_path):
        return {"success": False, "error": f"文件不存在: {file_path}"}
    try:
        from src.tools.ocr.ocr_tool import paddleocr_doc_parsing

        # 同步阻塞云 API 调用（单次可达分钟级），server 线程池承载
        return paddleocr_doc_parsing(
            file_path=file_path, file_url=file_url, file_type=file_type)
    except Exception as error:  # noqa: BLE001
        logger.opt(exception=True).error(f"[HeavyTool] paddleocr 执行失败: {error}")
        return {"success": False, "error": "PaddleOCR API调用失败"}


# ==================== upload_data_file（解析段） ====================

def _upload_parse(handler: Optional[str], params: Dict[str, Any]) -> Dict[str, Any]:
    if handler not in (None, "parse"):
        return {"success": False, "error": f"upload_data_file 不支持远程 handler: {handler}"}
    file_path = params.get("file_path") or ""
    if not file_path or not os.path.exists(file_path):
        return {"success": False, "error": f"文件不存在: {file_path}"}
    ext = os.path.splitext(file_path)[1].lower()
    if ext not in (".xlsx", ".xls", ".csv"):
        return {"success": False, "error": f"不支持的文件格式: {ext}，仅支持 .xlsx、.xls、.csv"}
    try:
        from src.services.data_analysis.sheet_parser import SheetParser

        sheets = SheetParser().parse_file(file_path)
    except Exception as error:  # noqa: BLE001 - 与轻容器解析失败口径一致
        logger.opt(exception=True).error(f"[HeavyTool] 解析文件失败: {error}")
        return {"success": False, "error": f"解析文件失败: {error}"}
    if not sheets:
        return {"success": False, "error": "文件中没有找到有效的数据表"}
    return {"success": True, "sheets": [_json_safe(sheet) for sheet in sheets]}


def _json_safe(value: Any) -> Any:
    """sheet_info JSON 安全化：pandas 采样值可能含 NaN/Timestamp 等不可序列化形态。"""
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except Exception:
        return value


# ==================== 路径 allowlist（server 校验用，§4.1） ====================

# params 中按字段名单校验的路径键（其值为字符串或字符串列表）
PATH_PARAM_KEYS = ("file_path", "file_paths", "template_file", "output_dir", "paths")


def collect_request_paths(params: Dict[str, Any],
                          context: Dict[str, Any]) -> List[str]:
    """收集请求中需要 allowlist 校验的文件路径（设计 §4.1 路径入参服务端校验）。

    只校验声明的路径字段——content/指令文本等自由文本不做前缀判定（避免
    误伤以 / 开头的正文）；路径字段一律要求绝对路径且落在共享卷 allowlist 内。
    """
    paths: List[str] = []
    for key in PATH_PARAM_KEYS:
        value = params.get(key)
        if isinstance(value, str):
            paths.append(value)
        elif isinstance(value, (list, tuple)):
            paths.extend(item for item in value if isinstance(item, str))
    workdir = context.get("skill_workdir")
    if isinstance(workdir, str) and workdir:
        paths.append(workdir)
    return paths


def assert_paths_allowed(paths: List[str], roots: "tuple[str, ...]") -> Optional[str]:
    """全部路径必须为绝对路径且落在 allowlist 前缀下；违规返回错误描述。"""
    real_roots = [os.path.realpath(root) for root in roots]
    for path in paths:
        if not path.startswith("/"):
            return f"远程路径必须为绝对路径: {path}"
        resolved = os.path.realpath(path)
        if not any(resolved == root or resolved.startswith(root + os.sep)
                   for root in real_roots):
            return f"路径超出重工具服务允许范围: {path}"
    return None
