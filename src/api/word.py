"""
Word 文档处理 API

提供 Markdown→Word、Word→Markdown 等文档转换的 HTTP 接口
"""

import re
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from loguru import logger

from src.api.auth import get_current_user

router = APIRouter(prefix="/api/word", tags=["word"])


# ============== 请求/响应模型 ==============

class MdToDocxRequest(BaseModel):
    """Markdown 转 Word 请求"""
    markdown_content: str = Field(..., description="Markdown 文本内容")
    filename: Optional[str] = Field(None, description="输出文件名，如 会议纪要.docx")
    template: Optional[str] = Field(None, description="可选 .docx 模板文件路径，传给 Pandoc --reference-doc")
    title: Optional[str] = Field(None, description="文档标题")


class DocxToMdRequest(BaseModel):
    """Word 转 Markdown 请求"""
    file_path: str = Field(..., description="Word 文件路径")


class WordReadRequest(BaseModel):
    """Word 文档读取请求"""
    file_path: str = Field(..., description="Word 文件路径")


class WordModifyRequest(BaseModel):
    """Word 文档修改请求"""
    file_path: str = Field(..., description="Word 文件路径")
    operations: list = Field(..., description="修改操作列表")
    output_name: Optional[str] = Field(None, description="输出文件名")


class WordFormatRequest(BaseModel):
    """Word 文档格式化请求"""
    file_path: str = Field(..., description="Word 文件路径")
    format_operations: list = Field(..., description="格式化操作列表")
    output_name: Optional[str] = Field(None, description="输出文件名")


class WordFillTemplateRequest(BaseModel):
    """Word 模板填充请求"""
    file_path: str = Field(..., description="模板文件路径")
    variables: dict = Field(..., description="变量名→值的映射")
    output_name: Optional[str] = Field(None, description="输出文件名")


class WordDiffRequest(BaseModel):
    """Word 文档对比请求"""
    file_path_old: str = Field(..., description="旧版本文档路径")
    file_path_new: str = Field(..., description="新版本文档路径")
    output_format: Optional[str] = Field("text", description="差异报告格式：text 或 markdown")


# ============== 辅助函数 ==============

def sanitize_error_info(error_msg: str) -> str:
    if not error_msg:
        return error_msg
    sensitive_patterns = [
        r'password["\s:=]+\S+',
        r'token["\s:=]+\S+',
        r'api[_-]?key["\s:=]+\S+',
    ]
    sanitized = error_msg
    for pattern in sensitive_patterns:
        sanitized = re.sub(
            pattern,
            lambda m: m.group(0).split('=')[0] + '=***',
            sanitized,
            flags=re.IGNORECASE,
        )
    return sanitized


# ============== API 端点 ==============

@router.post("/md-to-docx")
async def md_to_docx(
    request: MdToDocxRequest,
    current_user: dict = Depends(get_current_user),
):
    """Markdown 转 Word 文件下载"""
    try:
        from src.tools.word.md_to_word import convert, save_as
        from src.tools.word.word_process_tool import WordProcessTool

        doc = convert(
            request.markdown_content,
            template=request.template,
            title=request.title or "",
        )
        filename = request.filename or "document.docx"
        result = save_as(doc, file_name=filename)

        # 文件已在 uploads 目录，直接注册（不 copy）
        tool = WordProcessTool()
        download_info = await tool._register_download(result["file_path"], filename)

        response = {
            "success": True,
            "file_path": result["file_path"],
            "file_size": result["file_size"],
        }
        if download_info:
            response["file_id"] = download_info["file_id"]
            response["download_url"] = download_info["download_url"]
        return response
    except Exception as e:
        logger.opt(exception=True).error(f"Markdown转Word失败: {e}")
        return {"success": False, "error": "转换失败，请稍后重试", "debug": sanitize_error_info(str(e))}


@router.post("/docx-to-md")
async def docx_to_md(
    request: DocxToMdRequest,
    current_user: dict = Depends(get_current_user),
):
    """Word 转 Markdown"""
    try:
        from src.tools.word.word_to_md import convert

        md_text = convert(request.file_path)
        return {"success": True, "markdown": md_text}
    except Exception as e:
        logger.opt(exception=True).error(f"Word转Markdown失败: {e}")
        return {"success": False, "error": "转换失败，请稍后重试", "debug": sanitize_error_info(str(e))}


@router.post("/read")
async def read_word(
    request: WordReadRequest,
    current_user: dict = Depends(get_current_user),
):
    """读取 Word 文档内容"""
    try:
        from src.tools.word.word_reader import read_content

        return read_content(request.file_path)
    except Exception as e:
        logger.opt(exception=True).error(f"读取Word文档失败: {e}")
        return {"success": False, "error": "读取失败，请稍后重试", "debug": sanitize_error_info(str(e))}


@router.post("/analyze")
async def analyze_word(
    request: WordReadRequest,
    current_user: dict = Depends(get_current_user),
):
    """分析 Word 文档结构"""
    try:
        from src.tools.word.word_reader import analyze_structure

        return analyze_structure(request.file_path)
    except Exception as e:
        logger.opt(exception=True).error(f"分析Word文档失败: {e}")
        return {"success": False, "error": "分析失败，请稍后重试", "debug": sanitize_error_info(str(e))}


