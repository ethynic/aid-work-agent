"""
PDF 转 Markdown 模块

使用 PyMuPDF4LLM 进行结构化转换，包含自动降级到 OCR 的策略。
"""

from typing import Any, Dict, List, Optional

from loguru import logger


def convert(file_path: str, pages: Optional[List[int]] = None) -> Dict[str, Any]:
    """将 PDF 转换为 Markdown（基于 PyMuPDF4LLM）。

    Args:
        file_path: PDF 文件路径
        pages: 指定页码列表（从0开始的索引），None 表示全部页面

    Returns:
        {"success": True, "markdown": str, "page_count": int}
    """
    import pymupdf4llm

    try:
        md_chunks = pymupdf4llm.to_markdown(file_path, page_chunks=True)

        if pages is not None:
            selected = [md_chunks[p] for p in pages if p < len(md_chunks)]
        else:
            selected = md_chunks

        full_text = "\n\n---\n\n".join(
            chunk.get("text", "") if isinstance(chunk, dict) else str(chunk)
            for chunk in selected
        )

        return {
            "success": True,
            "markdown": full_text,
            "page_count": len(selected),
        }
    except Exception as e:
        logger.error(f"[PdfToMd] 转换失败: {e}", exc_info=True)
        return {"success": False, "error": f"PDF转Markdown失败: {e}"}


def convert_smart(file_path: str) -> Dict[str, Any]:
    """智能转换：自动判断文字型或扫描件。

    1. 先尝试 PyMuPDF4LLM 提取（文字型 PDF）
    2. 如果结果为空或极少文本 → 判断为扫描件 → 自动降级到 OCR
    """
    result = convert(file_path)
    if not result.get("success"):
        # PyMuPDF4LLM 失败，尝试 OCR
        return _ocr_fallback(file_path)

    md_text = result.get("markdown", "").strip()
    if len(md_text) < 50:
        logger.info("[PdfToMd] 文本过少，自动降级到 OCR")
        return _ocr_fallback(file_path)

    result["source"] = "text_extract"
    return result


def _ocr_fallback(file_path: str) -> Dict[str, Any]:
    """OCR 降级：调用已有 PaddleOCR 工具。"""
    try:
        from src.tools.ocr.ocr_tool import paddleocr_doc_parsing
        ocr_result = paddleocr_doc_parsing(file_path=file_path, file_type=0)
        if ocr_result.get("success"):
            return {
                "success": True,
                "markdown": ocr_result["full_text"],
                "page_count": len(ocr_result.get("texts", [])),
                "source": "ocr",
            }
        return ocr_result
    except Exception as e:
        logger.error(f"[PdfToMd] OCR 降级失败: {e}", exc_info=True)
        return {"success": False, "error": f"OCR识别失败: {e}"}
