"""PDF 结构检查模块。"""

from pathlib import Path
from typing import Any, Dict

from loguru import logger


def inspect_pdf(file_path: str) -> Dict[str, Any]:
    """检查 PDF 基础结构、页面信息和元数据。"""
    path = Path(file_path)
    if not path.exists():
        return {"success": False, "error": f"文件不存在: {file_path}"}
    if path.suffix.lower() != ".pdf":
        return {"success": False, "error": f"不是PDF文件: {file_path}"}

    encrypted = False
    pypdf_error = ""
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        encrypted = bool(reader.is_encrypted)
        if encrypted:
            return {
                "success": False,
                "error": "PDF已加密，无法检查内容",
                "encrypted": True,
                "file_size": path.stat().st_size,
            }
    except Exception as e:
        pypdf_error = str(e)

    try:
        import fitz

        doc = fitz.open(str(path))
        pages = []
        warnings = []

        for i in range(doc.page_count):
            page = doc[i]
            rect = page.rect
            text = page.get_text("text") or ""
            text_chars = len(text.strip())
            if text_chars == 0:
                warnings.append(f"第 {i + 1} 页未提取到文本，可能是扫描件或空白页")
            pages.append({
                "page": i + 1,
                "width": round(float(rect.width), 2),
                "height": round(float(rect.height), 2),
                "rotation": int(page.rotation),
                "text_chars": text_chars,
            })

        metadata = dict(doc.metadata or {})
        page_count = int(doc.page_count)
        doc.close()

        result = {
            "success": True,
            "file_path": str(path.absolute()),
            "file_size": path.stat().st_size,
            "page_count": page_count,
            "encrypted": encrypted,
            "pages": pages,
            "metadata": metadata,
            "warnings": warnings,
        }
        if pypdf_error:
            result["warnings"].append(f"pypdf 检查失败，已使用 PyMuPDF 结果: {pypdf_error}")
        return result
    except Exception as e:
        logger.error(f"[PdfInspector] 检查失败: {e}", exc_info=True)
        return {"success": False, "error": f"PDF检查失败: {e}", "encrypted": encrypted}