@router.post("/modify")
async def modify_word(
    request: WordModifyRequest,
    current_user: dict = Depends(get_current_user),
):
    """修改 Word 文档内容"""
    try:
        from src.tools.word.word_lib import WordFileHandler
        from src.tools.word.word_modifier import batch_modify
        from src.tools.word.word_process_tool import WordProcessTool
        from pathlib import Path

        doc, info = WordFileHandler.copy_and_open(request.file_path)
        result = batch_modify(doc, request.operations)

        output_name = request.output_name or (Path(request.file_path).stem + "_modified.docx")
        save_result = WordFileHandler.save_temp(doc, file_name=output_name)

        # 文件已在 uploads 目录，直接注册（不 copy）
        tool = WordProcessTool()
        download_info = await tool._register_download(save_result["file_path"], output_name)

        response = {
            "success": True,
            "file_path": save_result["file_path"],
            "file_size": save_result["file_size"],
            "operations_applied": result["operations_applied"],
        }
        if download_info:
            response["file_id"] = download_info["file_id"]
            response["download_url"] = download_info["download_url"]
        return response
    except Exception as e:
        logger.opt(exception=True).error(f"修改Word文档失败: {e}")
        return {"success": False, "error": "修改失败，请稍后重试", "debug": sanitize_error_info(str(e))}


@router.post("/format")
async def format_word(
    request: WordFormatRequest,
    current_user: dict = Depends(get_current_user),
):
    """格式化 Word 文档"""
    try:
        from src.tools.word.word_lib import WordFileHandler
        from src.tools.word.word_formatter import batch_format
        from src.tools.word.word_process_tool import WordProcessTool
        from pathlib import Path

        doc, info = WordFileHandler.copy_and_open(request.file_path)
        result = batch_format(doc, request.format_operations)

        output_name = request.output_name or (Path(request.file_path).stem + "_formatted.docx")
        save_result = WordFileHandler.save_temp(doc, file_name=output_name)

        # 文件已在 uploads 目录，直接注册（不 copy）
        tool = WordProcessTool()
        download_info = await tool._register_download(save_result["file_path"], output_name)

        response = {
            "success": True,
            "file_path": save_result["file_path"],
            "file_size": save_result["file_size"],
            "operations_applied": result["operations_applied"],
        }
        if download_info:
            response["file_id"] = download_info["file_id"]
            response["download_url"] = download_info["download_url"]
        return response
    except Exception as e:
        logger.opt(exception=True).error(f"格式化Word文档失败: {e}")
        return {"success": False, "error": "格式化失败，请稍后重试", "debug": sanitize_error_info(str(e))}


@router.post("/fill-template")
async def fill_template(
    request: WordFillTemplateRequest,
    current_user: dict = Depends(get_current_user),
):
    """填充 Word 模板变量"""
    try:
        from src.tools.word.word_lib import WordFileHandler
        from src.tools.word.template_manager import fill_template as do_fill
        from src.tools.word.word_process_tool import WordProcessTool
        from pathlib import Path

        doc, info = WordFileHandler.copy_and_open(request.file_path)
        fill_result = do_fill(doc, request.variables)

        output_name = request.output_name or (Path(request.file_path).stem + "_filled.docx")
        save_result = WordFileHandler.save_temp(doc, file_name=output_name)

        # 文件已在 uploads 目录，直接注册（不 copy）
        tool = WordProcessTool()
        download_info = await tool._register_download(save_result["file_path"], output_name)

        response = {
            "success": True,
            "file_path": save_result["file_path"],
            "file_size": save_result["file_size"],
            "variables_replaced": fill_result.get("total", 0),
            "per_variable": fill_result.get("per_variable", {}),
            "unmatched_variables": fill_result.get("unmatched_variables", []),
            "remaining_placeholders": fill_result.get("remaining_placeholders", []),
        }
        if download_info:
            response["file_id"] = download_info["file_id"]
            response["download_url"] = download_info["download_url"]
        return response
    except Exception as e:
        logger.opt(exception=True).error(f"填充模板失败: {e}")
        return {"success": False, "error": "填充失败，请稍后重试", "debug": sanitize_error_info(str(e))}


@router.post("/diff")
async def diff_word(
    request: WordDiffRequest,
    current_user: dict = Depends(get_current_user),
):
    """对比两个 Word 文档差异"""
    try:
        from src.tools.word.word_differ import diff

        return diff(request.file_path_old, request.file_path_new, output_format=request.output_format)
    except Exception as e:
        logger.opt(exception=True).error(f"文档对比失败: {e}")
        return {"success": False, "error": "对比失败，请稍后重试", "debug": sanitize_error_info(str(e))}


@router.get("/templates")
async def list_templates(
    current_user: dict = Depends(get_current_user),
):
    """列出可用的文档模板"""
    try:
        from src.tools.word.template_manager import list_templates

        templates = list_templates()
        return {"success": True, "templates": templates}
    except Exception as e:
        logger.opt(exception=True).error(f"获取模板列表失败: {e}")
        return {"success": False, "error": "获取模板列表失败", "debug": sanitize_error_info(str(e))}
