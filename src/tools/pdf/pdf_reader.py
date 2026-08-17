"""
PDF 读取模块

使用 PyMuPDF 提取文本内容和元数据，使用 pdfplumber 提取表格。
"""

from typing import Any, Dict, List, Optional

from loguru import logger


def read_text(file_path: str, pages: Optional[List[int]] = None) -> Dict[str, Any]:
    """读取 PDF 文本内容。

    Args:
        file_path: PDF 文件路径
        pages: 指定页码列表（从0开始的索引），None 表示全部页面

    Returns:
        {"success": True, "content": str, "pages": list, "metadata": dict}
    """
    import fitz

    try:
        doc = fitz.open(file_path)
        metadata = {
            "page_count": doc.page_count,
            "title": doc.metadata.get("title", ""),
            "author": doc.metadata.get("author", ""),
            "creation_date": doc.metadata.get("creationDate", ""),
            "mod_date": doc.metadata.get("modDate", ""),
        }

        target_pages = pages if pages is not None else range(doc.page_count)
        page_texts = []
        for page_num in target_pages:
            if page_num < 0 or page_num >= doc.page_count:
                continue
            page = doc[page_num]
            text = page.get_text("text")
            page_texts.append({"page": page_num + 1, "text": text})

        doc.close()

        full_content = "\n\n".join(p["text"] for p in page_texts)
        return {
            "success": True,
            "content": full_content,
            "pages": page_texts,
            "metadata": metadata,
        }
    except Exception as e:
        logger.opt(exception=True).error(f"[PdfReader] 读取失败: {e}")
        return {"success": False, "error": f"读取PDF失败: {e}"}


def extract_tables(file_path: str, pages: Optional[List[int]] = None) -> Dict[str, Any]:
    """提取 PDF 中的表格。

    Args:
        file_path: PDF 文件路径
        pages: 指定页码列表（从0开始的索引），None 表示全部页面

    Returns:
        {"success": True, "tables": list, "count": int}
    """
    import pdfplumber

    try:
        tables_all = []
        with pdfplumber.open(file_path) as pdf:
            target_pages = pages if pages is not None else range(len(pdf.pages))
            for page_num in target_pages:
                if page_num < 0 or page_num >= len(pdf.pages):
                    continue
                page = pdf.pages[page_num]
                tables = page.extract_tables()
                for i, table in enumerate(tables):
                    tables_all.append({
                        "page": page_num + 1,
                        "table_index": i + 1,
                        "data": table,
                    })

        return {
            "success": True,
            "tables": tables_all,
            "count": len(tables_all),
        }
    except Exception as e:
        logger.opt(exception=True).error(f"[PdfReader] 表格提取失败: {e}")
        return {"success": False, "error": f"提取表格失败: {e}"}


def get_metadata(file_path: str) -> Dict[str, Any]:
    """读取 PDF 元数据。"""
    import fitz

    try:
        doc = fitz.open(file_path)
        meta = dict(doc.metadata)
        meta["page_count"] = doc.page_count
        doc.close()
        return {"success": True, "metadata": meta}
    except Exception as e:
        return {"success": False, "error": f"读取元数据失败: {e}"}
